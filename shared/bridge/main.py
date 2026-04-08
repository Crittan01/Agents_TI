"""
Router unificado AnsibleBot.
Un solo webhook de Teams despacha a health-check o log-monitor
segun el intent resuelto por el NLU compartido.
"""
import os
import sys
import re
import json
import time
import hmac
import hashlib
import base64
import logging
import threading
import urllib3

import anthropic
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel

# ─── Path setup ──────────────────────────────────────────────────────────────
_HERE       = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR = os.path.dirname(os.path.dirname(_HERE))

sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_AGENTS_DIR, "health-check",     "bridge"))
sys.path.insert(0, os.path.join(_AGENTS_DIR, "log-monitor",      "bridge"))
sys.path.insert(0, os.path.join(_AGENTS_DIR, "remediator",       "bridge"))
sys.path.insert(0, os.path.join(_AGENTS_DIR, "inventory-query",  "bridge"))

load_dotenv(os.path.join(_HERE, ".env"))

from nlu import parse_intent
from aap import (
    aap_get,
    resolve_host_name,
    host_exists_in_inventory,
    group_exists_in_inventory,
    launch_health_job,
    launch_log_job,
    launch_remediation_job,
    extract_health_data,
    extract_log_data,
    extract_remediation_data,
)
import health_cards     as hc
import log_cards        as lc
import remediator_cards as rc
import inventory_cards  as ic
from inventory_reader import get_inventory_text, get_inventory

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_HERE, "ansiblebot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TEAMS_WEBHOOK_URL  = os.getenv("TEAMS_WEBHOOK_URL", "")
TEAMS_HMAC_TOKEN   = os.getenv("TEAMS_HMAC_TOKEN",  "")
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_inv_client = (
    anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY) if _ANTHROPIC_API_KEY else None
)

_DEFAULT_LOG_PARAMS = {
    "time_window_hours": 2,
    "severity":          "ERROR",
    "keyword":           "",
    "max_sample_lines":  30,
}

_PAGE_SIZE = 15

app = FastAPI()


# ─── Modelos ─────────────────────────────────────────────────────────────────

class TeamsPayload(BaseModel):
    text: str
    model_config = {"extra": "allow"}


# ─── Seguridad ───────────────────────────────────────────────────────────────

def verify_teams_signature(body: bytes, auth_header: str) -> bool:
    if not TEAMS_HMAC_TOKEN or not auth_header:
        return False
    try:
        _, received_sig = auth_header.split(" ", 1)
        key = base64.b64decode(TEAMS_HMAC_TOKEN)
        expected_sig = base64.b64encode(
            hmac.new(key, body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(received_sig, expected_sig)
    except Exception:
        return False


# ─── Utilidades ──────────────────────────────────────────────────────────────

def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def post_to_teams(card: dict, _retries: int = 3, _backoff: float = 5.0) -> None:
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }
    size = len(json.dumps(payload).encode("utf-8"))
    logger.info("TEAMS payload size: %d bytes", size)
    for attempt in range(1, _retries + 1):
        try:
            r = requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
            logger.info("TEAMS response: %s %s", r.status_code, r.text[:200])
            if r.status_code in (200, 201, 202):
                return
            if r.status_code < 500:
                # 4xx — no tiene sentido reintentar
                logger.error("TEAMS error %s (no reintentable)", r.status_code)
                return
            # 5xx — reintentable
            logger.warning("TEAMS %s en intento %d/%d — reintentando en %.0fs",
                           r.status_code, attempt, _retries, _backoff)
        except Exception as e:
            logger.warning("TEAMS excepcion en intento %d/%d: %s", attempt, _retries, e)
        if attempt < _retries:
            time.sleep(_backoff)
            _backoff *= 2   # backoff exponencial: 5s → 10s → 20s
    logger.error("TEAMS: se agotaron %d intentos — card no entregada", _retries)


def _extract_log_params(parsed: dict) -> dict:
    return {
        "time_window_hours": int(parsed.get("time_window_hours", _DEFAULT_LOG_PARAMS["time_window_hours"])),
        "severity":          parsed.get("severity", _DEFAULT_LOG_PARAMS["severity"]).upper(),
        "keyword":           parsed.get("keyword",  _DEFAULT_LOG_PARAMS["keyword"]),
        "max_sample_lines":  _DEFAULT_LOG_PARAMS["max_sample_lines"],
    }


def _error_card(text: str) -> dict:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [{"type": "TextBlock", "text": text, "color": "Attention", "wrap": True}],
    }


# ─── Background tasks: SALUD ─────────────────────────────────────────────────

def _wait_health_group(group: str, job_id: int, fleet: dict, lock: threading.Lock) -> None:
    logger.info("FLEET-H esperando job #%d para %s", job_id, group)
    for _ in range(60):
        time.sleep(5)
        status = aap_get(f"/api/v2/jobs/{job_id}/").get("status", "")
        if status == "successful":
            data = extract_health_data(job_id, group)
            if data:
                with lock:
                    fleet[group] = data
            logger.info("FLEET-H %s OK — %d hosts", group, len(data or {}))
            return
        if status in ("failed", "error", "canceled"):
            logger.warning("FLEET-H %s job #%d terminó: %s", group, job_id, status)
            return
    logger.warning("FLEET-H %s timeout job #%d", group, job_id)


def wait_and_report_fleet_health(targets: list, environment: str, filter_mode: str) -> None:
    group_jobs: dict[str, int] = {}
    for group in targets:
        resp   = launch_health_job(group)
        job_id = resp.get("id")
        if job_id:
            group_jobs[group] = job_id
            logger.info("FLEET-H job #%d lanzado para %s", job_id, group)
        else:
            logger.warning("FLEET-H no se pudo lanzar job para %s", group)

    if not group_jobs:
        post_to_teams(_error_card("Fleet check: no se pudo lanzar ningun job."))
        return

    fleet: dict[str, dict] = {}
    lock    = threading.Lock()
    threads = [
        threading.Thread(target=_wait_health_group, args=(g, jid, fleet, lock), daemon=True)
        for g, jid in group_jobs.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=370)

    if not fleet:
        post_to_teams(_error_card("Fleet check: jobs completados pero sin datos de salud."))
        return

    if filter_mode == "critical":
        all_critical: list[dict] = []
        for group, hosts in sorted(fleet.items()):
            for hostname, data in sorted(hosts.items()):
                cpu     = float(data.get("cpu", 0))
                ram_pct = float(data.get("ram", {}).get("used_percent", 0))
                disks   = data.get("disks", [])
                worst_d = max(disks, key=lambda d: float(d.get("usage_pct", 0)), default={})
                disk    = float(worst_d.get("usage_pct", 0)) if worst_d else 0
                if max(cpu, ram_pct, disk) >= 70:
                    all_critical.append({
                        "hostname": hostname, "cpu": cpu, "ram": ram_pct, "disk": disk,
                        "mount": worst_d.get("mount", "") if worst_d else "",
                    })
        all_critical.sort(key=lambda x: -max(x["cpu"], x["ram"], x["disk"]))
        chunks      = [all_critical[i:i + _PAGE_SIZE] for i in range(0, max(len(all_critical), 1), _PAGE_SIZE)]
        total_pages = len(chunks)
        for page_num, chunk in enumerate(chunks, start=1):
            card = hc._rebuild_fleet_card_page(environment, fleet, chunk, page_num, total_pages)
            logger.info("FLEET-H card %d/%d — %d bytes", page_num, total_pages,
                        len(json.dumps(card).encode("utf-8")))
            post_to_teams(card)
    else:
        card = hc.build_fleet_results_card(environment, fleet)
        logger.info("FLEET-H card — %d bytes", len(json.dumps(card).encode("utf-8")))
        post_to_teams(card)


def wait_and_report_health(job_id: int, target: str, target_type: str,
                           resources: list = None) -> None:
    resources = resources or ["all"]
    logger.info("BG-H esperando job #%d para %s (%s) resources=%s",
                job_id, target, target_type, resources)
    for _ in range(60):
        time.sleep(5)
        status = aap_get(f"/api/v2/jobs/{job_id}/").get("status", "")
        logger.info("BG-H job #%d status: %s", job_id, status)

        if status == "successful":
            data = extract_health_data(job_id, target)
            if data:
                if target_type == "group" or len(data) > 1:
                    card = hc.build_group_results_card(target, job_id, data)
                else:
                    host_data = data.get(target, next(iter(data.values()), {}))
                    card = hc.build_results_card(target, job_id, host_data)
            else:
                card = hc.build_error_card(target, job_id, "Job exitoso pero sin datos de salud.")
            post_to_teams(card)
            return

        if status in ("failed", "error", "canceled"):
            post_to_teams(hc.build_error_card(target, job_id, f"El job terminó con estado: {status}"))
            return

    post_to_teams(hc.build_error_card(target, job_id, "Timeout: el job superó 5 minutos."))


# ─── Background tasks: LOGS ──────────────────────────────────────────────────

def _wait_log_group(group: str, job_id: int, fleet: dict, lock: threading.Lock) -> None:
    logger.info("FLEET-L esperando job #%d para %s", job_id, group)
    for _ in range(60):
        time.sleep(5)
        status = aap_get(f"/api/v2/jobs/{job_id}/").get("status", "")
        if status == "successful":
            data = extract_log_data(job_id)
            if data:
                with lock:
                    fleet[group] = data
            logger.info("FLEET-L %s OK — %d hosts", group, len(data or {}))
            return
        if status in ("failed", "error", "canceled"):
            logger.warning("FLEET-L %s job #%d terminó: %s", group, job_id, status)
            return
    logger.warning("FLEET-L %s timeout job #%d", group, job_id)


def wait_and_report_fleet_log(targets: list, environment: str, params: dict) -> None:
    group_jobs: dict[str, int] = {}
    for group in targets:
        resp   = launch_log_job(group, params)
        job_id = resp.get("id")
        if job_id:
            group_jobs[group] = job_id
            logger.info("FLEET-L job #%d lanzado para %s", job_id, group)
        else:
            logger.warning("FLEET-L no se pudo lanzar job para %s", group)

    if not group_jobs:
        post_to_teams(_error_card("Log fleet: no se pudo lanzar ningun job."))
        return

    fleet: dict[str, dict] = {}
    lock    = threading.Lock()
    threads = [
        threading.Thread(target=_wait_log_group, args=(g, jid, fleet, lock), daemon=True)
        for g, jid in group_jobs.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=370)

    if not fleet:
        post_to_teams(_error_card("Log fleet: jobs completados pero sin datos de logs."))
        return

    all_hosts: dict[str, dict] = {}
    for group_dict in fleet.values():
        all_hosts.update(group_dict)

    sorted_hosts = sorted(all_hosts.items())
    chunks       = [sorted_hosts[i:i + _PAGE_SIZE] for i in range(0, max(len(sorted_hosts), 1), _PAGE_SIZE)]
    total_pages  = len(chunks)
    job_id_ref   = list(group_jobs.values())[0]

    for page_num, chunk in enumerate(chunks, start=1):
        card = lc.build_log_group_results_card(
            environment.capitalize(), job_id_ref, dict(chunk), params, page_num, total_pages
        )
        logger.info("FLEET-L card %d/%d — %d bytes", page_num, total_pages,
                    len(json.dumps(card).encode("utf-8")))
        post_to_teams(card)


def wait_and_report_log(job_id: int, target: str, target_type: str, params: dict) -> None:
    logger.info("BG-L esperando job #%d para %s (%s)", job_id, target, target_type)
    for _ in range(60):
        time.sleep(5)
        status = aap_get(f"/api/v2/jobs/{job_id}/").get("status", "")
        logger.info("BG-L job #%d status: %s", job_id, status)

        if status == "successful":
            data = extract_log_data(job_id)
            if data:
                if target_type == "group" or len(data) > 1:
                    sorted_hosts = sorted(data.items())
                    chunks       = [sorted_hosts[i:i + _PAGE_SIZE]
                                    for i in range(0, max(len(sorted_hosts), 1), _PAGE_SIZE)]
                    for page_num, chunk in enumerate(chunks, start=1):
                        card = lc.build_log_group_results_card(
                            target, job_id, dict(chunk), params, page_num, len(chunks)
                        )
                        post_to_teams(card)
                else:
                    host_data = data.get(target, next(iter(data.values()), {}))
                    post_to_teams(lc.build_log_results_card(target, job_id, host_data))
            else:
                post_to_teams(lc.build_error_card(
                    target, job_id, "Job exitoso pero sin datos de logs."
                ))
            return

        if status in ("failed", "error", "canceled"):
            post_to_teams(lc.build_error_card(target, job_id, f"El job terminó con estado: {status}"))
            return

    post_to_teams(lc.build_error_card(target, job_id, "Timeout: el job superó 5 minutos."))


def wait_and_report_remediation(job_id: int, target: str,
                                target_type: str, params: dict) -> None:
    logger.info("BG-R esperando job #%d para %s (%s)", job_id, target, target_type)
    mode = params.get("remediation_mode", "diagnose")
    for _ in range(60):
        time.sleep(5)
        job    = aap_get(f"/api/v2/jobs/{job_id}/")
        status = job.get("status", "")
        if status == "successful":
            data = extract_remediation_data(job_id)
            if data:
                if target_type == "group" or len(data) > 1:
                    card = rc.build_remediation_group_card(target, job_id, data)
                else:
                    card = rc.build_remediation_card(target, job_id, data)
            else:
                card = rc.build_error_card(target, job_id, "Job exitoso pero sin datos de remediacion.")
            post_to_teams(card)
            return
        if status in ("failed", "error", "canceled"):
            post_to_teams(rc.build_error_card(target, job_id, f"El job terminó con estado: {status}"))
            return
    post_to_teams(rc.build_error_card(target, job_id, "Timeout: el job superó 5 minutos."))


# ─── Inventory query (sincrono, sin AWX) ─────────────────────────────────────

_INV_SYSTEM = (
    "Eres AnsibleBot, asistente de infraestructura. "
    "Responde preguntas sobre el inventario de servidores usando los datos provistos. "
    "Sé conciso y preciso. Usa el español. "
    "Si la respuesta es una lista de hosts, empiézala con el prefijo LIST: "
    "seguido de los hostnames separados por coma (ej: LIST: HOST1, HOST2, HOST3). "
    "Si es un número o estadística, da el dato directo. "
    "Si la pregunta no tiene respuesta en el inventario, di exactamente: "
    "No encontré esa información en el inventario."
)

def answer_inventory_query(pregunta: str) -> dict:
    """
    Responde en lenguaje natural usando Claude + el inventario dummy.
    Devuelve una Adaptive Card lista para enviar.
    """
    if _inv_client is None:
        return ic.build_error_card(pregunta, "API key de Anthropic no configurada.")

    inv_text = get_inventory_text()
    try:
        resp = _inv_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_INV_SYSTEM + "\n\nINVENTARIO:\n" + inv_text,
            messages=[{"role": "user", "content": pregunta}],
        )
        answer = resp.content[0].text.strip()
    except Exception as e:
        logger.error("INV query error: %s", e)
        return ic.build_error_card(pregunta, f"Error consultando el inventario: {e}")

    # Si Claude devuelve una lista de hosts → card especializada
    if answer.upper().startswith("LIST:"):
        raw_list = answer[5:].strip()
        hosts    = [h.strip() for h in raw_list.split(",") if h.strip()]
        inv      = get_inventory()
        # Determinar título de la consulta (primeras 6 palabras)
        titulo = " ".join(pregunta.split()[:6])
        return ic.build_host_list_card(pregunta, titulo, hosts, len(hosts))

    return ic.build_query_card(pregunta, answer)


# ─── Endpoint principal ──────────────────────────────────────────────────────

@app.post("/teams/webhook")
async def teams_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    auth = request.headers.get("Authorization", "")

    if not verify_teams_signature(body, auth):
        logger.warning("SECURITY firma HMAC invalida — origen: %s", request.client.host)
        raise HTTPException(status_code=401, detail="Firma HMAC invalida")

    try:
        payload = TeamsPayload(**json.loads(body))
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalido")

    clean  = clean_html(payload.text)
    parsed = parse_intent(clean)
    intent = parsed.get("intent")

    logger.info("ROUTER intent=%r  mensaje=%r", intent, clean[:80])

    # ── Clarificacion ─────────────────────────────────────────────────────────
    if intent == "clarify":
        return {
            "type": "message",
            "text": parsed.get("question", "Necesito mas informacion. Indica el servidor o grupo exacto."),
        }

    # ── Fleet check de SALUD ──────────────────────────────────────────────────
    if intent == "fleet_check":
        targets     = parsed.get("targets", [])
        environment = parsed.get("environment", "produccion")
        filter_mode = parsed.get("filter", "all")

        valid = [g for g in targets if group_exists_in_inventory(g)]
        if not valid:
            return {
                "type": "message",
                "text": f"No se encontraron grupos para el ambiente '{environment}' en el inventario.",
            }

        label = "criticos" if filter_mode == "critical" else "estado general"
        background_tasks.add_task(wait_and_report_fleet_health, valid, environment, filter_mode)
        return {
            "type": "message",
            "text": (
                f"Fleet check de salud iniciado — {environment.capitalize()} ({len(valid)} grupos).\n"
                f"Buscando: {label}. Los resultados llegan en ~1-2 minutos."
            ),
        }

    # ── Health check individual / grupo ───────────────────────────────────────
    if intent == "health_check":
        raw_target  = parsed.get("target", "")
        target_type = parsed.get("type", "host")
        resources   = parsed.get("resources", ["all"])

        if target_type == "group":
            target = raw_target.upper()
            exists = group_exists_in_inventory(target)
        else:
            target = resolve_host_name(raw_target) or raw_target.upper()
            exists = resolve_host_name(raw_target) is not None

        if not exists:
            return hc._card_response(hc.build_not_found_card(target, target_type))

        resp   = launch_health_job(target, resources)
        job_id = resp.get("id")
        if job_id:
            background_tasks.add_task(wait_and_report_health, job_id, target, target_type, resources)

        return hc._card_response(hc.build_launch_card(target, job_id, target_type, resources))

    # ── Log fleet ─────────────────────────────────────────────────────────────
    if intent == "log_fleet":
        targets     = parsed.get("targets", [])
        environment = parsed.get("environment", "produccion")
        params      = _extract_log_params(parsed)

        valid = [g for g in targets if group_exists_in_inventory(g)]
        if not valid:
            return {
                "type": "message",
                "text": f"No se encontraron grupos para el ambiente '{environment}' en el inventario.",
            }

        background_tasks.add_task(wait_and_report_fleet_log, valid, environment, params)
        return {
            "type": "message",
            "text": (
                f"Log fleet iniciado — {environment.capitalize()} ({len(valid)} grupos).\n"
                f"Ventana: {params['time_window_hours']}h  |  Severidad: {params['severity']}.\n"
                "Los resultados llegan en ~1-2 minutos."
            ),
        }

    # ── Log check individual / grupo ──────────────────────────────────────────
    if intent == "log_check":
        raw_target  = parsed.get("target", "")
        target_type = parsed.get("type", "host")
        params      = _extract_log_params(parsed)

        if target_type == "group":
            target = raw_target.upper()
            exists = group_exists_in_inventory(target)
        else:
            target = resolve_host_name(raw_target) or raw_target.upper()
            exists = resolve_host_name(raw_target) is not None

        if not exists:
            return lc._card_response(lc.build_not_found_card(target, target_type))

        resp   = launch_log_job(target, params)
        job_id = resp.get("id")
        if job_id:
            background_tasks.add_task(wait_and_report_log, job_id, target, target_type, params)

        return lc._card_response(lc.build_log_launch_card(target, job_id, params, target_type))

    # ── Diagnose / Remediate / Full remediate ─────────────────────────────────
    if intent in ("diagnose", "remediate", "full_remediate"):
        raw_target  = parsed.get("target", "")
        target_type = parsed.get("type", "host")
        issue_type  = parsed.get("issue_type", "auto")

        mode_map = {"diagnose": "diagnose", "remediate": "remediate", "full_remediate": "full"}
        remediation_mode = mode_map[intent]

        if target_type == "group":
            target = raw_target.upper()
            exists = group_exists_in_inventory(target)
        else:
            target = resolve_host_name(raw_target) or raw_target.upper()
            exists = resolve_host_name(raw_target) is not None

        if not exists:
            return rc._card_response(rc.build_not_found_card(target))

        params = {"remediation_mode": remediation_mode, "issue_type": issue_type}
        resp   = launch_remediation_job(target, params)
        job_id = resp.get("id")
        if job_id:
            background_tasks.add_task(
                wait_and_report_remediation, job_id, target, target_type, params
            )

        return rc._card_response(
            rc.build_remediation_launch_card(target, job_id, remediation_mode, issue_type)
        )

    # ── Inventory query ───────────────────────────────────────────────────────
    if intent == "inventory_query":
        query = parsed.get("query", clean)
        logger.info("INV query: %r", query[:120])
        card = answer_inventory_query(query)
        return ic._card_response(card)

    # ── Unknown ───────────────────────────────────────────────────────────────
    logger.info("ROUTER intent desconocido para: %r", clean[:80])
    return {
        "type": "message",
        "text": (
            "Comando no reconocido. Ejemplos validos:\n"
            "- @AnsibleBot valida SGWLSAPPP01\n"
            "- @AnsibleBot salud de produccion\n"
            "- @AnsibleBot logs de WEBLOGIC_PDN\n"
            "- @AnsibleBot errores en laboratorio ultima hora\n"
            "- @AnsibleBot limpia el disco de ol9server1\n"
            "- @AnsibleBot libera RAM en ol9server1\n"
            "- @AnsibleBot diagnostica ol9server1\n"
            "- @AnsibleBot cuantas maquinas tiene produccion\n"
            "- @AnsibleBot dame la lista de servidores Weblogic"
        ),
    }

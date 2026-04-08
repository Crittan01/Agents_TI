import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta

import streamlit as st
from dotenv import load_dotenv

_SHARED_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../shared/bridge")
sys.path.insert(0, _SHARED_BRIDGE)

from nlu import parse_intent
from aap import aap_get, host_exists_in_inventory, group_exists_in_inventory, launch_log_job, extract_log_data

load_dotenv(os.path.join(_SHARED_BRIDGE, ".env"))

AWX_URL         = os.getenv("AWX_URL", "https://10.216.24.208")
JOB_TEMPLATE_ID = int(os.getenv("LOG_JOB_TEMPLATE_ID", "179"))
INVENTORY_ID    = int(os.getenv("INVENTORY_ID", "35"))

COT = timezone(timedelta(hours=-5))


# ─── Helpers UI ──────────────────────────────────────────────────────────────

def semaforo_log(data: dict) -> str:
    if data.get("unreachable") or data.get("block_error"):
        return "🔴"
    if int(data.get("errors", 0)) > 0:
        return "🟡"
    return "🟢"


def ts_ahora() -> str:
    return datetime.now(tz=COT).strftime("%Y-%m-%d %H:%M:%S")


def wait_for_job(job_id: int, placeholder) -> bool:
    for _ in range(60):
        time.sleep(5)
        job    = aap_get(f"/api/v2/jobs/{job_id}/")
        status = job.get("status", "")
        placeholder.info(f"Job **#{job_id}** — estado: `{status}`")
        if status == "successful":
            return True
        if status in ("failed", "error", "canceled"):
            return False
    return False


# ─── Layout ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="VOLT — Log Monitor",
    page_icon="📋",
    layout="wide",
)

if "history" not in st.session_state:
    st.session_state.history = []

# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Configuracion")
    token_ok = bool(os.getenv("AWX_TOKEN"))
    st.markdown(f"**AAP:** `{AWX_URL}`")
    st.markdown(f"**Inventario:** `{INVENTORY_ID}`")
    st.markdown(f"**Job Template:** `{JOB_TEMPLATE_ID}`")
    st.markdown(f"**Token:** {'configurado' if token_ok else 'NO configurado'}")

    st.divider()
    st.header("Parametros de busqueda")
    sidebar_hours    = st.selectbox("Ventana de tiempo", [1, 2, 4, 8, 12, 24], index=1,
                                    format_func=lambda x: f"{x} hora{'s' if x != 1 else ''}")
    sidebar_severity = st.selectbox("Severidad", ["ERROR", "WARN", "ALL"])
    sidebar_keyword  = st.text_input("Keyword (opcional)", placeholder="ORA-  OutOfMemory  NullPointer")

    st.divider()
    st.header("Historial de sesion")
    if st.session_state.history:
        n = len(st.session_state.history)
        st.caption(f"{n} consulta{'s' if n != 1 else ''} en esta sesion")
        st.dataframe(
            st.session_state.history,
            use_container_width=True,
            hide_index=True,
        )
        if st.button("Limpiar historial", use_container_width=True):
            st.session_state.history = []
            st.rerun()
    else:
        st.caption("Sin consultas aun.")

if not os.getenv("AWX_TOKEN"):
    st.error("AWX_TOKEN no configurado. Revisa el archivo .env")
    st.stop()

# ─── Cabecera ─────────────────────────────────────────────────────────────────

st.title("Log Monitor")
st.caption("Consulta logs de aplicacion en servidores Sura (WebLogic, Joomla, P8)")

# ─── Input ───────────────────────────────────────────────────────────────────

st.divider()
col_input, col_btn = st.columns([4, 1])

with col_input:
    server_input = st.text_input(
        "Servidor o descripcion",
        placeholder="Ej: SGWLSAPPP01  ·  'errores en P8_DESA'  ·  WEBLOGIC_PDN  ·  'logs de produccion'",
        label_visibility="collapsed",
    ).strip()

with col_btn:
    run = st.button("Consultar", type="primary", use_container_width=True)

# ─── Ejecucion ───────────────────────────────────────────────────────────────

if run:
    if not server_input:
        st.warning("Ingresa el nombre o descripcion del servidor.")
        st.stop()

    # Los parametros del sidebar son la fuente de verdad (mas predecibles para pruebas)
    params = {
        "time_window_hours": sidebar_hours,
        "severity":          sidebar_severity,
        "keyword":           sidebar_keyword.strip(),
        "max_sample_lines":  30,
    }

    with st.spinner("Interpretando solicitud..."):
        parsed = parse_intent(server_input)

    intent = parsed.get("intent")

    if intent == "clarify":
        st.warning(f"Necesito mas informacion: {parsed.get('question', 'Puedes ser mas especifico?')}")
        st.stop()

    # ── log_fleet ─────────────────────────────────────────────────────────────
    if intent == "log_fleet":
        targets     = parsed.get("targets", [])
        environment = parsed.get("environment", "produccion")

        st.subheader(f"Log Fleet — {environment.capitalize()}")
        st.caption(
            f"{len(targets)} grupos  ·  ventana: {params['time_window_hours']}h"
            f"  ·  severidad: {params['severity']}"
            + (f"  ·  keyword: '{params['keyword']}'" if params["keyword"] else "")
        )

        group_jobs: dict[str, int] = {}
        for group in targets:
            if group_exists_in_inventory(group):
                resp   = launch_log_job(group, params)
                job_id = resp.get("id")
                if job_id:
                    group_jobs[group] = job_id
                    st.info(f"Job **#{job_id}** lanzado para `{group}`")

        if not group_jobs:
            st.error("No se pudo lanzar ningun job.")
            st.stop()

        fleet: dict[str, dict] = {}
        lock  = threading.Lock()

        def _wait_one_fleet(group: str, jid: int) -> None:
            for _ in range(60):
                time.sleep(5)
                j      = aap_get(f"/api/v2/jobs/{jid}/")
                status = j.get("status", "")
                if status == "successful":
                    ld = extract_log_data(jid)
                    if ld:
                        with lock:
                            fleet[group] = ld
                    return
                if status in ("failed", "error", "canceled"):
                    return

        threads = [threading.Thread(target=_wait_one_fleet, args=(g, jid), daemon=True)
                   for g, jid in group_jobs.items()]
        for t in threads:
            t.start()

        progress_slot = st.empty()
        while any(t.is_alive() for t in threads):
            time.sleep(3)
            with lock:
                done = len(fleet)
            progress_slot.info(f"Esperando resultados... {done}/{len(group_jobs)} grupos completados")

        for t in threads:
            t.join(timeout=5)
        progress_slot.empty()

        if not fleet:
            st.error("Jobs completados pero sin datos de logs.")
            st.stop()

        # Consolidar en una tabla plana
        all_rows = []
        for group, host_dict in sorted(fleet.items()):
            for hostname, hdata in sorted(host_dict.items()):
                errors      = int(hdata.get("errors", 0))
                warns       = int(hdata.get("warns", 0))
                files       = int(hdata.get("files_scanned", 0))
                unreachable = hdata.get("unreachable", False)
                estado      = ("UNREACHABLE" if unreachable
                               else ("ERROR BLOQUE" if hdata.get("block_error") else "OK"))
                sample      = hdata.get("sample", [])
                last_line   = sample[-1] if sample else ""
                last_line   = (last_line[:120] + "…") if len(last_line) > 120 else last_line
                last_line   = last_line or estado
                all_rows.append({
                    "Grupo":          group,
                    "Servidor":       hostname,
                    "Errores":        errors,
                    "Warnings":       warns,
                    "Estado":         semaforo_log(hdata),
                    "Ultimo error":   last_line,
                })

        total = len(all_rows)
        total_err = sum(r["Errores"] for r in all_rows)
        st.success(f"Completado — {total} servidores evaluados  |  {total_err} error(es) encontrados")
        st.divider()

        with_issues = [r for r in all_rows if r["Errores"] > 0 or r["Estado"] != "🟢"]
        clean_hosts = [r for r in all_rows if r["Errores"] == 0 and r["Estado"] == "🟢"]

        if with_issues:
            st.subheader("Con hallazgos")
            with_issues.sort(key=lambda r: -r["Errores"])
            st.dataframe(with_issues, use_container_width=True, hide_index=True)

        if clean_hosts:
            with st.expander(f"Sin hallazgos ({len(clean_hosts)} servidores)"):
                st.dataframe(clean_hosts, use_container_width=True, hide_index=True)

        st.session_state.history.append({
            "Target":    f"Fleet: {environment}",
            "Servidor":  f"{total} servidores",
            "Job":       ", ".join(str(j) for j in group_jobs.values()),
            "Hora COT":  ts_ahora(),
            "Archivos":  "-",
            "Errores":   total_err,
            "Severity":  params["severity"],
        })
        st.stop()

    # ── Intento no reconocido ─────────────────────────────────────────────────
    if intent != "log_check":
        st.error(
            "No entendi la solicitud. Intenta:\n"
            "- Servidor exacto: `SGWLSAPPP01`\n"
            "- Descripcion libre: `errores en P8_DESA`\n"
            "- Grupo: `WEBLOGIC_PDN` o `logs de produccion`"
        )
        st.stop()

    # ── log_check ─────────────────────────────────────────────────────────────
    target      = parsed.get("target", "").upper()
    target_type = parsed.get("type", "host")

    if target.upper() != server_input.upper():
        label = "Grupo" if target_type == "group" else "Servidor"
        st.info(f"{label} identificado: **{target}**")

    with st.spinner(f"Verificando **{target}** en inventario AAP..."):
        existe = (group_exists_in_inventory(target) if target_type == "group"
                  else host_exists_in_inventory(target))
        if not existe:
            label = "grupo" if target_type == "group" else "servidor"
            st.error(f"El {label} **{target}** no existe en el inventario de AAP.")
            st.stop()

    st.success(f"**{target}** encontrado en inventario.")

    with st.spinner("Lanzando job en AAP..."):
        response = launch_log_job(target, params)
        job_id   = response.get("id")

    if not job_id:
        st.error("No se pudo lanzar el job en AAP.")
        st.stop()

    job_url = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    label   = "grupo" if target_type == "group" else "servidor"
    st.info(
        f"Job **#{job_id}** lanzado para {label} **{target}**. [Ver en AAP]({job_url})\n\n"
        f"Ventana: **{params['time_window_hours']}h**  ·  Severidad: **{params['severity']}**"
        + (f"  ·  Keyword: **'{params['keyword']}'**" if params["keyword"] else "")
    )

    status_slot = st.empty()
    success     = wait_for_job(job_id, status_slot)
    status_slot.empty()

    if not success:
        st.error(f"El job termino con error o fue cancelado. [Ver en AAP]({job_url})")
        st.stop()

    log_dict = extract_log_data(job_id)
    if not log_dict:
        st.error("Job exitoso pero no se pudieron leer los datos de logs.")
        st.stop()

    # ─── Resultados ───────────────────────────────────────────────────────────

    with st.expander("Artefacto raw (debug)", expanded=False):
        st.json(log_dict)

    st.divider()
    es_grupo = target_type == "group" or len(log_dict) > 1

    if es_grupo:
        n            = len(log_dict)
        total_errors = sum(int(v.get("errors", 0)) for v in log_dict.values())
        total_warns  = sum(int(v.get("warns", 0))  for v in log_dict.values())

        st.subheader(f"Log Report — {target}")
        st.caption(
            f"Job #{job_id}  ·  {n} servidor{'es' if n != 1 else ''}"
            f"  ·  {total_errors} error(es)  ·  {total_warns} warning(s)"
        )

        resumen = []
        for hostname, hdata in sorted(log_dict.items()):
            sample    = hdata.get("sample", [])
            last_line = sample[-1] if sample else ""
            last_line = (last_line[:120] + "…") if len(last_line) > 120 else last_line
            last_line = last_line or ("UNREACHABLE" if hdata.get("unreachable") else "—")
            resumen.append({
                "Servidor":     hostname,
                "Errores":      int(hdata.get("errors", 0)),
                "Warnings":     int(hdata.get("warns", 0)),
                "Estado":       semaforo_log(hdata),
                "Ultimo error": last_line,
            })

        resumen.sort(key=lambda r: (-r["Errores"], r["Servidor"]))
        st.dataframe(resumen, use_container_width=True, hide_index=True)

        st.divider()
        for hostname, hdata in sorted(log_dict.items()):
            errors  = int(hdata.get("errors", 0))
            warns   = int(hdata.get("warns", 0))
            files   = int(hdata.get("files_scanned", 0))
            sample  = hdata.get("sample", [])
            sem     = semaforo_log(hdata)

            with st.expander(
                f"{sem} **{hostname}**  —  {errors} error(es)  {warns} warning(s)  ({files} archivos)"
            ):
                col_e, col_w, col_f = st.columns(3)
                col_e.metric("Errores",  errors)
                col_w.metric("Warnings", warns)
                col_f.metric("Archivos", files)

                if hdata.get("unreachable"):
                    st.error("Host no alcanzable durante la ejecucion.")
                elif hdata.get("block_error"):
                    st.error(f"Error en bloque: {hdata.get('block_error_msg', 'desconocido')}")
                elif sample:
                    st.subheader("Muestra de logs")
                    st.code("\n".join(sample[-20:]), language=None)
                else:
                    st.info("Sin lineas de muestra en la ventana de tiempo.")

            st.session_state.history.append({
                "Target":   target,
                "Servidor": hostname,
                "Job":      job_id,
                "Hora COT": ts_ahora(),
                "Archivos": files,
                "Errores":  errors,
                "Severity": params["severity"],
            })

    else:
        host_data = log_dict.get(target, next(iter(log_dict.values()), {}))
        errors    = int(host_data.get("errors", 0))
        warns     = int(host_data.get("warns", 0))
        files     = int(host_data.get("files_scanned", 0))
        sample    = host_data.get("sample", [])

        st.subheader(f"Log Report — {target}")
        st.caption(f"Job #{job_id}  ·  {ts_ahora()} COT")

        col_e, col_w, col_f = st.columns(3)
        col_e.metric("Errores",  errors)
        col_w.metric("Warnings", warns)
        col_f.metric("Archivos escaneados", files)

        st.divider()

        if host_data.get("unreachable"):
            st.error("Host no alcanzable durante la ejecucion.")
        elif host_data.get("block_error"):
            st.error(f"Error en bloque: {host_data.get('block_error_msg', 'desconocido')}")
        elif sample:
            st.subheader("Muestra de logs")
            st.code("\n".join(sample), language=None)
        else:
            st.info("Sin lineas de muestra en la ventana de tiempo.")

        st.session_state.history.append({
            "Target":   target,
            "Servidor": target,
            "Job":      job_id,
            "Hora COT": ts_ahora(),
            "Archivos": files,
            "Errores":  errors,
            "Severity": params["severity"],
        })

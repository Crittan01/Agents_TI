"""
AAP client unificado para el router AnsibleBot.
Maneja tres job templates: health-check (HEALTH_JOB_TEMPLATE_ID)
                           log-monitor  (LOG_JOB_TEMPLATE_ID)
                           remediator   (REMEDIATION_JOB_TEMPLATE_ID)
"""
import os
import time
import logging
import requests
import urllib3
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

AWX_URL                = os.getenv("AWX_URL", "https://ol9-awx.lab.com/")
AWX_TOKEN              = os.getenv("AWX_TOKEN", "")
HEALTH_JOB_TEMPLATE_ID     = int(os.getenv("HEALTH_JOB_TEMPLATE_ID",     "9"))
LOG_JOB_TEMPLATE_ID        = int(os.getenv("LOG_JOB_TEMPLATE_ID",        "10"))
REMEDIATION_JOB_TEMPLATE_ID = int(os.getenv("REMEDIATION_JOB_TEMPLATE_ID", "13"))
INVENTORY_ID               = int(os.getenv("INVENTORY_ID", "2"))

_HEADERS = {
    "Authorization": f"Bearer {AWX_TOKEN}",
    "Content-Type":  "application/json",
}


def aap_get(path: str) -> dict:
    try:
        r = requests.get(f"{AWX_URL}{path}", headers=_HEADERS, timeout=10, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("AAP GET %s: %s", path, e)
        return {}


def resolve_host_name(server: str) -> Optional[str]:
    """Devuelve el nombre exacto del host tal como está en AWX (case-insensitive). None si no existe."""
    for variant in (server, server.upper(), server.lower()):
        result = aap_get(f"/api/v2/inventories/{INVENTORY_ID}/hosts/?name={variant}&page_size=1")
        if result.get("count", 0) > 0:
            return result["results"][0]["name"]
    return None


def host_exists_in_inventory(server: str) -> bool:
    return resolve_host_name(server) is not None


def group_exists_in_inventory(group: str) -> bool:
    result = aap_get(f"/api/v2/inventories/{INVENTORY_ID}/groups/?name={group.upper()}&page_size=1")
    return result.get("count", 0) > 0


def launch_health_job(target: str, resources: list = None) -> dict:
    extra_vars = {
        "target":           target,
        "health_resources": resources if resources else ["all"],
    }
    try:
        r = requests.post(
            f"{AWX_URL}/api/v2/job_templates/{HEALTH_JOB_TEMPLATE_ID}/launch/",
            headers=_HEADERS,
            json={"extra_vars": extra_vars},
            timeout=10, verify=False,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("AAP launch health job: %s", e)
        return {}


def launch_log_job(target: str, params: dict) -> dict:
    extra_vars = {
        "app_server_group":      target,
        "log_time_window_hours": int(params.get("time_window_hours", 2)),
        "log_severity":          params.get("severity", "ERROR").upper(),
        "log_keyword":           params.get("keyword", ""),
        "log_max_sample_lines":  int(params.get("max_sample_lines", 30)),
    }
    try:
        r = requests.post(
            f"{AWX_URL}/api/v2/job_templates/{LOG_JOB_TEMPLATE_ID}/launch/",
            headers=_HEADERS,
            json={"extra_vars": extra_vars},
            timeout=10, verify=False,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("AAP launch log job: %s", e)
        return {}


def extract_health_data(job_id: int, target: str = "") -> Optional[dict]:
    for intento in range(5):
        job       = aap_get(f"/api/v2/jobs/{job_id}/")
        artifacts = job.get("artifacts", {})
        host_entries = {k: v for k, v in artifacts.items()
                        if k != "health" and isinstance(v, dict)
                        and ("cpu" in v or v.get("unreachable"))}
        if host_entries:
            return host_entries
        health = artifacts.get("health")
        if isinstance(health, dict):
            hostname = health.get("server", target)
            return {hostname: health}
        logger.info("AAP health artifacts no disponibles (intento %d/5)...", intento + 1)
        time.sleep(5)
    return None


def extract_log_data(job_id: int) -> Optional[dict]:
    for intento in range(5):
        job       = aap_get(f"/api/v2/jobs/{job_id}/")
        artifacts = job.get("artifacts", {})
        host_entries = {k: v for k, v in artifacts.items()
                        if isinstance(v, dict) and "files_scanned" in v}
        if host_entries:
            return host_entries
        logger.info("AAP log artifacts no disponibles (intento %d/5)...", intento + 1)
        time.sleep(5)
    return None


def launch_remediation_job(target: str, params: dict) -> dict:
    extra_vars = {
        "target":               target,
        "remediation_mode":     params.get("remediation_mode", "diagnose"),
        "issue_type":           params.get("issue_type", "auto"),
        "cpu_kill_threshold":   int(params.get("cpu_kill_threshold", 80)),
        "ram_kill_threshold":   int(params.get("ram_kill_threshold", 20)),
        "disk_clean_path":      params.get("disk_clean_path", "/tmp"),
        "disk_clean_days":      int(params.get("disk_clean_days", 7)),
    }
    try:
        r = requests.post(
            f"{AWX_URL}/api/v2/job_templates/{REMEDIATION_JOB_TEMPLATE_ID}/launch/",
            headers=_HEADERS,
            json={"extra_vars": extra_vars},
            timeout=10, verify=False,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("AAP launch remediation job: %s", e)
        return {}


def extract_remediation_data(job_id: int) -> Optional[dict]:
    for intento in range(5):
        job       = aap_get(f"/api/v2/jobs/{job_id}/")
        artifacts = job.get("artifacts", {})
        host_entries = {k: v for k, v in artifacts.items()
                        if isinstance(v, dict) and "status" in v and "diagnosis" in v}
        if host_entries:
            return host_entries
        logger.info("AAP remediation artifacts no disponibles (intento %d/5)...", intento + 1)
        time.sleep(5)
    return None

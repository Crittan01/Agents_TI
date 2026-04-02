#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# MODULO ANSIBLE: read_logs
#
# Lee archivos de log de aplicacion en el servidor remoto y retorna
# un resumen compacto: conteos de errores/warnings y una muestra de lineas.
#
# Estrategia:
#   1. find(paths, patterns, mtime >= cutoff) — localiza archivos recientes
#   2. grep multihilo por severidad/keyword — eficiente en archivos grandes
#   3. Limita la muestra a max_sample_lines (las mas recientes)
#
# Requiere: Python 3.6+, grep disponible en el servidor (standard en Linux)
# Elevacion: debe ejecutarse con become: true (root) para leer archivos
#            de oracle, wasadmin o apache sin become_user especifico.
# =============================================================================

from __future__ import annotations

import os
import re
import glob
import subprocess
import threading
from datetime import datetime, timedelta

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: read_logs
short_description: Lee logs de aplicacion y retorna resumen de errores
options:
  log_paths:
    description: Lista de {paths, patterns} desde log_config.log_paths
    required: true
    type: list
  time_window_hours:
    description: Ventana de tiempo en horas hacia atras desde ahora
    required: false
    default: 2
    type: int
  severity:
    description: Nivel de severidad a buscar (ERROR, WARN, ALL)
    required: false
    default: ERROR
    type: str
  keyword:
    description: Termino libre adicional para filtrar lineas
    required: false
    default: ""
    type: str
  max_sample_lines:
    description: Maximo de lineas de muestra a retornar
    required: false
    default: 30
    type: int
"""

# Patrones de grep por severidad
_SEVERITY_PATTERNS = {
    "ERROR": r"ERROR|Exception|FATAL|CRITICAL|OOM|OutOfMemory",
    "WARN":  r"ERROR|Exception|FATAL|CRITICAL|OOM|OutOfMemory|WARN",
    "ALL":   r"ERROR|Exception|FATAL|CRITICAL|OOM|OutOfMemory|WARN|INFO",
}


def find_recent_files(paths: list, patterns: list, cutoff_epoch: float) -> tuple:
    """
    Retorna (archivos_encontrados, paths_omitidos).
    Omite silenciosamente rutas que no existen o no son directorios.
    """
    found    = set()
    skipped  = []
    for base_path in paths:
        if not os.path.isdir(base_path):
            skipped.append(base_path)
            continue
        for pattern in patterns:
            matched = glob.glob(
                os.path.join(base_path, "**", pattern),
                recursive=True,
            )
            for filepath in matched:
                try:
                    if os.path.isfile(filepath) and os.path.getmtime(filepath) >= cutoff_epoch:
                        found.add(filepath)
                except OSError:
                    pass
    return sorted(found), skipped


def grep_file(filepath: str, grep_pattern: str, keyword: str) -> dict:
    """
    Ejecuta grep sobre un archivo y retorna {errors, warns, lines[]}.
    Usa subprocess con grep para eficiencia en archivos grandes.
    """
    result = {"errors": 0, "warns": 0, "lines": []}
    try:
        cmd = ["grep", "-iE", grep_pattern, filepath]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if proc.returncode not in (0, 1):
            return result

        lines = proc.stdout.decode("utf-8", errors="replace").splitlines()

        # Excluir lineas generadas por el propio modulo (evita auto-referencia en syslog)
        lines = [l for l in lines if "ansible-read_logs" not in l]

        # Filtrar por keyword si se especifico
        if keyword:
            lines = [l for l in lines if keyword.lower() in l.lower()]

        for line in lines:
            line_lower = line.lower()
            if any(p in line_lower for p in ("error", "exception", "fatal", "critical", "oom", "outofmemory")):
                result["errors"] += 1
            else:
                result["warns"] += 1
            result["lines"].append(line.rstrip())

    except subprocess.TimeoutExpired:
        result["lines"].append(f"[TIMEOUT] {filepath}")
    except Exception as e:
        result["lines"].append(f"[ERROR reading {filepath}]: {e}")

    return result


def process_path_group(path_group: dict, cutoff_epoch: float,
                       grep_pattern: str, keyword: str,
                       lock: threading.Lock, accumulator: dict) -> None:
    """Worker de thread: procesa un grupo {paths, patterns}."""
    files, skipped = find_recent_files(
        path_group.get("paths", []),
        path_group.get("patterns", []),
        cutoff_epoch,
    )
    for filepath in files:
        file_result = grep_file(filepath, grep_pattern, keyword)
        with lock:
            accumulator["files_scanned"]  += 1
            accumulator["errors"]         += file_result["errors"]
            accumulator["warns"]          += file_result["warns"]
            accumulator["raw_lines"].extend(file_result["lines"])
    if skipped:
        with lock:
            accumulator["paths_skipped"].extend(skipped)


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            log_paths=dict(type="list", required=True),
            time_window_hours=dict(type="int",  default=2),
            severity=dict(type="str",  default="ERROR",
                          choices=["ERROR", "WARN", "ALL"]),
            keyword=dict(type="str",  default=""),
            max_sample_lines=dict(type="int",  default=30),
        ),
        supports_check_mode=True,
    )

    log_paths         = module.params["log_paths"]
    time_window_hours = module.params["time_window_hours"]
    severity          = module.params["severity"].upper()
    keyword           = module.params["keyword"].strip()
    max_sample_lines  = module.params["max_sample_lines"]

    cutoff_epoch = (datetime.now() - timedelta(hours=time_window_hours)).timestamp()
    grep_pattern = _SEVERITY_PATTERNS.get(severity, _SEVERITY_PATTERNS["ERROR"])

    accumulator = {"files_scanned": 0, "errors": 0, "warns": 0, "raw_lines": [], "paths_skipped": []}
    lock        = threading.Lock()

    threads = [
        threading.Thread(
            target=process_path_group,
            args=(pg, cutoff_epoch, grep_pattern, keyword, lock, accumulator),
            daemon=True,
        )
        for pg in log_paths
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # Tomar las ultimas max_sample_lines (mas recientes al final del archivo)
    sample = accumulator["raw_lines"][-max_sample_lines:]

    module.exit_json(
        changed=False,
        files_scanned=accumulator["files_scanned"],
        errors=accumulator["errors"],
        warns=accumulator["warns"],
        sample=sample,
        paths_skipped=accumulator["paths_skipped"],
        time_window_hours=time_window_hours,
        severity=severity,
        keyword=keyword,
    )


def main():
    run_module()


if __name__ == "__main__":
    main()

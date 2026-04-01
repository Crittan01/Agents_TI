import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

AWX_URL = os.getenv("AWX_URL", "https://10.216.24.208")
COT     = timezone(timedelta(hours=-5))

# Maximo de lineas de muestra a incluir en la card de Teams (limite de 28 KB)
_MAX_SAMPLE_IN_CARD = 10
# Maximo de chars por linea de muestra antes de truncar
_MAX_LINE_LEN       = 90
# Maximo de hosts por pagina en cards de grupo (para no superar 28 KB)
_PAGE_SIZE          = 15


def _fmt_ts_now() -> str:
    return datetime.now(tz=COT).strftime("%Y-%m-%d %H:%M:%S")


def _card_response(card: dict) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def _status_color(data: dict) -> str:
    if data.get("unreachable") or data.get("block_error"):
        return "Attention"
    if int(data.get("errors", 0)) > 0:
        return "Warning"
    return "Good"


def _status_label(data: dict) -> str:
    if data.get("unreachable"):
        return "UNREACHABLE"
    if data.get("block_error"):
        return "ERROR BLOQUE"
    errors = int(data.get("errors", 0))
    warns  = int(data.get("warns", 0))
    if errors == 0 and warns == 0:
        return "OK"
    parts = []
    if errors: parts.append(f"{errors} err")
    if warns:  parts.append(f"{warns} warn")
    return " / ".join(parts)


def build_not_found_card(target: str, target_type: str = "host") -> dict:
    label = "Grupo" if target_type == "group" else "Host"
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {
                "type": "TextBlock",
                "text": f"{label} no encontrado: {target}",
                "size": "Large",
                "weight": "Bolder",
                "color": "Attention",
            },
            {
                "type": "TextBlock",
                "text": f"'{target}' no existe en el inventario de AAP. Verifica el nombre exacto.",
                "wrap": True,
            },
        ],
    }


def build_log_launch_card(target: str, job_id: Optional[int],
                          params: dict, target_type: str = "host") -> dict:
    job_url  = f"{AWX_URL}/#/jobs/playbook/{job_id}/details" if job_id else f"{AWX_URL}/#/jobs"
    label    = "grupo" if target_type == "group" else "servidor"
    sev      = params.get("severity", "ERROR")
    hours    = params.get("time_window_hours", 2)
    keyword  = params.get("keyword", "")
    subtitle = (
        f"Job #{job_id} lanzado — resultados del {label} llegaran en segundos"
        if job_id else "Error al lanzar el job"
    )
    detail = f"Ventana: {hours}h  |  Severidad: {sev}"
    if keyword:
        detail += f"  |  Keyword: '{keyword}'"

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {
                "type": "TextBlock",
                "text": f"Log Monitor iniciado — {target}",
                "size": "Large",
                "weight": "Bolder",
            },
            {"type": "TextBlock", "text": subtitle,
             "isSubtle": True, "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": detail,
             "isSubtle": True, "size": "Small", "spacing": "Small"},
        ],
        "actions": [
            {"type": "Action.OpenUrl", "title": "Ver job en AAP", "url": job_url}
        ],
    }


def build_log_results_card(target: str, job_id: int, data: dict) -> dict:
    """Card para un unico servidor. data = {files_scanned, errors, warns, sample, ...}."""
    job_url  = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    ts       = _fmt_ts_now()
    hours    = data.get("time_window_hours", 2)
    sev      = data.get("severity", "ERROR")
    keyword  = data.get("keyword", "")
    files    = int(data.get("files_scanned", 0))
    errors   = int(data.get("errors", 0))
    warns    = int(data.get("warns", 0))
    sample   = data.get("sample", [])
    status   = data.get("status", "ok")

    sev_detail = f"Ventana: {hours}h  |  Severidad: {sev}"
    if keyword:
        sev_detail += f"  |  Keyword: '{keyword}'"

    e_color = "Attention" if errors > 0 else "Good"
    w_color = "Warning"   if warns  > 0 else "Default"

    body = [
        {"type": "TextBlock", "text": f"Log Report — {target}",
         "size": "Large", "weight": "Bolder"},
        {"type": "TextBlock",
         "text": f"Job #{job_id}  |  {ts} COT  |  {files} archivo(s) escaneado(s)",
         "isSubtle": True, "spacing": "Small"},
        {"type": "TextBlock", "text": sev_detail,
         "isSubtle": True, "size": "Small", "spacing": "None"},
    ]

    if status in ("unreachable", "error", "module_error"):
        body.append({
            "type": "TextBlock",
            "text": f"Estado: {status.upper()}",
            "color": "Attention", "weight": "Bolder", "spacing": "Medium",
        })
        if data.get("block_error_msg"):
            body.append({
                "type": "TextBlock",
                "text": str(data["block_error_msg"])[:200],
                "color": "Attention", "wrap": True, "size": "Small",
            })
    else:
        body.append({
            "type": "ColumnSet", "spacing": "Medium",
            "columns": [
                {"type": "Column", "width": "stretch",
                 "items": [
                     {"type": "TextBlock", "text": "Errores",  "weight": "Bolder", "size": "Small"},
                     {"type": "TextBlock", "text": str(errors), "size": "ExtraLarge",
                      "color": e_color, "weight": "Bolder"},
                 ]},
                {"type": "Column", "width": "stretch",
                 "items": [
                     {"type": "TextBlock", "text": "Warnings", "weight": "Bolder", "size": "Small"},
                     {"type": "TextBlock", "text": str(warns),  "size": "ExtraLarge",
                      "color": w_color, "weight": "Bolder"},
                 ]},
            ],
        })

        if sample:
            lines_to_show = sample[-_MAX_SAMPLE_IN_CARD:]
            truncated     = [
                (line[:_MAX_LINE_LEN] + "…") if len(line) > _MAX_LINE_LEN else line
                for line in lines_to_show
            ]
            remaining = len(sample) - len(lines_to_show)
            sample_text = "\n".join(truncated)
            if remaining > 0:
                sample_text += f"\n… y {remaining} linea(s) mas"

            body.append({"type": "TextBlock", "text": "Muestra de logs",
                         "weight": "Bolder", "spacing": "Medium"})
            body.append({
                "type": "TextBlock",
                "text": sample_text,
                "wrap": True,
                "size": "Small",
                "fontType": "Monospace",
                "spacing": "Small",
            })
        else:
            body.append({
                "type": "TextBlock",
                "text": "Sin lineas de muestra en la ventana de tiempo.",
                "isSubtle": True, "spacing": "Medium",
            })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
        "actions": [{"type": "Action.OpenUrl", "title": "Ver job completo en AAP", "url": job_url}],
    }


def build_log_group_results_card(group: str, job_id: int, log_dict: dict,
                                 params: dict, page: int = 1, total_pages: int = 1) -> dict:
    """
    Card resumen para un grupo. log_dict = {hostname: {files_scanned, errors, warns, ...}}.
    Soporta paginacion si hay mas de _PAGE_SIZE hosts.
    """
    job_url     = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    ts          = _fmt_ts_now()
    n           = len(log_dict)
    hours       = params.get("time_window_hours", 2)
    sev         = params.get("severity", "ERROR")
    page_label  = f"  |  pag {page}/{total_pages}" if total_pages > 1 else ""
    total_errors = sum(int(v.get("errors", 0)) for v in log_dict.values()
                       if not v.get("unreachable"))

    header = {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "120px",
             "items": [{"type": "TextBlock", "text": "Servidor", "weight": "Bolder", "size": "Small"}]},
            {"type": "Column", "width": "50px",
             "items": [{"type": "TextBlock", "text": "Err", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "50px",
             "items": [{"type": "TextBlock", "text": "Warn", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "stretch",
             "items": [{"type": "TextBlock", "text": "Ultimo error", "weight": "Bolder", "size": "Small"}]},
        ],
    }

    rows = []
    for hostname, data in sorted(log_dict.items()):
        errors      = int(data.get("errors", 0))
        warns       = int(data.get("warns", 0))
        sample      = data.get("sample", [])
        last_line   = sample[-1] if sample else ""
        last_line   = (last_line[:80] + "…") if len(last_line) > 80 else last_line
        last_line   = last_line or ("UNREACHABLE" if data.get("unreachable") else "—")
        rows.append({
            "type": "ColumnSet", "spacing": "Small",
            "columns": [
                {"type": "Column", "width": "120px",
                 "items": [{"type": "TextBlock", "text": hostname, "size": "Small", "wrap": True}]},
                {"type": "Column", "width": "50px",
                 "items": [{"type": "TextBlock", "text": str(errors), "size": "Small",
                            "color": "Attention" if errors > 0 else "Default",
                            "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "50px",
                 "items": [{"type": "TextBlock", "text": str(warns), "size": "Small",
                            "color": "Warning" if warns > 0 else "Default",
                            "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "stretch",
                 "items": [{"type": "TextBlock", "text": last_line, "size": "Small",
                            "color": "Attention" if errors > 0 else "Default",
                            "wrap": True}]},
            ],
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": f"Log Report — {group}",
             "size": "Large", "weight": "Bolder"},
            {"type": "TextBlock",
             "text": (f"Job #{job_id}  |  {ts} COT  |  {n} servidor{'es' if n != 1 else ''}"
                      f"  |  {total_errors} error(es)  |  Ventana: {hours}h  |  {sev}{page_label}"),
             "isSubtle": True, "spacing": "Small"},
            header,
        ] + rows,
        "actions": [{"type": "Action.OpenUrl", "title": "Ver job en AAP", "url": job_url}],
    }


def build_error_card(target: str, job_id: int, reason: str) -> dict:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {
                "type": "TextBlock",
                "text": f"Log Monitor fallido — {target}",
                "size": "Large",
                "weight": "Bolder",
                "color": "Attention",
            },
            {"type": "TextBlock", "text": reason, "isSubtle": True, "wrap": True},
        ],
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Ver job en AAP",
                "url": f"{AWX_URL}/#/jobs/playbook/{job_id}/details",
            }
        ],
    }

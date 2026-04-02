"""
Adaptive Cards para el agente remediador.
Formato compatible con Microsoft Teams Incoming Webhook.
"""
from __future__ import annotations
from typing import Any

# ── Colores semáforo ──────────────────────────────────────────────────────────
_GREEN  = "Good"
_YELLOW = "Warning"
_RED    = "Attention"

def _color(pct: float, warn: int = 70, crit: int = 85) -> str:
    if pct >= crit:  return _RED
    if pct >= warn:  return _YELLOW
    return _GREEN

def _delta_icon(pre: float, post: float) -> str:
    pre, post = float(pre), float(post)
    if post < pre - 2:  return "↓"
    if post > pre + 2:  return "↑"
    return "→"


def _metric_row(label: str, pre, post=None, unit: str = "%") -> dict:
    pre = float(pre)
    if post is not None:
        post  = float(post)
        icon  = _delta_icon(pre, post)
        color = _color(post)
        text  = f"{pre}{unit} {icon} **{post}{unit}**"
    else:
        color = _color(pre)
        text  = f"**{pre}{unit}**"
    return {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "stretch",
             "items": [{"type": "TextBlock", "text": label, "weight": "Bolder"}]},
            {"type": "Column", "width": "auto",
             "items": [{"type": "TextBlock", "text": text,
                        "color": color, "horizontalAlignment": "Right"}]},
        ]
    }


def _status_badge(status: str) -> tuple[str, str]:
    """Devuelve (emoji_texto, color) según el status del artefacto."""
    mapping = {
        "remediated":       ("✅ REMEDIADO",        _GREEN),
        "diagnosed":        ("🔍 DIAGNOSTICADO",    _YELLOW),
        "no_action_needed": ("✅ SIN ACCION",        _GREEN),
        "error":            ("❌ ERROR",             _RED),
        "unreachable":      ("🔴 NO ALCANZABLE",     _RED),
    }
    return mapping.get(status, ("⚪ DESCONOCIDO", "Default"))


# =============================================================================
# CARD: resultado de un host individual
# =============================================================================
def build_remediation_card(target: str, job_id: int, data: dict) -> dict:
    host_data  = data.get(target, next(iter(data.values()), {}))
    status     = host_data.get("status", "unknown")
    mode       = host_data.get("mode", "diagnose")
    issue      = host_data.get("issue_type", "none")
    diag       = host_data.get("diagnosis", {})
    actions    = host_data.get("actions_taken", [])
    pre        = host_data.get("pre_metrics", {})
    post       = host_data.get("post_metrics", {})
    freed_mb   = host_data.get("disk_freed_mb", 0)

    badge_text, badge_color = _status_badge(status)

    body: list[dict] = [
        # Header
        {"type": "TextBlock",
         "text": f"🔧 Remediador — {target}",
         "weight": "Bolder", "size": "Medium"},
        {"type": "ColumnSet", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock",
                 "text": f"Job #{job_id}  |  Modo: **{mode.upper()}**  |  Issue: **{issue.upper()}**",
                 "isSubtle": True, "wrap": True}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": badge_text,
                 "color": badge_color, "weight": "Bolder"}]},
        ]},
        {"type": "Separator"},
    ]

    # Métricas antes/después
    # pre_metrics = snapshot al inicio del job (correcto para "antes")
    # post_metrics = snapshot tras la remediación (correcto para "después")
    # diagnosis.cpu_pct refleja el último diagnóstico, no sirve como "antes"
    has_pre  = bool(pre)
    has_post = bool(post)
    cpu_before  = pre.get("cpu_pct",  diag.get("cpu_pct",  0))
    ram_before  = pre.get("ram_pct",  diag.get("ram_pct",  0))
    disk_before = pre.get("disk_pct", diag.get("disk_pct", 0))

    body.append({"type": "TextBlock", "text": "📊 Métricas",
                 "weight": "Bolder", "spacing": "Medium"})
    body.append(_metric_row("CPU",   cpu_before,  post.get("cpu_pct")  if has_post else None))
    body.append(_metric_row("RAM",   ram_before,  post.get("ram_pct")  if has_post else None))
    body.append(_metric_row("Disco", disk_before, post.get("disk_pct") if has_post else None))

    if freed_mb and int(freed_mb) > 0:
        body.append({"type": "TextBlock",
                     "text": f"💾 Espacio liberado: **{freed_mb} MB**",
                     "color": _GREEN, "spacing": "Small"})

    # Top procesos CPU
    top_cpu = diag.get("top_cpu_procs", [])
    if top_cpu:
        body.append({"type": "Separator"})
        body.append({"type": "TextBlock", "text": "🖥️ Top procesos CPU",
                     "weight": "Bolder"})
        body.append({"type": "TextBlock",
                     "text": "\n".join(top_cpu[:5]),
                     "fontType": "Monospace", "wrap": True, "isSubtle": True})

    # Top procesos RAM
    top_ram = diag.get("top_ram_procs", [])
    if top_ram:
        body.append({"type": "Separator"})
        body.append({"type": "TextBlock", "text": "🧠 Top procesos RAM",
                     "weight": "Bolder"})
        body.append({"type": "TextBlock",
                     "text": "\n".join(top_ram[:5]),
                     "fontType": "Monospace", "wrap": True, "isSubtle": True})

    # Archivos grandes
    large = diag.get("large_files", [])
    if large:
        body.append({"type": "Separator"})
        body.append({"type": "TextBlock", "text": "📁 Archivos grandes (>100MB)",
                     "weight": "Bolder"})
        body.append({"type": "TextBlock",
                     "text": "\n".join(large[:5]),
                     "fontType": "Monospace", "wrap": True, "isSubtle": True})

    # Acciones tomadas
    if actions:
        body.append({"type": "Separator"})
        body.append({"type": "TextBlock", "text": "🛠️ Acciones ejecutadas",
                     "weight": "Bolder"})
        for action in actions:
            body.append({"type": "TextBlock", "text": f"• {action}",
                         "wrap": True, "spacing": "Small"})

    # Error
    if host_data.get("block_error"):
        body.append({"type": "Separator"})
        body.append({"type": "TextBlock",
                     "text": f"⚠️ {host_data.get('block_error_msg', '')}",
                     "color": _RED, "wrap": True})

    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": body}


# =============================================================================
# CARD: resultado de múltiples hosts (grupo)
# =============================================================================
def build_remediation_group_card(target: str, job_id: int, data: dict) -> dict:
    rows: list[dict] = [
        {"type": "TextBlock",
         "text": f"🔧 Remediador — {target}  |  Job #{job_id}",
         "weight": "Bolder", "size": "Medium"},
        {"type": "Separator"},
    ]

    for hostname, hd in sorted(data.items()):
        status         = hd.get("status", "unknown")
        badge, color   = _status_badge(status)
        pre            = hd.get("pre_metrics", hd.get("diagnosis", {}))
        post           = hd.get("post_metrics", {})
        actions        = hd.get("actions_taken", [])
        freed          = hd.get("disk_freed_mb", 0)

        diag_h = hd.get("diagnosis", {})
        cpu_b  = float(pre.get("cpu_pct",  diag_h.get("cpu_pct",  0)))
        ram_b  = float(pre.get("ram_pct",  diag_h.get("ram_pct",  0)))
        disk_b = float(pre.get("disk_pct", diag_h.get("disk_pct", 0)))

        rows.append({"type": "ColumnSet", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "text": f"**{hostname}**",
                 "weight": "Bolder"},
                {"type": "TextBlock",
                 "text": (f"CPU {cpu_b}% | RAM {ram_b}% | Disco {disk_b}%"
                          + (f" → CPU {float(post.get('cpu_pct',0))}% | "
                             f"RAM {float(post.get('ram_pct',0))}% | "
                             f"Disco {float(post.get('disk_pct',0))}%"
                             if post else "")),
                 "isSubtle": True, "wrap": True, "spacing": "None"},
                {"type": "TextBlock",
                 "text": " | ".join(actions[:2]) if actions else "sin acciones",
                 "isSubtle": True, "wrap": True, "spacing": "None"},
            ]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": badge,
                 "color": color, "weight": "Bolder",
                 "horizontalAlignment": "Right"},
                {"type": "TextBlock",
                 "text": f"↓{freed}MB" if int(freed or 0) > 0 else "",
                 "color": _GREEN, "horizontalAlignment": "Right",
                 "spacing": "None"},
            ]},
        ]})
        rows.append({"type": "Separator"})

    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": rows}


# =============================================================================
# CARD: lanzamiento del job
# =============================================================================
def build_remediation_launch_card(target: str, job_id: int,
                                  mode: str, issue: str) -> dict:
    mode_labels = {
        "diagnose":  "🔍 Diagnosticando sin hacer cambios",
        "remediate": "🛠️ Ejecutando correcciones",
        "full":      "🔧 Diagnosticando y corrigiendo",
    }
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": [
                {"type": "TextBlock",
                 "text": f"🔧 Remediador — {target}",
                 "weight": "Bolder", "size": "Medium"},
                {"type": "TextBlock",
                 "text": mode_labels.get(mode, mode),
                 "color": "Accent"},
                {"type": "FactSet", "facts": [
                    {"title": "Job ID",  "value": str(job_id)},
                    {"title": "Destino", "value": target},
                    {"title": "Modo",    "value": mode.upper()},
                    {"title": "Issue",   "value": issue.upper()},
                ]},
                {"type": "TextBlock",
                 "text": "⏳ Procesando... Los resultados llegarán en breve.",
                 "isSubtle": True},
            ]}


# =============================================================================
# CARD: error / host no encontrado
# =============================================================================
def build_not_found_card(target: str) -> dict:
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": [
                {"type": "TextBlock", "text": "🔧 Remediador",
                 "weight": "Bolder", "size": "Medium"},
                {"type": "TextBlock",
                 "text": f"❌ Host no encontrado: **{target}**",
                 "color": _RED},
                {"type": "TextBlock",
                 "text": f"'{target}' no existe en el inventario de AAP. Verifica el nombre.",
                 "isSubtle": True, "wrap": True},
            ]}


def build_error_card(target: str, job_id: int, msg: str) -> dict:
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": [
                {"type": "TextBlock",
                 "text": f"🔧 Remediador — {target}  |  Job #{job_id}",
                 "weight": "Bolder"},
                {"type": "TextBlock", "text": f"❌ {msg}",
                 "color": _RED, "wrap": True},
            ]}


def _card_response(card: dict) -> dict:
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
    ]}

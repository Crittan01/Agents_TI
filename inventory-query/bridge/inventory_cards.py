"""
inventory_cards.py — Adaptive Cards para respuestas de consulta de inventario.
"""

_ACCENT = "Accent"
_GOOD   = "Good"
_WARN   = "Warning"


def _card_response(card: dict) -> dict:
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


def build_query_card(pregunta: str, respuesta: str, datos: list[dict] = None) -> dict:
    """
    Card genérica para respuestas de inventario.

    pregunta : texto original del usuario
    respuesta: respuesta en lenguaje natural de Claude
    datos    : lista opcional de filas [{label, value}] para mostrar en tabla
    """
    body = [
        {
            "type": "TextBlock",
            "text": "Consulta de Inventario",
            "weight": "Bolder",
            "size": "Medium",
        },
        {
            "type": "TextBlock",
            "text": pregunta,
            "isSubtle": True,
            "wrap": True,
            "spacing": "Small",
        },
        {"type": "TextBlock", "text": " ", "spacing": "Small"},
        {
            "type": "TextBlock",
            "text": respuesta,
            "wrap": True,
            "color": _ACCENT,
        },
    ]

    if datos:
        body.append({"type": "TextBlock", "text": " ", "spacing": "Small"})
        facts = [{"title": d["label"], "value": str(d["value"])} for d in datos]
        body.append({"type": "FactSet", "facts": facts})

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
    }


def build_host_list_card(pregunta: str, titulo: str,
                         hosts: list[str], total: int) -> dict:
    """Card para respuestas que listan hosts (con paginación si hay muchos)."""
    MAX_INLINE = 30

    body = [
        {
            "type": "TextBlock",
            "text": "Consulta de Inventario",
            "weight": "Bolder",
            "size": "Medium",
        },
        {
            "type": "TextBlock",
            "text": pregunta,
            "isSubtle": True,
            "wrap": True,
            "spacing": "Small",
        },
        {"type": "TextBlock", "text": " ", "spacing": "Small"},
        {
            "type": "TextBlock",
            "text": f"{titulo} — {total} servidor{'es' if total != 1 else ''}",
            "weight": "Bolder",
            "color": _ACCENT,
        },
    ]

    if not hosts:
        body.append({
            "type": "TextBlock",
            "text": "No se encontraron servidores.",
            "isSubtle": True,
        })
    else:
        shown = hosts[:MAX_INLINE]
        # Mostrar en columnas de 2 para aprovechar el ancho
        pairs = [shown[i:i+2] for i in range(0, len(shown), 2)]
        for pair in pairs:
            cols = []
            for h in pair:
                cols.append({
                    "type": "Column",
                    "width": "stretch",
                    "items": [{"type": "TextBlock", "text": h,
                               "size": "Small", "fontType": "Default"}],
                })
            body.append({"type": "ColumnSet", "columns": cols, "spacing": "None"})

        if len(hosts) > MAX_INLINE:
            body.append({
                "type": "TextBlock",
                "text": f"... y {len(hosts) - MAX_INLINE} más.",
                "isSubtle": True,
                "spacing": "Small",
            })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
    }


def build_summary_card(pregunta: str, summary: dict) -> dict:
    """Card para resúmenes estadísticos del inventario."""
    body = [
        {
            "type": "TextBlock",
            "text": "Consulta de Inventario",
            "weight": "Bolder",
            "size": "Medium",
        },
        {
            "type": "TextBlock",
            "text": pregunta,
            "isSubtle": True,
            "wrap": True,
            "spacing": "Small",
        },
        {"type": "TextBlock", "text": " ", "spacing": "Small"},
        {
            "type": "TextBlock",
            "text": f"Total: {summary['total_hosts']} servidores en {summary['total_groups']} grupos",
            "weight": "Bolder",
            "color": _ACCENT,
        },
        {"type": "TextBlock", "text": " ", "spacing": "Small"},
        {"type": "TextBlock", "text": "Por ambiente", "weight": "Bolder", "size": "Small"},
        {
            "type": "FactSet",
            "facts": [
                {"title": k.capitalize(), "value": str(v)}
                for k, v in sorted(summary["by_ambiente"].items())
            ],
        },
        {"type": "TextBlock", "text": " ", "spacing": "Small"},
        {"type": "TextBlock", "text": "Por aplicacion", "weight": "Bolder", "size": "Small"},
        {
            "type": "FactSet",
            "facts": [
                {"title": k, "value": str(v)}
                for k, v in sorted(summary["by_app"].items(), key=lambda x: -x[1])
            ],
        },
    ]

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
    }


def build_error_card(pregunta: str, mensaje: str) -> dict:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": "Consulta de Inventario",
             "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": pregunta,
             "isSubtle": True, "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": mensaje,
             "color": "Attention", "wrap": True},
        ],
    }

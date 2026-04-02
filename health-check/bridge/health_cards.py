import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

AWX_URL = os.getenv("AWX_URL", "https://10.216.24.208")
COT     = timezone(timedelta(hours=-5))


def color_for(pct: float) -> str:
    if pct >= 85:
        return "Attention"
    if pct >= 70:
        return "Warning"
    return "Good"


def _mb_to_gb(mb) -> str:
    try:
        return f"{round(int(mb) / 1024, 1)} GB"
    except Exception:
        return f"{mb} MB"


def _fmt_ts(gen_at: str) -> str:
    try:
        return (
            datetime.fromisoformat(gen_at[:19])
            .replace(tzinfo=timezone.utc)
            .astimezone(COT)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return gen_at


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


def build_launch_card(target: str, job_id: Optional[int], target_type: str = "host",
                      resources: list = None) -> dict:
    resources = resources or ["all"]
    job_url   = f"{AWX_URL}/#/jobs/playbook/{job_id}/details" if job_id else f"{AWX_URL}/#/jobs"
    label     = "grupo" if target_type == "group" else "servidor"
    if "all" in resources:
        res_label = "CPU · RAM · Disco"
    else:
        _map = {"cpu": "CPU", "ram": "RAM", "disk": "Disco"}
        res_label = " · ".join(_map.get(r, r.upper()) for r in resources)
    subtitle  = (
        f"Job #{job_id} lanzado — verificando {res_label} en {label}"
        if job_id else "Error al lanzar el job"
    )
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {
                "type": "TextBlock",
                "text": f"Health Check iniciado — {target}",
                "size": "Large",
                "weight": "Bolder",
            },
            {
                "type": "TextBlock",
                "text": subtitle,
                "isSubtle": True,
                "wrap": True,
                "spacing": "Small",
            },
        ],
        "actions": [
            {"type": "Action.OpenUrl", "title": "Ver job en AAP", "url": job_url}
        ],
    }


def build_results_card(target: str, job_id: int, data: dict) -> dict:
    """Card para un unico servidor. Renderiza solo los recursos presentes en data."""
    if data.get("unreachable"):
        return build_error_card(target, job_id, "Host no alcanzable durante la ejecucion.")

    job_url = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    ts      = _fmt_ts(data.get("generated_at", ""))

    def _metric_row(label: str, value_text: str, pct: float) -> dict:
        return {
            "type": "ColumnSet", "spacing": "Small",
            "columns": [
                {"type": "Column", "width": "70px",
                 "items": [{"type": "TextBlock", "text": label, "weight": "Bolder", "size": "Small"}]},
                {"type": "Column", "width": "stretch",
                 "items": [{"type": "TextBlock", "text": value_text,
                            "color": color_for(pct), "size": "Small", "wrap": True}]},
            ],
        }

    body = [
        {"type": "TextBlock", "text": f"Health Report — {target}", "size": "Large", "weight": "Bolder"},
        {"type": "TextBlock", "text": f"Job #{job_id}  |  {ts} COT", "isSubtle": True, "spacing": "Small"},
    ]

    # CPU — solo si fue recolectado
    if "cpu" in data:
        cpu = float(data["cpu"])
        body.append(_metric_row("CPU", f"{cpu}%", cpu))

    # RAM — solo si fue recolectado
    if "ram" in data:
        ram       = float(data["ram"].get("used_percent", 0))
        ram_free  = data["ram"].get("free_mb", "?")
        ram_total = data["ram"].get("total_mb", "?")
        body.append(_metric_row(
            "RAM",
            f"{ram}%  ({_mb_to_gb(ram_free)} libres / {_mb_to_gb(ram_total)} total)",
            ram,
        ))

    # Disco — solo si fue recolectado
    if "disks" in data and data["disks"]:
        body.append({"type": "TextBlock", "text": "Discos", "weight": "Bolder", "spacing": "Medium"})
        for d in data["disks"]:
            usage_val = float(d.get("usage_pct", 0))
            body.append({
                "type": "ColumnSet",
                "columns": [
                    {"type": "Column", "width": "stretch",
                     "items": [{"type": "TextBlock", "text": d.get("mount", "?"), "wrap": True}]},
                    {"type": "Column", "width": "auto",
                     "items": [{"type": "TextBlock",
                                "text": f"{usage_val}%  ({d.get('used_gb','?')}/{d.get('total_gb','?')} GB)",
                                "color": color_for(usage_val)}]},
                ],
            })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
        "actions": [{"type": "Action.OpenUrl", "title": "Ver job completo en AAP", "url": job_url}],
    }


def build_group_results_card(group: str, job_id: int, health_dict: dict) -> dict:
    """Card resumen para un grupo. health_dict = {hostname: {cpu, ram, disks, ...}}."""
    job_url = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    first   = next(iter(health_dict.values()), {})
    ts      = _fmt_ts(first.get("generated_at", ""))
    n       = len(health_dict)

    header = {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "stretch",
             "items": [{"type": "TextBlock", "text": "Servidor", "weight": "Bolder", "size": "Small"}]},
            {"type": "Column", "width": "60px",
             "items": [{"type": "TextBlock", "text": "CPU", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "60px",
             "items": [{"type": "TextBlock", "text": "RAM", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "80px",
             "items": [{"type": "TextBlock", "text": "Disco (max)", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
        ],
    }

    rows = []
    for hostname, data in sorted(health_dict.items()):
        if data.get("unreachable"):
            rows.append({
                "type": "ColumnSet",
                "columns": [
                    {"type": "Column", "width": "stretch",
                     "items": [{"type": "TextBlock", "text": hostname, "size": "Small", "wrap": True}]},
                    {"type": "Column", "width": "200px",
                     "items": [{"type": "TextBlock", "text": "UNREACHABLE", "size": "Small",
                                "color": "Attention", "horizontalAlignment": "Center"}]},
                ],
            })
            continue

        cpu        = float(data.get("cpu", 0))
        ram_pct    = float(data.get("ram", {}).get("used_percent", 0))
        disks      = data.get("disks", [])
        worst_disk = max(disks, key=lambda d: float(d.get("usage_pct", 0)), default={})
        disk_max   = float(worst_disk.get("usage_pct", 0)) if worst_disk else 0
        disk_mount = worst_disk.get("mount", "") if worst_disk else ""
        disk_text  = f"{disk_max}%\n{disk_mount}" if disk_mount else f"{disk_max}%"

        rows.append({
            "type": "ColumnSet",
            "columns": [
                {"type": "Column", "width": "stretch",
                 "items": [{"type": "TextBlock", "text": hostname, "size": "Small", "wrap": True}]},
                {"type": "Column", "width": "60px",
                 "items": [{"type": "TextBlock", "text": f"{cpu}%", "size": "Small",
                            "color": color_for(cpu), "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "60px",
                 "items": [{"type": "TextBlock", "text": f"{ram_pct}%", "size": "Small",
                            "color": color_for(ram_pct), "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "80px",
                 "items": [{"type": "TextBlock", "text": disk_text, "size": "Small",
                            "color": color_for(disk_max), "horizontalAlignment": "Center", "wrap": True}]},
            ],
        })

    body = [
        {"type": "TextBlock", "text": f"Health Report — {group}", "size": "Large", "weight": "Bolder"},
        {"type": "TextBlock",
         "text": f"Job #{job_id}  |  {ts} COT  |  {n} servidor{'es' if n != 1 else ''}",
         "isSubtle": True, "spacing": "Small"},
        header,
    ] + rows

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
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
                "text": f"Health Check fallido — {target}",
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


def _rebuild_fleet_card_page(environment: str, fleet: dict, chunk: list,
                             page: int, total_pages: int) -> dict:
    """Construye una Adaptive Card para un chunk paginado de filas criticas."""
    total_hosts = sum(len(v) for v in fleet.values())
    ts          = datetime.now(tz=COT).strftime("%Y-%m-%d %H:%M:%S")
    env_label   = environment.capitalize()
    page_label  = f"  |  {page}/{total_pages}" if total_pages > 1 else ""

    all_critical_count = sum(
        1 for hosts in fleet.values() for data in hosts.values()
        if max(
            float(data.get("cpu", 0)),
            float(data.get("ram", {}).get("used_percent", 0)),
            max((float(d.get("usage_pct", 0)) for d in data.get("disks", [])), default=0),
        ) >= 70
    )
    n_c = sum(
        1 for hosts in fleet.values() for data in hosts.values()
        if max(
            float(data.get("cpu", 0)),
            float(data.get("ram", {}).get("used_percent", 0)),
            max((float(d.get("usage_pct", 0)) for d in data.get("disks", [])), default=0),
        ) >= 85
    )
    n_w    = all_critical_count - n_c
    parts  = []
    if n_c: parts.append(f"{n_c} criticos")
    if n_w: parts.append(f"{n_w} en alerta")
    subtitle = f"{ts} COT  |  {total_hosts} evaluados  |  " + "  ".join(parts) + page_label

    header = {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "stretch",
             "items": [{"type": "TextBlock", "text": "Servidor", "weight": "Bolder", "size": "Small"}]},
            {"type": "Column", "width": "50px",
             "items": [{"type": "TextBlock", "text": "CPU", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "50px",
             "items": [{"type": "TextBlock", "text": "RAM", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "80px",
             "items": [{"type": "TextBlock", "text": "Disco", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
        ],
    }
    rows = []
    for r in chunk:
        disk_text = f"{r['disk']}%\n{r['mount']}" if r["mount"] else f"{r['disk']}%"
        rows.append({
            "type": "ColumnSet", "spacing": "Small",
            "columns": [
                {"type": "Column", "width": "stretch",
                 "items": [{"type": "TextBlock", "text": r["hostname"], "size": "Small", "wrap": True}]},
                {"type": "Column", "width": "50px",
                 "items": [{"type": "TextBlock", "text": f"{r['cpu']}%", "size": "Small",
                            "color": color_for(r["cpu"]), "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "50px",
                 "items": [{"type": "TextBlock", "text": f"{r['ram']}%", "size": "Small",
                            "color": color_for(r["ram"]), "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "80px",
                 "items": [{"type": "TextBlock", "text": disk_text, "size": "Small",
                            "color": color_for(r["disk"]), "horizontalAlignment": "Center", "wrap": True}]},
            ],
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": f"Fleet Health — {env_label}",
             "size": "Large", "weight": "Bolder"},
            {"type": "TextBlock", "text": subtitle, "isSubtle": True, "spacing": "Small"},
            header,
        ] + rows,
    }


def build_fleet_results_card(environment: str, fleet: dict) -> dict:
    """
    Card resumen de fleet_check con filter=all.
    fleet = {group: {hostname: {cpu, ram, disks, ...}}}
    Para filter=critical usar _rebuild_fleet_card_page con paginacion.
    """
    total_hosts = sum(len(v) for v in fleet.values())
    ts          = datetime.now(tz=COT).strftime("%Y-%m-%d %H:%M:%S")
    env_label   = environment.capitalize()

    group_stats: dict[str, dict] = {}
    for group, hosts in sorted(fleet.items()):
        n_ok = n_warn = n_crit = 0
        for hostname, data in sorted(hosts.items()):
            cpu      = float(data.get("cpu", 0))
            ram_pct  = float(data.get("ram", {}).get("used_percent", 0))
            disks    = data.get("disks", [])
            worst_d  = max(disks, key=lambda d: float(d.get("usage_pct", 0)), default={})
            disk_max = float(worst_d.get("usage_pct", 0)) if worst_d else 0
            worst    = max(cpu, ram_pct, disk_max)
            if worst >= 85:
                n_crit += 1
            elif worst >= 70:
                n_warn += 1
            else:
                n_ok += 1
        group_stats[group] = {"n": len(hosts), "crit": n_crit, "warn": n_warn, "ok": n_ok}

    header = {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "stretch",
             "items": [{"type": "TextBlock", "text": "Grupo", "weight": "Bolder", "size": "Small"}]},
            {"type": "Column", "width": "auto",
             "items": [{"type": "TextBlock", "text": "Hosts", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "60px",
             "items": [{"type": "TextBlock", "text": "Criticos", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "55px",
             "items": [{"type": "TextBlock", "text": "Alertas", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
            {"type": "Column", "width": "40px",
             "items": [{"type": "TextBlock", "text": "Ok", "weight": "Bolder", "size": "Small",
                        "horizontalAlignment": "Center"}]},
        ],
    }
    rows = []
    for group, st in group_stats.items():
        crit_color = "Attention" if st["crit"] else "Default"
        warn_color = "Warning"   if st["warn"] else "Default"
        ok_color   = "Good"      if st["ok"]   else "Default"
        rows.append({
            "type": "ColumnSet", "spacing": "Small",
            "columns": [
                {"type": "Column", "width": "stretch",
                 "items": [{"type": "TextBlock", "text": group, "size": "Small", "wrap": True}]},
                {"type": "Column", "width": "auto",
                 "items": [{"type": "TextBlock", "text": str(st["n"]), "size": "Small",
                            "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "60px",
                 "items": [{"type": "TextBlock", "text": str(st["crit"]), "size": "Small",
                            "color": crit_color, "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "55px",
                 "items": [{"type": "TextBlock", "text": str(st["warn"]), "size": "Small",
                            "color": warn_color, "horizontalAlignment": "Center"}]},
                {"type": "Column", "width": "40px",
                 "items": [{"type": "TextBlock", "text": str(st["ok"]), "size": "Small",
                            "color": ok_color, "horizontalAlignment": "Center"}]},
            ],
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": f"Fleet Health — {env_label}",
             "size": "Large", "weight": "Bolder"},
            {"type": "TextBlock",
             "text": f"{ts} COT  |  {total_hosts} servidores  |  {len(fleet)} grupos",
             "isSubtle": True, "spacing": "Small"},
            header,
        ] + rows,
    }

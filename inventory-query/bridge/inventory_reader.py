"""
inventory_reader.py — Carga y estructura el inventario INI para consultas.

Construye un modelo de datos completo:
  - Todos los hosts con su IP y grupos directos
  - Todos los grupos con sus hosts (expandidos, incluyendo hijos)
  - Índices inversos para búsqueda rápida
"""
import os
import configparser
from typing import Optional

# ── Ruta al inventario dummy ──────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR  = os.path.join(_HERE, "..", "data")
_INV_FILE  = os.path.join(_DATA_DIR, "dummy_inventory.ini")

# ── Sufijos de ambiente ───────────────────────────────────────────────────────
_PROD_SUFFIXES  = {"_PDN", "_PROD"}
_LAB_SUFFIXES   = {"_LAB", "_LABO"}
_DLLO_SUFFIXES  = {"_DLLO", "_DESA"}

_AMBIENTE_MAP = {}
for s in _PROD_SUFFIXES:  _AMBIENTE_MAP[s] = "produccion"
for s in _LAB_SUFFIXES:   _AMBIENTE_MAP[s] = "laboratorio"
for s in _DLLO_SUFFIXES:  _AMBIENTE_MAP[s] = "desarrollo"


def _ambiente_de_grupo(nombre: str) -> str:
    upper = nombre.upper()
    for suffix, amb in _AMBIENTE_MAP.items():
        if upper.endswith(suffix):
            return amb
    return "desconocido"


def _parse_ini(path: str) -> dict:
    """
    Parsea el archivo INI y devuelve:
      groups_direct[grupo] = {host: ip}         — hosts directos de cada grupo
      children[grupo]      = [subgrupo, ...]     — grupos hijo (:children)
    """
    groups_direct: dict[str, dict[str, str]] = {}
    children:      dict[str, list[str]]      = {}

    cfg = configparser.RawConfigParser(allow_no_value=True, strict=False)
    cfg.read(path, encoding="utf-8")

    for section in cfg.sections():
        if section.endswith(":children"):
            parent = section[:-len(":children")]
            children[parent] = [k for k, _ in cfg.items(section)]
        else:
            hosts = {}
            for key, val in cfg.items(section):
                if key.startswith("#"):
                    continue
                # configparser splits at first '=':
                #   "hostname    ansible_host=10.x.x.x"
                #   → key="hostname    ansible_host", val="10.x.x.x"
                hostname = key.split()[0].upper()
                ip = ""
                if "ansible_host" in key:
                    # val is the IP directly
                    ip = (val or "").strip()
                elif val and "ansible_host=" in val:
                    ip = val.split("ansible_host=")[1].split()[0]
                hosts[hostname] = ip
            groups_direct[section.upper()] = hosts

    return groups_direct, children


def _expand_group(name: str, groups_direct: dict, children: dict,
                  visited: set = None) -> dict[str, str]:
    """Devuelve todos los hosts del grupo (incluyendo sub-grupos recursivamente)."""
    if visited is None:
        visited = set()
    if name in visited:
        return {}
    visited.add(name)

    hosts = dict(groups_direct.get(name, {}))
    for child in children.get(name, []):
        hosts.update(_expand_group(child.upper(), groups_direct, children, visited))
    return hosts


def load_inventory(path: str = _INV_FILE) -> dict:
    """
    Carga el inventario y devuelve un dict estructurado:

    {
      "hosts": {
        "SGWLSAPPP01": {
          "ip": "10.10.1.1",
          "groups": ["WEBLOGIC_PDN", "WEBLOGIC", "PRODUCCION"],
          "ambiente": "produccion",
          "app": "WEBLOGIC"
        }, ...
      },
      "groups": {
        "WEBLOGIC_PDN": {
          "hosts": {"SGWLSAPPP01": "10.10.1.1", ...},
          "ambiente": "produccion",
          "app": "WEBLOGIC",
          "count": 10
        }, ...
      },
      "summary": {
        "total_hosts": 80,
        "total_groups": 20,
        "by_ambiente": {"produccion": 45, "laboratorio": 20, "desarrollo": 15},
        "by_app": {"WEBLOGIC": 17, "JOOMLA": 13, ...}
      }
    }
    """
    groups_direct, children = _parse_ini(path)

    # Construir grupos expandidos (leaf + padres)
    all_group_names = set(groups_direct.keys()) | set(children.keys())
    groups: dict[str, dict] = {}
    for gname in all_group_names:
        expanded = _expand_group(gname, groups_direct, children)
        ambiente = _ambiente_de_grupo(gname)
        # app = primer token del nombre de grupo (WEBLOGIC, JOOMLA, P8, OHS, SAP, ADM, ...)
        app = gname.split("_")[0] if "_" in gname else gname
        groups[gname] = {
            "hosts":    expanded,
            "ambiente": ambiente,
            "app":      app,
            "count":    len(expanded),
            "is_leaf":  gname in groups_direct,
        }

    # Construir índice de hosts con sus grupos
    # Primera pasada: metadatos desde grupos hoja (amb/app correctos)
    hosts: dict[str, dict] = {}
    for gname, gdata in groups.items():
        if not gdata["is_leaf"]:
            continue
        for hostname, ip in gdata["hosts"].items():
            if hostname not in hosts:
                hosts[hostname] = {
                    "ip":      ip,
                    "groups":  [],
                    "ambiente": _ambiente_de_grupo(gname),
                    "app":     gdata["app"],
                }

    # Segunda pasada: registrar membresía en todos los grupos (leaf + padre)
    for gname, gdata in groups.items():
        for hostname in gdata["hosts"]:
            if hostname in hosts:
                hosts[hostname]["groups"].append(gname)

    # Resumen estadístico
    by_ambiente: dict[str, int] = {}
    by_app:      dict[str, int] = {}
    for hdata in hosts.values():
        amb = hdata["ambiente"]
        app = hdata["app"]
        by_ambiente[amb] = by_ambiente.get(amb, 0) + 1
        by_app[app]      = by_app.get(app, 0)      + 1

    summary = {
        "total_hosts":  len(hosts),
        "total_groups": len(groups),
        "by_ambiente":  by_ambiente,
        "by_app":       by_app,
    }

    return {"hosts": hosts, "groups": groups, "summary": summary}


def inventory_as_text(inv: dict) -> str:
    """
    Serializa el inventario como texto compacto para pasarlo al contexto de Claude.
    Formato legible pero conciso para no desperdiciar tokens.
    """
    lines = []

    # Resumen general
    s = inv["summary"]
    lines.append(f"INVENTARIO — {s['total_hosts']} hosts totales en {s['total_groups']} grupos")
    lines.append("")

    # Por ambiente
    lines.append("HOSTS POR AMBIENTE:")
    for amb, cnt in sorted(s["by_ambiente"].items()):
        lines.append(f"  {amb}: {cnt}")
    lines.append("")

    # Por aplicacion
    lines.append("HOSTS POR APLICACION:")
    for app, cnt in sorted(s["by_app"].items()):
        lines.append(f"  {app}: {cnt}")
    lines.append("")

    # Grupos hoja con sus hosts
    lines.append("GRUPOS Y SUS HOSTS:")
    for gname, gdata in sorted(inv["groups"].items()):
        if not gdata["is_leaf"] or gdata["count"] == 0:
            continue
        hostlist = ", ".join(sorted(gdata["hosts"].keys()))
        lines.append(f"  [{gname}] ({gdata['ambiente']}, {gdata['count']} hosts): {hostlist}")
    lines.append("")

    # Grupos padre (resumen)
    lines.append("GRUPOS PADRE (aggregados):")
    for gname, gdata in sorted(inv["groups"].items()):
        if gdata["is_leaf"]:
            continue
        lines.append(f"  [{gname}] {gdata['count']} hosts totales")

    return "\n".join(lines)


# ── Singleton cacheado ────────────────────────────────────────────────────────
_cached: Optional[dict] = None

def get_inventory() -> dict:
    global _cached
    if _cached is None:
        _cached = load_inventory()
    return _cached


def get_inventory_text() -> str:
    return inventory_as_text(get_inventory())

"""
NLU unificado: resuelve intenciones de health-check, log-monitor y remediator.
La regla de oro para distinguirlos:
  - "CPU / RAM / disco / memoria / salud / estado"        → health
  - "logs / errores en logs / busca / ORA- / Exception"   → log
  - "limpia / libera / arregla / remedia / mata procesos" → remediate
  - "diagnostica / qué tiene / qué pasa"                  → diagnose
  - "valida [servidor]" sin contexto → health (accion por defecto)
  - "hay errores en [ambiente]"       → log  (errores = errores de aplicacion)
"""
import os
import re
import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

_HERE    = os.path.dirname(os.path.abspath(__file__))
_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

def _resolve(env_key: str, default_rel: str) -> str:
    """Resuelve una ruta: si es relativa, la ancla al directorio de nlu.py."""
    raw = os.getenv(env_key, "")
    path = raw if raw else default_rel
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(_HERE, path))

INVENTORY_PATH         = _resolve("INVENTORY_PATH",         "../inventory/hosts_inventario")
NAMING_CONVENTION_PATH = _resolve("NAMING_CONVENTION_PATH", "../docs/naming_convention.md")


def _load_file(path: str, label: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception as e:
        print(f"[NLU] No se pudo leer {label} en '{path}': {e}")
        return ""


_INVENTORY_TEXT    = _load_file(INVENTORY_PATH, "inventario")
_NAMING_CONVENTION = _load_file(NAMING_CONVENTION_PATH, "convencion de nombres")

_GROUPS_SET = set()
for _m in re.findall(r'\[([^\]]+)\]', _INVENTORY_TEXT):
    _GROUPS_SET.add(_m.replace(":children", "").strip())
_GROUPS_LIST = "\n".join(f"  - {g}" for g in sorted(_GROUPS_SET))

_ENV_GROUPS = {
    "produccion": ["WEBLOGIC_PDN", "JOOMLA_PROD", "P8_PROD"],
    "desarrollo":  ["WEBLOGIC_DLLO", "P8_DESA"],
    "laboratorio": ["WEBLOGIC_LAB", "JOOMLA_LABO", "P8_LABO"],
}

_SYSTEM_PROMPT = (
    "Eres VOLT, asistente de operaciones de infraestructura Linux para Sura / NTT Data.\n"
    "Puedes ejecutar TRES tipos de acciones: verificar SALUD del sistema (CPU/RAM/disco), "
    "consultar LOGS de aplicacion (errores, warnings, excepciones), "
    "o REMEDIAR problemas (liberar RAM, limpiar disco, matar procesos de CPU).\n\n"
    "INVENTARIO:\n" + _INVENTORY_TEXT + "\n\n"
    "GRUPOS:\n" + _GROUPS_LIST + "\n\n"
    "CONVENCION DE NOMBRES:\n" + _NAMING_CONVENTION + "\n\n"
    "SUFIJOS DE AMBIENTE:\n"
    "  _DESA/_DLLO = Desarrollo  |  _LABO/_LAB = Laboratorio  |  _PROD/_PDN = Produccion\n\n"
    "AMBIENTES COMPLETOS:\n"
    "  produccion : WEBLOGIC_PDN, JOOMLA_PROD, P8_PROD\n"
    "  desarrollo : WEBLOGIC_DLLO, P8_DESA\n"
    "  laboratorio: WEBLOGIC_LAB, JOOMLA_LABO, P8_LABO\n\n"
    "REGLAS DE CLASIFICACION — aplica en orden estricto, la primera que coincida gana:\n\n"
    "  REGLA 0 — SIEMPRE REMEDIACION si el mensaje contiene acciones correctivas:\n"
    "    limpia, libera, arregla, remedia, corrige, mata procesos, elimina, borra, solucion,\n"
    "    soluciona, limpiar, liberar, remediar, fix, clean, free up.\n"
    "    Parametro issue_type segun contexto:\n"
    "      'disco/espacio/tmp' → disk  |  'RAM/memoria' → ram  |  'CPU/procesos' → cpu\n"
    "      'todo/all/ambos' o sin especificar → auto\n"
    "    'diagnostica / que pasa / que tiene / que hay' → diagnose (sin cambios)\n"
    "    'revisa y arregla / diagnostica y corrige' → full (diagnostica + remedia)\n\n"
    "  REGLA 1 — SIEMPRE LOG si el mensaje contiene alguna de estas palabras:\n"
    "    errores, error, logs, log, busca, ORA-, Exception, OutOfMemory, WARN, falla, fallos,\n"
    "    warnings, excepciones, monitorea, revisa logs.\n"
    "    IMPORTANTE: 'hay errores en X', 'errores en X', 'que errores hay' → SIEMPRE log_fleet o log_check.\n"
    "    La palabra 'errores' referencia SIEMPRE logs de aplicacion, NUNCA metricas de salud.\n\n"
    "  REGLA 2 — SALUD solo si NO hay ninguna palabra de REGLA 0 o REGLA 1 Y el mensaje contiene:\n"
    "    CPU, RAM, disco, memoria, salud, health, recursos, como esta, valida, checa, revisar,\n"
    "    umbral, critico, criticos, saturado, saturados, estan mal, andan mal, mal estado.\n\n"
    "  REGLA 3 — Si hay ambiguedad de INTENT → usa LOG (nunca clarify).\n"
    "    'clarify' se reserva EXCLUSIVAMENTE para cuando hay 2+ hosts/grupos con el mismo nombre.\n\n"
    "EJEMPLOS DE CLASIFICACION:\n"
    "  'limpia el disco de ol9server1'                 → remediate  issue_type=disk\n"
    "  'libera RAM en WEBLOGIC_PDN'                    → remediate  issue_type=ram\n"
    "  'arregla ol9server1'                            → remediate  issue_type=auto\n"
    "  'diagnostica ol9server1'                        → diagnose\n"
    "  'que esta consumiendo CPU en ol9server1'        → diagnose   issue_type=cpu\n"
    "  'revisa y arregla todo en ol9server1'           → full_remediate\n"
    "  'hay errores en laboratorio'                    → log_fleet\n"
    "  'salud de laboratorio'                          → fleet_check\n"
    "  'como esta el disco de P8_DESA'                 → health_check  resources=[\"disk\"]\n"
    "  'solo dame la RAM de ol9server1'                → health_check  resources=[\"ram\"]\n"
    "  'cpu y disco de ol9server1'                     → health_check  resources=[\"cpu\",\"disk\"]\n"
    "  'como esta ol9server1'                          → health_check  resources=[\"all\"]\n"
    "  'algo raro en desarrollo'                       → log_fleet\n"
    "  'cuantas maquinas tiene produccion'             → inventory_query\n"
    "  'dame la lista de servidores de desarrollo'     → inventory_query\n"
    "  'servidores criticos de produccion'             → fleet_check  (criticos=salud, NO inventario)\n"
    "  'cuantos servidores hay en Weblogic'            → inventory_query\n\n"
    "INTENTS DE SALUD:\n"
    "  fleet_check : ambiente completo\n"
    "  health_check: grupo especifico o host individual\n"
    "  Parametro resources: lista de recursos solicitados.\n"
    "    Valores: cpu | ram | disk | all\n"
    "    Si el usuario pide solo un recurso → [\"cpu\"], [\"ram\"], [\"disk\"]\n"
    "    Si pide dos → [\"cpu\",\"ram\"], [\"cpu\",\"disk\"], etc.\n"
    "    Si pide todo o no especifica → [\"all\"]\n"
    "    Palabras clave: 'CPU/procesador' → cpu | 'RAM/memoria/memoria RAM' → ram | 'disco/espacio/almacenamiento' → disk\n\n"
    "INTENTS DE LOG:\n"
    "  log_fleet, log_check\n"
    "  Parametros: time_window_hours, severity (ERROR|WARN|ALL), keyword\n\n"
    "INTENTS DE REMEDIACION:\n"
    "  diagnose      : solo recolecta info, NO hace cambios\n"
    "  remediate     : ejecuta la correccion\n"
    "  full_remediate: diagnostica + corrige + verifica\n"
    "  Parametros: issue_type (cpu|ram|disk|all|auto)\n\n"
    "INTENTS DE INVENTARIO:\n"
    "  inventory_query: cuando el usuario pregunta SOBRE EL INVENTARIO en si mismo:\n"
    "    listados de maquinas, conteos, grupos existentes, cuantos servidores hay,\n"
    "    en que ambiente esta X, a que grupo pertenece X, cuantos hosts tiene Y grupo,\n"
    "    dame la lista de produccion/laboratorio/desarrollo, etc.\n"
    "    NO se ejecuta ningun playbook ni se verifica salud/logs — solo consulta de datos.\n"
    "    IMPORTANTE: si la pregunta usa palabras de salud (criticos, umbral, saturados,\n"
    "    mal estado, andan mal) → NO usar inventory_query, usar fleet_check.\n"
    "    Parametro query: la pregunta original del usuario, sin modificar.\n\n"
    "OTROS:\n"
    "  clarify : SOLO para ambiguedad de nombre de host/grupo\n"
    "  unknown : accion no relacionada\n\n"
    "Responde UNICAMENTE con JSON valido en una sola linea, sin markdown.\n\n"
    "FORMATOS (elige exactamente uno):\n"
    '{"intent":"health_check","target":"NOMBRE","type":"host","resources":["all"]}\n'
    '{"intent":"health_check","target":"NOMBRE","type":"host","resources":["ram"]}\n'
    '{"intent":"health_check","target":"NOMBRE","type":"host","resources":["cpu","disk"]}\n'
    '{"intent":"health_check","target":"NOMBRE","type":"group","resources":["all"]}\n'
    '{"intent":"fleet_check","targets":["G1","G2"],"environment":"produccion|desarrollo|laboratorio","filter":"all|critical"}\n'
    '{"intent":"log_check","target":"NOMBRE","type":"host"}\n'
    '{"intent":"log_check","target":"NOMBRE","type":"group","time_window_hours":2,"severity":"ERROR","keyword":""}\n'
    '{"intent":"log_fleet","targets":["G1","G2"],"environment":"produccion|desarrollo|laboratorio","time_window_hours":2,"severity":"ERROR","keyword":""}\n'
    '{"intent":"diagnose","target":"NOMBRE","type":"host","issue_type":"auto"}\n'
    '{"intent":"diagnose","target":"NOMBRE","type":"group","issue_type":"cpu"}\n'
    '{"intent":"remediate","target":"NOMBRE","type":"host","issue_type":"disk"}\n'
    '{"intent":"remediate","target":"NOMBRE","type":"group","issue_type":"ram"}\n'
    '{"intent":"full_remediate","target":"NOMBRE","type":"host","issue_type":"auto"}\n'
    '{"intent":"inventory_query","query":"pregunta original del usuario"}\n'
    '{"intent":"clarify","question":"pregunta corta max 15 palabras"}\n'
    '{"intent":"unknown"}\n'
)

_client: Optional[anthropic.Anthropic] = (
    anthropic.Anthropic(api_key=_API_KEY) if _API_KEY else None
)


def _fallback_parse(text: str) -> dict:
    lower = text.lower()

    remediate_kw = ("limpia", "libera", "arregla", "remedia", "corrige",
                    "mata proceso", "elimina", "soluciona", "fix", "clean")
    diagnose_kw  = ("diagnostica", "que pasa", "que tiene", "que hay",
                    "que esta consumiendo", "que consume")
    full_kw      = ("revisa y arregla", "diagnostica y corrige", "revisa y corrige")
    log_kw       = ("log", "errores", "hay error", "busca", "ora-", "exception",
                    "outofmemory", "warn", "falla", "fallos")
    health_kw    = ("cpu", "ram", "disco", "salud", "health", "valida", "revisar", "checa",
                    "memoria", "como esta", "como anda", "estado", "recursos")
    inv_kw       = ("cuantas maquinas", "cuantos servidores", "cuantos hosts", "dame la lista",
                    "listado de", "que maquinas", "que servidores", "que grupos", "que ambientes",
                    "en que grupo", "a que grupo", "en que ambiente", "cuantos hay en",
                    "dame los servidores", "servidores de produccion", "servidores de laboratorio",
                    "servidores de desarrollo", "maquinas de produccion", "maquinas de laboratorio",
                    "maquinas de desarrollo", "inventario")

    is_full      = any(kw in lower for kw in full_kw)
    is_remediate = any(kw in lower for kw in remediate_kw) and not is_full
    is_diagnose  = any(kw in lower for kw in diagnose_kw) and not is_full and not is_remediate
    is_inv       = any(kw in lower for kw in inv_kw) and not is_full and not is_remediate and not is_diagnose
    is_log       = any(kw in lower for kw in log_kw) and not is_remediate and not is_diagnose and not is_inv
    is_health    = any(kw in lower for kw in health_kw) and not is_log and not is_remediate and not is_inv

    # Detectar issue_type para remediación
    issue = "auto"
    if "disco" in lower or "tmp" in lower or "espacio" in lower: issue = "disk"
    elif "ram" in lower or "memoria" in lower:                   issue = "ram"
    elif "cpu" in lower or "proceso" in lower:                   issue = "cpu"

    # Extraer target (último token que pueda ser host/grupo)
    parts     = text.strip().split()
    candidate = parts[-1].upper() if parts else ""
    ttype     = "group" if candidate in _GROUPS_SET else "host"

    if is_inv:
        return {"intent": "inventory_query", "query": text}
    if is_full:
        return {"intent": "full_remediate", "target": candidate, "type": ttype, "issue_type": issue}
    if is_remediate:
        return {"intent": "remediate", "target": candidate, "type": ttype, "issue_type": issue}
    if is_diagnose:
        return {"intent": "diagnose", "target": candidate, "type": ttype, "issue_type": issue}

    intent_prefix = "log" if (is_log or not is_health) else "health"

    # Detectar ambiente → intent de fleet
    for env, groups in _ENV_GROUPS.items():
        if env in lower:
            if intent_prefix == "log":
                return {"intent": "log_fleet", "targets": groups, "environment": env}
            return {"intent": "fleet_check", "targets": groups, "environment": env, "filter": "all"}

    # Detectar resources para health_check
    res_cpu  = any(k in lower for k in ("cpu", "procesador", "procesamiento"))
    res_ram  = any(k in lower for k in ("ram", "memoria", "memory"))
    res_disk = any(k in lower for k in ("disco", "disk", "espacio", "almacenamiento"))
    if res_cpu or res_ram or res_disk:
        resources = (
            (["cpu"] if res_cpu  else []) +
            (["ram"] if res_ram  else []) +
            (["disk"] if res_disk else [])
        )
    else:
        resources = ["all"]

    if candidate in _GROUPS_SET:
        if intent_prefix == "log":
            return {"intent": "log_check", "target": candidate, "type": "group"}
        return {"intent": "health_check", "target": candidate, "type": "group", "resources": resources}
    if candidate:
        if intent_prefix == "log":
            return {"intent": "log_check", "target": candidate, "type": "host"}
        return {"intent": "health_check", "target": candidate, "type": "host", "resources": resources}
    return {"intent": "unknown"}


def _extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON valido del texto del modelo."""
    text = text.strip()
    # Buscar bloque JSON entre llaves
    start = text.find("{")
    if start == -1:
        raise ValueError("No se encontro '{'")
    # Intentar parsear desde cada '{' encontrado (el modelo a veces antepone texto)
    for i in range(start, len(text)):
        if text[i] != "{":
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[i:j+1]
                    try:
                        result = json.loads(candidate)
                        # Asegurar que intent sea string, no dict
                        if isinstance(result.get("intent"), dict):
                            raise ValueError("intent es dict, no string")
                        return result
                    except (json.JSONDecodeError, ValueError):
                        break
    raise ValueError(f"No se encontro JSON valido en: {text!r}")


def parse_intent(user_message: str) -> dict:
    if _client is None:
        print("[NLU] ANTHROPIC_API_KEY no configurada — usando parser fallback")
        return _fallback_parse(user_message)
    raw = ""
    try:
        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )
        raw = response.content[0].text.strip()
        return _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        print(f"[NLU] Respuesta no es JSON valido, usando fallback. Raw: {raw!r}")
        return _fallback_parse(user_message)
    except Exception as e:
        print(f"[NLU] Error llamando LLM: {e} — usando fallback")
        return _fallback_parse(user_message)

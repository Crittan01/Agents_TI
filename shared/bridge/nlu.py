"""
NLU unificado: resuelve intenciones de health-check Y log-monitor.
La regla de oro para distinguirlos:
  - "CPU / RAM / disco / memoria / salud / estado"  → health
  - "logs / errores en logs / busca / ORA- / Exception" → log
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
    "Eres AnsibleBot, asistente de operaciones de infraestructura Linux para Sura / NTT Data.\n"
    "Puedes ejecutar DOS tipos de acciones: verificar SALUD del sistema (CPU/RAM/disco) "
    "o consultar LOGS de aplicacion (errores, warnings, excepciones).\n\n"
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
    "  REGLA 1 — SIEMPRE LOG si el mensaje contiene alguna de estas palabras:\n"
    "    errores, error, logs, log, busca, ORA-, Exception, OutOfMemory, WARN, falla, fallos,\n"
    "    warnings, excepciones, monitorea, revisa logs.\n"
    "    IMPORTANTE: 'hay errores en X', 'errores en X', 'que errores hay' → SIEMPRE log_fleet o log_check.\n"
    "    La palabra 'errores' referencia SIEMPRE logs de aplicacion, NUNCA metricas de salud.\n\n"
    "  REGLA 2 — SALUD solo si NO hay ninguna palabra de REGLA 1 Y el mensaje contiene:\n"
    "    CPU, RAM, disco, memoria, salud, health, recursos, como esta, valida, checa, revisar,\n"
    "    umbral, critico, criticos, saturado, saturados, estan mal, andan mal, mal estado.\n\n"
    "  REGLA 3 — Si hay ambiguedad de INTENT (no sabes si es log o health) → usa LOG (nunca clarify).\n"
    "    'clarify' se reserva EXCLUSIVAMENTE para cuando hay 2+ hosts/grupos con el mismo nombre\n"
    "    y no puedes distinguir cual eligio el usuario. NO uses clarify para ambiguedad de intent.\n\n"
    "EJEMPLOS DE CLASIFICACION:\n"
    "  'hay errores en laboratorio'                    → log_fleet  (errores = logs de aplicacion)\n"
    "  'errores en WEBLOGIC_PDN'                       → log_check  (grupo especifico)\n"
    "  'logs de produccion'                            → log_fleet\n"
    "  'salud de laboratorio'                          → fleet_check\n"
    "  'como esta el disco de P8_DESA'                 → health_check\n"
    "  'valida SGWLSAPPP01'                            → health_check  (no menciona errores ni logs)\n"
    "  'valida errores en laboratorio'                 → log_fleet  (errores tiene prioridad)\n"
    "  'que servidores estan mal en laboratorio'       → fleet_check  (mal estado = salud, REGLA 2)\n"
    "  'algo raro en desarrollo'                       → log_fleet  (ambiguo → LOG, REGLA 3)\n"
    "  'muestrame detalle de umbral alto de X Env'     → fleet_check/health_check  (umbral = salud)\n"
    "  'hay problemas en produccion'                   → fleet_check  (problemas sin errores = salud)\n\n"
    "INTENTS DE SALUD:\n"
    "  fleet_check : ambiente completo sin tecnologia especifica (toda produccion, todo lab)\n"
    "  health_check: grupo especifico o host individual\n\n"
    "INTENTS DE LOG:\n"
    "  log_fleet : ambiente completo sin tecnologia especifica\n"
    "  log_check : grupo especifico o host individual\n"
    "  Parametros opcionales (extrae solo si el usuario los menciona):\n"
    "    time_window_hours: 'ultima hora'->1, 'ultimas 4h'->4, 'hoy'->24, 'ultimas 24h/24 horas/un dia'->24,\n"
    "      'ultimas 12h'->12, 'ultimas 8h'->8, 'ultimas 48h/2 dias'->48 (default omitir)\n"
    "    severity: 'solo errores'->ERROR, 'errores y/o warnings'->WARN, 'warnings'->WARN, 'todo/all'->ALL\n"
    "    keyword: termino libre de busqueda ('busca ORA-', 'contiene NullPointer')\n\n"
    "OTROS:\n"
    "  clarify : SOLO cuando 2+ hosts/grupos tienen el mismo nombre y no se puede distinguir cual\n"
    "  unknown : accion no relacionada (reiniciar, instalar, saludar, etc.)\n\n"
    "Responde UNICAMENTE con JSON valido en una sola linea, sin markdown.\n\n"
    "FORMATOS (elige exactamente uno):\n"
    '{"intent":"health_check","target":"NOMBRE","type":"host"}\n'
    '{"intent":"health_check","target":"NOMBRE","type":"group"}\n'
    '{"intent":"fleet_check","targets":["G1","G2"],"environment":"produccion|desarrollo|laboratorio","filter":"all|critical"}\n'
    '{"intent":"log_check","target":"NOMBRE","type":"host"}\n'
    '{"intent":"log_check","target":"NOMBRE","type":"group","time_window_hours":2,"severity":"ERROR","keyword":""}\n'
    '{"intent":"log_fleet","targets":["G1","G2"],"environment":"produccion|desarrollo|laboratorio","time_window_hours":2,"severity":"ERROR","keyword":""}\n'
    '{"intent":"clarify","question":"pregunta corta max 15 palabras"}\n'
    '{"intent":"unknown"}\n'
)

_client: Optional[anthropic.Anthropic] = (
    anthropic.Anthropic(api_key=_API_KEY) if _API_KEY else None
)


def _fallback_parse(text: str) -> dict:
    lower = text.lower()
    log_kw    = ("log", "errores", "hay error", "busca", "ora-", "exception",
                 "outofmemory", "warn", "falla", "fallos")
    health_kw = ("cpu", "ram", "disco", "salud", "health", "valida", "revisar", "checa")
    is_log    = any(kw in lower for kw in log_kw)
    is_health = any(kw in lower for kw in health_kw) and not is_log

    intent_prefix = "log" if (is_log or not is_health) else "health"

    # Detectar ambiente → intent de fleet
    for env, groups in _ENV_GROUPS.items():
        if env in lower:
            if intent_prefix == "log":
                return {"intent": "log_fleet", "targets": groups, "environment": env}
            return {"intent": "fleet_check", "targets": groups, "environment": env, "filter": "all"}

    # Detectar grupo o host individual
    parts     = text.strip().split()
    candidate = parts[-1].upper() if parts else ""
    if candidate in _GROUPS_SET:
        if intent_prefix == "log":
            return {"intent": "log_check", "target": candidate, "type": "group"}
        return {"intent": "health_check", "target": candidate, "type": "group"}
    if candidate:
        if intent_prefix == "log":
            return {"intent": "log_check", "target": candidate, "type": "host"}
        return {"intent": "health_check", "target": candidate, "type": "host"}
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

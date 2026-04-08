"""
Script de prueba para el NLU de VOLT.
Ejecutar desde shared/bridge/:
    python3 test_nlu.py
"""
import json
import sys
import os

# Asegurar que el directorio actual este en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nlu import parse_intent

# ---------------------------------------------------------------------------
# Casos de prueba: (frase, intent_esperado, descripcion)
# ---------------------------------------------------------------------------
CASOS = [
    # ── SALUD individual / grupo ─────────────────────────────────────────────
    ("valida SGWLSAPPP01",                          "health_check", "host exacto"),
    ("como esta SGWLSAPPP01",                       "health_check", "host exacto pregunta natural"),
    ("como estan los WebLogic de produccion",       "health_check", "tecnologia + ambiente"),
    ("como esta P8 de laboratorio",                 "health_check", "tecnologia + ambiente"),
    ("salud de WEBLOGIC_PDN",                       "health_check", "grupo exacto — health_check o fleet_check aceptable"),

    # ── SALUD fleet ──────────────────────────────────────────────────────────
    ("como se encuentra Desarrollo",                "fleet_check",  "ambiente completo"),
    ("como va produccion",                          "fleet_check",  "ambiente coloquial"),
    ("todo bien en laboratorio",                    "fleet_check",  "pregunta de disponibilidad"),
    ("andan bien los servidores de lab",            "fleet_check",  "muy coloquial"),
    ("salud de produccion",                         "fleet_check",  "ambiente directo"),

    # ── SALUD fleet criticos ─────────────────────────────────────────────────
    ("hay algun servidor en umbral alto en Desarrollo",  "fleet_check",  "critico — umbral"),
    ("servidores criticos de produccion",                "fleet_check",  "critico — palabra clave"),
    ("que servidores estan mal en laboratorio",          "fleet_check",  "critico — coloquial"),
    ("hay servidores saturados en produccion",           "fleet_check",  "critico — saturados"),

    # ── LOGS individual / grupo ──────────────────────────────────────────────
    ("hay errores en P8_DESA",                      "log_check",   "grupo exacto"),
    ("errores en P8 de desarrollo",                 "log_check",   "tecnologia + ambiente"),
    ("logs de WEBLOGIC_PDN",                        "log_check",   "grupo exacto"),
    ("hay errores en los WebLogic de produccion",   "log_check",   "tecnologia + ambiente"),
    ("busca NullPointer en P8_PROD",                "log_check",   "busqueda con keyword"),

    # ── LOGS fleet ───────────────────────────────────────────────────────────
    ("hay errores en produccion",                   "log_fleet",   "ambiente completo"),
    ("hay errores en Desarrollo",                   "log_fleet",   "ambiente completo"),
    ("logs de laboratorio",                         "log_fleet",   "ambiente directo"),
    ("algo raro en desarrollo",                     "log_fleet",   "coloquial ambiguo → logs"),
    ("que paso en produccion anoche",               "log_fleet",   "temporal → logs"),
    ("errores y warnings en laboratorio hoy",       "log_fleet",   "severidad + tiempo"),

    # ── Salud con 'detalle' (reportado como bug real) ────────────────────────
    ("muestrame detalle de umbral alto de WebLogic Desarrollo", "health_check", "detalle + umbral → salud WEBLOGIC_DLLO"),
    ("muestrame los criticos de P8 produccion",     "health_check", "criticos + tecnologia → salud P8_PROD"),

    # ── Ambiguos / borde ─────────────────────────────────────────────────────
    ("hay problemas en produccion",                 "fleet_check", "ambiguo — modelo elige salud, aceptable"),
    ("revisa produccion",                           "fleet_check", "revisa sin errores → salud"),

    # ── Parametros opcionales (ventana de tiempo + severidad) ────────────────
    ("hay errores o warnings en Laboratorio las ultimas 24 horas", "log_fleet", "24h + WARN"),
    ("errores en produccion las ultimas 12 horas",                 "log_fleet", "12h + ERROR"),

    # ── INVENTARIO (nuevo modulo) ─────────────────────────────────────────────
    ("cuantas maquinas tiene produccion",           "inventory_query", "conteo por ambiente"),
    ("dame la lista de servidores de desarrollo",   "inventory_query", "listado de ambiente"),
    ("cuantos hosts hay en total",                  "inventory_query", "conteo total"),
    ("listado de servidores Weblogic",              "inventory_query", "listado por app"),
    ("en que grupo esta SGWLSAPPP01",               "inventory_query", "busqueda de host"),
    ("cuantos servidores tiene laboratorio Joomla", "inventory_query", "conteo filtrado"),

    # ── Unknown ──────────────────────────────────────────────────────────────
    ("reinicia el servidor SGWLSAPPP01",            "unknown",     "accion no soportada"),
    ("hola como estas",                             "unknown",     "saludo"),
]

# ---------------------------------------------------------------------------
# Ejecucion
# ---------------------------------------------------------------------------
VERDE   = "\033[92m"
ROJO    = "\033[91m"
AMARILLO= "\033[93m"
RESET   = "\033[0m"
NEGRITA = "\033[1m"

ok = 0
fail = 0

print(f"\n{NEGRITA}{'─'*80}")
print(f"  VOLT NLU — Test Suite ({len(CASOS)} casos)")
print(f"{'─'*80}{RESET}\n")

# Grupos de intents equivalentes funcionalmente (el modelo puede elegir cualquiera)
# health_check(group) y fleet_check([grupo]) producen el mismo resultado en main.py
# log_check(group)    y log_fleet([grupo])   producen el mismo resultado en main.py
_EQUIVALENTES = {
    "health_check":    {"health_check", "fleet_check"},
    "fleet_check":     {"fleet_check",  "health_check"},
    "log_check":       {"log_check",    "log_fleet"},
    "log_fleet":       {"log_fleet",    "log_check"},
    "inventory_query": {"inventory_query"},
}

for frase, esperado, descripcion in CASOS:
    resultado = parse_intent(frase)
    intent    = resultado.get("intent", "?")
    # Aceptar intents equivalentes
    aceptables = _EQUIVALENTES.get(esperado, {esperado})
    coincide   = intent in aceptables

    if coincide:
        ok += 1
        icono = f"{VERDE}✓{RESET}"
    else:
        fail += 1
        icono = f"{ROJO}✗{RESET}"

    # Extraer detalles relevantes del resultado
    detalles = {k: v for k, v in resultado.items() if k != "intent"}
    detalles_str = "  " + json.dumps(detalles, ensure_ascii=False) if detalles else ""

    print(f"  {icono} [{str(intent):<13}]  {frase}")
    if not coincide:
        print(f"    {AMARILLO}esperado: {esperado}{RESET}")
    if detalles_str:
        print(f"    {AMARILLO}{detalles_str}{RESET}")

print(f"\n{NEGRITA}{'─'*80}")
color = VERDE if fail == 0 else ROJO
print(f"  {color}Resultado: {ok}/{len(CASOS)} correctos  |  {fail} fallos{RESET}")
print(f"{'─'*80}{RESET}\n")

sys.exit(0 if fail == 0 else 1)

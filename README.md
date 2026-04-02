# AnsibleBot — Agentes de Automatización Inteligente

Sistema de agentes sobre **AAP / AWX** que permite operar servidores Linux en lenguaje natural
desde **Microsoft Teams**. El operador escribe una frase libre; un NLU basado en Claude la
convierte en un job de Ansible, que se ejecuta en el servidor objetivo y devuelve una tarjeta
Adaptive Card con el resultado.

---

## Índice

1. [Arquitectura general](#arquitectura)
2. [Estructura de carpetas](#estructura)
3. [Puesta en marcha](#puesta-en-marcha)
4. [Casos de uso](#casos)
   - [Health Check](#health-check)
   - [Log Monitor](#log-monitor)
   - [Remediador](#remediador)
5. [NLU — Intents y ejemplos](#nlu)
6. [Recursos AWX](#recursos-awx)
7. [Stack tecnológico](#stack)
8. [Cómo agregar un nuevo caso de uso](#nuevo-caso)

---

## Arquitectura general <a name="arquitectura"></a>

```
Operador (Teams — lenguaje natural)
          │
          ▼
  Cloudflare Tunnel  ──►  FastAPI /teams/webhook  (shared/bridge/main.py)
                                    │
                          HMAC-SHA256 verificado
                                    │
                                    ▼
                          NLU (Claude Haiku)  ──►  intent + target + params
                                    │
                                    ▼
                          AAP REST API  ──►  lanza Job Template con extra_vars
                                    │
                                    ▼
                    Rol Ansible ejecuta en servidor(es) Linux
                    Publica resultados via  set_stats  →  artifacts
                                    │
                                    ▼
                     Bridge lee artifacts  ──►  Adaptive Card a Teams
```

**Principios de diseño:**
- Un solo webhook unificado despacha los 3 casos de uso según el intent NLU.
- Los errores en servidores individuales no detienen la ejecución global.
- Hosts no alcanzables siempre aparecen en el resultado, marcados como `unreachable`.
- Todo configurable vía `.env` — sin credenciales hardcodeadas en código.

---

## Estructura de carpetas <a name="estructura"></a>

```
agents/
├── README.md
│
├── shared/
│   ├── bridge/                      ← bridge unificado (único webhook)
│   │   ├── main.py                  ← FastAPI: router de intents + background tasks
│   │   ├── nlu.py                   ← NLU con Claude Haiku (LLM + fallback keywords)
│   │   ├── aap.py                   ← cliente REST de AWX
│   │   ├── .env                     ← credenciales y IDs (no commitear)
│   │   ├── requirements.txt
│   │   └── ansiblebot.log
│   ├── inventory/
│   │   └── hosts_inventario         ← inventario INI (grupos + hosts)
│   └── docs/
│       ├── naming_convention.md     ← convención de nombres de servidores
│       └── Rutas_de_logs.xlsx       ← catálogo de rutas de logs por servidor
│
├── health-check/
│   ├── playbooks/
│   │   ├── health-check.yml         ← playbook principal (3 plays)
│   │   └── roles/health_check/
│   │       └── tasks/
│   │           ├── main.yml         ← condicional por health_resources
│   │           ├── cpu.yml          ← delta /proc/stat (1s)
│   │           ├── memory.yml       ← ansible_memtotal_mb / free_mb
│   │           └── disk.yml         ← ansible_mounts filtrado por fstype
│   └── bridge/
│       └── health_cards.py          ← Adaptive Cards (render condicional por recurso)
│
├── log-monitor/
│   ├── playbooks/
│   │   ├── log-monitor.yml          ← playbook principal (4 plays)
│   │   ├── group_vars/all.yml       ← rutas base de logs del sistema
│   │   └── roles/log_monitor/
│   │       ├── library/read_logs.py ← módulo Ansible custom multihilo
│   │       └── tasks/
│   │           ├── main.yml         ← block/rescue/always + set_stats
│   │           └── read_logs.yml    ← invoca read_logs.py (become: true)
│   └── bridge/
│       └── log_cards.py
│
└── remediator/
    ├── playbooks/
    │   ├── remediator.yml           ← playbook principal (3 plays)
    │   └── roles/remediator/
    │       └── tasks/
    │           ├── main.yml         ← orquestación + block/rescue/always
    │           ├── diagnose.yml     ← métricas CPU/RAM/Disco + top procesos
    │           ├── fix_cpu.yml      ← mata procesos con CPU > umbral
    │           ├── fix_ram.yml      ← drop_caches + journal + kill selectivo
    │           └── fix_disk.yml     ← /tmp grandes + logs rotados + journal + DNF + cores
    └── bridge/
        └── remediator_cards.py
```

---

## Puesta en marcha <a name="puesta-en-marcha"></a>

### Requisitos

- Python 3.9+
- Acceso a AWX con token de servicio
- Clave API de Anthropic (`ANTHROPIC_API_KEY`)
- Microsoft Teams con Incoming Webhook configurado
- Cloudflare (para exponer el webhook a Teams)

### Instalación

```bash
cd /Ansible/agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r shared/bridge/requirements.txt
```

### Variables de entorno (`shared/bridge/.env`)

```env
# AWX
AWX_URL=https://ol9-awx.lab.com/
AWX_TOKEN=<token_de_servicio>

# Job Templates
HEALTH_JOB_TEMPLATE_ID=9
LOG_JOB_TEMPLATE_ID=10
REMEDIATION_JOB_TEMPLATE_ID=13
INVENTORY_ID=2

# Teams
TEAMS_WEBHOOK_URL=https://...office.com/webhookb2/...
TEAMS_HMAC_TOKEN=<base64_token>

# NLU
ANTHROPIC_API_KEY=sk-ant-...

# Rutas
INVENTORY_PATH=../inventory/hosts_inventario
NAMING_CONVENTION_PATH=../docs/naming_convention.md
```

### Arranque

**Terminal 1 — API:**
```bash
cd /Ansible/agents
uvicorn shared.bridge.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Tunnel:**
```bash
cloudflared tunnel --url http://localhost:8000 --protocol http2
```

---

## Casos de uso <a name="casos"></a>

### 1. Health Check <a name="health-check"></a>

Verifica el estado de CPU, RAM y/o Disco de un host o grupo.

**Ejemplos en Teams:**
```
AnsibleBot como esta ol9server1
AnsibleBot dame la RAM de ol9server1
AnsibleBot CPU y disco de ol9server1
AnsibleBot salud de produccion
AnsibleBot criticos en laboratorio
```

**Parámetro `resources`:** el NLU extrae qué recursos se solicitaron.
El playbook ejecuta **solo** los tasks necesarios y el artifact solo contiene las claves pedidas.

| Solicitud | `resources` | Playbook ejecuta | Tarjeta muestra |
|-----------|------------|-----------------|-----------------|
| "como esta ol9server1" | `["all"]` | cpu + ram + disk | CPU · RAM · Disco |
| "dame la RAM" | `["ram"]` | solo memory.yml | solo RAM |
| "CPU y disco" | `["cpu","disk"]` | cpu + disk | CPU · Disco |

**Umbrales:** Verde < 70% · Amarillo 70–84% · Rojo ≥ 85%

**Artifact publicado:**
```json
{
  "ol9server1": {
    "cpu": 4.4,
    "ram": {"used_percent": 77.0, "free_mb": 1200, "total_mb": 8192},
    "disks": [{"mount": "/", "usage_pct": 42.0, "used_gb": 12.1, "total_gb": 28.7}],
    "generated_at": "2026-04-02T14:00:00",
    "status": "ok"
  }
}
```

---

### 2. Log Monitor <a name="log-monitor"></a>

Escanea logs del sistema y de aplicación en busca de errores, warnings o palabras clave.

**Ejemplos en Teams:**
```
AnsibleBot hay errores en ol9server1
AnsibleBot busca errores en ol9server1 ultima hora
AnsibleBot errores en produccion
AnsibleBot busca OutOfMemory en WEBLOGIC_PDN
```

**Parámetros:** `time_window_hours` (default 2h) · `severity` (ERROR/WARN/ALL) · `keyword`

**Rutas base** (siempre escaneadas, `group_vars/all.yml`):
```yaml
base_log_paths:
  - paths: [/var/log]
    patterns: ["messages*", "syslog*", "dmesg*"]
  - paths: [/var/log]
    patterns: ["secure*", "audit*"]
```

Rutas adicionales por host en `host_vars/{HOSTNAME}.yml` (desde `Rutas_de_logs.xlsx`).
Si una ruta no existe en el servidor, se omite silenciosamente y se reporta en `paths_skipped`.

**Artifact publicado:**
```json
{
  "ol9server1": {
    "hostname": "ol9server1",
    "files_scanned": 3,
    "errors": 8,
    "warns": 0,
    "sample": ["Apr 2 12:41:09 ol9server1 kernel: RAS: Correctable Errors..."],
    "paths_skipped": [],
    "time_window_hours": 2,
    "severity": "ERROR",
    "status": "ok"
  }
}
```

---

### 3. Remediador <a name="remediador"></a>

Diagnostica y/o corrige problemas de CPU, RAM y Disco en servidores Linux.

**Modos:**

| Modo | Intent NLU | Qué hace |
|------|-----------|---------|
| `diagnose` | `diagnose` | Solo lee métricas y top procesos, sin cambios |
| `remediate` | `remediate` | Ejecuta las correcciones del issue solicitado |
| `full` | `full_remediate` | Diagnostica → corrige → toma métricas post |

**Ejemplos en Teams:**
```
AnsibleBot diagnostica ol9server1
AnsibleBot arregla la CPU de ol9server1
AnsibleBot limpia el disco de ol9server1
AnsibleBot libera RAM de ol9server1
AnsibleBot arregla ol9server1              ← issue_type=auto (detecta qué hay alto)
AnsibleBot revisa y arregla ol9server1     ← full + auto
```

**Parámetro `issue_type`:** `cpu` · `ram` · `disk` · `auto`
Con `auto`, el playbook detecta qué recursos superan el 85% y actúa sobre ellos.

**Qué hace cada fixer:**

| Fixer | Acciones |
|-------|---------|
| `fix_cpu.yml` | Identifica procesos con CPU > umbral (default 80%), excluye servicios críticos del sistema, mata los candidatos con SIGTERM |
| `fix_ram.yml` | `drop_caches` (siempre seguro) + `journalctl --vacuum-size=100M` + kill de procesos con alto %MEM si RAM sigue ≥ 80% |
| `fix_disk.yml` | Archivos >500MB en /tmp + archivos viejos en `disk_clean_path` + logs rotados en /var/log + `journalctl --vacuum-time=7d` + `dnf clean all` + core dumps |

**Servicios excluidos del kill (CPU y RAM):**
`sshd, systemd, auditd, crond, rsyslogd, tuned, polkitd, dbus-daemon, NetworkManager, firewalld, chronyd, uwsgi, uvicorn, cloudflared, python3, ansible`

**Artifact publicado:**
```json
{
  "ol9server1": {
    "hostname": "ol9server1",
    "mode": "remediate",
    "issue_type": "disk",
    "actions_taken": ["DISK: eliminados 1 archivo(s) grande(s) en /tmp (3400 MB)"],
    "disk_freed_mb": 3400,
    "pre_metrics":  {"cpu_pct": "0", "ram_pct": "84.2", "disk_pct": "86"},
    "post_metrics": {"cpu_pct": "0", "ram_pct": "80.9", "disk_pct": "2"},
    "status": "remediated"
  }
}
```

**Estados posibles:** `diagnosed` · `remediated` · `no_action_needed` · `error`

---

## NLU — Intents y ejemplos <a name="nlu"></a>

El NLU usa Claude Haiku como modelo principal y un parser de palabras clave como fallback.
Carga el inventario y la convención de nombres en el system prompt para resolver hosts.

### Intents disponibles

```json
// Salud individual / grupo
{"intent":"health_check","target":"ol9server1","type":"host","resources":["ram"]}
{"intent":"health_check","target":"WEBLOGIC_PDN","type":"group","resources":["all"]}

// Salud de ambiente completo
{"intent":"fleet_check","targets":["WEBLOGIC_PDN","JOOMLA_PROD"],"environment":"produccion","filter":"all"}
{"intent":"fleet_check","targets":["WEBLOGIC_LAB"],"environment":"laboratorio","filter":"critical"}

// Logs
{"intent":"log_check","target":"ol9server1","type":"host","time_window_hours":2,"severity":"ERROR","keyword":""}
{"intent":"log_fleet","targets":["WEBLOGIC_PDN"],"environment":"produccion","time_window_hours":2,"severity":"ERROR"}

// Remediación
{"intent":"diagnose","target":"ol9server1","type":"host","issue_type":"auto"}
{"intent":"remediate","target":"ol9server1","type":"host","issue_type":"disk"}
{"intent":"full_remediate","target":"ol9server1","type":"host","issue_type":"auto"}

// Otros
{"intent":"clarify","question":"¿Te refieres a producción o laboratorio?"}
{"intent":"unknown"}
```

### Reglas de clasificación (orden estricto)

1. **Remediación** — palabras: `limpia, libera, arregla, remedia, corrige, fix, clean`
2. **Log** — palabras: `errores, logs, busca, ORA-, Exception, OutOfMemory, WARN`
3. **Salud** — palabras: `CPU, RAM, disco, memoria, salud, como esta, valida, revisar`
4. Ambigüedad → **Log** (nunca `clarify`, salvo colisión de nombres de host)

---

## Recursos AWX <a name="recursos-awx"></a>

| Recurso | ID | Nombre |
|---|---|---|
| Organización | 2 | Bancolombia |
| Execution Environment | 2 | — |
| Credencial SSH | 3 | — |
| Inventario | 2 | — |
| Proyecto | 8 | Agents_TI (rama: `develop`) |
| Job Template Health Check | 9 | Agent - Health Check |
| Job Template Log Monitor | 10 | Agent - Log Monitor |
| Job Template Remediador | 13 | Agent - Remediator |
| AWX URL | — | `https://ol9-awx.lab.com/` |
| Repositorio | — | `https://github.com/Crittan01/Agents_TI.git` |

---

## Stack tecnológico <a name="stack"></a>

| Componente | Versión | Uso |
|---|---|---|
| Python | 3.9 | Bridge y módulos Ansible |
| FastAPI | 0.128+ | Endpoint /teams/webhook |
| uvicorn | 0.39+ | Servidor ASGI |
| anthropic SDK | 0.40+ | NLU con Claude Haiku |
| requests | 2.32+ | Cliente HTTP AWX y Teams |
| python-dotenv | 1.2+ | Variables de entorno |
| Ansible | — | Playbooks en AWX |
| AWX | — | Plataforma de automatización |
| Claude Haiku | claude-haiku-4-5-20251001 | Modelo NLU |
| Cloudflare Tunnel | — | Exposición del webhook a Teams |

---

## Cómo agregar un nuevo caso de uso <a name="nuevo-caso"></a>

### 1. Crear estructura de carpetas

```bash
mkdir -p agents/nuevo-caso/playbooks/roles/nuevo_rol/tasks
mkdir -p agents/nuevo-caso/bridge
```

### 2. Playbook — estructura de 3 plays (patrón del proyecto)

```
Play 1: Detectar hosts alcanzables (ignore_unreachable + group_by: reachable_hosts)
Play 2: Ejecutar rol (solo reachable_hosts) — block/rescue/always — set_stats al final
Play 3: Consolidar en localhost (incluye unreachable en artifact)
```

### 3. Bridge Python

```
nuevo-caso/bridge/nuevo_cards.py   ← constructores de Adaptive Cards
```

En `shared/bridge/`:
- `nlu.py` — agregar nuevos intents al system prompt y al fallback parser
- `aap.py` — agregar `launch_nuevo_job()` y `extract_nuevo_data()`
- `main.py` — agregar handler para el nuevo intent

### 4. AWX

1. Crear Job Template apuntando al playbook nuevo
2. Agregar `NUEVO_JOB_TEMPLATE_ID` al `.env`
3. Sync del proyecto desde la rama `develop`

### 5. Actualizar este README

Agregar sección en [Casos de uso](#casos) con:
descripción · ejemplos de frases · formato del artifact · estados posibles.

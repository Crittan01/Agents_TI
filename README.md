# VOLT — Agentes de Automatización Inteligente

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
8. [Pruebas de stress](#stress)
9. [Cómo agregar un nuevo caso de uso](#nuevo-caso)

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
                          AAP REST API  ──►  Job Template con extra_vars
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
- Filtrado de recursos end-to-end: el NLU extrae qué recursos se pidieron, el playbook
  ejecuta solo esos tasks, el artifact solo contiene esas claves, la card solo renderiza
  lo que llegó.
- Los errores en servidores individuales no detienen la ejecución global.
- Hosts no alcanzables siempre aparecen en el resultado, marcados como `unreachable`.
- Todo configurable vía `.env` — sin credenciales hardcodeadas.

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
│   │   └── volt.log
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
    │           ├── diagnose.yml     ← métricas CPU/RAM/Disco + top procesos (pre y post)
    │           ├── fix_cpu.yml      ← mata procesos con CPU > umbral
    │           ├── fix_ram.yml      ← drop_caches + journal + kill selectivo
    │           └── fix_disk.yml     ← 5 estrategias de limpieza de disco
    ├── bridge/
    │   └── remediator_cards.py
    └── tests/
        └── stress.sh                ← genera carga realista para certificar el remediador
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
source .venv/bin/activate
uvicorn shared.bridge.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Tunnel:**
```bash
cloudflared tunnel --url http://localhost:8000 --protocol http2
```

> `--protocol http2` es obligatorio si QUIC está bloqueado por el firewall
> (síntoma: `failed to dial` / `context deadline exceeded`).

---

## Casos de uso <a name="casos"></a>

### 1. Health Check <a name="health-check"></a>

Verifica el estado de CPU, RAM y/o Disco de un host o grupo.

**Ejemplos en Teams:**
```
VOLT como esta ol9server1
VOLT dame la RAM de ol9server1
VOLT CPU y disco de ol9server1
VOLT salud de produccion
VOLT criticos en laboratorio
```

**Filtrado por recurso — end-to-end:**

| Solicitud | `resources` | Playbook ejecuta | Artifact contiene | Card muestra |
|-----------|------------|-----------------|-------------------|--------------|
| `como esta ol9server1` | `["all"]` | cpu + ram + disk | cpu, ram, disks | CPU · RAM · Disco |
| `dame la RAM` | `["ram"]` | solo memory.yml | ram | solo RAM |
| `dame el disco` | `["disk"]` | solo disk.yml | disks | solo Disco |
| `CPU y disco` | `["cpu","disk"]` | cpu + disk | cpu, disks | CPU · Disco |

**Umbrales de color:** Verde < 70% · Amarillo 70–84% · Rojo >= 85%

**Artifact publicado:**
```json
{
  "ol9server1": {
    "generated_at": "2026-04-02T14:00:00Z",
    "status": "ok",
    "cpu": 4.4,
    "ram": {"used_percent": "77.0", "free_mb": "79", "total_mb": "335"},
    "disks": [
      {"mount": "/",    "fstype": "xfs", "usage_pct": 36.9, "used_gb": 2.56, "total_gb": 6.94},
      {"mount": "/tmp", "fstype": "xfs", "usage_pct": 85.8, "used_gb": 3.38, "total_gb": 3.94}
    ]
  }
}
```

> Las claves `cpu`, `ram`, `disks` solo aparecen en el artifact si fueron solicitadas.

---

### 2. Log Monitor <a name="log-monitor"></a>

Escanea logs del sistema y de aplicación en busca de errores, warnings o palabras clave.

**Ejemplos en Teams:**
```
VOLT hay errores en ol9server1
VOLT busca errores en ol9server1 ultima hora
VOLT errores en produccion
VOLT busca OutOfMemory en WEBLOGIC_PDN
```

**Parámetros:** `time_window_hours` (default 2h) · `severity` (ERROR/WARN/ALL) · `keyword`

**Rutas base** (siempre escaneadas — `group_vars/all.yml`):
```yaml
base_log_paths:
  - paths: [/var/log]
    patterns: ["messages*", "syslog*", "dmesg*"]
  - paths: [/var/log]
    patterns: ["secure*", "audit*"]
```

Rutas adicionales por host en `host_vars/{HOSTNAME}.yml` (pobladas desde `Rutas_de_logs.xlsx`).
Si una ruta no existe en el servidor se omite silenciosamente y se reporta en `paths_skipped`.

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
| `remediate` | `remediate` | Ejecuta correcciones + captura antes/después |
| `full` | `full_remediate` | Diagnostica → corrige → métricas post |

> `remediate` y `full` siempre producen `pre_metrics` (antes) y `post_metrics` (después).

**Ejemplos en Teams:**
```
VOLT diagnostica ol9server1
VOLT arregla la CPU de ol9server1
VOLT limpia el disco de ol9server1
VOLT libera RAM de ol9server1
VOLT arregla ol9server1              ← issue_type=auto
VOLT revisa y arregla ol9server1     ← full + auto
```

**`issue_type`:** `cpu` · `ram` · `disk` · `auto`

Con `auto`, el playbook detecta qué recursos superan el 85% y actúa solo sobre ellos.
La card resultante también filtra y muestra únicamente las métricas del issue resuelto.

**Acciones por fixer:**

| Fixer | Estrategias |
|-------|------------|
| `fix_cpu.yml` | Detecta procesos con CPU > umbral (default 80%), excluye servicios críticos, mata candidatos con SIGTERM |
| `fix_ram.yml` | `sync && drop_caches` (siempre seguro) → `journalctl --vacuum-size=100M` → kill de procesos con alto %MEM si RAM ≥ 80% post-drop |
| `fix_disk.yml` | 1) Archivos >500MB en `/tmp` · 2) Archivos viejos >7d en `disk_clean_path` · 3) Logs rotados `*.gz *.1 *.2 *-YYYYMMDD` · 4) `journalctl --vacuum-time=7d` · 5) `dnf clean all` · 6) Core dumps en `/var/crash` y `core.*` |

**Servicios excluidos del kill (CPU y RAM):**
```
sshd  systemd  auditd  crond  rsyslogd  tuned  polkitd  dbus-daemon
NetworkManager  firewalld  chronyd  uwsgi  uvicorn  cloudflared  python3  ansible
```

**Artifact publicado:**
```json
{
  "ol9server1": {
    "hostname": "ol9server1",
    "mode": "remediate",
    "issue_type": "cpu,disk",
    "diagnosis": {"cpu_pct": "100", "ram_pct": "82.1", "disk_pct": "86"},
    "pre_metrics":  {"cpu_pct": "100", "ram_pct": "82.1", "disk_pct": "86"},
    "post_metrics": {"cpu_pct": "0",   "ram_pct": "84.8", "disk_pct": "2"},
    "actions_taken": [
      "CPU: eliminado PID 47368 (ansible 98.7% bash)",
      "DISK: eliminados 2 archivo(s) grande(s) en /tmp (3400 MB)"
    ],
    "disk_freed_mb": 3400,
    "block_error": false,
    "status": "remediated"
  }
}
```

**Estados posibles:** `diagnosed` · `remediated` · `no_action_needed` · `error` · `unreachable`

---

## NLU — Intents y ejemplos <a name="nlu"></a>

El NLU usa **Claude Haiku** como modelo principal y un parser de palabras clave como fallback.
Carga el inventario y la convención de nombres en el system prompt para resolver hosts.

### Intents disponibles

```json
// Salud individual / grupo
{"intent":"health_check","target":"ol9server1","type":"host","resources":["ram"]}
{"intent":"health_check","target":"ol9server1","type":"host","resources":["cpu","disk"]}
{"intent":"health_check","target":"WEBLOGIC_PDN","type":"group","resources":["all"]}

// Salud de ambiente completo
{"intent":"fleet_check","targets":["WEBLOGIC_PDN","JOOMLA_PROD"],"environment":"produccion","filter":"all"}
{"intent":"fleet_check","targets":["WEBLOGIC_LAB"],"environment":"laboratorio","filter":"critical"}

// Logs
{"intent":"log_check","target":"ol9server1","type":"host","time_window_hours":2,"severity":"ERROR","keyword":""}
{"intent":"log_fleet","targets":["WEBLOGIC_PDN"],"environment":"produccion","time_window_hours":2,"severity":"ERROR"}

// Remediacion
{"intent":"diagnose",        "target":"ol9server1","type":"host","issue_type":"auto"}
{"intent":"remediate",       "target":"ol9server1","type":"host","issue_type":"disk"}
{"intent":"full_remediate",  "target":"ol9server1","type":"host","issue_type":"auto"}

// Otros
{"intent":"clarify","question":"Te refieres a produccion o laboratorio?"}
{"intent":"unknown"}
```

### Reglas de clasificación (orden estricto)

1. **Remediación** — palabras: `limpia, libera, arregla, remedia, corrige, fix, clean, depura, soluciona`
2. **Log** — palabras: `errores, logs, busca, ORA-, Exception, OutOfMemory, WARN`
3. **Salud** — palabras: `CPU, RAM, disco, memoria, salud, como esta, valida, estado, recursos`
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
| Job Template — Health Check | 9 | Agent - Health Check |
| Job Template — Log Monitor | 10 | Agent - Log Monitor |
| Job Template — Remediador | 13 | Agent - Remediator |
| AWX URL | — | `https://ol9-awx.lab.com/` |
| Repositorio | — | `https://github.com/Crittan01/Agents_TI.git` |

---

## Stack tecnológico <a name="stack"></a>

| Componente | Uso |
|---|---|
| Python 3.9 | Bridge y módulos Ansible custom |
| FastAPI + uvicorn | Endpoint `/teams/webhook` (ASGI, `--reload` en dev) |
| anthropic SDK | NLU con Claude Haiku (`claude-haiku-4-5-20251001`) |
| requests | Cliente HTTP para AWX REST API y Teams webhook |
| python-dotenv | Variables de entorno desde `.env` |
| Ansible / AWX | Ejecución de playbooks en servidores Linux |
| Cloudflare Tunnel | Exposición del webhook a Microsoft Teams |
| Microsoft Teams | Incoming Webhook + Adaptive Cards v1.4 |

---

## Pruebas de stress <a name="stress"></a>

El script `remediator/tests/stress.sh` genera carga realista para certificar el remediador.
Se corre directamente vía SSH, sin necesidad de Job Template.

### Uso

```bash
# Stress de un recurso específico
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh cpu  180
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh ram  180
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh disk

# Todo a la vez — ideal para probar auto-detect
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh all  180

# Limpiar cuando termines
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh clean
```

### Qué genera cada tipo

| Tipo | Resultado esperado | Killable por fixer |
|------|-------------------|--------------------|
| `cpu` | 4 × `sha256sum /dev/urandom` → CPU ~100% | Sí — `sha256sum` no está excluido |
| `ram` | Perl adaptativo: calcula MB necesarios para llegar a 87% · `setsid` para sobrevivir al SSH | Sí — `perl` no está excluido |
| `disk` | `/tmp/stress_large.bin` 3.2GB + archivos viejos backdateados 30d + logs rotados `*.1/*.gz/*-YYYYMMDD` + core dumps → /tmp ~88% | Sí — ejercita los 5 paths de `fix_disk.yml` |
| `all` | CPU 100% · RAM ~87% · Disco /tmp ~88% | Los tres recursos remediables |

### Paths de fix_disk.yml cubiertos

| Path | Archivo de stress creado |
|------|--------------------------|
| Archivos >500MB en `/tmp` | `/tmp/stress_large.bin` (3.2 GB) |
| Archivos viejos >7d | `/tmp/stress_old_a.tmp`, `/tmp/stress_old_b.tmp` (backdateados 30 días) |
| Logs rotados `*.1 *.2 *.gz *-YYYYMMDD` | `/var/log/stress_app.log.1-4`, `.log.3.gz`, `.log.4.gz`, `.log-YYYYMMDD` |
| `journalctl --vacuum` | No requiere stress — el journal siempre tiene datos |
| Core dumps | `/tmp/core.stress_app`, `/var/crash/core.stress_svc` |

### Secuencia de certificación completa

```bash
# 1. Levantar entorno
uvicorn shared.bridge.main:app --host 0.0.0.0 --port 8000 --reload &
cloudflared tunnel --url http://localhost:8000 --protocol http2 &

# 2. Generar carga
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh all 180

# 3. Enviar comandos desde Teams (dentro de los 180s de duracion)
#    VOLT arregla ol9server1        ← auto detecta CPU + disco
#    VOLT arregla la CPU de ol9server1
#    VOLT limpia el disco de ol9server1
#    VOLT libera RAM de ol9server1

# 4. Limpiar si quedó algo
ssh ansible@ol9server1 'bash -s' < remediator/tests/stress.sh clean
```

---

## Cómo agregar un nuevo caso de uso <a name="nuevo-caso"></a>

### 1. Crear estructura de carpetas

```bash
mkdir -p agents/nuevo-caso/playbooks/roles/nuevo_rol/tasks
mkdir -p agents/nuevo-caso/bridge
```

### 2. Playbook — patrón de 3 plays del proyecto

```
Play 1: Detectar hosts alcanzables
        (ignore_unreachable + group_by: reachable_hosts)

Play 2: Ejecutar rol (solo reachable_hosts)
        block / rescue / always
        set_stats al final con el artifact del host

Play 3: Consolidar en localhost
        (incluye unreachable en artifact, lanza set_stats global)
```

### 3. Bridge Python

```
nuevo-caso/bridge/nuevo_cards.py   ← constructores de Adaptive Cards
```

En `shared/bridge/`:
- `nlu.py` — agregar nuevos intents al system prompt y al fallback parser
- `aap.py` — agregar `launch_nuevo_job()` y `extract_nuevo_data()`
- `main.py` — agregar handler para el nuevo intent en el router

### 4. AWX

1. Crear Job Template apuntando al playbook nuevo
2. Agregar `NUEVO_JOB_TEMPLATE_ID` al `.env`
3. Sync del proyecto desde la rama `develop`

### 5. Actualizar este README

Agregar sección en [Casos de uso](#casos) con:
descripción · frases de ejemplo · formato del artifact · estados posibles.

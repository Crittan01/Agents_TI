# Agents — AnsibleBot (Sura / NTT Data)

Sistema de agentes de automatizacion inteligente sobre **AAP (Ansible Automation Platform)**.
Cada agente combina un rol Ansible (backend remoto) con una o mas interfaces de usuario
(bridge Python), conectadas a traves de la API REST de AAP y un modulo NLU basado en Claude.

> Este documento sirve de contexto completo para desarrolladores, operadores y sistemas de IA
> que necesiten entender, mantener o extender el proyecto.

---

## Indice

1. [Filosofia del sistema](#filosofia)
2. [Estructura de carpetas](#estructura)
3. [Recursos compartidos](#shared)
4. [Patrones tecnicos reutilizables](#patrones)
5. [Casos de uso](#casos)
   - [health-check](#health-check)
   - [log-monitor](#log-monitor)
6. [Stack tecnologico](#stack)
7. [Como agregar un nuevo caso de uso](#nuevo-caso)

---

## Filosofia del sistema <a name="filosofia"></a>

```
Operador (lenguaje natural)
        |
        v
NLU (Claude Haiku) — resuelve intent + target
        |
        v
AAP REST API — lanza job template con extra_vars: {target}
        |
        v
Rol Ansible — ejecuta en servidor(es) Linux, publica artifacts via set_stats
        |
        v
Bridge Python — lee artifacts, presenta resultados (Teams / Streamlit)
```

**Principios:**
- El operador escribe en lenguaje libre, no necesita conocer nombres exactos de servidores.
- Los errores en servidores individuales NO detienen la ejecucion global — se reportan.
- Los servidores no alcanzables (unreachable) siempre aparecen en el resultado, marcados.
- Teams y Streamlit son interfaces **independientes** — el cliente elige una o ambas.
- El codigo no asume rutas ni credenciales hardcodeadas — todo via `.env` y `host_vars`.

---

## Estructura de carpetas <a name="estructura"></a>

```
agents/
├── README.md                        <- este archivo
│
├── shared/                          <- recursos comunes a todos los agentes
│   ├── inventory/
│   │   └── hosts_inventario         <- inventario INI de AAP (hosts + grupos)
│   └── docs/
│       ├── naming_convention.md     <- convencion de nombres de servidores Sura
│       ├── Excel_abreviaturas_servidores.xlsx  <- fuente original de la convencion
│       └── Rutas_de_logs.xlsx       <- rutas de logs por servidor (WebLogic/Joomla/P8)
│
├── health-check/                    <- Caso de uso 1: Health Check Linux
│   ├── playbooks/
│   │   ├── health-check.yml         <- playbook principal
│   │   └── roles/health_check/      <- rol que recolecta CPU/RAM/Disco via /proc + mounts
│   └── bridge/
│       ├── aap.py                   <- cliente AAP REST (compartido con Streamlit)
│       ├── nlu.py                   <- NLU con Claude Haiku
│       ├── cards.py                 <- constructores Adaptive Cards (Teams only)
│       ├── main.py                  <- FastAPI endpoint (Teams interface)
│       ├── app.py                   <- Streamlit UI (Streamlit interface)
│       ├── .env / .env.example
│       ├── requirements.txt
│       └── README.md
│
└── log-monitor/                     <- Caso de uso 2: Log Monitor (en diseno)
    ├── playbooks/
    │   ├── log-monitor.yml
    │   └── roles/log_monitor/
    │       ├── library/read_logs.py <- modulo Ansible custom (multihilo)
    │       └── tasks/
    └── bridge/
        ├── aap.py
        ├── nlu.py
        ├── cards.py
        ├── main.py
        ├── app.py
        └── .env.example
```

---

## Recursos compartidos (`shared/`) <a name="shared"></a>

### `shared/inventory/hosts_inventario`

Inventario INI de AAP. Contiene todos los hosts y grupos del middleware Sura.
Grupos principales:

| Grupo | Tecnologia | Ambiente |
|---|---|---|
| `WEBLOGIC_PDN` | Oracle WebLogic | Produccion |
| `WEBLOGIC_DLLO` | Oracle WebLogic | Desarrollo |
| `WEBLOGIC_LAB` | Oracle WebLogic | Laboratorio |
| `JOOMLA_PROD` / `JOOMLA_LABO` | Joomla + Apache HTTPD | Prod / Lab |
| `P8_PROD` / `P8_DESA` / `P8_LABO` | IBM FileNet P8 + WebSphere | Prod / Desa / Lab |

Cargado dinamicamente por `nlu.py` via env var `INVENTORY_PATH`.

### `shared/docs/naming_convention.md`

Documento generado desde `Excel_abreviaturas_servidores.xlsx`. Define el patron:

```
[Unidad][Tecnologia][Funcion][Ambiente][NN]
Ejemplo: SGWLSAPPP01 = Seguros + WebLogic + APP + Produccion + nodo 01
```

Cargado por `nlu.py` via env var `NAMING_CONVENTION_PATH` para que el LLM
resuelva descripciones libres como "agente SR produccion 3" → `SRWLSAGEP03`.

### `shared/docs/Rutas_de_logs.xlsx`

Catalogo de rutas de logs por servidor. Tres hojas:

| Hoja | Tecnologia | Usuario OS | Ruta base |
|---|---|---|---|
| `WEBLOGIC` | Oracle WebLogic / OHS | `oracle:oinstall` | `/u01/app/oracle/admin/12.2.1/{Domain}/servers/` |
| `JOOMLA` | Apache HTTPD + Joomla | `apache:apache` | `/opt/app/utils/httpd/httpd/logs/{sitio}/` |
| `P8` | IBM WebSphere + FileNet | `wasadmin:wasadmin` | `/opt/IBM/WebSphere/AppServer/profiles/AppSrv01/logs/{server}/` |

Columnas: `Servidor | Ambiente | Ruta_logs (multilinea) | Extension (multilinea)`.
Fuente de verdad para generar `host_vars/` del caso de uso `log-monitor`.

---

## Patrones tecnicos reutilizables <a name="patrones"></a>

### 1. host_vars por servidor

Cada host tiene su propio `playbooks/host_vars/{HOSTNAME}.yml` con su configuracion.
Generados automaticamente desde Excel via script `scripts/migrate_to_hostvars.py`.
Un centinela `__SIN_CONFIGURAR__` permite detectar hosts sin configuracion en preflight.

```yaml
# Ejemplo: health-check no usa host_vars (target via extra_vars en AAP)
# Ejemplo: log-monitor (patron tomado de cm03-depuracion-file-system)
log_config:
  servicio: WEBLOGIC_PDN
  become_user: oracle      # referencia, NO usado en become_user (se usa root via sudo)
  specific_paths:
    - paths:
        - /u01/app/oracle/admin/12.2.1/Sura_Func_PDN_Domain1/servers
      patterns: ['*.log*', '*.out*']
```

### 2. Manejo de hosts no alcanzables (patron cm03)

Resuelto en dos plays del playbook:

```yaml
# Play 2: detectar alcanzables
- hosts: "{{ target }}"
  gather_facts: true
  ignore_unreachable: true
  tasks:
    - group_by: key=reachable_hosts
      when: ansible_hostname is defined

# Play 4: reconstruir unreachables comparando grupos
unreachable_hosts = grupo_original - groups['reachable_hosts']
```

Los hosts unreachable aparecen en el artifact con `"status": "unreachable"`,
garantizando que el bridge siempre reciba un resultado completo.

### 3. Publicacion de resultados via set_stats (patron health-check)

```yaml
- ansible.builtin.set_stats:
    data:
      HOSTNAME:
        cpu: 4.4
        ram: {used_percent: 77.0, free_mb: 16099, total_mb: 69905}
        disks: [...]
        status: ok
```

El bridge lee el artifact via `GET /api/v2/jobs/{id}/` → `response.artifacts`.
**Importante:** AWX puede demorar hasta 25s en publicar artifacts tras finalizar el job.
El bridge reintenta hasta 5 veces con pausa de 5s.

### 4. Estructura del bridge Python

Cada caso de uso tiene su propio bridge con estos modulos:

| Modulo | Compartido | Descripcion |
|---|---|---|
| `aap.py` | Teams + Streamlit | Cliente REST de AAP: `aap_get`, `launch_awx_job`, `extract_health_data`, validacion en inventario |
| `nlu.py` | Teams + Streamlit | NLU con Claude Haiku — carga inventario + naming convention en system prompt |
| `cards.py` | Teams only | Constructores de Adaptive Cards para Microsoft Teams |
| `main.py` | Teams only | FastAPI endpoint + validacion HMAC-SHA256 + background tasks |
| `app.py` | Streamlit only | UI con sidebar (config + historial), tablas, barras de progreso |

Teams y Streamlit son **independientes** — `app.py` no importa de `cards.py`.

### 5. Elevacion de privilegios

El usuario SSH (credencial AAP ID 51) tiene `sudo` sin contrasena pero **no puede**
elevar a usuario especifico (`become_user`). Por eso todos los `become_user:` estan
comentados en los roles — se usa `become: true` que eleva a root directamente.
Root puede leer archivos de cualquier usuario (oracle, wasadmin, apache).

### 6. NLU — intents disponibles

```json
{"intent": "health_check", "target": "SGWLSAPPP01", "type": "host"}
{"intent": "health_check", "target": "WEBLOGIC_PDN", "type": "group"}
{"intent": "fleet_check",  "targets": ["WEBLOGIC_PDN", "JOOMLA_PROD"],
                           "environment": "produccion", "filter": "all|critical"}
{"intent": "clarify",      "question": "pregunta corta al operador"}
{"intent": "unknown"}
```

Fallback a parser de palabras clave si `ANTHROPIC_API_KEY` no esta configurada.

---

## Casos de uso <a name="casos"></a>

### 1. health-check <a name="health-check"></a>

**Estado:** Activo
**Descripcion:** Health check de servidores Linux — CPU, RAM, Disco.
**Documentacion completa:** [`health-check/bridge/README.md`](health-check/bridge/README.md)

**Flujo:**
1. Operador escribe en Teams o Streamlit (lenguaje libre o nombre exacto)
2. NLU resuelve: `health_check` (host/grupo) o `fleet_check` (ambiente completo)
3. AAP lanza el job template 178 con `extra_vars: {target: NOMBRE}`
4. Rol `health_check` recolecta metricas via `/proc/stat` (CPU), facts de memoria, `ansible_mounts`
5. `set_stats` consolida todos los hosts en un unico artifact (`run_once`)
6. Bridge lee artifacts y presenta: card Teams con colores por umbral / tabla Streamlit con barras

**Umbrales:** Verde < 70% | Amarillo 70–84% | Rojo >= 85%
**Recursos AAP:** Job Template 178 | Inventario 35 | Proyecto 61 | Credencial 51

**Limitaciones conocidas:**
- AWX artifacts pueden demorar ~25s — bridge reintenta x5
- fleet_check critico pagina cards a 15 filas/card (limite 28KB Teams Incoming Webhook)
- SSL desactivado en llamadas AAP (`verify=False`) — certificado auto-firmado del cliente

---

### 2. log-monitor <a name="log-monitor"></a>

**Estado:** En diseno — no implementado
**Descripcion:** Consulta de logs de aplicacion en servidores Linux (WebLogic, Joomla, P8).

**Problema que resuelve:**
Los operadores necesitan revisar errores en logs sin acceso SSH directo.
La consulta se hace en lenguaje natural desde Teams o Streamlit.

**Fuente de datos:**
`shared/docs/Rutas_de_logs.xlsx` — catalogo completo de rutas por servidor y tecnologia.
Un script `scripts/migrate_to_hostvars.py` (adaptar del cm03) genera los `host_vars/`
automaticamente desde el Excel.

**Intents NLU planeados:**

| Consulta | Intent | Extra params |
|---|---|---|
| `¿hay errores en SGWLSAPPP01 hoy?` | `log_errors` | `time_window_hours: 24` |
| `muestra los ultimos logs de P8_PROD` | `log_tail` | `lines: 20` |
| `¿hay OutOfMemoryError en produccion?` | `log_search` | `keyword: "OutOfMemoryError"`, `fleet: true` |
| `¿que paso en WebLogic prod ayer a las 3pm?` | `log_search` | `time_start`, `time_end` |

**Formato del artifact:**
```json
{
  "SGWLSAPPP01": {
    "status": "ok",
    "servicio": "WEBLOGIC_PDN",
    "window_hours": 2,
    "files_scanned": 4,
    "errors": 14,
    "warns": 3,
    "sample": [
      "2026-03-25 10:41:22 ERROR NullPointerException at ...",
      "2026-03-25 10:38:01 ERROR Connection refused ..."
    ]
  },
  "SGWLSAPPP02": {
    "status": "unreachable",
    "errors": 0,
    "sample": []
  }
}
```

**Rol Ansible planeado:**

```
roles/log_monitor/
├── library/
│   └── read_logs.py        <- modulo custom multihilo (como delete_files.py en cm03)
│                              Params: paths, patterns, time_window_hours, severity, keyword
│                              Output: {files_scanned, errors, warns, sample[max 50 lineas]}
└── tasks/
    ├── main.yml            <- bloque/rescue/always + set_stats
    ├── read_logs.yml       <- invoca read_logs.py con become: true (root)
    └── unreachable.yml     <- marcado de hosts no alcanzables
```

**Patrones reutilizados de cm03-depuracion-file-system:**
- `host_vars/{HOST}.yml` con centinela `__SIN_CONFIGURAR__`
- Play 1: Preflight estricto (para si hay hosts sin config)
- Play 2: `ignore_unreachable` + `group_by: reachable_hosts`
- Play 4: Reconstruccion de unreachables comparando grupos (evita race condition)
- `scripts/migrate_to_hostvars.py` — adaptar para Rutas_de_logs.xlsx
- `become: true` sin `become_user` (root lee todos los archivos)

**Referencia cm03:**
`/Ansible/sura/1-automatizaciones-ntt-ansible-app-server-conf/cm03-ansible-depuracion-file-system`

---

## Stack tecnologico <a name="stack"></a>

| Componente | Version | Uso |
|---|---|---|
| Python | 3.9+ | Bridge y scripts |
| FastAPI | 0.128.8 | Endpoint Teams (main.py) |
| uvicorn | 0.39.0 | Servidor ASGI |
| Streamlit | >= 1.35.0 | UI web (app.py) |
| anthropic | >= 0.40.0 | NLU con Claude Haiku |
| requests | 2.32.5 | Cliente HTTP AAP y Teams |
| pydantic | 2.12.5 | Validacion de payloads |
| python-dotenv | 1.2.1 | Variables de entorno |
| openpyxl | — | Lectura de Excel en scripts |
| Ansible | — | Playbooks ejecutados en AAP |
| AAP / AWX | — | Plataforma de automatizacion |
| Claude Haiku | claude-haiku-4-5-20251001 | Modelo NLU |

---

## Como agregar un nuevo caso de uso <a name="nuevo-caso"></a>

### 1. Crear estructura

```bash
mkdir -p agents/nombre-caso/playbooks/roles/nombre_rol/tasks
mkdir -p agents/nombre-caso/bridge
```

### 2. Bridge Python

```python
# aap.py     — copiar de health-check/bridge/aap.py sin cambios
# nlu.py     — adaptar system prompt y _ENV_GROUPS para el nuevo dominio
# cards.py   — nuevos constructores de cards para Teams
# main.py    — adaptar intents del endpoint
# app.py     — adaptar UI Streamlit
```

### 3. Paths a shared/

Desde cualquier `bridge/` los paths relativos son:
```
../../shared/inventory/hosts_inventario
../../shared/docs/naming_convention.md
../../shared/docs/Rutas_de_logs.xlsx
```

### 4. Playbook — estructura de 4 plays (patron cm03)

```
Play 0: Inicializacion (localhost)
Play 1: Preflight estricto — valida host_vars (para si falla 1 host)
Play 2: Detectar alcanzables (ignore_unreachable + group_by: reachable_hosts)
Play 3: Ejecucion (solo reachable_hosts) + set_stats
Play 4: Consolidado en localhost (incluye unreachable en resultado)
```

### 5. Actualizar este README

Agregar el nuevo caso en la tabla de la seccion [Casos de uso](#casos)
y documentar: descripcion, flujo, formato de artifact, intents NLU, patrones reutilizados.

---

## Recursos AAP

| Recurso | ID | Nombre |
|---|---|---|
| Job Template health-check | 178 | Agent - Health Check Linux |
| Inventario | 35 | Inventario Linea Base - Middleware |
| Proyecto | 61 | Sura_NTT_Data |
| Credencial SSH | 51 | Machine SSH (sudo sin contrasena, sin become_user especifico) |
| Organizacion | 5 | Sura_NTT_Data |
| AAP URL | — | `https://10.216.24.208` |

---

## Autor

Cristian Camilo Garzon — crisgrta@suramericana.com.co

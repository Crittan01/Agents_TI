# VOLT — Health Check Bridge

Sistema de automatizacion que permite ejecutar health checks sobre servidores y grupos Linux desde dos interfaces: **Microsoft Teams** y **Streamlit** (web app local). Conecta con AAP (Ansible Automation Platform) para lanzar playbooks y retornar resultados en tiempo real.

El sistema incluye un modulo NLU basado en Claude (Anthropic) que interpreta lenguaje natural, resolviendo descripciones libres a nombres exactos de host, grupo o ambiente completo del inventario.

---

## Interfaces disponibles

| Interfaz | Archivo | Puerto | Uso |
|---|---|---|---|
| Teams (Outgoing Webhook) | `main.py` | 8000 | Canal corporativo, notificacion automatica |
| Streamlit (web app) | `app.py` | 8501 | Uso local, visualizacion con graficas |

Ambas interfaces son **completamente independientes** — el cliente elige una o la otra. Comparten el mismo `.env` pero ninguna depende de la otra. Los modulos `aap.py` y `nlu.py` son compartidos; `cards.py` es exclusivo de Teams.

---

## Estructura del proyecto

```
agents/teams-webhook/
├── main.py       # Interface Teams — FastAPI endpoint, HMAC, background tasks
├── app.py        # Interface Streamlit — UI web, visualizacion de metricas
├── aap.py        # Modulo compartido — interaccion con AAP (REST API)
├── cards.py      # Modulo Teams — constructores de Adaptive Cards
├── nlu.py        # Modulo compartido — NLU con Claude Haiku
├── .env          # Variables de entorno (no commitear)
└── requirements.txt
```

| Modulo | Usado por | Responsabilidad |
|---|---|---|
| `aap.py` | Teams + Streamlit | `aap_get`, `launch_awx_job`, `extract_health_data`, validacion en inventario |
| `nlu.py` | Teams + Streamlit | Interpretar lenguaje libre → intent + target |
| `cards.py` | Teams only | Construir Adaptive Cards para Microsoft Teams |
| `main.py` | Teams only | Endpoint FastAPI, firma HMAC, envio de cards |
| `app.py` | Streamlit only | UI con sidebar, tablas, barras de progreso |

---

## Arquitectura

### Escenario actual — NTT Data operando al cliente (pruebas)

```
Teams NTT Data
    | Outgoing Webhook (HMAC-SHA256)
    v
Cloudflare Tunnel  <-- necesario: WSL no tiene IP publica
    |
    v
FastAPI :8000  [WSL NTTD-170N1B4, tiene VPN al cliente]
    |
    | NLU (Claude Haiku) -- resuelve lenguaje libre a host/grupo
    |
    | REST API Bearer Token
    v
AAP Sura [10.216.24.208]
    | SSH
    v
Servidor(es) Linux target
    | Rol health_check + set_stats (hostvars + run_once)
    v
Artifacts del job --> FastAPI --> Adaptive Card en Teams

─────────────────────────────────────────────
Browser local
    |
    v
Streamlit :8501  [WSL, misma maquina, misma VPN]
    |
    v
AAP Sura --> Servidor(es) Linux --> Resultados en pantalla
```

### Escenario objetivo — Cliente opera su propio sistema

```
Teams Sura (Microsoft cloud)
    | HTTPS — IPs Microsoft 365 whitelisteadas
    v
Firewall / Reverse Proxy Sura (DMZ, IP publica corporativa)
    | HTTP interno
    v
Servidor Linux Sura  [red interna]
    | REST API Bearer Token (misma red, sin VPN)
    v
AAP Sura [10.216.24.208]
    | SSH
    v
Servidor(es) Linux target --> Artifacts --> Adaptive Card en Teams

─────────────────────────────────────────────
Browser interno Sura
    |
    v
Streamlit :8501  [mismo servidor interno, sin exposicion externa]
    |
    v
AAP Sura --> Resultados en pantalla
```

Ventaja clave: AAP es accesible directamente desde el servidor bridge (misma red interna) sin VPN ni dependencia de laptops de NTT Data.

### Flujo interno (comun a ambas interfaces)

1. Recibe texto libre del operador (Teams o Streamlit)
2. **NLU** interpreta la descripcion y resuelve al intent correcto:
   - `health_check` → host o grupo especifico
   - `fleet_check` → ambiente completo (multiples grupos en paralelo)
3. Valida firma HMAC-SHA256 del request (solo Teams)
4. Verifica existencia en inventario de AAP
5. Lanza job(s) en AAP con `extra_vars: {target: <host_o_grupo>}`
6. Sondea estado cada 5s (timeout: 5 min por job)
7. Lee artifacts via `GET /api/v2/jobs/{id}/`
8. Presenta resultados (card/s en Teams / metricas en Streamlit)

---

## Modulo NLU (`nlu.py`)

Interpreta mensajes en lenguaje libre y retorna la intencion y el objetivo exacto. Usa **Claude Haiku** (`claude-haiku-4-5-20251001`) con el inventario completo en el system prompt.

### Intents y respuestas posibles

```json
{"intent": "health_check", "target": "SGWLSAPPP01",   "type": "host"}
{"intent": "health_check", "target": "WEBLOGIC_PDN",   "type": "group"}
{"intent": "fleet_check",  "targets": ["WEBLOGIC_PDN", "JOOMLA_PROD", "P8_PROD"],
                           "environment": "produccion", "filter": "all|critical"}
{"intent": "clarify",      "question": "..."}
{"intent": "unknown"}
```

### Ejemplos de resolucion

| Entrada del operador | Intent | Resultado |
|---|---|---|
| `SGWLSAPPP01` | health_check | `SGWLSAPPP01 / host` |
| `agente suramericana produccion 3` | health_check | `SRWLSAGEP03 / host` |
| `como esta el servidor 7 de WLS prod` | health_check | `SGWLSAPPP07 / host` |
| `valida el grupo P8 de Desarrollo` | health_check | `P8_DESA / group` |
| `checa los WebLogic de produccion` | health_check | `WEBLOGIC_PDN / group` |
| `¿como esta produccion?` | fleet_check | `[WEBLOGIC_PDN, JOOMLA_PROD, P8_PROD] / all` |
| `¿hay problemas en prod?` | fleet_check | `[WEBLOGIC_PDN, JOOMLA_PROD, P8_PROD] / critical` |
| `¿hay servidores en umbral alto en desarrollo?` | fleet_check | `[WEBLOGIC_DLLO, P8_DESA] / critical` |
| `revisa todo laboratorio` | fleet_check | `[WEBLOGIC_LAB, JOOMLA_LABO, P8_LABO] / all` |
| `valida los OHS de Seguros prod` | clarify | no existe grupo OHS |

### Convencion de nombres

```
{Unidad}{Tecnologia}{Funcion}{Ambiente}{Numero}
  SG = Seguros       WLS = WebLogic     D/DESA = Desarrollo
  SR = Suramericana  OHS = Oracle HTTP  L/LABO = Laboratorio
  ARL = ARL          P8  = FileNet P8   P/PDN  = Produccion
  EPS = EPS          WAS = WebSphere
  DN  = Rep. Dom.    JOOMLA = Joomla
```

### Fallback

Si `ANTHROPIC_API_KEY` no esta configurada o el LLM falla, el modulo cae a un parser basico de palabras clave (sin inteligencia de nombres).

---

## Modos de operacion

### health_check — host o grupo especifico

El job se lanza **una sola vez** contra el host o grupo. Para grupos, el playbook recolecta metricas en paralelo y las consolida en un unico artifact mediante `hostvars` + `run_once`:

```
Host1 → set_fact(_health_data)  ┐
Host2 → set_fact(_health_data)  ├── en paralelo
Host3 → set_fact(_health_data)  ┘
         ↓ (run_once, delegate_to: localhost)
         set_stats({ Host1: data, Host2: data, Host3: data })
```

**Un job, un artifact, N hosts.**

- Teams: card con tabla `Servidor | CPU | RAM | Disco (max)` con colores por umbral
- Streamlit: tabla resumen + expander con detalle por servidor

### fleet_check — barrido de ambiente completo

Se lanzan **N jobs en paralelo** (uno por grupo del ambiente) mediante threads. El sistema espera todos y consolida los resultados.

```
"¿como esta produccion?"
         ↓ NLU
fleet_check: [WEBLOGIC_PDN, JOOMLA_PROD, P8_PROD]
         ↓ threads en paralelo
Job #1 (WEBLOGIC_PDN) ──┐
Job #2 (JOOMLA_PROD)  ──┼── wait concurrente
Job #3 (P8_PROD)      ──┘
         ↓ consolidado
Card resumen por grupo  o  Cards de servidores en umbral
```

**filter=all**: una card con tabla `Grupo | Hosts | Criticos | Alertas | Ok`

**filter=critical**: cards con los servidores >= 70%, ordenados por severidad. Si superan el limite de 28 KB de Teams Incoming Webhook, se paginan automaticamente (`1/N, 2/N...`).

- Colores por metrica: cada columna (CPU, RAM, Disco) muestra su propio color — un servidor puede tener CPU verde y RAM roja. El servidor aparece en la lista si **alguna** metrica supera el umbral.
- Ambientes disponibles: `produccion` (PDN/PROD), `desarrollo` (DLLO/DESA), `laboratorio` (LAB/LABO)

---

## Como se obtienen los datos del servidor

El rol Ansible `health_check` recolecta metricas en el servidor remoto y las publica en AAP mediante `ansible.builtin.set_stats`:

```
/proc/stat (x2, delta 1s) --> cpu_usage
ansible_memtotal_mb / memfree_mb --> ram_used_percent
ansible_mounts (filtrado por fstype) --> disks_health
        |
        v (run_once desde localhost)
set_stats --> artifacts del job en AAP
        |
        v
GET /api/v2/jobs/{id}/ --> { "artifacts": { "HOSTNAME": { ... } } }
```

Formato del artifact (un host por clave raiz):

```json
{
  "SGWLSAPPP01": {
    "cpu": 4.4,
    "ram": { "used_percent": 77.0, "free_mb": 16099, "total_mb": 69905 },
    "disks": [
      { "mount": "/", "usage_pct": 4.0, "used_gb": 0.16, "total_gb": 3.99, "fstype": "xfs" }
    ],
    "generated_at": "2026-03-23T15:43:24Z",
    "status": "ok"
  },
  "SGWLSAPPP02": { ... }
}
```

---

## Requisitos

### Escenario actual (NTT Data / WSL)

- Python 3.9+
- VPN activa al cliente (Cisco AnyConnect + ISE Posture en Windows — WSL hereda la red)
- Cloudflare Tunnel corriendo (solo para Teams)
- Cuenta de usuario en AAP con Bearer Token valido
- Outgoing Webhook configurado en Teams con Security Token
- API Key de Anthropic (para NLU con Claude)

### Escenario cliente (infraestructura Sura)

- Servidor Linux (RHEL 8+), 2 vCPU / 2 GB RAM minimo
- Python 3.9+
- Acceso interno al AAP (`10.216.24.208:443`)
- Puerto 443 entrante desde IPs de Microsoft 365 (para Teams)
- Puerto 8501 entrante desde red interna (para Streamlit, opcional)
- Puerto 443 saliente hacia `*.webhook.office.com` y `api.anthropic.com`
- Certificado SSL emitido por CA interna o publica para el FQDN del servidor
- Registro DNS interno: `volt.sura.com.co` → IP del servidor
- Admin de Teams de Sura configura el Outgoing Webhook

---

## Configuracion

### 1. Entorno virtual

```bash
cd agents/teams-webhook
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo rpm -ivh https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm
```

### 2. Variables de entorno

```bash
cp .env.example .env
# Editar .env con los valores reales
```

| Variable | Descripcion | Default |
|---|---|---|
| `AWX_URL` | URL base del AAP | `https://10.216.24.208` |
| `AWX_TOKEN` | Bearer token del usuario AAP | — |
| `JOB_TEMPLATE_ID` | ID del Job Template | `178` |
| `INVENTORY_ID` | ID del inventario | `35` |
| `TEAMS_WEBHOOK_URL` | Incoming Webhook de Teams (respuestas) | — |
| `TEAMS_HMAC_TOKEN` | Security token del Outgoing Webhook | — |
| `ANTHROPIC_API_KEY` | API Key de Anthropic para el NLU | — |
| `INVENTORY_PATH` | Ruta al archivo de inventario local | `../inventory/hosts_inventario` |

#### Como obtener TEAMS_HMAC_TOKEN

```
Canal Teams → ••• → Aplicaciones → VOLT → Editar → Security token
```

---

## Arranque

### Interface Teams

```bash
# Terminal 1 — FastAPI
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2 — Cloudflare Tunnel (solo escenario NTT Data)
cloudflared tunnel --url http://localhost:8000 --protocol http2
```

Actualizar la URL del Outgoing Webhook en Teams cada vez que cambie el tunnel:
```
Canal → ••• → Aplicaciones → VOLT → Editar
URL: https://<nueva-url>.trycloudflare.com/teams/webhook
```

### Interfaz Streamlit

```bash
source venv/bin/activate
streamlit run app.py
# Abrir: http://localhost:8501
```

No requiere Cloudflare. Acceso directo via browser local o interno.

---

## Uso

### Teams

**Servidor individual:**
```
@VOLT validar SGWLSAPPP01
@VOLT checa el agente suramericana produccion 3
@VOLT como esta el servidor 7 de WLS produccion
```

**Grupo especifico:**
```
@VOLT revisa el grupo P8 de desarrollo
@VOLT como van los WebLogic de produccion
@VOLT valida todos los P8 de laboratorio
```

**Barrido de ambiente completo:**
```
@VOLT ¿como esta produccion?
@VOLT ¿hay problemas en prod?
@VOLT ¿hay servidores en umbral alto en desarrollo?
@VOLT estado general de laboratorio
```

Respuesta inmediata: mensaje de texto confirmando los jobs lanzados.
Respuesta final (~1-2 min): card(s) con resultados. Si hay muchos servidores en umbral, llegan varias cards paginadas.

### Streamlit

1. Escribir nombre exacto, descripcion libre o nombre de grupo en el campo de texto
2. Click en **Validar**
3. El estado del job se actualiza en tiempo real
4. Los resultados se muestran con metricas visuales y barras de progreso
5. El **sidebar** muestra la configuracion de conexion y el historial de consultas de la sesion

---

## Seguridad

### Validacion HMAC-SHA256 (Teams)

Teams firma cada request con `HMAC-SHA256` usando el Security Token. FastAPI verifica la firma antes de procesar. Requests sin firma valida retornan `401`:

```
[SECURITY] Firma HMAC invalida — origen: 1.2.3.4
```

### Semaforo de umbrales

| Indicador | Umbral |
|---|---|
| Good / Verde | < 70% |
| Warning / Amarillo | 70% — 84% |
| Attention / Rojo | >= 85% |

### Zona horaria

Todos los timestamps se muestran en **COT (UTC-5)**. Colombia no tiene horario de verano.

---

## Recursos AAP

| Recurso | ID | Nombre |
|---|---|---|
| Job Template | 178 | Agent - Health Check Linux |
| Inventario | 35 | Inventario Linea Base - Middleware |
| Proyecto | 61 | Sura_NTT_Data |
| Credencial | 51 | Machine SSH |
| Organizacion | 5 | Sura_NTT_Data |

---

## Stack

| Componente | Version |
|---|---|
| FastAPI | 0.128.8 |
| uvicorn | 0.39.0 |
| Streamlit | >= 1.35.0 |
| requests | 2.32.5 |
| pydantic | 2.12.5 |
| python-dotenv | 1.2.1 |
| anthropic | >= 0.40.0 |

---

## Limitaciones escenario actual (NTT Data / WSL)

- **URL de Cloudflare cambia** en cada reinicio del tunnel. Solucion: Named Tunnel de Cloudflare con cuenta gratuita.
- **Depende de VPN personal activa** (Cisco AnyConnect + ISE Posture). Un servidor en la red del cliente elimina esta dependencia.
- **SSL desactivado** en llamadas al AAP (`verify=False`) por certificado auto-firmado del cliente.
- **artifacts de AWX** pueden tener un retardo de hasta 25s en estar disponibles tras finalizar el job (comportamiento conocido en AWX). El bridge reintenta hasta 5 veces con pausa de 5s.
- **Grupos sin subgrupos** en el inventario actual no permiten filtrar por tecnologia dentro de un ambiente (ej: solo los OHS de produccion). Requiere agregar subgrupos al inventario de AAP.
- **fleet_check filter=critical incluye >= 70%** (Warning + Attention). En ambientes con muchos servidores en alerta, esto genera varias cards paginadas. Pendiente: distinguir "criticos" (>= 85%) de "en alerta" (>= 70%) desde el NLU.

---

## Autor

- Cristian Camilo Garzon — crisgrta@suramericana.com.co

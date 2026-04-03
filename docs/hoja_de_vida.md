# AnsibleBot — Hoja de Vida

**Proyecto:** AnsibleBot — Interfaz de Lenguaje Natural para Operaciones de Infraestructura
**Plataforma:** AAP / AWX · Microsoft Teams · Python · Ansible
**Repositorio:** `https://github.com/Crittan01/Agents_TI` · rama `develop`

---

## 1. Escenario

### Problema que resuelve

Los equipos de operaciones de infraestructura atienden incidentes que requieren acciones
repetitivas sobre servidores Linux: verificar el estado de recursos, revisar logs de error,
liberar espacio en disco, matar procesos que consumen CPU o RAM en exceso.

Estas acciones normalmente implican:
- Acceso SSH directo al servidor
- Conocimiento de comandos específicos de Linux
- Disponibilidad del operador en cualquier momento
- Tiempo de respuesta variable según la carga del equipo

### Solución

**AnsibleBot** permite que cualquier operador ejecute estas acciones escribiendo una frase
en lenguaje natural dentro de **Microsoft Teams**, sin acceso SSH, sin conocimiento de
comandos y con respuesta en menos de 2 minutos.

```
Operador en Teams:                    AnsibleBot responde (< 2 min):
─────────────────────────────         ──────────────────────────────────
"AnsibleBot como esta ol9server1"  →  Tarjeta con CPU 4% · RAM 77% · Disco 36%
"AnsibleBot hay errores en ol9server1" → Tarjeta con 8 errores + muestra de logs
"AnsibleBot arregla ol9server1"    →  Tarjeta con acciones ejecutadas + antes/después
```

### Contexto de uso

- Operadores de infraestructura que reciben alertas y necesitan validar estado
- Guardia / on-call que necesita respuesta rápida sin abrir sesión SSH
- Líderes técnicos que quieren visibilidad del estado de servidores desde Teams

---

## 2. Funcionalidades

### 2.1 Verificación de salud (Health Check)

Consulta el estado de CPU, RAM y/o Disco de un servidor o grupo de servidores.

**Capacidad selectiva:** el operador puede pedir uno o varios recursos en la misma frase.
El sistema ejecuta solo lo necesario y la tarjeta muestra solo lo que fue solicitado.

| Frase | Recursos verificados |
|-------|---------------------|
| `como esta ol9server1` | CPU + RAM + Disco |
| `dame la RAM de ol9server1` | Solo RAM |
| `CPU y disco de ol9server1` | CPU + Disco |
| `salud de produccion` | Todos los servidores del ambiente |
| `criticos en laboratorio` | Solo servidores con algún recurso >= 70% |

**Umbrales:** Verde < 70% · Amarillo 70–84% · Rojo >= 85%

---

### 2.2 Monitor de Logs

Escanea los archivos de log del sistema y de aplicación buscando errores, advertencias
o palabras clave en una ventana de tiempo configurable.

| Frase | Comportamiento |
|-------|---------------|
| `hay errores en ol9server1` | Últimas 2 horas, severidad ERROR |
| `errores en ol9server1 ultima hora` | Última 1 hora, severidad ERROR |
| `busca OutOfMemory en WEBLOGIC_PDN` | Todos los servidores del grupo, busca keyword |
| `errores en produccion` | Todos los ambientes productivos |

Escanea rutas base del sistema (`/var/log/messages`, `/var/log/secure`, etc.) más rutas
específicas por aplicación catalogadas por servidor.

---

### 2.3 Remediador

Corrige automáticamente problemas de CPU, RAM y Disco. Siempre captura métricas **antes**
y **después** para mostrar el impacto de las acciones.

**Modos de operación:**

| Modo | Frase de ejemplo | Comportamiento |
|------|-----------------|----------------|
| Diagnosticar | `diagnostica ol9server1` | Solo lee y reporta — sin cambios |
| Remediar recurso | `limpia el disco de ol9server1` | Aplica correcciones al recurso indicado |
| Remediar general | `arregla ol9server1` | Detecta automáticamente qué recursos están críticos (>= 85%) y los corrige |
| Revisar y arreglar | `revisa y arregla ol9server1` | Diagnostica + corrige + métricas post |

**Acciones por recurso:**

| Recurso | Acciones que ejecuta |
|---------|---------------------|
| CPU | Identifica procesos con alto consumo · Excluye servicios críticos del sistema · Termina procesos candidatos |
| RAM | Libera caché del sistema (`drop_caches`) · Compacta journal del sistema · Kill selectivo si RAM sigue >= 80% |
| Disco | Elimina archivos grandes en `/tmp` · Borra archivos viejos (> 7 días) · Limpia logs rotados · Compacta journal · Limpia caché DNF · Elimina core dumps |

**Resultado en tarjeta (ejemplo disco):**

```
Remediador — ol9server1
Issue: DISK
Disco: 86% → 2%   (espacio liberado: 3400 MB)
Acciones:
  • DISK: eliminados 2 archivo(s) grande(s) en /tmp (3400 MB)
```

La tarjeta solo muestra las métricas del recurso que fue intervenido.

---

## 3. Arquitectura

### Diagrama de componentes

```
┌─────────────────────────────────────────────────────────────────────┐
│  Microsoft Teams                                                    │
│  Operador escribe frase en canal configurado                        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  HTTP POST (Adaptive Card / texto)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Cloudflare Tunnel                                                  │
│  Expone el webhook interno a internet de forma segura               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  HTTPS → localhost:8000
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  FastAPI  /teams/webhook                      (shared/bridge/)      │
│                                                                     │
│  1. Valida firma HMAC-SHA256 del mensaje de Teams                   │
│  2. Extrae el texto de la solicitud                                 │
│  3. Invoca NLU (Claude Haiku) → intent + parámetros                │
│  4. Responde de inmediato con tarjeta de confirmación               │
│  5. Lanza tarea en background → AWX REST API                        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  REST API v2
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AAP / AWX                                                          │
│                                                                     │
│  Job Template  ──►  Execution Environment  ──►  Playbook Ansible    │
│                                                                     │
│  ┌──────────────────┐  ┌───────────────────┐  ┌─────────────────┐  │
│  │  Health Check    │  │  Log Monitor      │  │  Remediador     │  │
│  │  Job Template 9  │  │  Job Template 10  │  │  Job Template 13│  │
│  └──────────────────┘  └───────────────────┘  └─────────────────┘  │
│           │                     │                      │            │
│           └─────────────────────┴──────────────────────┘            │
│                                 │                                   │
│                    set_stats → artifacts                            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  Ejecuta sobre
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Servidores Linux (RHEL 9)                                          │
│  ol9server1 · ol9server2 · grupos de inventario                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  Resultado via artifacts
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Bridge (Python)                                                    │
│  Lee artifacts de AWX · Construye Adaptive Card · Publica a Teams   │
└─────────────────────────────────────────────────────────────────────┘
```

### Componentes principales

| Componente | Tecnología | Responsabilidad |
|-----------|-----------|----------------|
| Webhook endpoint | FastAPI + uvicorn | Recibe mensajes de Teams, valida HMAC, despacha intents |
| NLU | Claude Haiku (Anthropic API) | Interpreta lenguaje natural → intent + parámetros estructurados |
| Cliente AWX | Python requests | Lanza jobs, consulta estado, lee artifacts |
| Playbooks | Ansible (AWX) | Ejecutan acciones en servidores Linux |
| Bridge de tarjetas | Python | Transforma artifacts JSON en Adaptive Cards de Teams |
| Tunnel | Cloudflare | Expone el webhook privado a internet |

### Seguridad

- Cada mensaje de Teams lleva firma **HMAC-SHA256** que el webhook verifica antes de procesar.
- El token AWX es de servicio, con permisos mínimos (ejecutar jobs del proyecto).
- La clave Anthropic solo se usa en el proceso NLU local.
- No hay credenciales SSH en el código — AWX usa su propia credencial con llave pública.
- Los servidores objetivo solo son accedidos a través de AWX (no hay SSH directo desde el bridge).

---

## 4. Entradas y Salidas

### Entrada

**Canal:** Mensaje de texto en Microsoft Teams (chat del canal donde está el webhook)

**Formato:** Lenguaje natural en español. No requiere sintaxis especial.

**Ejemplos válidos:**
```
AnsibleBot como esta ol9server1
AnsibleBot dame solo la RAM de ol9server1
AnsibleBot hay errores en ol9server1
AnsibleBot busca errores en produccion ultima hora
AnsibleBot arregla ol9server1
AnsibleBot limpia el disco de ol9server1
AnsibleBot libera RAM de ol9server1
AnsibleBot diagnostica ol9server1
AnsibleBot salud de laboratorio
AnsibleBot criticos en produccion
```

**Parámetros extraídos por el NLU:**

| Campo | Descripción | Ejemplo |
|-------|-------------|---------|
| `intent` | Qué acción ejecutar | `health_check`, `remediate`, `log_check` |
| `target` | Servidor o grupo objetivo | `ol9server1`, `WEBLOGIC_PDN` |
| `type` | `host` o `group` | `host` |
| `resources` | Para health check: qué recursos | `["ram"]`, `["cpu","disk"]`, `["all"]` |
| `issue_type` | Para remediador: qué recurso | `cpu`, `ram`, `disk`, `auto` |
| `severity` | Para logs: nivel de severidad | `ERROR`, `WARN` |
| `time_window_hours` | Para logs: ventana de tiempo | `1`, `2`, `24` |

---

### Salida

**Canal:** Tarjeta Adaptive Card publicada en el mismo canal de Teams

**Fase 1 — Confirmación inmediata** (< 1 segundo):

La tarjeta de confirmación se publica de inmediato con el número de job y un enlace directo a AWX.

```
Health Check iniciado — ol9server1
Job #106 lanzado — verificando CPU · RAM · Disco en servidor
[ Ver job en AAP ]
```

**Fase 2 — Resultado** (1–2 minutos después):

La tarjeta de resultado incluye los datos procesados del servidor.

**Health Check:**
```
Health Check — ol9server1                    Job #106
─────────────────────────────────────────────────────
CPU       4%    [==                        ]   Normal
RAM      77%    [===============           ]   Normal
─────────────────────────────────────────────────────
Disco
  /          37%  2.6 GB / 6.9 GB           Normal
  /tmp       86%  3.4 GB / 3.9 GB           Critico
  /var        9%  0.4 GB / 3.9 GB           Normal
─────────────────────────────────────────────────────
Top procesos CPU          Top procesos RAM
  0.8%  sshd                1.2%  python3
  0.5%  auditd              0.9%  uwsgi
```

**Remediador (con antes/después):**
```
Remediador — ol9server1                      Job #114
─────────────────────────────────────────────────────
Estado: REMEDIADO
─────────────────────────────────────────────────────
Metricas          Antes    Despues
  CPU              100%       0%
  Disco             86%       2%
─────────────────────────────────────────────────────
Espacio liberado: 3400 MB
─────────────────────────────────────────────────────
Acciones ejecutadas
  CPU: eliminado PID 47368 (ansible 98.7% bash)
  CPU: eliminado PID 47367 (ansible 98.5% bash)
  DISK: eliminados 2 archivo(s) grande(s) en /tmp (3400 MB)
```

**Log Monitor:**
```
Log Report — ol9server1                      Job #75
─────────────────────────────────────────────────────
Estado: OK    Errores: 8    Archivos: 3    Ventana: 2h
─────────────────────────────────────────────────────
Muestra de logs (8 errores)
  Apr 2 12:41:09 ol9server1 kernel: RAS: Correctable Errors...
  Apr 2 13:02:17 ol9server1 systemd: Failed to start ...
  ...
```

---

### Estados posibles en la tarjeta de resultado

| Estado | Significado |
|--------|-------------|
| `REMEDIADO` | Se ejecutaron acciones y el recurso mejoró |
| `SIN ACCION` | Los recursos estaban dentro de los umbrales — no se necesitó intervenir |
| `DIAGNOSTICADO` | Modo solo-lectura — métricas reportadas sin cambios |
| `ERROR` | El playbook encontró un error inesperado en el servidor |
| `NO ALCANZABLE` | El servidor no respondió al momento de la ejecución |

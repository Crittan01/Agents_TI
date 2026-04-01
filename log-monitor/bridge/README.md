# Log Monitor — Bridge

Puente entre operadores (Teams o Streamlit) y el playbook `log-monitor.yml` en AAP.

## Arquitectura

```
Operador → Teams Outgoing Webhook  →  main.py (FastAPI)  →  AAP Job  →  Artifacts
Operador → Streamlit UI            →  app.py             →  AAP Job  →  Artifacts
```

Ambos canales son **despliegues independientes** — se elige uno u otro, no ambos.
Ambos importan `aap.py` y `nlu.py` pero no dependen entre si.

## Modulos

| Archivo        | Rol                                                                   |
|----------------|-----------------------------------------------------------------------|
| `aap.py`       | Cliente AAP: GET, launch_log_job, extract_log_data                   |
| `nlu.py`       | Interpreta lenguaje natural → `log_check` / `log_fleet` via Claude   |
| `cards.py`     | Constructores de Adaptive Cards para Teams (solo Teams)              |
| `main.py`      | FastAPI endpoint `/teams/webhook` con validacion HMAC                |
| `app.py`       | Streamlit UI con sidebar de config + historial de sesion             |

## Extra-vars que envia el bridge al playbook

| Variable              | Descripcion                          | Default |
|-----------------------|--------------------------------------|---------|
| `app_server_group`    | Host o grupo de inventario a revisar | —       |
| `log_time_window_hours` | Ventana de tiempo hacia atras (h)  | 2       |
| `log_severity`        | ERROR / WARN / ALL                   | ERROR   |
| `log_keyword`         | Termino libre de busqueda adicional  | ""      |
| `log_max_sample_lines`| Max lineas de muestra en el artifact | 30      |

## Artefacto que devuelve el playbook

```json
{
  "HOSTNAME": {
    "hostname":          "HOSTNAME",
    "servicio":          "WEBLOGIC_PDN",
    "unreachable":       false,
    "block_error":       false,
    "files_scanned":     5,
    "errors":            3,
    "warns":             1,
    "sample":            ["ERROR 2024-01-01 ..."],
    "time_window_hours": 2,
    "severity":          "ERROR",
    "keyword":           "",
    "status":            "ok"
  }
}
```

## Instalacion

```bash
cd agents/log-monitor/bridge
cp .env.example .env
# Editar .env con los valores reales (AWX_TOKEN, JOB_TEMPLATE_ID, etc.)
pip install -r requirements.txt
```

## Ejecucion

**Teams (FastAPI):**
```bash
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

**Streamlit:**
```bash
streamlit run app.py --server.port 8502
```

> Nota: usar puertos distintos a health-check (8000/8501) para no colisionar.

## Configuracion de Teams

1. Crear un **Outgoing Webhook** en el canal de Teams
2. Copiar la URL generada por el servidor (ej: `https://tu-ip:8001/teams/webhook`)
3. Copiar el **Security Token** al campo `TEAMS_HMAC_TOKEN` del `.env`
4. Crear un **Incoming Webhook** en el mismo canal y copiar la URL a `TEAMS_WEBHOOK_URL`

## Intents reconocidos

| Intent      | Ejemplo de mensaje                                   |
|-------------|------------------------------------------------------|
| `log_check` | `logs de SGWLSAPPP01`                               |
| `log_check` | `errores en P8_DESA ultima hora`                    |
| `log_check` | `busca OutOfMemory en WEBLOGIC_PDN`                 |
| `log_fleet` | `logs de produccion`                                |
| `log_fleet` | `hay errores en desarrollo`                         |
| `clarify`   | (cuando el host es ambiguo)                         |
| `unknown`   | (accion no relacionada con logs)                    |

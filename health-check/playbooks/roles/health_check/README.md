# Rol: health_check

Rol de Ansible para recolectar metricas de salud en servidores Linux (CPU, RAM y discos) y publicarlas como artifacts en AAP mediante `set_stats`.

Soporta ejecucion sobre un **host individual** o un **grupo completo**. Cuando se ejecuta sobre un grupo, consolida los datos de todos los hosts en un unico artifact usando `hostvars` + `run_once`.

Forma parte del flujo de automatizacion **VOLT**:

```
Teams / Streamlit
    └── NLU (Claude) -- resuelve lenguaje libre a host o grupo
        └── Webhook Bridge (FastAPI)
            └── AAP Job Template "Agent - Health Check Linux" (ID 178)
                └── Rol health_check (corre en paralelo sobre N hosts)
                    └── set_stats --> artifacts del job en AAP
                        └── Bridge lee artifacts --> card / metricas
```

---

## Requisitos

- Ansible 2.12 o superior
- `gather_facts: true` en el play (el rol usa facts nativos)
- Usuario de conexion con privilegios `sudo` (el play usa `become: true`)
- Sistemas de archivos soportados para discos: `ext2`, `ext3`, `ext4`, `xfs`, `btrfs`, `vfat`, `nfs`, `nfs4`, `cifs`, `zfs`

No requiere paquetes adicionales en el host target.

---

## Variables

| Variable | Definida en | Descripcion |
|---|---|---|
| `target` | `extra_vars` (AAP) | Hostname o nombre de grupo del inventario a evaluar |

La variable `target` es inyectada por el Webhook Bridge al momento de lanzar el Job Template:

```json
{ "extra_vars": { "target": "WEBLOGIC_PDN" } }
```

El playbook la consume como:

```yaml
hosts: "{{ target | default([]) }}"
```

Si `target` no coincide con ningun host o grupo del inventario, el job se ejecuta sin errores pero sin acciones. El Webhook Bridge valida la existencia antes de lanzar el job.

---

## Estructura del rol

```
roles/health_check/
├── tasks/
│   ├── main.yml       # Orquestador: cpu, memory, disk, set_fact y set_stats
│   ├── cpu.yml        # Doble lectura de /proc/stat (delta 1s) → cpu_usage
│   ├── memory.yml     # Facts nativos ansible_memtotal_mb / memfree_mb → ram_*
│   └── disk.yml       # ansible_mounts filtrado por fstype → disks_health
├── defaults/
│   └── main.yml       # (vacio)
└── vars/
    └── main.yml       # (vacio)
```

---

## Flujo de tareas (`tasks/main.yml`)

```
cpu.yml     → cpu_usage (float, porcentaje)
memory.yml  → ram_used_percent, ram_free, ram_total
disk.yml    → disks_health (lista de particiones)
                   |
                   v (cada host)
set_fact _health_data = { cpu, ram, disks, generated_at, status }
                   |
                   v (run_once, delegate_to: localhost)
set_stats data = { Host1: _health_data, Host2: _health_data, ... }
```

El `set_stats` final se ejecuta **una sola vez** desde `localhost` y construye el dict completo leyendo `hostvars` de todos los hosts del play. Esto garantiza que el artifact contenga los datos de **todos** los hosts sin importar cuantos sean.

---

## Artifact generado

El artifact del job en AAP tiene un hostname por clave raiz:

```json
{
  "SGWLSAPPP01": {
    "cpu": 4.4,
    "ram": {
      "used_percent": 77.0,
      "free_mb": 16099,
      "total_mb": 69905
    },
    "disks": [
      {
        "mount": "/",
        "usage_pct": 4.0,
        "used_gb": 0.16,
        "total_gb": 3.99,
        "fstype": "xfs"
      }
    ],
    "generated_at": "2026-03-23T15:43:24Z",
    "status": "ok"
  },
  "SGWLSAPPP02": {
    "cpu": 12.1,
    ...
  }
}
```

Accesible via API de AAP:

```
GET /api/v2/jobs/{id}/
→ response.artifacts = { "HOSTNAME": { cpu, ram, disks, ... }, ... }
```

---

## Uso en playbook

```yaml
- name: Health Check para servidores Linux
  hosts: "{{ target | default([]) }}"
  gather_facts: true
  become: true
  roles:
    - health_check
```

Lanzamiento manual desde la API de AAP:

```bash
# Host individual
curl -sk -X POST https://<AAP_URL>/api/v2/job_templates/178/launch/ \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"extra_vars": {"target": "SGWLSAPPP01"}}'

# Grupo completo
curl -sk -X POST https://<AAP_URL>/api/v2/job_templates/178/launch/ \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"extra_vars": {"target": "WEBLOGIC_PDN"}}'
```

---

## Medicion de CPU

La CPU se mide con dos lecturas de `/proc/stat` separadas por 1 segundo (delta real), no con un snapshot puntual. Esto da un valor representativo del uso en el momento de la ejecucion.

---

## Proyecto y rama

- **Repositorio:** `automatizacion-ansible_ansible-conf` (Azure DevOps — Gerencia_Tecnologia)
- **Rama:** `Ansible_Sura`
- **Project AAP:** ID 61 — `Sura_NTT_Data`
- **Job Template AAP:** ID 178 — `Agent - Health Check Linux`
- **Inventario AAP:** ID 35 — `Inventario Linea Base - Middleware`
- **Credencial AAP:** ID 51 (tipo Machine, SSH)
- **Organizacion AAP:** ID 5 — `Sura_NTT_Data`

---

## Autores

- Cristian Camilo Garzon — crisgrta@suramericana.com.co

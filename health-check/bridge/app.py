import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta

import streamlit as st
from dotenv import load_dotenv

_SHARED_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../shared/bridge")
sys.path.insert(0, _SHARED_BRIDGE)

from nlu import parse_intent
from aap import aap_get, host_exists_in_inventory, group_exists_in_inventory, launch_health_job, extract_health_data

load_dotenv(os.path.join(_SHARED_BRIDGE, ".env"))

AWX_URL         = os.getenv("AWX_URL", "https://10.216.24.208")
JOB_TEMPLATE_ID = int(os.getenv("HEALTH_JOB_TEMPLATE_ID", "178"))
INVENTORY_ID    = int(os.getenv("INVENTORY_ID", "35"))

COT = timezone(timedelta(hours=-5))


# ─── Helpers UI ─────────────────────────────────────────────────────────────

def _mb_to_gb(mb) -> str:
    try:
        return f"{round(int(mb) / 1024, 1)} GB"
    except Exception:
        return f"{mb} MB"


def semaforo(pct: float) -> str:
    if pct >= 85:
        return "🔴"
    if pct >= 70:
        return "🟡"
    return "🟢"


def formato_ts(gen_at: str) -> str:
    try:
        return (
            datetime.fromisoformat(gen_at[:19])
            .replace(tzinfo=timezone.utc)
            .astimezone(COT)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return gen_at


def wait_for_job(job_id: int, placeholder) -> bool:
    for _ in range(60):
        time.sleep(5)
        job    = aap_get(f"/api/v2/jobs/{job_id}/")
        status = job.get("status", "")
        placeholder.info(f"Job **#{job_id}** — estado: `{status}`")
        if status == "successful":
            return True
        if status in ("failed", "error", "canceled"):
            return False
    return False


# ─── Layout ─────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AnsibleBot — Health Check",
    page_icon="🖥️",
    layout="wide",
)

if "history" not in st.session_state:
    st.session_state.history = []

# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Configuracion")
    token_ok = bool(os.getenv("AWX_TOKEN"))
    st.markdown(f"**AAP:** `{AWX_URL}`")
    st.markdown(f"**Inventario:** `{INVENTORY_ID}`")
    st.markdown(f"**Job Template:** `{JOB_TEMPLATE_ID}`")
    st.markdown(f"**Token:** {'configurado' if token_ok else 'NO configurado'}")

    st.divider()
    st.header("Historial de sesion")
    if st.session_state.history:
        n = len(st.session_state.history)
        st.caption(f"{n} consulta{'s' if n != 1 else ''} en esta sesion")
        st.dataframe(
            st.session_state.history,
            use_container_width=True,
            hide_index=True,
        )
        if st.button("Limpiar historial", use_container_width=True):
            st.session_state.history = []
            st.rerun()
    else:
        st.caption("Sin consultas aun.")

if not os.getenv("AWX_TOKEN"):
    st.error("AWX_TOKEN no configurado. Revisa el archivo .env")
    st.stop()

# ─── Cabecera ─────────────────────────────────────────────────────────────────

st.title("Health Check")
st.caption("Verifica CPU, RAM y disco de servidores Sura (WebLogic, Joomla, P8)")

# ─── Input ──────────────────────────────────────────────────────────────────

st.divider()
col_input, col_btn = st.columns([4, 1])

with col_input:
    server_input = st.text_input(
        "Servidor",
        placeholder="Ej: SGWLSAPPP01  ·  'agente SR prod 3'  ·  P8_DESA  ·  'todos los WebLogic de prod'",
        label_visibility="collapsed",
    ).strip()

with col_btn:
    run = st.button("Validar", type="primary", use_container_width=True)

# ─── Ejecucion ──────────────────────────────────────────────────────────────

if run:
    if not server_input:
        st.warning("Ingresa el nombre o descripcion del servidor.")
        st.stop()

    with st.spinner("Interpretando solicitud..."):
        parsed = parse_intent(server_input)

    intent = parsed.get("intent")

    if intent == "clarify":
        st.warning(f"Necesito mas informacion: {parsed.get('question', 'Puedes ser mas especifico?')}")
        st.stop()

    # ── fleet_check ───────────────────────────────────────────────────────────
    if intent == "fleet_check":
        targets     = parsed.get("targets", [])
        environment = parsed.get("environment", "produccion")
        filter_mode = parsed.get("filter", "all")

        st.subheader(f"Fleet Health — {environment.capitalize()}")
        label = "servidores en umbral alto" if filter_mode == "critical" else "estado general"
        st.caption(f"{len(targets)} grupos · buscando: {label}")

        group_jobs: dict[str, int] = {}
        for group in targets:
            if group_exists_in_inventory(group):
                resp   = launch_health_job(group)
                job_id = resp.get("id")
                if job_id:
                    group_jobs[group] = job_id
                    st.info(f"Job **#{job_id}** lanzado para `{group}`")

        if not group_jobs:
            st.error("No se pudo lanzar ningun job.")
            st.stop()

        fleet: dict[str, dict] = {}
        lock  = threading.Lock()

        def _wait_one(group: str, job_id: int) -> None:
            for _ in range(60):
                time.sleep(5)
                job    = aap_get(f"/api/v2/jobs/{job_id}/")
                status = job.get("status", "")
                if status == "successful":
                    hd = extract_health_data(job_id, group)
                    if hd:
                        with lock:
                            fleet[group] = hd
                    return
                if status in ("failed", "error", "canceled"):
                    return

        threads = [threading.Thread(target=_wait_one, args=(g, jid), daemon=True)
                   for g, jid in group_jobs.items()]
        for t in threads:
            t.start()

        progress_slot = st.empty()
        while any(t.is_alive() for t in threads):
            time.sleep(3)
            with lock:
                done = len(fleet)
            progress_slot.info(f"Esperando resultados... {done}/{len(group_jobs)} grupos completados")

        for t in threads:
            t.join(timeout=5)
        progress_slot.empty()

        if not fleet:
            st.error("Jobs completados pero sin datos de salud.")
            st.stop()

        total = sum(len(v) for v in fleet.values())
        st.success(f"Completado — {total} servidores evaluados en {len(fleet)} grupos")
        st.divider()

        all_rows = []
        for group, hosts in sorted(fleet.items()):
            for hostname, hdata in sorted(hosts.items()):
                cpu_f      = float(hdata.get("cpu", 0))
                ram_f      = float(hdata.get("ram", {}).get("used_percent", 0))
                disks_f    = hdata.get("disks", [])
                worst_d    = max(disks_f, key=lambda d: float(d.get("usage_pct", 0)), default={})
                disco_f    = float(worst_d.get("usage_pct", 0)) if worst_d else 0
                disco_part = worst_d.get("mount", "") if worst_d else ""
                worst      = max(cpu_f, ram_f, disco_f)
                all_rows.append({
                    "Grupo":     group,
                    "Servidor":  hostname,
                    "CPU %":     cpu_f,
                    "RAM %":     ram_f,
                    "Disco %":   disco_f,
                    "Particion": disco_part,
                    "Estado":    semaforo(worst),
                })

        _PROG_COLS = {
            "CPU %":   st.column_config.ProgressColumn("CPU %",   min_value=0, max_value=100, format="%.1f%%"),
            "RAM %":   st.column_config.ProgressColumn("RAM %",   min_value=0, max_value=100, format="%.1f%%"),
            "Disco %": st.column_config.ProgressColumn("Disco %", min_value=0, max_value=100, format="%.1f%%"),
        }

        if filter_mode == "critical":
            filtered = [r for r in all_rows if max(r["CPU %"], r["RAM %"], r["Disco %"]) >= 70]
            if not filtered:
                st.success("Todo en verde — ningun servidor supera el 70%")
            else:
                filtered.sort(key=lambda r: -max(r["CPU %"], r["RAM %"], r["Disco %"]))
                st.dataframe(filtered, use_container_width=True, hide_index=True, column_config=_PROG_COLS)
        else:
            for group, hosts in sorted(fleet.items()):
                group_rows = [r for r in all_rows if r["Grupo"] == group]
                worst_g    = max((max(r["CPU %"], r["RAM %"], r["Disco %"]) for r in group_rows), default=0)
                n          = len(group_rows)
                with st.expander(f"{semaforo(worst_g)} **{group}** — {n} servidor{'es' if n != 1 else ''}"):
                    group_rows.sort(key=lambda r: -max(r["CPU %"], r["RAM %"], r["Disco %"]))
                    st.dataframe(group_rows, use_container_width=True, hide_index=True, column_config=_PROG_COLS)

        st.session_state.history.append({
            "Target":   f"Fleet: {environment}",
            "Servidor": f"{total} servidores",
            "Job":      ", ".join(str(j) for j in group_jobs.values()),
            "Hora COT": formato_ts(datetime.now(tz=COT).isoformat()),
            "CPU %":    "",
            "RAM %":    "",
            "Discos":   "",
        })
        st.stop()

    # ── Intento no reconocido ─────────────────────────────────────────────────
    if intent != "health_check":
        st.error(
            "No entendi la solicitud. Intenta:\n"
            "- Servidor exacto: `SGWLSAPPP01`\n"
            "- Descripcion libre: `agente suramericana produccion 3`\n"
            "- Grupo: `P8_DESA` o `todos los WebLogic de produccion`"
        )
        st.stop()

    # ── health_check ──────────────────────────────────────────────────────────
    target      = parsed.get("target", "").upper()
    target_type = parsed.get("type", "host")

    if target.upper() != server_input.upper():
        label = "Grupo" if target_type == "group" else "Servidor"
        st.info(f"{label} identificado: **{target}**")

    with st.spinner(f"Verificando **{target}** en inventario AAP..."):
        existe = group_exists_in_inventory(target) if target_type == "group" else host_exists_in_inventory(target)
        if not existe:
            label = "grupo" if target_type == "group" else "servidor"
            st.error(f"El {label} **{target}** no existe en el inventario de AAP.")
            st.stop()

    st.success(f"**{target}** encontrado en inventario.")

    with st.spinner("Lanzando job en AAP..."):
        response = launch_health_job(target)
        job_id   = response.get("id")

    if not job_id:
        st.error("No se pudo lanzar el job en AAP.")
        st.stop()

    job_url = f"{AWX_URL}/#/jobs/playbook/{job_id}/details"
    label   = "grupo" if target_type == "group" else "servidor"
    st.info(f"Job **#{job_id}** lanzado para {label} **{target}**. [Ver en AAP]({job_url})")

    status_slot = st.empty()
    success     = wait_for_job(job_id, status_slot)
    status_slot.empty()

    if not success:
        st.error(f"El job terminó con error o fue cancelado. [Ver en AAP]({job_url})")
        st.stop()

    health_dict = extract_health_data(job_id, target)
    if not health_dict:
        st.error("Job exitoso pero no se pudieron leer los datos de salud.")
        st.stop()

    # ─── Resultados ──────────────────────────────────────────────────────────

    st.divider()
    es_grupo = target_type == "group" or len(health_dict) > 1

    if es_grupo:
        n        = len(health_dict)
        first_ts = formato_ts(next(iter(health_dict.values()), {}).get("generated_at", ""))
        st.subheader(f"Health Report — {target}")
        st.caption(f"Job #{job_id}  ·  {first_ts} COT  ·  {n} servidor{'es' if n != 1 else ''}")

        def _severidad(row: dict) -> int:
            worst = max(row["CPU %"], row["RAM %"], row["Disco %"])
            if worst >= 85: return 0
            if worst >= 70: return 1
            return 2

        resumen = []
        for hostname, hdata in health_dict.items():
            if hdata.get("unreachable"):
                resumen.append({
                    "Servidor":  hostname,
                    "CPU %":     0.0,
                    "RAM %":     0.0,
                    "Disco %":   0.0,
                    "Particion": "",
                    "Estado":    "🔴 UNREACHABLE",
                })
                continue
            cpu_h      = float(hdata.get("cpu", 0))
            ram_h      = float(hdata.get("ram", {}).get("used_percent", 0))
            disks_h    = hdata.get("disks", [])
            worst_d    = max(disks_h, key=lambda d: float(d.get("usage_pct", 0)), default={})
            disco_max  = float(worst_d.get("usage_pct", 0)) if worst_d else 0
            disco_part = worst_d.get("mount", "") if worst_d else ""
            resumen.append({
                "Servidor":  hostname,
                "CPU %":     cpu_h,
                "RAM %":     ram_h,
                "Disco %":   disco_max,
                "Particion": disco_part,
                "Estado":    semaforo(max(cpu_h, ram_h, disco_max)),
            })

        resumen.sort(key=_severidad)
        st.dataframe(
            resumen,
            use_container_width=True,
            hide_index=True,
            column_config={
                "CPU %":   st.column_config.ProgressColumn("CPU %",   min_value=0, max_value=100, format="%.1f%%"),
                "RAM %":   st.column_config.ProgressColumn("RAM %",   min_value=0, max_value=100, format="%.1f%%"),
                "Disco %": st.column_config.ProgressColumn("Disco %", min_value=0, max_value=100, format="%.1f%%"),
            },
        )

        st.divider()
        for hostname, hdata in sorted(health_dict.items()):
            if hdata.get("unreachable"):
                with st.expander(f"🔴 **{hostname}** — UNREACHABLE"):
                    st.error("Host no alcanzable durante la ejecucion.")
                st.session_state.history.append({
                    "Target":   target, "Servidor": hostname, "Job": job_id,
                    "Hora COT": formato_ts(""), "CPU %": "", "RAM %": "", "Discos": "",
                })
                continue

            cpu_h      = float(hdata.get("cpu", 0))
            ram_h      = hdata.get("ram", {})
            ram_pct_h  = float(ram_h.get("used_percent", 0))
            ram_free_h = ram_h.get("free_mb", "?")
            ram_tot_h  = ram_h.get("total_mb", "?")
            disks_h    = hdata.get("disks", [])
            disco_max_h = max((float(d.get("usage_pct", 0)) for d in disks_h), default=0)

            with st.expander(
                f"{semaforo(cpu_h)} {semaforo(ram_pct_h)} {semaforo(disco_max_h)}  "
                f"**{hostname}**  — CPU {cpu_h}%  RAM {ram_pct_h}%  Disco {disco_max_h}%"
            ):
                col_cpu, col_ram = st.columns(2)
                with col_cpu:
                    st.metric(f"{semaforo(cpu_h)} CPU", f"{cpu_h}%")
                    st.progress(cpu_h / 100)
                with col_ram:
                    st.metric(f"{semaforo(ram_pct_h)} RAM", f"{ram_pct_h}%")
                    st.progress(ram_pct_h / 100)
                    st.caption(f"{_mb_to_gb(ram_free_h)} libres · {_mb_to_gb(ram_tot_h)} total")

                for d in disks_h:
                    pct_d = float(d.get("usage_pct", 0))
                    col_m, col_p = st.columns([2, 5])
                    with col_m:
                        st.write(f"{semaforo(pct_d)} **{d.get('mount','?')}** `{d.get('fstype','')}`")
                    with col_p:
                        st.progress(pct_d / 100, text=f"{pct_d}%  —  {d.get('used_gb','?')} / {d.get('total_gb','?')} GB")

            st.session_state.history.append({
                "Target":    target,
                "Servidor":  hostname,
                "Job":       job_id,
                "Hora COT":  formato_ts(hdata.get("generated_at", "")),
                "CPU %":     cpu_h,
                "RAM %":     ram_pct_h,
                "Discos":    len(disks_h),
            })

    else:
        host_data = health_dict.get(target, next(iter(health_dict.values()), {}))

        if host_data.get("unreachable"):
            st.error(f"Host **{target}** no alcanzable durante la ejecucion.")
            st.session_state.history.append({
                "Target": target, "Servidor": target, "Job": job_id,
                "Hora COT": "", "CPU %": "", "RAM %": "", "Discos": "",
            })
            st.stop()

        cpu      = float(host_data.get("cpu", 0))
        ram      = host_data.get("ram", {})
        ram_pct  = float(ram.get("used_percent", 0))
        ram_free = ram.get("free_mb", "?")
        ram_tot  = ram.get("total_mb", "?")
        disks    = host_data.get("disks", [])
        ts       = formato_ts(host_data.get("generated_at", ""))

        st.subheader(f"Health Report — {target}")
        st.caption(f"Job #{job_id}  ·  {ts} COT")

        col_cpu, col_ram = st.columns(2)
        with col_cpu:
            st.metric(f"{semaforo(cpu)} CPU", f"{cpu}%")
            st.progress(cpu / 100)
        with col_ram:
            st.metric(f"{semaforo(ram_pct)} RAM", f"{ram_pct}%")
            st.progress(ram_pct / 100)
            st.caption(f"{_mb_to_gb(ram_free)} libres · {_mb_to_gb(ram_tot)} total")

        st.divider()
        st.subheader("Discos")
        for d in disks:
            pct      = float(d.get("usage_pct", 0))
            col_m, col_p = st.columns([2, 5])
            with col_m:
                st.write(f"{semaforo(pct)} **{d.get('mount','?')}** `{d.get('fstype','')}`")
            with col_p:
                st.progress(pct / 100, text=f"{pct}%  —  {d.get('used_gb','?')} / {d.get('total_gb','?')} GB")

        st.session_state.history.append({
            "Target":   target,
            "Servidor": target,
            "Job":      job_id,
            "Hora COT": ts,
            "CPU %":    cpu,
            "RAM %":    ram_pct,
            "Discos":   len(disks),
        })


#!/bin/bash
# =============================================================================
# stress.sh — Genera carga para certificar el remediador
#
# Uso:
#   ssh ansible@HOST 'bash -s' < stress.sh [tipo] [duracion_segs]
#
# Tipos:
#   cpu   → procesos sha256sum (killables, no excluidos del fixer)
#   ram   → procesos perl con arrays en heap (killables)
#   disk  → archivos /tmp grandes + viejos + logs rotados + core dumps
#   all   → los tres anteriores  (default)
#   clean → elimina todos los archivos de stress
#
# Ejemplos:
#   ssh ansible@ol9server1 'bash -s' < stress.sh all 180
#   ssh ansible@ol9server1 'bash -s' < stress.sh disk
#   ssh ansible@ol9server1 'bash -s' < stress.sh clean
# =============================================================================

TYPE=${1:-all}
DURATION=${2:-180}

CPU_PROCS=4       # procesos sha256sum
RAM_PROCS=3       # procesos perl
RAM_MB=60         # MB por proceso perl  (3 x 60 = 180MB total)

# ── Helpers ───────────────────────────────────────────────────────────────────
cpu_pct() {
    read _ u1 n1 s1 i1 _ < <(grep '^cpu ' /proc/stat); sleep 1
    read _ u2 n2 s2 i2 _ < <(grep '^cpu ' /proc/stat)
    local total=$(( (u2+n2+s2+i2) - (u1+n1+s1+i1) ))
    local idle=$(( i2 - i1 ))
    echo $(( (total - idle) * 100 / (total > 0 ? total : 1) ))
}

ram_pct() {
    free -m | awk '/^Mem:/{printf "%.0f", ($3/$2)*100}'
}

status() {
    echo "---"
    echo "CPU:        $(cpu_pct)%"
    echo "RAM:        $(ram_pct)%"
    df -h /tmp  | awk 'NR==2{print "Disco /tmp: "$5" ("$3" de "$2")"}'
}

# ── CPU ───────────────────────────────────────────────────────────────────────
do_cpu() {
    echo "[CPU] Lanzando $CPU_PROCS procesos sha256sum por ${DURATION}s..."
    for i in $(seq 1 $CPU_PROCS); do
        timeout $DURATION bash -c 'dd if=/dev/urandom bs=64k 2>/dev/null | sha256sum > /dev/null' &
    done
    sleep 2
    echo "[CPU] Activos: $(pgrep -c sha256sum 2>/dev/null) procesos — CPU: $(cpu_pct)%"
}

# ── RAM ───────────────────────────────────────────────────────────────────────
do_ram() {
    echo "[RAM] Lanzando $RAM_PROCS procesos perl (${RAM_MB}MB c/u = $(( RAM_PROCS * RAM_MB ))MB total)..."
    for i in $(seq 1 $RAM_PROCS); do
        perl -e "my \@m; push \@m, 'A' x (1024*1024) for 1..$RAM_MB; sleep $DURATION;" &
    done
    sleep 2
    echo "[RAM] Activos: $(pgrep -c perl 2>/dev/null) procesos — RAM: $(ram_pct)%"
}

# ── DISK ──────────────────────────────────────────────────────────────────────
do_disk() {
    echo "[DISK] Creando archivos de stress..."

    # Path 1b — archivo grande en /tmp (>500MB)
    echo "  > /tmp/stress_large.bin  (1500MB)"
    dd if=/dev/zero of=/tmp/stress_large.bin bs=1M count=1500 2>/dev/null

    # Path 1 — archivos viejos backdateados (>7 dias)
    echo "  > /tmp/stress_old_*.tmp  (200MB, backdateados 30 dias)"
    dd if=/dev/zero of=/tmp/stress_old_a.tmp bs=1M count=100 2>/dev/null
    dd if=/dev/zero of=/tmp/stress_old_b.tmp bs=1M count=100 2>/dev/null
    touch -d "30 days ago" /tmp/stress_old_a.tmp /tmp/stress_old_b.tmp

    # Path 2 — logs rotados en /var/log (*.1, *.2, *.gz, *-YYYYMMDD)
    echo "  > /var/log/stress_app.log.1-4 + .gz  (320MB)"
    for i in 1 2 3 4; do
        dd if=/dev/zero of=/var/log/stress_app.log.$i bs=1M count=80 2>/dev/null
    done
    gzip -f /var/log/stress_app.log.3 2>/dev/null || true
    gzip -f /var/log/stress_app.log.4 2>/dev/null || true
    dd if=/dev/zero of="/var/log/stress_app.log-$(date +%Y%m%d)" bs=1M count=50 2>/dev/null

    # Path 5 — core dumps
    echo "  > core dumps (100MB)"
    dd if=/dev/zero of=/tmp/core.stress_app     bs=1M count=60 2>/dev/null
    dd if=/dev/zero of=/var/crash/core.stress_svc bs=1M count=40 2>/dev/null 2>/dev/null || true

    df -h /tmp | awk 'NR==2{print "[DISK] /tmp: "$5" ("$3" de "$2")"}'
}

# ── CLEAN ─────────────────────────────────────────────────────────────────────
do_clean() {
    echo "[CLEAN] Eliminando archivos de stress..."
    rm -f /tmp/stress_*.bin /tmp/stress_*.tmp /tmp/core.stress_* \
          /var/log/stress_app.* /var/crash/core.stress_svc 2>/dev/null
    pkill -f 'sha256sum' 2>/dev/null
    pkill -f 'perl.*sleep' 2>/dev/null
    echo "[CLEAN] Hecho."
    status
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo "=== stress.sh  tipo=$TYPE  duracion=${DURATION}s ==="
echo "Estado ANTES:"
status

case $TYPE in
    cpu)   do_cpu  ;;
    ram)   do_ram  ;;
    disk)  do_disk ;;
    all)   do_cpu; do_ram; do_disk ;;
    clean) do_clean; exit 0 ;;
    *)     echo "Tipo invalido: $TYPE  (cpu|ram|disk|all|clean)"; exit 1 ;;
esac

echo ""
echo "Estado DESPUES:"
status
echo ""
echo "Cuando termines: ssh ansible@$(hostname) 'bash -s' < stress.sh clean"

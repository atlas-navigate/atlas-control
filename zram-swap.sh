#!/usr/bin/env bash
# Atlas zram swap manager — installed by install.sh to /usr/local/sbin/atlas-zram
# and driven by zram-swap.service. Sizes zram0 to 50% of RAM with zstd and turns
# it on as high-priority swap (preferred over the default-priority disk swapfiles
# that setup_native_osrm.sh / build_places_safe.sh add for real extra capacity).
#
# `start` is idempotent: it tears down and re-initialises zram0, so re-running it
# (service restart, install re-run) never fails with "device busy".
set -euo pipefail

ZRAM_FRACTION_NUM=1   # 1/2 = 50% of RAM
ZRAM_FRACTION_DEN=2
ALGO=zstd

reset_zram0() {
    swapoff /dev/zram0 2>/dev/null || true
    [[ -e /sys/block/zram0/reset ]] && echo 1 > /sys/block/zram0/reset 2>/dev/null || true
}

case "${1:-start}" in
    start)
        # Already swapping on zram0 (e.g. an install --update re-run on a live
        # box)? Leave it be. Tearing it down would swapoff active pages back into
        # RAM and could OOM a loaded box; a resize needs an explicit stop/start.
        if swapon --show=NAME --noheadings 2>/dev/null | grep -qx /dev/zram0; then
            sysctl -q -w vm.swappiness=100 vm.vfs_cache_pressure=200 || true
            exit 0
        fi
        # modprobe fails (non-zero) on a kernel with no zram module; set -e then
        # aborts and the service is reported failed — install.sh treats that as
        # non-fatal and continues without zram.
        modprobe zram
        reset_zram0
        mem_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo)
        size=$(( mem_kb * 1024 * ZRAM_FRACTION_NUM / ZRAM_FRACTION_DEN ))
        echo "$ALGO" > /sys/block/zram0/comp_algorithm 2>/dev/null || true
        echo "$size" > /sys/block/zram0/disksize
        mkswap /dev/zram0 >/dev/null
        swapon --priority 100 /dev/zram0
        # Swap aggressively to (fast) zram, keep the filesystem cache lean.
        # Best-effort: /etc/sysctl.d/99-zram.conf also persists these at boot.
        sysctl -q -w vm.swappiness=100 vm.vfs_cache_pressure=200 || true
        ;;
    stop)
        reset_zram0
        ;;
    *)
        echo "usage: ${0##*/} {start|stop}" >&2
        exit 2
        ;;
esac

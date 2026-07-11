#!/usr/bin/env bash
# Memory-safe launcher for build_places_index.py on the 8 GB Jetson.
#
# The places-index build (osmium extract + a ~30M-row FTS5 index) needs real
# memory headroom, but Ray/Ollama hold ~3.8 GB of unified memory that is SHARED
# with the GPU. Run together they exhaust RAM and HARD-FREEZE the Tegra — the
# kernel never gets to run a clean OOM-kill, the box just hangs. This wrapper
# makes the one-time build safe:
#   1. adds a temporary disk swapfile for genuine overflow headroom
#      (zram is only compressed RAM, not extra capacity)
#   2. stops ONLY ollama (Ray's LLM) to free its ~3.8 GB — GPS, mesh and
#      routing in atlas-control keep running; just the AI is offline
#   3. runs the build in a memory-capped cgroup, so a runaway is CONTAINED
#      (cgroup-OOM-kills only the build) instead of taking down the whole box
#   4. always restarts ollama + removes the swapfile on exit — even on Ctrl-C/error
#
# Run it with sudo (swapon + systemctl need root):
#     sudo bash build_places_safe.sh                      # all states
#     sudo bash build_places_safe.sh virginia maryland    # named states only
#     sudo bash build_places_safe.sh --resume             # continue an interrupted build
#
# Watch progress from another shell:  tail -f logs/places_build.log
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Fall back to the app-dir owner (matches install.sh) so a root shell without
# SUDO_USER doesn't run the build as a user that only exists on the dev box.
ATLAS_USER="${SUDO_USER:-$(stat -c %U "$APP_DIR" 2>/dev/null || echo ubuntu)}"
SWAPFILE="/atlas_data/places_build.swap"
SWAP_GB=16
MEM_HIGH="5G"     # soft throttle: at 5G RAM the kernel reclaims/swaps the build hard
MEM_MAX="6G"      # hard cap on RAM: past this the build (only) is OOM-killed, not the box
LOG="$APP_DIR/logs/places_build.log"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo:  sudo bash $0 $*" >&2
    exit 1
fi
mkdir -p "$APP_DIR/logs"
# Everything below goes to both the terminal and the log, in THIS shell, so the
# cleanup trap and its state flags stay valid (a pipe-to-tee would subshell them).
exec > >(tee -a "$LOG") 2>&1

cleanup() {
    # Self-healing: always leave the box in the right end state — Ray running,
    # no temp swapfile — regardless of how we got here. This matters because a
    # prior run killed before cleanup (e.g. the terminal closed) can leave
    # ollama stopped and the swapfile mounted; a plain rerun must still restore
    # them. Starting an already-running service is a harmless no-op.
    echo "── restoring Ray + releasing temp swap ─────────────────────────"
    systemctl start ollama.service && echo "ollama running"
    if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$SWAPFILE"; then
        swapoff "$SWAPFILE" 2>/dev/null && echo "swapped off temp file"
    fi
    [[ -e "$SWAPFILE" ]] && rm -f "$SWAPFILE" && echo "removed temp swapfile"
}
trap cleanup EXIT INT TERM

echo "=== places build $(date) ==="

# 1) Temporary swapfile (dd, not fallocate — swapon rejects sparse/holey files).
if ! swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$SWAPFILE"; then
    echo "creating ${SWAP_GB}G temp swapfile at $SWAPFILE …"
    dd if=/dev/zero of="$SWAPFILE" bs=1M count=$((SWAP_GB * 1024)) status=none
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" >/dev/null
    swapon "$SWAPFILE"
else
    echo "temp swapfile already active at $SWAPFILE — reusing"
fi

# 2) Free ~3.8 GB by pausing Ray (ollama only) for the duration of the build.
#    atlas-control stays up, so GPS/mesh/routing keep working; Ray queries just
#    fail until ollama is back. The new places.db is opened read-only per geocode
#    query, so atlas-control picks it up live — no restart needed.
if systemctl is-active --quiet ollama.service; then
    systemctl stop ollama.service && echo "stopped ollama (frees ~3.8 GB)"
else
    echo "ollama already stopped"
fi
sleep 2
free -h | awk 'NR==1 || /Mem|Swap/'

# 3) Build inside a memory-capped transient scope, running as the atlas user so
#    the output DB keeps the right ownership. The cgroup cap is the guarantee
#    the box can't freeze: blow the cap and only this scope is OOM-killed; then
#    just rerun with --resume (the salvage path reuses the loaded data).
echo "launching build  (MemoryHigh=$MEM_HIGH MemoryMax=$MEM_MAX, ${SWAP_GB}G temp swap) …"
systemd-run --scope --quiet \
    -p MemoryHigh="$MEM_HIGH" -p MemoryMax="$MEM_MAX" \
    runuser -u "$ATLAS_USER" -- \
    nice -n 10 ionice -c2 -n7 python3 "$APP_DIR/build_places_index.py" "$@"
rc=$?

echo "build exit code: $rc"
[[ $rc -ne 0 ]] && echo "(non-zero — rerun the same command with --resume to continue)"
exit $rc

# 30 — Memory headroom: zram + a persistent disk swapfile.
# The Orin Nano shares 8 GB of RAM between CPU and GPU. The heaviest steps —
# osrm-extract/partition/customize on the big states (CA/TX/FL) and the places
# index — plus long-range routing at runtime drive it into the OOM killer.
# zram (compressed RAM) helps but is NOT extra capacity, so both a zram device
# AND a real disk swapfile are set up here, before any heavy provisioning.
section "Swap / Memory Headroom"

# ── zram (compressed RAM, high priority) ─────────────────────────────────────
# The helper, its unit and the memory policy are the release's own system
# files (system/files.json), placed now for the dataset builds; every update
# keeps them current. The helper is idempotent. Non-fatal: a kernel without
# the zram module must not abort the install.
CHANGED="$(kit_sysfiles bin/atlas-zram systemd/zram-swap.service sysctl/99-atlas-memory.conf)" \
    || die "Could not install the release's swap files"
if [[ -n "$CHANGED" ]]; then
    systemctl daemon-reload
    sysctl -q -p "$R/etc/sysctl.d/99-atlas-memory.conf" >/dev/null 2>&1 || true
fi
if systemctl enable --now zram-swap.service >/dev/null 2>&1; then
    ZRAM_SIZE=$(swapon --show=NAME,SIZE --noheadings 2>/dev/null | awk '/zram/{print $2; exit}')
    log "zram swap enabled${ZRAM_SIZE:+ (${ZRAM_SIZE} compressed)}"
else
    warn "Could not enable zram swap (kernel lacks the zram module?) — continuing without it"
fi

# ── Persistent disk swapfile (real capacity, low priority) ───────────────────
# zram fills first (priority 100); this NVMe-backed swapfile (priority 10) is
# spillover for genuine overflow — the headroom the big-state OSRM builds and
# long-range routing need. dd, not fallocate: swapon rejects sparse/holey files.
ATLAS_DISK_SWAP_GB=32
ATLAS_SWAPFILE="$ATLAS_DATA/atlas-swap.swap"
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "${ATLAS_SWAPFILE#$R}"; then
    log "Persistent disk swap already active ($ATLAS_SWAPFILE)"
else
    NEED_KB=$(( ATLAS_DISK_SWAP_GB * 1024 * 1024 ))
    FREE_KB=$(df -P "$ATLAS_DATA" 2>/dev/null | awk 'NR==2{print $4}')
    if [[ -f "$ATLAS_SWAPFILE" || "${FREE_KB:-0}" -gt $(( NEED_KB + 5 * 1024 * 1024 )) ]]; then
        info "Adding ${ATLAS_DISK_SWAP_GB} GB persistent disk swap at $ATLAS_SWAPFILE ..."
        # Chain create → format → enable in one if-condition so a failure at any
        # step (e.g. dd hitting ENOSPC) degrades to the else branch instead of
        # tripping set -e and aborting the install.
        if { [[ -f "$ATLAS_SWAPFILE" ]] || dd if=/dev/zero of="$ATLAS_SWAPFILE" bs=1M count=$(( ATLAS_DISK_SWAP_GB * 1024 )) status=none 2>/dev/null; } \
             && chmod 600 "$ATLAS_SWAPFILE" \
             && mkswap "$ATLAS_SWAPFILE" >/dev/null 2>&1 \
             && swapon --priority 10 "$ATLAS_SWAPFILE" 2>/dev/null; then
            log "Persistent disk swap active (${ATLAS_DISK_SWAP_GB} GB, priority 10)"
        else
            warn "Could not set up disk swapfile at $ATLAS_SWAPFILE — continuing (big-state OSRM may OOM)"
            swapoff "$ATLAS_SWAPFILE" 2>/dev/null || true
            rm -f "$ATLAS_SWAPFILE"
        fi
    else
        warn "Not enough free space on /atlas_data for a ${ATLAS_DISK_SWAP_GB} GB swapfile (need ~$(( ATLAS_DISK_SWAP_GB + 5 )) GB) — skipping disk swap"
    fi
fi
# Persist across reboots at low priority (below zram). Idempotent; also
# repairs a unit whose swapfile is active but was never added.
if [[ -f "$ATLAS_SWAPFILE" ]] && ! grep -qF "${ATLAS_SWAPFILE#$R} " "$R/etc/fstab" 2>/dev/null; then
    printf '%s none swap sw,pri=10 0 0\n' "${ATLAS_SWAPFILE#$R}" >> "$R/etc/fstab"
    log "Disk swap added to /etc/fstab"
fi

# 00 — Preflight: can this machine run Atlas, and is the release trusted?
# Seconds, before anything is changed or downloaded: the release needs an
# aarch64 Jetson Orin on Ubuntu 24.04 (JetPack 7) with the system Python 3.12,
# and a signature from a key this kit trusts.
section "Preflight"

[[ "$(uname -m)" == "aarch64" ]] || die "Atlas runs on an aarch64 Jetson Orin; this is $(uname -m)."

MODEL="$(tr -d '\0' < "$R/proc/device-tree/model" 2>/dev/null || true)"
if [[ "$MODEL" == *Orin* ]]; then
    log "Hardware: $MODEL"
else
    warn "Hardware: ${MODEL:-unknown} — Atlas is built and measured for the Jetson Orin family"
fi

OS_ID="$(. "$R/etc/os-release" 2>/dev/null && echo "${ID:-}-${VERSION_ID:-}")"
[[ "$OS_ID" == "ubuntu-24.04" ]] \
    || die "Atlas needs Ubuntu 24.04 (JetPack 7); this is ${OS_ID:-an unknown system}. Flash JetPack 7 first."
PYV="$("$SYS_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
[[ "$PYV" == "3.12" ]] || die "Atlas needs the system Python 3.12 at $SYS_PY (found ${PYV:-none})."
if [[ -f "$R/etc/nv_tegra_release" ]]; then
    log "Platform: $(head -1 "$R/etc/nv_tegra_release" | sed 's/^# *//; s/, GCID.*//'), Ubuntu 24.04, Python $PYV"
else
    log "Platform: Ubuntu 24.04, Python $PYV"
fi

# The signature and bundle are checked again by the installer proper (and by
# the update engine); this only refuses an untrusted or damaged release early.
"$SYS_PY" -I "$SRC_DIR/tools/migrate/migrate.py" check --bundle "$BUNDLE" ${DEV_KEY:+--dev-key "$DEV_KEY"} \
    || die "The release in $BUNDLE is not trusted or is damaged — nothing was changed."

# Free-space advisory only (never blocks): the full dataset — US basemap +
# all-state OSRM + topo/DBs, ~150 GB once built — plus the 32 GB swapfile and
# build scratch is large, but a payload-provisioned unit or a partial state
# selection needs far less.
mkdir -p "$ATLAS_DATA"
FREE_GB=$(df -BG --output=avail "$ATLAS_DATA" 2>/dev/null | tail -1 | tr -dc '0-9')
if [[ -n "$FREE_GB" && "$FREE_GB" -lt "$RECOMMENDED_FREE_GB" ]]; then
    warn "Only ${FREE_GB} GB free on /atlas_data (~${RECOMMENDED_FREE_GB} GB recommended to build every dataset here)."
fi

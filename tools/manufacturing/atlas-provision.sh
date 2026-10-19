#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ATLAS PROVISION — seed a freshly-flashed Jetson from the data payload   ║
# ║                                                                          ║
# ║  Run this ON THE NEW UNIT, after it has been flashed with JetPack 7      ║
# ║  (L4T R39.2) and you have completed OOBE creating the 'admin' user.      ║
# ║                                                                          ║
# ║  Usage:  sudo ./atlas-provision.sh [--yes] [--no-install] [--dev-key PUB]║
# ║            --yes         skip the confirmation prompt                    ║
# ║            --no-install  copy the payload but don't run install.sh       ║
# ║            --dev-key PUB make a developer unit (trusts that key, keeps   ║
# ║                          SSH) — never for units that are sold            ║
# ║                                                                          ║
# ║  What it does:                                                           ║
# ║    1. Copy payload/ -> /atlas_data   (~150 GB, no downloads)             ║
# ║    2. Install the staged ollama + OSRM binaries                          ║
# ║    3. Run the kit's install.sh with the staged signed release: system    ║
# ║       setup, the Atlas software through its update engine, this unit's   ║
# ║       own identity and label, and retail lock-down                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -Eeuo pipefail

BACKUP_LABEL="atlas-backup"
BACKUP_UUID="9b6fe19b-34c1-434f-8e6e-c87cb1f370c4"
BACKUP_MNT="/mnt/atlas-backup"
ATLAS_DATA="/atlas_data"
NEED_GB=200          # payload (~150) + 32 GB swapfile it will create + headroom

ASSUME_YES=0; RUN_INSTALL=1; DEV_KEY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --no-install) RUN_INSTALL=0; shift ;;
        --dev-key)    DEV_KEY="${2:-}"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
LABELS="/root/atlas-labels"

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; BLU=$'\033[0;34m'; BLD=$'\033[1m'; NC=$'\033[0m'
log()  { echo -e "${GRN}[✓]${NC} $*"; }
info() { echo -e "${BLU}[i]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo; echo -e "${BLD}── $* ${NC}"; }

[[ $EUID -eq 0 ]] || die "Must run as root:  sudo $0"

# ════════════════════════════════════════════════════════════════════════════
section "Preflight"
# ════════════════════════════════════════════════════════════════════════════
[[ -f /etc/nv_tegra_release ]] || die "Not a Jetson (no /etc/nv_tegra_release)."

TGT_L4T=$(dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null || echo "")
[[ -n "$TGT_L4T" ]] || warn "nvidia-l4t-core not installed — is this box fully flashed?"

# Locate the target user. install.sh derives ATLAS_USER from /atlas_data
# ownership, and atlas-control.service runs as 'admin', so this must line up.
TGT_USER="${SUDO_USER:-}"
if [[ -z "$TGT_USER" || "$TGT_USER" == "root" ]]; then
    TGT_USER=$(getent passwd 1000 | cut -d: -f1)
fi
[[ -n "$TGT_USER" ]] || die "Could not determine the target user (expected 'admin' at UID 1000)."
id "$TGT_USER" >/dev/null 2>&1 || die "User '$TGT_USER' does not exist."
[[ "$TGT_USER" == "admin" ]] || warn "Target user is '$TGT_USER', not 'admin' — install.sh will adapt, but confirm this is intended."
log "Target user: $TGT_USER (uid $(id -u "$TGT_USER"))"

# Mount the payload drive
SRC_DEV=""
for cand in "/dev/disk/by-uuid/$BACKUP_UUID" "/dev/disk/by-label/$BACKUP_LABEL"; do
    [[ -e "$cand" ]] && { SRC_DEV="$cand"; break; }
done
[[ -n "$SRC_DEV" ]] || die "Payload drive not found (looked for UUID $BACKUP_UUID and label '$BACKUP_LABEL').
    Plug it in and re-run."
# A desktop session may have auto-mounted it at /media/$USER/atlas-backup; use
# that rather than mounting the same filesystem twice.
EXISTING_MNT=$(findmnt -n -o TARGET --source "$(readlink -f "$SRC_DEV")" | head -1)
if [[ -n "$EXISTING_MNT" ]]; then
    BACKUP_MNT="$EXISTING_MNT"
    log "Payload drive already mounted at $BACKUP_MNT — using it"
else
    mkdir -p "$BACKUP_MNT"
    mount -o ro "$SRC_DEV" "$BACKUP_MNT" || die "Failed to mount $SRC_DEV"
    log "Mounted $SRC_DEV read-only at $BACKUP_MNT"
fi

PAYLOAD="$BACKUP_MNT/payload"
STAGE="$BACKUP_MNT/stage"
[[ -d "$PAYLOAD/atlas-control" ]] || die "$PAYLOAD/atlas-control missing — run atlas-capture.sh on the golden unit first."
[[ -f "$PAYLOAD/atlas-control/static/tiles/map.pmtiles" ]] || die "Basemap missing from payload — capture was incomplete."
[[ -f "$STAGE/release/atlas-release.json" && -f "$STAGE/kit/install.sh" ]] \
    || die "No signed release on the drive ($STAGE/release, $STAGE/kit) — re-run atlas-capture.sh --release DIR."

if [[ -f "$BACKUP_MNT/MANIFEST.txt" ]]; then
    echo; sed 's/^/    /' "$BACKUP_MNT/MANIFEST.txt"; echo
    SRC_L4T=$(awk -F': *' '/nvidia-l4t-core/{print $2}' "$BACKUP_MNT/MANIFEST.txt")
    if [[ -n "$SRC_L4T" && -n "$TGT_L4T" && "$SRC_L4T" != "$TGT_L4T" ]]; then
        warn "L4T mismatch — payload built on $SRC_L4T, this box runs $TGT_L4T."
        warn "The app will likely still work, but flashing the matching JetPack is safer."
    else
        log "L4T version matches the payload ($TGT_L4T)"
    fi
fi

# Space on the target root
AVAIL_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
(( AVAIL_GB >= NEED_GB )) || die "Only ${AVAIL_GB} GB free on / (need ~${NEED_GB} GB for the payload plus its 32 GB swapfile)."
log "Free space: ${AVAIL_GB} GB"

if [[ -d "$ATLAS_DATA/atlas-control" ]]; then
    warn "$ATLAS_DATA/atlas-control already exists — this run will overwrite matching files."
fi

if (( ! ASSUME_YES )); then
    echo
    echo -e "${BLD}About to provision this device:${NC}"
    echo "  payload  $PAYLOAD  ->  $ATLAS_DATA   (~150 GB, 20-30 min)"
    (( RUN_INSTALL )) && echo "  then install Atlas $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$STAGE/release/atlas-release.json") and give this unit its identity"
    read -rp "Proceed? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted by user."
fi

# ════════════════════════════════════════════════════════════════════════════
section "Copying payload  (~150 GB — expect 20-30 min)"
# ════════════════════════════════════════════════════════════════════════════
mkdir -p "$ATLAS_DATA"
# No --delete: this is an additive seed onto a fresh box, and we must never
# remove something the operator put there deliberately.
rsync -aHAX --numeric-ids --info=progress2 --human-readable \
    "$PAYLOAD/" "$ATLAS_DATA/"
chown -R "$TGT_USER:$TGT_USER" "$ATLAS_DATA"
chmod 755 "$ATLAS_DATA"
log "Payload copied and ownership set to $TGT_USER"

# ════════════════════════════════════════════════════════════════════════════
section "Installing staged binaries"
# ════════════════════════════════════════════════════════════════════════════
# ollama has no prebuilt hatch in install.sh — it checks `command -v ollama`
# and otherwise curls the installer, so put it on PATH first.
if [[ -x "$STAGE/ollama" ]]; then
    install -m755 "$STAGE/ollama" /usr/local/bin/ollama
    if [[ -d "$STAGE/ollama-lib" ]]; then
        # The GPU runners: without them Ollama runs Ray on the CPU.
        mkdir -p /usr/local/lib/ollama
        rsync -a --delete "$STAGE/ollama-lib/" /usr/local/lib/ollama/
        log "ollama installed with its GPU runners: $(/usr/local/bin/ollama --version 2>/dev/null | head -1)"
    else
        warn "$STAGE/ollama-lib missing (drive captured by an older atlas-capture.sh) — install.sh reinstalls Ollama online."
    fi
else
    warn "$STAGE/ollama missing — install.sh will download ollama instead (needs internet)."
fi

# OSRM binaries are left in /atlas_data/osrm-native-bin/ where install.sh L843
# finds them and copies them to /usr/local/bin itself.
if [[ -x "$ATLAS_DATA/osrm-native-bin/osrm-routed" ]]; then
    log "Prebuilt OSRM staged at $ATLAS_DATA/osrm-native-bin — install.sh will skip the source build"
else
    warn "No prebuilt OSRM found — install.sh will build from source (20-40 min)."
fi

install -d -m755 /opt/atlas-imaging
if [[ -d "$STAGE/atlas-imaging" ]]; then
    install -m755 "$STAGE"/atlas-imaging/*.sh /opt/atlas-imaging/ 2>/dev/null || true
fi

# ════════════════════════════════════════════════════════════════════════════
if (( RUN_INSTALL )); then
section "Running install.sh  (configuration only — no map downloads)"
# ════════════════════════════════════════════════════════════════════════════
    info "Watch for 'already present — skipping' on the basemap and NO per-state"
    info "OSRM processing. If it starts downloading map data, stop and diagnose."
    echo
    # Unattended: never a prompt, and only the routing states the payload brought.
    bash "$STAGE/kit/install.sh" --bundle "$STAGE/release" --labels "$LABELS" --yes --states keep \
        ${DEV_KEY:+--dev-key "$DEV_KEY"}
    log "install.sh finished"
    # Keep a copy of the label on the drive for printing and support, if the
    # drive is writable (a desktop auto-mount is; our own mount is read-only).
    if mkdir -p "$BACKUP_MNT/labels" 2>/dev/null && cp -a "$LABELS"/. "$BACKUP_MNT/labels/" 2>/dev/null; then
        log "Label copied to $BACKUP_MNT/labels/"
    else
        warn "Drive is read-only: the label files are in $LABELS on this unit"
    fi
else
    info "Skipping install.sh (--no-install). Run it yourself with:"
    echo "    sudo bash $STAGE/kit/install.sh --bundle $STAGE/release --labels $LABELS --yes --states keep"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Post-install checks"
# ════════════════════════════════════════════════════════════════════════════
for svc in atlas-control nginx ollama avahi-daemon atlas-helper.socket atlas-firewall; do
    if systemctl is-active --quiet "$svc"; then log "$svc active"; else warn "$svc NOT active"; fi
done
swapon --show || true
echo
log "${BLD}Provisioning complete.${NC}"
info "Print the label (${LABELS}), reboot, then verify from a phone: scan the label's app QR code"
info "Unmount the payload drive:  sudo umount $BACKUP_MNT"

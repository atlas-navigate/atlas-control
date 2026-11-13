#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ATLAS CAPTURE — build a deployable data payload from this golden unit   ║
# ║                                                                          ║
# ║  Usage:  sudo tools/manufacturing/atlas-capture.sh --release DIR         ║
# ║                [--drive LABEL] [--dev]                                   ║
# ║            DIR   = a signed Atlas release (atlas-release.json, .sig and  ║
# ║                    the bundle), checked against this unit's trusted keys ║
# ║            LABEL = the drive's filesystem label (atlas-control-prod)     ║
# ║            --dev = a drive for developer units only: its release need    ║
# ║                    not be signed by a production key                     ║
# ║                                                                          ║
# ║  Produces on the drive:                                                  ║
# ║    payload/            ~150 GB of /atlas_data, device secrets stripped   ║
# ║    payload/osrm-native-bin/   OSRM binaries (install.sh prebuilt hatch)  ║
# ║    stage/ollama        the ollama binary (lives outside /atlas_data)     ║
# ║    stage/ollama-lib/   its GPU runners (/usr/local/lib/ollama)           ║
# ║    stage/release/      the signed Atlas release every new unit installs  ║
# ║    stage/kit/          that same release unpacked (minus wheels): its    ║
# ║                        install.sh, tools and dataset builders            ║
# ║    stage/atlas-imaging/  the release's own atlas-provision.sh            ║
# ║    MANIFEST.txt        provenance: L4T ver, release, sizes, date         ║
# ║                                                                          ║
# ║  Re-running is cheap: rsync only transfers deltas.                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -Eeuo pipefail

# A test sandbox (tests/test_manufacturing.py) sets ATLAS_ROOT and puts stub
# system commands first on PATH, as for install.sh. Unset on a real unit.
R="${ATLAS_ROOT:-}"

DRIVE_LABEL="atlas-control-prod"   # the production payload drive
ATLAS_DATA="$R/atlas_data"
APP_DIR="$ATLAS_DATA/atlas-control"
MIN_FREE_GB=200
# PAYLOAD and STAGE are derived after the mountpoint is resolved below.

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; BLU=$'\033[0;34m'; BLD=$'\033[1m'; NC=$'\033[0m'
log()  { echo -e "${GRN}[✓]${NC} $*"; }
info() { echo -e "${BLU}[i]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo; echo -e "${BLD}── $* ${NC}"; }

[[ $EUID -eq 0 || -n "$R" ]] || die "Must run as root:  sudo $0"

RELEASE=""; DEV_DRIVE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --release) RELEASE="${2:-}"; shift 2 ;;
        --drive)   DRIVE_LABEL="${2:-}"; shift 2 ;;
        --dev)     DEV_DRIVE=1; shift ;;
        *) die "Unknown option: $1  (usage: $0 --release DIR [--drive LABEL] [--dev])" ;;
    esac
done
[[ "$DRIVE_LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || die "--drive needs the drive's filesystem label, e.g. atlas-control-prod"
BACKUP_MNT="$R/mnt/$DRIVE_LABEL"   # fallback only; an existing mount wins
[[ -n "$RELEASE" && -f "$RELEASE/atlas-release.json" ]] \
    || die "Give the signed Atlas release to ship:  $0 --release DIR  (DIR holds atlas-release.json)"
# Only a release this unit's update engine trusts goes onto the drive.
RELEASE_INFO=$("$R"/usr/local/lib/atlas/bin/atlas-engine verify "$RELEASE") \
    || die "$RELEASE is not signed by a key this unit trusts — refusing to stage it."
RELEASE_VERSION=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<<"$RELEASE_INFO")
RELEASE_BUNDLE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["bundle"])' <<<"$RELEASE_INFO")
log "Release $RELEASE_VERSION verified"

# ── Services we stop for a consistent snapshot, restarted no matter how we exit
STOPPED=()
restore_services() {
    local rc=$?
    if [[ ${#STOPPED[@]} -gt 0 ]]; then
        echo
        info "Restarting services: ${STOPPED[*]}"
        systemctl start "${STOPPED[@]}" 2>/dev/null || warn "Could not restart all services — check 'systemctl status'"
    fi
    (( rc != 0 )) && echo -e "${RED}[✗]${NC} capture aborted (exit $rc)" >&2
    return $rc
}
trap restore_services EXIT

# ════════════════════════════════════════════════════════════════════════════
section "Preflight"
# ════════════════════════════════════════════════════════════════════════════
[[ -d "$APP_DIR" ]] || die "$APP_DIR not found — is this the golden Atlas unit?"

# The GNOME desktop auto-mounts this drive at /media/$USER/<label>. Honour
# whatever mountpoint it already has rather than mounting the same filesystem a
# second time at /mnt/<label> — that would work, but would write the payload
# somewhere the operator isn't looking.
BACKUP_DEV="$R/dev/disk/by-label/$DRIVE_LABEL"
[[ -e "$BACKUP_DEV" ]] || die "No drive labelled '$DRIVE_LABEL' is attached.
    Plug it in and re-run (a new drive: sudo mkfs.ext4 -L $DRIVE_LABEL /dev/sdX1 — see MANUFACTURING.md)."
EXISTING_MNT=$(findmnt -n -o TARGET --source "$(readlink -f "$BACKUP_DEV")" | head -1)
if [[ -n "$EXISTING_MNT" ]]; then
    BACKUP_MNT="$EXISTING_MNT"
    log "Backup drive already mounted at $BACKUP_MNT — using it"
else
    mkdir -p "$BACKUP_MNT"
    mount "$BACKUP_DEV" "$BACKUP_MNT" \
        || die "Failed to mount the backup drive at $BACKUP_MNT"
    log "Mounted $BACKUP_DEV at $BACKUP_MNT"
fi
PAYLOAD="$BACKUP_MNT/payload"
STAGE="$BACKUP_MNT/stage"
info "Payload destination: $PAYLOAD"

# Never let source and destination be the same filesystem.
SRC_FS=$(findmnt -n -o SOURCE --target "$ATLAS_DATA")
DST_FS=$(findmnt -n -o SOURCE --target "$BACKUP_MNT")
[[ "$SRC_FS" != "$DST_FS" ]] || die "Backup drive resolves to the same filesystem as $ATLAS_DATA ($SRC_FS) — refusing."

FREE_GB=$(df -BG --output=avail "$BACKUP_MNT" | tail -1 | tr -dc '0-9')
(( FREE_GB >= MIN_FREE_GB )) \
    || die "Only ${FREE_GB} GB free on $BACKUP_MNT (need ~${MIN_FREE_GB} GB).
    Free space or use a larger drive."
log "Free space on backup drive: ${FREE_GB} GB"

# All four are mandatory — install.sh L843 falls back to a 20-40 min source
# build if even one is missing from the prebuilt dir.
for t in osrm-routed osrm-extract osrm-partition osrm-customize; do
    command -v "$t" >/dev/null || die "$t not on PATH — cannot stage prebuilt OSRM binaries."
done
command -v ollama >/dev/null || die "ollama not on PATH — cannot stage the ollama binary."
log "OSRM toolchain and ollama located"

mkdir -p "$PAYLOAD" "$STAGE"

# ════════════════════════════════════════════════════════════════════════════
section "Quiescing services"
# ════════════════════════════════════════════════════════════════════════════
# The SQLite DBs (places.db, nps_trails.db, meshtastic.db) have -wal/-shm
# sidecars; copying them while written yields a torn database.
for svc in atlas-control ollama; do
    if systemctl is-active --quiet "$svc"; then
        systemctl stop "$svc"
        STOPPED+=("$svc")
    fi
done
[[ ${#STOPPED[@]} -gt 0 ]] && log "Stopped: ${STOPPED[*]}" || info "Nothing needed stopping"
sync

# ════════════════════════════════════════════════════════════════════════════
section "Copying payload  (~150 GB — expect 20-30 min on the USB HDD)"
# ════════════════════════════════════════════════════════════════════════════
# Exclusions serve two purposes:
#   1. bulk we never want    — the 33 GB swapfile, caches, logs, source trees
#   2. per-device state      — this unit's database (mesh history, conversations,
#      paired devices, security log), Wi-Fi hints, hotspot password, Flask
#      secret, database copies and downloaded updates. A new unit gets its own
#      at provisioning; its identity lives in /etc/atlas, outside the payload.
# --delete-excluded: an excluded file already on the drive (from an older
# capture) is removed, not kept. osrm-native-bin/ is re-staged just below.
rsync -aHAX --numeric-ids --delete --delete-excluded --info=progress2 --human-readable \
    --exclude='/atlas-swap.swap' \
    --exclude='/osrm-native-bin/' \
    --exclude='/atlas-control-wt/***' \
    --exclude='/atlas-control/.git/***' \
    --exclude='/atlas-control/data/hotspot_config.json' \
    --exclude='/atlas-control/data/osrm_active.json' \
    --exclude='/atlas-control/data/backups/***' \
    --exclude='/atlas-control/updates/***' \
    --exclude='/atlas-control/cache/***' \
    --exclude='/atlas-control/logs/***' \
    --exclude='/atlas-control/__pycache__/***' \
    --exclude='/atlas-control/.pytest_cache/***' \
    --exclude='/atlas-control/.claude/***' \
    --exclude='/atlas-control/cert.pem' \
    --exclude='/atlas-control/key.pem' \
    --exclude='/atlas-control/hotspot_config.json' \
    --exclude='/atlas-control/data/.secret_key' \
    --exclude='/atlas-control/data/wifi_hints.json' \
    --exclude='/atlas-control/data/atlas.db' \
    --exclude='/atlas-control/data/atlas.db-shm' \
    --exclude='/atlas-control/data/atlas.db-wal' \
    --exclude='/atlas-control/data/meshtastic.db' \
    --exclude='/atlas-control/data/meshtastic.db-shm' \
    --exclude='/atlas-control/data/meshtastic.db-wal' \
    "$ATLAS_DATA/" "$PAYLOAD/"
log "Payload synced"

# ════════════════════════════════════════════════════════════════════════════
section "Staging binaries that live outside /atlas_data"
# ════════════════════════════════════════════════════════════════════════════
# Done AFTER the rsync so --delete can never remove them (--delete-excluded
# removes the excluded osrm-native-bin/ itself, so it is created here).
mkdir -p "$PAYLOAD/osrm-native-bin"
install -m755 "$R"/usr/local/bin/osrm-* "$PAYLOAD/osrm-native-bin/"
log "OSRM binaries staged: $(ls "$PAYLOAD/osrm-native-bin/" | tr '\n' ' ')"

install -m755 "$(command -v ollama)" "$STAGE/ollama"
log "ollama staged ($("$STAGE/ollama" --version 2>/dev/null | head -1))"
# Without its runners the binary falls back to the CPU and Ray crawls.
[[ -d "$R/usr/local/lib/ollama" ]] \
    || die "/usr/local/lib/ollama is missing — this unit's Ollama has no GPU runners. Reinstall Ollama, then capture."
rsync -a --delete "$R/usr/local/lib/ollama/" "$STAGE/ollama-lib/"
log "ollama GPU runners staged ($(du -sh "$STAGE/ollama-lib" | cut -f1))"

# The signed release, and the installer + tools from inside that same bundle
# (so the kit a technician runs always matches the software it installs).
rsync -a --delete "$RELEASE/" "$STAGE/release/"
rm -rf "$STAGE/kit"
mkdir -p "$STAGE/kit"
tar -xJf "$STAGE/release/$RELEASE_BUNDLE" -C "$STAGE/kit" --exclude='wheels' --exclude='wheels/*'
log "Release $RELEASE_VERSION and its installer kit staged"

# The imaging scripts and runbook from that same release, so the drive is
# self-contained and its atlas-provision.sh always matches the release it
# installs (never a stale copy left on this unit).
rm -rf "$STAGE/atlas-imaging"
mkdir -p "$STAGE/atlas-imaging"
install -m755 "$STAGE"/kit/tools/manufacturing/*.sh "$STAGE/atlas-imaging/"
install -m644 "$STAGE/kit/docs/MANUFACTURING.md" "$BACKUP_MNT/MANUFACTURING.md"
log "Imaging scripts + runbook staged from release $RELEASE_VERSION"

# ════════════════════════════════════════════════════════════════════════════
section "Manifest"
# ════════════════════════════════════════════════════════════════════════════
PROCESSED=$(find "$PAYLOAD/atlas-control/osrm-data/states" -name .processed 2>/dev/null | wc -l)
PAYLOAD_H=$(du -sh "$PAYLOAD" 2>/dev/null | cut -f1)

cat > "$BACKUP_MNT/MANIFEST.txt" <<EOF
Atlas Navigate — deployment payload
Captured           : $(date -Is)
Captured from      : $(hostname)
L4T / JetPack      : $(grep -o 'R[0-9]* (release), REVISION: [0-9.]*' /etc/nv_tegra_release 2>/dev/null || echo unknown)
nvidia-l4t-core    : $(dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null || echo unknown)
Kernel             : $(uname -r)
Board              : $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)
TNSPEC             : $(grep '^TNSPEC' /etc/nv_boot_control.conf 2>/dev/null | cut -d' ' -f2-)
Atlas release      : $RELEASE_VERSION
Drive              : $DRIVE_LABEL ($( (( DEV_DRIVE )) && echo "developer units only" || echo "production"))
Payload size       : $PAYLOAD_H
OSRM states built  : $PROCESSED  (expect 102 = 51 states x car+hiking)
OSRM version       : $(osrm-routed --version 2>/dev/null | head -1)
ollama version     : $(ollama --version 2>/dev/null | head -1)

Deploy with: sudo stage/atlas-imaging/atlas-provision.sh$( (( DEV_DRIVE )) && echo " --dev-key PUB")   (see MANUFACTURING.md)
EOF
cat "$BACKUP_MNT/MANIFEST.txt"

# ════════════════════════════════════════════════════════════════════════════
section "Verification"
# ════════════════════════════════════════════════════════════════════════════
FAIL=0
check() { if eval "$2"; then log "$1"; else warn "$1 — FAILED"; FAIL=1; fi; }

check "basemap present"            '[[ -f "$PAYLOAD/atlas-control/static/tiles/map.pmtiles" ]]'
check "places.db present"          '[[ -s "$PAYLOAD/atlas-control/static/data/places.db" ]]'
check "us_cities.db present"       '[[ -s "$PAYLOAD/atlas-control/static/data/us_cities.db" ]]'
check "nps_trails.db present"      '[[ -s "$PAYLOAD/atlas-control/static/data/nps_trails.db" ]]'
check "venv interpreter present"   '[[ -x "$PAYLOAD/atlas-control/venv/bin/python3" ]]'
check "ollama models present"      '[[ -d "$PAYLOAD/ollama/models/blobs" ]]'
check "OSRM markers == 102"        '[[ "$PROCESSED" -eq 102 ]]'
check "all 4 OSRM tools staged"    'for t in routed extract partition customize; do [[ -x "$PAYLOAD/osrm-native-bin/osrm-$t" ]] || exit 1; done'
check "swapfile excluded"          '[[ ! -e "$PAYLOAD/atlas-swap.swap" ]]'
check "TLS key stripped"           '[[ ! -e "$PAYLOAD/atlas-control/key.pem" ]]'
check "Flask secret stripped"      '[[ ! -e "$PAYLOAD/atlas-control/data/.secret_key" ]]'
check "mesh history stripped"      '[[ ! -e "$PAYLOAD/atlas-control/data/meshtastic.db" ]]'
check "hotspot password stripped"  '[[ ! -e "$PAYLOAD/atlas-control/data/hotspot_config.json" && ! -e "$PAYLOAD/atlas-control/hotspot_config.json" ]]'
check "database copies stripped"   '[[ ! -e "$PAYLOAD/atlas-control/data/backups" ]]'
check "release staged + kit"       '[[ -f "$STAGE/release/atlas-release.json" && -f "$STAGE/kit/install.sh" && -d "$STAGE/kit/system/atlas_engine" ]]'
check "provision script staged"    '[[ -x "$STAGE/atlas-imaging/atlas-provision.sh" ]]'
# A new retail unit trusts only the keys in the release's own keyring: run the
# check its install.sh starts with. A dev-signed release passes this unit's
# verify above but would be refused on the line, unit after unit.
if (( DEV_DRIVE )); then
    info "Developer drive: provision units from it with atlas-provision.sh --dev-key PUB"
elif RETAIL=$(python3 -I "$STAGE/kit/tools/migrate/migrate.py" check --bundle "$STAGE/release" 2>&1); then
    log "retail units trust release $RELEASE_VERSION"
else
    warn "retail units refuse release $RELEASE_VERSION (${RETAIL##*$'\n'}) — FAILED"
    warn "    sign it with the production key (docs/RELEASES.md); a drive for developer units takes --dev"
    FAIL=1
fi
check "ollama GPU runners staged"  '[[ -d "$STAGE/ollama-lib" && -n "$(ls -A "$STAGE/ollama-lib")" ]]'
# Every model the release offers (in this hardware's build) must be on the
# drive: a unit sold off-grid can never download one.
if [[ -f "$STAGE/kit/model_profiles.py" ]]; then
    while read -r RAY_MODEL; do
        RAY_MANIFEST="$PAYLOAD/ollama/models/manifests/registry.ollama.ai/library/${RAY_MODEL%%:*}/${RAY_MODEL#*:}"
        check "Ray model $RAY_MODEL in payload" '[[ -f "$RAY_MANIFEST" ]]'
    done < <(python3 -I "$STAGE/kit/model_profiles.py" --preinstall)
fi

sync
echo
if (( FAIL )); then
    die "Capture completed with FAILED checks above — do not deploy this payload until resolved."
fi
log "${BLD}Payload ready at $PAYLOAD ($PAYLOAD_H)${NC}"
info "Unmount before unplugging:  sudo umount $BACKUP_MNT"

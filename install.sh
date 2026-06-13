#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║              ATLAS CONTROL — MASTER INSTALLATION SCRIPT                 ║
# ║              Jetson Orin Nano (ARM64) · Ubuntu 22.04                     ║
# ║                                                                          ║
# ║  Usage:  sudo ./install.sh             fresh install (or prompts to      ║
# ║                                        update if Atlas is already here)  ║
# ║          sudo ./install.sh --update    quick update: pull latest code,   ║
# ║                                        refresh deps/configs, restart —   ║
# ║                                        no prompts, safe to automate      ║
# ║          sudo ./install.sh --full      force the full install flow       ║
# ║                                                                          ║
# ║  What this does:                                                         ║
# ║    1.  Detect & mount NVMe drive at /atlas_data                          ║
# ║    2.  Download / update Atlas Control source (GitHub)                   ║
# ║    3.  Install system dependencies                                        ║
# ║    4.  Set up Python environment                                          ║
# ║    5.  Install Ollama (AI / LLM engine)                                  ║
# ║    6.  Build OSRM routing engine from source                             ║
# ║    7.  Download vector basemap  (126GB)                                  ║
# ║    8.  Download topographic overlay                                       ║
# ║    9.  Download city & trail databases                                    ║
# ║   10.  Download & process US state routing data                           ║
# ║   11.  Configure HTTPS / nginx reverse proxy                              ║
# ║   12.  Install systemd services + udev hardware rules                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -euo pipefail
# A bare `set -e` death is invisible in logs/update.log; report it.  The [✗]
# marker is what /api/update/status keys on to surface a failed update.
set -E
# Subshell filter: $(...) substitution failures fire ERR too but don't kill
# the script — only report the top-level failure that actually aborts.
trap '(( BASH_SUBSHELL == 0 )) && echo -e "\033[0;31m[✗]\033[0m install.sh aborted (line $LINENO: $BASH_COMMAND)" >&2 || true' ERR

# ── Self-update safety ──────────────────────────────────────────────────────
# Section 2 may `git pull` the directory this script lives in, rewriting
# install.sh while bash is still reading it incrementally — which corrupts the
# running script.  Re-exec from a temp copy first; the original location is
# preserved in ATLAS_INSTALL_SELF so source-dir detection still works.
if [[ -z "${ATLAS_INSTALL_SELF:-}" ]]; then
    _self_tmp="$(mktemp /tmp/atlas-install.XXXXXX.sh)"
    cp "${BASH_SOURCE[0]}" "$_self_tmp"
    export ATLAS_INSTALL_SELF
    ATLAS_INSTALL_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export ATLAS_INSTALL_TMP="$_self_tmp"
    exec bash "$_self_tmp" "$@"
fi
trap '[[ -n "${ATLAS_INSTALL_TMP:-}" ]] && rm -f "$ATLAS_INSTALL_TMP"' EXIT

# ── Protomaps basemap source ────────────────────────────────────────────────
# Atlas resolves the latest published PMTiles build at install time from
# Protomaps metadata. If metadata lookup fails, it falls back to the last
# verified-good build below.
PROTOMAPS_METADATA_URL="https://build-metadata.protomaps.dev/builds.json"
# Last-resort key, used only if the metadata lookup above is unreachable.
# Protomaps prunes old daily builds, so this WILL 404 eventually — refresh it
# periodically. The basemap step (7b) HEAD-checks the resolved URL and aborts
# with a clear message rather than downloading a dead link into map.pmtiles.
PROTOMAPS_FALLBACK_KEY="20260608.pmtiles"

# Routing profiles to process per state (car always included; hiking optional)
ROUTING_PROFILES=("car" "hiking")

# Ollama models pulled on first run (also checked after updates)
OLLAMA_CHAT_MODEL="qwen3.5:2b"
OLLAMA_EMBED_MODEL="qwen3-embedding:0.6b"

# ── Atlas Control source repository ─────────────────────────────────────────
# install.sh fetches the current version of Atlas Control from here, so a
# box can be provisioned from nothing but this script:
#   curl -fsSLO https://raw.githubusercontent.com/atlas-navigate/atlas-control/main/install.sh
#   sudo bash install.sh
# Three modes (handled in section 2):
#   • standalone — only install.sh present → shallow-clones the repo
#   • git checkout — fast-forwards to the latest published commit first
#   • offline / dirty checkout — warns and installs the local copy as-is
ATLAS_REPO_URL="https://github.com/atlas-navigate/atlas-control.git"
ATLAS_REPO_BRANCH="main"
# The repo is public, so no auth is needed.  If it is ever made private,
# pass a GitHub personal-access token (repo read scope):
#   sudo ATLAS_REPO_TOKEN=ghp_xxxx ./install.sh
ATLAS_REPO_TOKEN="${ATLAS_REPO_TOKEN:-}"

# ── Colors & helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GRN}[✓]${NC} $*"; }
info() { echo -e "${CYN}[→]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

section() {
    echo ""
    echo -e "${BLD}${CYN}══ $* ══${NC}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

resolve_latest_protomaps_url() {
    local key
    key="$(python3 - <<PYEOF
import json, urllib.request
meta_url = "${PROTOMAPS_METADATA_URL}"
req = urllib.request.Request(meta_url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        builds = json.load(r)
    if isinstance(builds, list) and builds:
        print(builds[-1].get("key", ""))
except Exception:
    pass
PYEOF
)"
    if [[ -n "$key" ]]; then
        echo "https://build.protomaps.com/${key}"
    else
        echo "https://build.protomaps.com/${PROTOMAPS_FALLBACK_KEY}"
    fi
}

confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$(echo -e "${YLW}${prompt} [Y/n]:${NC} ")" yn
        [[ "${yn,,}" != "n" ]]
    else
        read -rp "$(echo -e "${YLW}${prompt} [y/N]:${NC} ")" yn
        [[ "${yn,,}" == "y" ]]
    fi
}

# ── Install mode: full | update ─────────────────────────────────────────────
# full   — everything: storage, packages, Ollama, OSRM build, maps, routing
# update — pull latest code, refresh Python deps / configs / services, restart.
#          Non-interactive, so it can run unattended (cron, remote update).
INSTALL_MODE=""
for arg in "$@"; do
    case "$arg" in
        --update|update)  INSTALL_MODE="update" ;;
        --full|--fresh)   INSTALL_MODE="full" ;;
        -h|--help)
            echo "Usage: sudo ./install.sh [--update | --full]"
            echo "  (no flag)  fresh install; offers a quick update if Atlas is already installed"
            echo "  --update   quick update: pull latest code, refresh deps/configs, restart services"
            echo "  --full     force the full install flow (storage, maps, routing data, ...)"
            exit 0 ;;
        *) die "Unknown option: $arg  (try --help)" ;;
    esac
done

# ── Must run as root ────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run with sudo:  sudo ./install.sh"
# In-app updates arrive via systemd-run: no SUDO_USER and no login session
# (logname fails), so fall back to the owner of the existing install rather
# than a hardcoded guess — getent on a nonexistent user exits 2, which under
# `set -eo pipefail` killed the updater before its first line of output.
ATLAS_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [[ -z "$ATLAS_USER" ]] || ! getent passwd "$ATLAS_USER" > /dev/null; then
    ATLAS_USER="$(stat -c %U /atlas_data/atlas-control 2>/dev/null || echo ubuntu)"
fi
ATLAS_HOME="$(getent passwd "$ATLAS_USER" | cut -d: -f6)" \
    || die "Cannot resolve home directory for user '$ATLAS_USER'"
# systemd-run units have no $HOME; tools like the ollama CLI panic without it.
export HOME="${HOME:-/root}"
SCRIPT_DIR="$ATLAS_INSTALL_SELF"

# Auto-detect an existing installation and offer the quick-update path.
if [[ -z "$INSTALL_MODE" ]]; then
    if [[ -f /atlas_data/atlas-control/.atlas_installed || -f /etc/systemd/system/atlas-control.service ]]; then
        if confirm "Existing Atlas Control installation detected — run a quick update instead of a full install?" y; then
            INSTALL_MODE="update"
        else
            INSTALL_MODE="full"
        fi
    else
        INSTALL_MODE="full"
    fi
fi
# Update mode needs something to update.
if [[ "$INSTALL_MODE" == "update" && ! -d /atlas_data/atlas-control ]]; then
    warn "No existing install at /atlas_data/atlas-control — switching to full install"
    INSTALL_MODE="full"
fi

echo -e "${BLD}${CYN}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║          ATLAS CONTROL — INSTALLER                  ║"
echo "  ║          Offline GPS Nav + Mesh Communications      ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Mode               : ${BLD}$([[ "$INSTALL_MODE" == "update" ]] && echo "UPDATE (quick)" || echo "FULL INSTALL")${NC}"
echo -e "  Installing for user: ${BLD}${ATLAS_USER}${NC}"
echo -e "  Source directory   : ${BLD}${SCRIPT_DIR}${NC}"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# 1.  NVMe DETECTION & STORAGE SETUP
# ════════════════════════════════════════════════════════════════════════════
section "NVMe Storage Detection"

ATLAS_DATA="/atlas_data"

# Helper — format and mount an NVMe device (defined early; used below)
_setup_nvme() {
    local dev="$1"
    local part="${dev}p1"

    info "Partitioning $dev ..."
    parted -s "$dev" mklabel gpt mkpart primary ext4 0% 100%
    partprobe "$dev"
    sleep 2

    info "Formatting ${part} as ext4 (label: atlas_data) ..."
    mkfs.ext4 -L atlas_data -F "$part"

    mkdir -p "$ATLAS_DATA"
    mount "$part" "$ATLAS_DATA"

    local UUID
    UUID=$(blkid -s UUID -o value "$part")
    if ! grep -q "$UUID" /etc/fstab; then
        echo "UUID=$UUID  $ATLAS_DATA  ext4  defaults,nofail  0  2" >> /etc/fstab
    fi

    log "NVMe mounted at $ATLAS_DATA (UUID: $UUID)"
}

# Find the root filesystem device (to exclude it)
ROOT_DEV=$(lsblk -no pkname "$(findmnt -n -o SOURCE /)" 2>/dev/null || echo "none")

# Check if /atlas_data is already mounted
if [[ "$INSTALL_MODE" == "update" ]]; then
    # Update mode never touches storage — whatever served the existing
    # install keeps serving it (mounted NVMe or root filesystem).
    log "Update mode — keeping existing storage at $ATLAS_DATA"
    mkdir -p "$ATLAS_DATA"
elif mountpoint -q "$ATLAS_DATA" 2>/dev/null; then
    ATLAS_DEV=$(findmnt -n -o SOURCE "$ATLAS_DATA")
    log "/atlas_data is already mounted on $ATLAS_DEV — skipping format"
else
    # Find NVMe devices that are NOT the root device
    NVME_CANDIDATES=()
    while IFS= read -r line; do
        DEV="/dev/$(echo "$line" | awk '{print $1}')"
        SIZE=$(echo "$line" | awk '{print $2}')
        # Skip the root device
        NAME=$(echo "$line" | awk '{print $1}')
        [[ "$NAME" == "$ROOT_DEV" ]] && continue
        NVME_CANDIDATES+=("$DEV ($SIZE)")
    done < <(lsblk -d -n -o NAME,SIZE,TYPE | awk '$3=="disk" && /nvme/{print}')

    if [[ ${#NVME_CANDIDATES[@]} -eq 0 ]]; then
        warn "No external NVMe drive detected."
        warn "Large data (maps, routing) will be stored on the root filesystem at $ATLAS_DATA."
        warn "~350 GB free recommended (basemap ~126 GB + OSRM ~125 GB + topo/DBs + scratch)."
        if ! confirm "Continue without NVMe?"; then
            die "Install cancelled. Connect an NVMe drive and re-run."
        fi
        mkdir -p "$ATLAS_DATA"
    elif [[ ${#NVME_CANDIDATES[@]} -eq 1 ]]; then
        RAW="${NVME_CANDIDATES[0]}"
        NVME_DEV="${RAW%% *}"
        info "Found NVMe drive: $RAW"
        if confirm "Format $NVME_DEV and mount at $ATLAS_DATA for Atlas data storage?" y; then
            _setup_nvme "$NVME_DEV"
        else
            warn "Skipping NVMe setup — using root filesystem."
            mkdir -p "$ATLAS_DATA"
        fi
    else
        echo "  Multiple NVMe drives found:"
        for i in "${!NVME_CANDIDATES[@]}"; do
            echo "    $((i+1)).  ${NVME_CANDIDATES[$i]}"
        done
        echo "    0.  None — use root filesystem"
        read -rp "  Select drive for Atlas data storage: " CHOICE
        if [[ "$CHOICE" =~ ^[1-9][0-9]*$ ]] && (( CHOICE <= ${#NVME_CANDIDATES[@]} )); then
            RAW="${NVME_CANDIDATES[$((CHOICE-1))]}"
            NVME_DEV="${RAW%% *}"
            _setup_nvme "$NVME_DEV"
        else
            warn "No NVMe selected — using root filesystem."
            mkdir -p "$ATLAS_DATA"
        fi
    fi
fi

APP_DIR="$ATLAS_DATA/atlas-control"
OLLAMA_DATA_DIR="$ATLAS_DATA/ollama"
if id -u ollama >/dev/null 2>&1; then
    OLLAMA_SERVICE_USER="ollama"
    OLLAMA_SERVICE_GROUP="$(id -gn ollama)"
else
    OLLAMA_SERVICE_USER="$ATLAS_USER"
    OLLAMA_SERVICE_GROUP="$ATLAS_USER"
fi

# Create app directory on chosen storage
mkdir -p "$APP_DIR"
chown "$ATLAS_USER:$ATLAS_USER" "$ATLAS_DATA" "$APP_DIR"

# ════════════════════════════════════════════════════════════════════════════
# 2.  DOWNLOAD / UPDATE & COPY APP FILES
# ════════════════════════════════════════════════════════════════════════════
section "App Files"

# Obtain the current Atlas Control source.
#   • Standalone install.sh (curl'd; no app files beside it): shallow-clone
#     the current version into a temp dir and install from there.
#   • Run from a git checkout (including APP_DIR itself on update re-runs):
#     fast-forward to the latest published commit first.  Being offline or
#     having local changes is non-fatal — the local copy installs as-is.
SRC_DIR="$SCRIPT_DIR"
# Never let git hang on a credential prompt during an unattended install.
export GIT_TERMINAL_PROMPT=0
# Token-authenticated URL for private-repo access.  Used for network
# operations only and never echoed, so the token stays out of the log.
GIT_FETCH_URL="$ATLAS_REPO_URL"
if [[ -n "$ATLAS_REPO_TOKEN" ]]; then
    GIT_FETCH_URL="${ATLAS_REPO_URL/https:\/\//https://x-access-token:${ATLAS_REPO_TOKEN}@}"
fi
if [[ ! -f "$SCRIPT_DIR/app.py" ]]; then
    BOOTSTRAP_PKGS=()
    command_exists git   || BOOTSTRAP_PKGS+=(git)
    command_exists rsync || BOOTSTRAP_PKGS+=(rsync)
    if [[ ${#BOOTSTRAP_PKGS[@]} -gt 0 ]]; then
        info "Installing bootstrap packages: ${BOOTSTRAP_PKGS[*]} ..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${BOOTSTRAP_PKGS[@]}" > /dev/null 2>&1
    fi
    CLONE_DIR="$(mktemp -d /tmp/atlas-control-src.XXXXXX)"
    trap 'rm -rf "$CLONE_DIR"; [[ -n "${ATLAS_INSTALL_TMP:-}" ]] && rm -f "$ATLAS_INSTALL_TMP"' EXIT
    info "No app files beside install.sh — downloading current version ..."
    info "  ${ATLAS_REPO_URL} (branch: ${ATLAS_REPO_BRANCH})"
    if ! git clone --depth 1 --branch "$ATLAS_REPO_BRANCH" "$GIT_FETCH_URL" "$CLONE_DIR" 2>/dev/null; then
        die "Could not download Atlas Control from $ATLAS_REPO_URL
    Possible causes:
      • machine is offline — restore connectivity and re-run
      • the repo is private — re-run with a GitHub token:
          sudo ATLAS_REPO_TOKEN=ghp_xxxx bash install.sh
      • or clone the repo manually and run its install.sh instead"
    fi
    # Detach the credentialed URL from the on-disk clone config.
    git -C "$CLONE_DIR" remote set-url origin "$ATLAS_REPO_URL"
    SRC_DIR="$CLONE_DIR"
    log "Downloaded $(git -C "$CLONE_DIR" rev-parse --short HEAD) ($ATLAS_REPO_BRANCH)"
elif [[ -d "$SCRIPT_DIR/.git" ]] && command_exists git; then
    info "Checking for a newer version of Atlas Control ..."
    REPO_OWNER="$(stat -c %U "$SCRIPT_DIR" 2>/dev/null || echo "$ATLAS_USER")"
    if sudo -u "$REPO_OWNER" GIT_TERMINAL_PROMPT=0 \
        git -C "$SCRIPT_DIR" pull --ff-only "$GIT_FETCH_URL" "$ATLAS_REPO_BRANCH" 2>/dev/null; then
        log "Source at $(sudo -u "$REPO_OWNER" git -C "$SCRIPT_DIR" rev-parse --short HEAD) ($ATLAS_REPO_BRANCH)"
        # If the pull brought a newer install.sh, hand off to it so the update
        # runs with the latest install logic (we're executing a pre-pull temp
        # copy).  The relaunched script pulls again, finds itself current, and
        # passes this check — no loop.
        if ! cmp -s "$SCRIPT_DIR/install.sh" "$ATLAS_INSTALL_TMP"; then
            warn "install.sh itself was updated — relaunching the new installer ..."
            rm -f "$ATLAS_INSTALL_TMP"
            ATLAS_INSTALL_SELF= ATLAS_INSTALL_TMP= exec bash "$SCRIPT_DIR/install.sh" "--$INSTALL_MODE"
        fi
    else
        warn "Could not update from $ATLAS_REPO_URL (offline, private repo without ATLAS_REPO_TOKEN, or local changes) — installing the local copy as-is"
    fi
fi

if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
    info "Copying app files to $APP_DIR ..."
    # Excludes protect everything created at runtime or by later install
    # steps from being clobbered by an update:
    #   venv/ osrm-data/ tiles/        — rebuilt/downloaded artifacts
    #   data/meshtastic.db*            — the live database + its WAL/SHM
    #   data/atlas.db* / atlas.db      — legacy db files (atlas.db is tracked
    #                                    in git but unused at runtime)
    #   .secret_key / hotspot_config   — per-device secrets & credentials
    #   osrm_active.json / logs        — runtime state
    rsync -a --exclude='.git/' \
              --exclude='venv/' --exclude='__pycache__/' \
              --exclude='*.pyc' --exclude='*.log' --exclude='logs/' \
              --exclude='osrm-data/' --exclude='static/tiles/' \
              --exclude='data/atlas.db*' --exclude='data/meshtastic.db*' \
              --exclude='atlas.db' \
              --exclude='data/.secret_key' \
              --exclude='hotspot_config.json' \
              --exclude='osrm_active.json' \
              "$SRC_DIR/" "$APP_DIR/"
    log "App files copied"
    # Standalone install: keep the clone's .git in APP_DIR so future re-runs
    # of $APP_DIR/install.sh can fast-forward to the current version.
    if [[ "$SRC_DIR" == "${CLONE_DIR:-}" && ! -d "$APP_DIR/.git" ]]; then
        mv "$CLONE_DIR/.git" "$APP_DIR/.git"
        log "Install directory linked to $ATLAS_REPO_URL for future updates"
    fi
else
    log "Running from install location — no copy needed"
fi

# A fresh checkout can carry stale SQLite WAL/SHM files that were committed
# to the repo.  Without their matching .db they would corrupt the brand-new
# database on first run — remove them.
for stale in "$APP_DIR/data/meshtastic.db-wal" "$APP_DIR/data/meshtastic.db-shm"; do
    if [[ -f "$stale" && ! -f "$APP_DIR/data/meshtastic.db" ]]; then
        rm -f "$stale"
        warn "Removed stale $(basename "$stale") (no matching database)"
    fi
done

# Create required directories
mkdir -p "$APP_DIR/data" \
         "$APP_DIR/static/fonts" \
         "$APP_DIR/static/tiles" \
         "$APP_DIR/static/data" \
         "$APP_DIR/osrm-data/states" \
         "$APP_DIR/logs" \
         "$OLLAMA_DATA_DIR/models"

chown -R "$ATLAS_USER:$ATLAS_USER" "$APP_DIR"
chown -R "$OLLAMA_SERVICE_USER:$OLLAMA_SERVICE_GROUP" "$OLLAMA_DATA_DIR"
chmod 755 "$ATLAS_DATA" "$OLLAMA_DATA_DIR" "$OLLAMA_DATA_DIR/models"

# Symlink ~/atlas-control → /atlas_data/atlas-control for convenience
LINK="$ATLAS_HOME/atlas-control"
if [[ "$APP_DIR" != "$LINK" ]]; then
    if [[ -L "$LINK" || -f "$LINK" ]]; then
        rm -f "$LINK"
    elif [[ -d "$LINK" ]]; then
        mv "$LINK" "${LINK}.bak"
    fi
    ln -s "$APP_DIR" "$LINK"
    chown -h "$ATLAS_USER:$ATLAS_USER" "$LINK"
    log "Symlink: $LINK → $APP_DIR"
fi

# ════════════════════════════════════════════════════════════════════════════
# 3.  SYSTEM DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════
section "System Dependencies"

PKGS=(
    python3 python3-pip python3-venv
    python3-dev
    python3-dbus
    python3-gi
    python3-smbus
    nginx
    openssl
    smartmontools
    wget curl
    rsync
    sudo
    git cmake
    build-essential
    gfortran
    libboost-all-dev
    libtbb-dev
    libluajit-5.1-dev
    libopenblas-dev
    libxml2-dev
    libzip-dev
    libosmpbf-dev
    libprotobuf-dev
    pkg-config
    libbz2-dev
    osmium-tool
    sqlite3
    parted
    e2fsprogs
    avahi-daemon
    bluez
    network-manager
    i2c-tools
    nodejs
)

if [[ "$INSTALL_MODE" == "update" ]]; then
    # Only fetch packages an update introduced — keeps the quick path quick
    # while still letting new releases add system dependencies.
    MISSING_PKGS=()
    for p in "${PKGS[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 || MISSING_PKGS+=("$p")
    done
    if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
        info "Installing new packages: ${MISSING_PKGS[*]} ..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PKGS[@]}" > /dev/null 2>&1
        log "New system packages installed"
    else
        log "System packages already present"
    fi
else
    info "Updating package lists ..."
    apt-get update -qq
    info "Installing packages ..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PKGS[@]}" > /dev/null 2>&1
    log "System packages installed"
fi

for cmd in rsync visudo nmcli osmium smartctl systemctl bluetoothctl; do
    command_exists "$cmd" || die "Required command missing after apt install: $cmd"
done
log "Required system commands verified"

# Serial port access for ATLAS_USER
if ! groups "$ATLAS_USER" | grep -q dialout; then
    usermod -a -G dialout "$ATLAS_USER"
    log "Added $ATLAS_USER to dialout group"
fi

# I2C access for optional Waveshare UPS telemetry
if getent group i2c >/dev/null 2>&1 && ! groups "$ATLAS_USER" | grep -q '\bi2c\b'; then
    usermod -a -G i2c "$ATLAS_USER"
    log "Added $ATLAS_USER to i2c group"
fi

# NVMe smartctl sudo rule
SUDOERS_ATLAS="/etc/sudoers.d/atlas-smartctl"
echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/sbin/smartctl" > "$SUDOERS_ATLAS"
chmod 440 "$SUDOERS_ATLAS"
visudo -c -f "$SUDOERS_ATLAS" > /dev/null
log "sudoers: smartctl access granted for NVMe monitoring"

# nmcli sudo rule — required for WiFi connect / hotspot management
SUDOERS_NM="/etc/sudoers.d/atlas-nmcli"
echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/bin/nmcli" > "$SUDOERS_NM"
chmod 440 "$SUDOERS_NM"
visudo -c -f "$SUDOERS_NM" > /dev/null
log "sudoers: nmcli access granted for WiFi management"

# avahi sudo rule — Atlas reloads avahi-daemon after a WiFi switch so mDNS
# announcements are re-broadcast on the new interface immediately rather
# than at the next periodic refresh (which can leave mobile apps unable to
# rediscover Atlas on a brand-new LAN for 30+ seconds).
SUDOERS_AVAHI="/etc/sudoers.d/atlas-avahi"
{
    echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload avahi-daemon"
    echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload-or-restart avahi-daemon"
    echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/sbin/avahi-daemon --reload"
} > "$SUDOERS_AVAHI"
chmod 440 "$SUDOERS_AVAHI"
visudo -c -f "$SUDOERS_AVAHI" > /dev/null
log "sudoers: avahi reload access granted for mDNS refresh after WiFi switch"

# Atlas needs a stable hostname so atlas.local resolves consistently via mDNS
if [[ "$(hostnamectl --static 2>/dev/null || hostname)" != "atlas" ]]; then
    hostnamectl set-hostname atlas
    log "System hostname set to atlas"
fi

# mDNS hygiene — by default avahi advertises atlas.local on EVERY interface,
# including docker0 (172.17.0.1) and the USB-gadget link-locals.  A phone that
# resolves atlas.local then gets handed one of those unreachable addresses and
# the connection silently fails ("worked earlier" = before docker0 / USB came
# up).  Restrict announcements to the real client-facing interfaces (WiFi +
# the L4T USB-ethernet bridge) and force IPv4 so clients don't latch onto a
# rotating privacy IPv6 either.
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
WIFI_IF=$(for w in /sys/class/net/*/wireless; do basename "$(dirname "$w")"; done 2>/dev/null | head -1)
AVAHI_IFACES="${WIFI_IF:-wlan0}"
[[ -d /sys/class/net/l4tbr0 ]] && AVAHI_IFACES="${AVAHI_IFACES},l4tbr0"
sed -i 's/^[#[:space:]]*use-ipv6=.*/use-ipv6=no/' "$AVAHI_CONF"
if grep -q '^[#[:space:]]*allow-interfaces=' "$AVAHI_CONF"; then
    sed -i "s/^[#[:space:]]*allow-interfaces=.*/allow-interfaces=${AVAHI_IFACES}/" "$AVAHI_CONF"
else
    sed -i "/^\[server\]/a allow-interfaces=${AVAHI_IFACES}" "$AVAHI_CONF"
fi
log "avahi restricted to ${AVAHI_IFACES} (IPv4) — atlas.local won't resolve to docker0/USB"

systemctl enable --now avahi-daemon
log "avahi-daemon enabled (atlas.local via mDNS)"

# Advertise Atlas via mDNS / DNS-SD so Android NSD and iOS Bonjour can discover
# Atlas on any LAN it joins without knowing the IP in advance.  We advertise
# BOTH the nginx HTTPS endpoint (443) AND the Flask direct endpoint (5000) so
# either lookup short-circuits — apps that browse `_https._tcp` get a hit
# immediately instead of waiting the full 8 s service-browse timeout for
# nothing and falling back to `_http._tcp`.
mkdir -p /etc/avahi/services
cat > /etc/avahi/services/atlas-http.service << 'AVAHI_EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Atlas Control on %h</name>
  <service>
    <type>_https._tcp</type>
    <port>443</port>
    <txt-record>path=/api/device</txt-record>
    <txt-record>app=atlas-control</txt-record>
  </service>
  <service>
    <type>_http._tcp</type>
    <port>5000</port>
    <txt-record>path=/api/device</txt-record>
    <txt-record>app=atlas-control</txt-record>
  </service>
</service-group>
AVAHI_EOF
log "avahi service file created (_https._tcp:443 + _http._tcp:5000 — DNS-SD discovery)"

# Avahi normally re-announces on interface change, but on a brand-new LAN the
# initial announcement timing is non-deterministic — sometimes it lands in the
# 3 s gap between mobile discovery passes and the apps miss it.  Reload now so
# the service files above take effect even on existing installs without a
# full systemd restart.
systemctl reload-or-restart avahi-daemon || true

systemctl enable --now bluetooth
log "bluetooth service enabled"

# ════════════════════════════════════════════════════════════════════════════
# 4.  PYTHON VIRTUAL ENVIRONMENT
# ════════════════════════════════════════════════════════════════════════════
section "Python Environment"

VENV="$APP_DIR/venv"
VENV_CFG="$VENV/pyvenv.cfg"
REBUILD_VENV=0
if [[ -x "$VENV/bin/python3" && -f "$VENV_CFG" ]]; then
    if ! grep -q '^include-system-site-packages = true$' "$VENV_CFG"; then
        warn "Existing virtual environment does not expose system site packages; rebuilding for Bluetooth support"
        REBUILD_VENV=1
    fi
fi

if [[ $REBUILD_VENV -eq 1 ]]; then
    rm -rf "$VENV"
fi

if [[ ! -x "$VENV/bin/python3" ]]; then
    info "Creating virtual environment ..."
    sudo -u "$ATLAS_USER" python3 -m venv --system-site-packages "$VENV"
fi

info "Installing Python packages ..."
sudo -u "$ATLAS_USER" "$VENV/bin/pip" install --upgrade pip -q
sudo -u "$ATLAS_USER" "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q
sudo -u "$ATLAS_USER" "$VENV/bin/python3" - <<'PYEOF'
import importlib
mods = [
    "flask",
    "flask_socketio",
    "flask_limiter",
    "gevent",
    "meshtastic",
    "numpy",
    "psutil",
    "pubsub",
    "pynmea2",
    "serial",
    "qrcode",
    "smbus",
]
missing = []
for mod in mods:
    try:
        importlib.import_module(mod)
    except Exception as exc:
        missing.append(f"{mod}: {exc}")
if missing:
    raise SystemExit("Python dependency verification failed:\n" + "\n".join(missing))
print("Python dependency verification passed")
PYEOF

sudo -u "$ATLAS_USER" APP_DIR="$APP_DIR" "$VENV/bin/python3" - <<'PYEOF'
import importlib.util
import os
import sys

app_dir = os.environ["APP_DIR"]
sys.path.insert(0, app_dir)
import mobile_bridge  # noqa: F401

missing = []
for mod in ("dbus", "gi"):
    if importlib.util.find_spec(mod) is None:
        missing.append(mod)

if missing:
    raise SystemExit("Bluetooth bootstrap dependency verification failed:\n" + "\n".join(missing))

print("Bluetooth bootstrap dependency verification passed")
PYEOF
log "Python environment ready"

# ── Pre-compile the mobile JSX bundle ────────────────────────────────────────
# app.py serves a lightweight precompiled page to the mobile WebView via
# /static/app.compiled.js (the in-browser Babel fallback can blank Android
# WebView).  app.py rebuilds this on demand at runtime whenever index.html
# changes, but compiling it now means the bundle is warm before the first
# mobile client connects.  Mirrors app.py._precompile_jsx exactly.  Non-fatal:
# if node/compile fails, the runtime rebuild path still covers it.
if command_exists node && [[ -f "$APP_DIR/compile_jsx.js" ]]; then
    info "Pre-compiling mobile JSX bundle ..."
    if sudo -u "$ATLAS_USER" "$VENV/bin/python3" - "$APP_DIR" <<'PYEOF'
import os, re, subprocess, sys, tempfile
base = sys.argv[1]
html = open(os.path.join(base, "templates", "index.html"), encoding="utf-8").read()
m = re.search(r'<script type="text/babel">(.*?)</script>', html, re.DOTALL)
if not m:
    print("no JSX block found; runtime rebuild will handle it")
    sys.exit(0)
with tempfile.NamedTemporaryFile(suffix=".jsx", mode="w", encoding="utf-8", delete=False) as t:
    t.write(m.group(1)); tmp = t.name
out = os.path.join(base, "static", "app.compiled.js")
r = subprocess.run(["node", os.path.join(base, "compile_jsx.js"), tmp, out],
                   capture_output=True, text=True, timeout=180)
os.unlink(tmp)
print((r.stdout or r.stderr).strip()[:300])
sys.exit(r.returncode)
PYEOF
    then
        log "Mobile JSX bundle compiled (static/app.compiled.js)"
    else
        warn "JSX pre-compile failed — app.py will retry at runtime"
    fi
else
    warn "node or compile_jsx.js missing — mobile bundle will build at runtime"
fi

# ════════════════════════════════════════════════════════════════════════════
# 5–8.  PROVISIONING (full install only)
#       Ollama · OSRM build · map data · state routing data
#       These are one-time provisioning steps; quick updates leave the
#       existing AI engine, maps, and routing data untouched.
# ════════════════════════════════════════════════════════════════════════════
STATE_INPUT="keep"
if [[ "$INSTALL_MODE" == "update" ]]; then
    section "Provisioning (Ollama / OSRM / Map & Routing Data)"
    log "Update mode — existing Ollama, OSRM, map and routing data left untouched"
else

# ════════════════════════════════════════════════════════════════════════════
# 5.  OLLAMA (AI / LLM)
# ════════════════════════════════════════════════════════════════════════════
section "Ollama AI Engine"

if command -v ollama &>/dev/null; then
    log "Ollama already installed: $(ollama --version 2>/dev/null | head -1)"
else
    info "Downloading and installing Ollama ..."
    curl -fsSL https://ollama.com/install.sh | OLLAMA_INSTALL_DIR=/usr/local/bin sh
    log "Ollama installed"
fi
# Service file is written in section 10 alongside atlas-control.service

# ════════════════════════════════════════════════════════════════════════════
# 6.  OSRM ROUTING ENGINE (native build from source)
# ════════════════════════════════════════════════════════════════════════════
section "OSRM Routing Engine"

if command -v osrm-routed &>/dev/null; then
    log "osrm-routed already installed: $(osrm-routed --version 2>/dev/null | head -1)"
else
    info "Building OSRM from source (this takes 20–40 minutes on Jetson) ..."
    bash "$APP_DIR/setup_native_osrm.sh" --no-cleanup-prompt
    log "OSRM routing engine built and installed"
fi

# ════════════════════════════════════════════════════════════════════════════
# 7.  MAP DATA
# ════════════════════════════════════════════════════════════════════════════
section "Map Data"

TILES_DIR="$APP_DIR/static/tiles"
BASEMAP_URL="$(resolve_latest_protomaps_url)"
log "Using Protomaps basemap: ${BASEMAP_URL##*/}"

# ── 7a. PMTiles CLI (go-pmtiles, ARM64 binary) ───────────────────────────────
PMTILES_BIN="$APP_DIR/pmtiles"
if [[ -x "$PMTILES_BIN" ]]; then
    log "pmtiles CLI already present"
else
    info "Downloading pmtiles CLI ..."
    # Fetch the latest release tag from GitHub, fall back to known-good version
    PMTILES_VER=$(curl -sfL "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null \
        || echo "v1.22.3")
    PMTILES_URL="https://github.com/protomaps/go-pmtiles/releases/download/${PMTILES_VER}/go-pmtiles_${PMTILES_VER#v}_Linux_arm64.tar.gz"
    info "  Version: $PMTILES_VER"
    curl -fsSL "$PMTILES_URL" | tar -xz -C "$APP_DIR" pmtiles
    chmod +x "$PMTILES_BIN"
    chown "$ATLAS_USER:$ATLAS_USER" "$PMTILES_BIN"
    log "pmtiles CLI installed ($PMTILES_VER)"
fi

# ── 7b. Vector basemap (Protomaps full-planet build, ~126 GB) ────────────────
BASEMAP="$TILES_DIR/map.pmtiles"
if [[ -f "$BASEMAP" ]]; then
    BSIZE=$(du -sh "$BASEMAP" | cut -f1)
    log "Vector basemap already present ($BSIZE) — skipping download"
else
    # Verify the resolved URL is reachable BEFORE the ~126 GB download so a
    # stale fallback key (Protomaps prunes old builds) or being offline fails
    # fast with a clear message instead of writing a 404 body into the .tmp.
    if ! curl -sfI --max-time 30 "$BASEMAP_URL" >/dev/null 2>&1; then
        die "Basemap URL not reachable: $BASEMAP_URL
    The Protomaps build may have been pruned, or the machine is offline.
    Find a current key at $PROTOMAPS_METADATA_URL, update PROTOMAPS_FALLBACK_KEY
    at the top of this script (or restore connectivity), then re-run."
    fi
    info "Downloading map.pmtiles (~126 GB, full-planet vector basemap) ..."
    info "URL: $BASEMAP_URL"
    sudo -u "$ATLAS_USER" wget -q --show-progress \
        -O "$BASEMAP.tmp" "$BASEMAP_URL" && \
        mv "$BASEMAP.tmp" "$BASEMAP"
    log "Vector basemap downloaded ($(du -sh "$BASEMAP" | cut -f1))"
fi

# ── 7b. Noto Sans glyph PBFs (required for map label rendering) ──────────────
# The font directory contains Unicode range PBF files, not TTF files.
FONT_PBF_COUNT=$(find "$APP_DIR/static/fonts" -name "*.pbf" 2>/dev/null | wc -l || true)
if [[ $FONT_PBF_COUNT -lt 255 ]]; then
    info "Downloading map font glyphs (~8 MB) ..."
    sudo -u "$ATLAS_USER" bash "$APP_DIR/download_fonts.sh"
    log "Font glyphs downloaded"
else
    log "Font glyphs already present ($FONT_PBF_COUNT ranges)"
fi

# ── 7c. Topographic overlay ───────────────────────────────────────────────────
TOPO_PMTILES="$TILES_DIR/topo.pmtiles"
TOPO_TILES="$TILES_DIR/topo"
TOPO_MIN_ZOOM=8
TOPO_MAX_ZOOM=13

mkdir -p "$TOPO_TILES"
chown -R "$ATLAS_USER:$ATLAS_USER" "$TOPO_TILES"

TOPO_FORMAT_INFO="$(
    python3 - "$TOPO_TILES" <<'PYEOF'
import os, sys

tile_dir = sys.argv[1]
counts = {"png": 0, "jpg": 0}
for root, _, files in os.walk(tile_dir):
    for name in files:
        ext = os.path.splitext(name)[1].lower()
        if ext == ".png":
            counts["png"] += 1
        elif ext in (".jpg", ".jpeg"):
            counts["jpg"] += 1

if counts["png"] > 0:
    print(f"purge_png jpg png {counts['jpg']} {counts['png']}")
elif counts["jpg"] > 0:
    print(f"single jpg none {counts['jpg']} 0")
else:
    print("none none none 0 0")
PYEOF
)"
read -r TOPO_FORMAT_STATE TOPO_DOMINANT_FORMAT TOPO_PURGE_FORMAT TOPO_KEEP_COUNT TOPO_PURGE_COUNT <<<"$TOPO_FORMAT_INFO"

if [[ "$TOPO_FORMAT_STATE" == "purge_png" ]]; then
    info "Topo tile cache has PNG files (existing .jpg: $TOPO_KEEP_COUNT, removing .png: $TOPO_PURGE_COUNT) ..."
    sudo -u "$ATLAS_USER" python3 - "$TOPO_TILES" "$TOPO_PURGE_FORMAT" <<'PYEOF'
import os, sys

tile_dir = sys.argv[1]
purge_ext = "." + sys.argv[2]
aliases = {purge_ext}
if purge_ext == ".jpg":
    aliases.add(".jpeg")

removed = 0
for root, _, files in os.walk(tile_dir):
    for name in files:
        if os.path.splitext(name)[1].lower() in aliases:
            os.unlink(os.path.join(root, name))
            removed += 1

print(f"Removed {removed} files")
PYEOF
    info "Refreshing topo cache after format cleanup ..."
    sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_topo.py" \
        --min-zoom 8 --max-zoom 13 \
        --output-dir "$TOPO_TILES" \
        --workers 20 --yes
    log "Topo tile cache normalized to .jpg"
fi

if [[ -f "$TOPO_PMTILES" ]]; then
    log "Topographic PMTiles archive present ($(du -sh "$TOPO_PMTILES" | cut -f1)) — skipping"
else
    # Check for a sufficiently complete tile cache at the target max zoom.
    mkdir -p "$TOPO_TILES"
    TARGET_ZOOM_COUNT=$(find "$TOPO_TILES/$TOPO_MAX_ZOOM" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) 2>/dev/null | wc -l || true)
    if [[ $TARGET_ZOOM_COUNT -lt 1000 ]]; then
        info "Downloading topo tiles (z${TOPO_MIN_ZOOM}–${TOPO_MAX_ZOOM}, all states, ~5–15 GB) ..."
        sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_topo.py" \
            --min-zoom "$TOPO_MIN_ZOOM" --max-zoom "$TOPO_MAX_ZOOM" \
            --output-dir "$TOPO_TILES" \
            --workers 20 --yes
        log "Topo tiles downloaded"
    else
        log "Topo tile cache present (z${TOPO_MAX_ZOOM}: $TARGET_ZOOM_COUNT tiles) — skipping download"
    fi

    info "Building topo.pmtiles ..."
    sudo -u "$ATLAS_USER" bash "$APP_DIR/setup_topo_pmtiles.sh"
    log "topo.pmtiles built ($(du -sh "$TOPO_PMTILES" | cut -f1))"
fi

# ── 7d. City & trail databases ────────────────────────────────────────────────
DATA_DIR="$APP_DIR/static/data"
mkdir -p "$DATA_DIR"

if [[ ! -s "$DATA_DIR/us_cities.db" ]]; then
    info "Downloading US cities database ..."
    sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_cities.py" \
        --output "$DATA_DIR/us_cities.db"
    log "US cities database ready"
else
    log "US cities database present ($(du -sh "$DATA_DIR/us_cities.db" | cut -f1))"
fi

# app.py geocoder checks nps_trails.db first, then falls back to trailheads.db
if [[ ! -s "$DATA_DIR/nps_trails.db" ]]; then
    info "Downloading NPS trails database ..."
    if sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_nps_trails.py"; then
        log "NPS trails database ready"
    elif sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_trailheads.py"; then
        log "Legacy trailheads database ready"
    else
        die "Failed to build trail database"
    fi
else
    log "NPS trails database present ($(du -sh "$DATA_DIR/nps_trails.db" | cut -f1))"
fi

# ════════════════════════════════════════════════════════════════════════════
# 8.  OSRM STATE ROUTING DATA
# ════════════════════════════════════════════════════════════════════════════
section "Routing Data (US States)"

# Geofabrik name mapping  (state abbreviation → Geofabrik download name)
declare -A GF=(
    [AL]=alabama         [AK]=alaska          [AZ]=arizona
    [AR]=arkansas        [CA]=california       [CO]=colorado
    [CT]=connecticut     [DE]=delaware         [FL]=florida
    [GA]=georgia         [HI]=hawaii           [ID]=idaho
    [IL]=illinois        [IN]=indiana          [IA]=iowa
    [KS]=kansas          [KY]=kentucky         [LA]=louisiana
    [ME]=maine           [MD]=maryland         [MA]=massachusetts
    [MI]=michigan        [MN]=minnesota        [MS]=mississippi
    [MO]=missouri        [MT]=montana          [NE]=nebraska
    [NV]=nevada          [NH]=new-hampshire    [NJ]=new-jersey
    [NM]=new-mexico      [NY]=new-york         [NC]=north-carolina
    [ND]=north-dakota    [OH]=ohio             [OK]=oklahoma
    [OR]=oregon          [PA]=pennsylvania     [RI]=rhode-island
    [SC]=south-carolina  [SD]=south-dakota     [TN]=tennessee
    [TX]=texas           [UT]=utah             [VT]=vermont
    [VA]=virginia        [WA]=washington       [WV]=west-virginia
    [WI]=wisconsin       [WY]=wyoming          [DC]=district-of-columbia
)

OSRM_DIR="$APP_DIR/osrm-data"
CAR_LUA="/usr/local/share/osrm/profiles/car.lua"
HIKE_LUA="$APP_DIR/hiking.lua"

_process_state() {
    local abbr="$1"
    local profile="$2"   # car | hiking
    local gf_name="${GF[$abbr]}"
    local state_dir="$OSRM_DIR/states/${gf_name}/${profile}"
    local pbf="$OSRM_DIR/${gf_name}.osm.pbf"

    # Already processed?
    [[ -f "$state_dir/.processed" ]] && return 0

    mkdir -p "$state_dir"

    # Download .pbf — also re-download if a previous attempt left a 0-byte file
    local pbf_size
    pbf_size=$(stat -c%s "$pbf" 2>/dev/null || echo 0)
    if [[ ! -f "$pbf" || "$pbf_size" -eq 0 ]]; then
        [[ "$pbf_size" -eq 0 && -f "$pbf" ]] && rm -f "$pbf"
        info "  Downloading ${abbr} (${gf_name}) ..."
        if ! wget -q --show-progress \
                -O "${pbf}.tmp" \
                "https://download.geofabrik.de/north-america/us/${gf_name}-latest.osm.pbf"; then
            warn "  Download failed for ${abbr} — skipping"
            rm -f "${pbf}.tmp"
            return 1
        fi
        mv "${pbf}.tmp" "$pbf"
        chown "$ATLAS_USER:$ATLAS_USER" "$pbf"
    fi

    # Choose Lua profile
    local lua_file
    if [[ "$profile" == "hiking" ]]; then
        lua_file="$HIKE_LUA"
    else
        lua_file="$CAR_LUA"
    fi

    info "  Processing ${abbr}/${profile} ..."
    cp "$pbf" "$state_dir/region.osm.pbf"

    local extract_log="$state_dir/osrm-extract.log"
    local partition_log="$state_dir/osrm-partition.log"
    local customize_log="$state_dir/osrm-customize.log"

    if ! osrm-extract -p "$lua_file" "$state_dir/region.osm.pbf" > /dev/null 2>"$extract_log"; then
        warn "  osrm-extract failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$extract_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$extract_log"

    if ! osrm-partition "$state_dir/region.osrm" > /dev/null 2>"$partition_log"; then
        warn "  osrm-partition failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$partition_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$partition_log"

    if ! osrm-customize "$state_dir/region.osrm" > /dev/null 2>"$customize_log"; then
        warn "  osrm-customize failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$customize_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$customize_log"

    rm -f "$state_dir/region.osm.pbf"
    touch "$state_dir/.processed"
    chown -R "$ATLAS_USER:$ATLAS_USER" "$state_dir"
    log "  ${abbr}/${profile} ready"
}

echo ""
echo -e "  Atlas Control supports offline routing for all 50 US states."
echo -e "  Processing all states takes ${YLW}many hours${NC} and ~220 GB of storage."
echo ""
echo -e "  Options:"
echo -e "    ${BLD}all${NC}    — All 50 states + DC (full national coverage)"
echo -e "    ${BLD}STATE${NC}  — Space-separated abbreviations, e.g: MD VA WV PA"
echo -e "    ${BLD}none${NC}   — Skip now; run setup_osrm_all.sh later"
echo ""
read -rp "  States to install [all]: " STATE_INPUT
STATE_INPUT="${STATE_INPUT:-all}"

if [[ "${STATE_INPUT,,}" == "none" ]]; then
    warn "Routing data skipped. Add states later by re-running this script."
elif [[ "${STATE_INPUT,,}" == "all" ]]; then
    STATES_TO_PROCESS=("${!GF[@]}")
    info "Downloading and processing all 50 states + DC (this takes many hours) ..."
    for abbr in "${STATES_TO_PROCESS[@]}"; do
        for profile in "${ROUTING_PROFILES[@]}"; do
            _process_state "$abbr" "$profile" || warn "  ${abbr}/${profile} failed — continuing"
        done
    done
    log "State routing data processing complete"
else
    for abbr in $STATE_INPUT; do
        abbr="${abbr^^}"
        if [[ -v GF[$abbr] ]]; then
            for profile in "${ROUTING_PROFILES[@]}"; do
                _process_state "$abbr" "$profile" || warn "  ${abbr}/${profile} failed — continuing"
            done
        else
            warn "Unknown state: $abbr — skipping"
        fi
    done
fi

fi  # end full-install-only provisioning (sections 5–8)

# ════════════════════════════════════════════════════════════════════════════
# 9.  HTTPS / NGINX
# ════════════════════════════════════════════════════════════════════════════
section "HTTPS / Nginx"

SSL_DIR="/etc/ssl/atlas"
CERT="$SSL_DIR/cert.pem"
KEY="$SSL_DIR/key.pem"
mkdir -p "$SSL_DIR"

if [[ ! -f "$CERT" ]]; then
    info "Generating self-signed TLS certificate ..."
    LAN_IP=$(hostname -I | tr ' ' '\n' | grep -v '^10\.' | grep -v '^127\.' | head -1 || echo "127.0.0.1")
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$KEY" -out "$CERT" -days 3650 \
        -subj "/CN=atlas" \
        -addext "subjectAltName=DNS:atlas.local,DNS:localhost,IP:${LAN_IP},IP:10.42.0.1,IP:127.0.0.1" \
        2>/dev/null
    chmod 600 "$KEY"
    chmod 644 "$CERT"
    # Copy back to app dir so setup_https.sh finds them if re-run
    cp "$CERT" "$APP_DIR/cert.pem"
    cp "$KEY"  "$APP_DIR/key.pem"
    log "TLS certificate generated (valid 10 years)"
else
    log "TLS certificate already present"
fi

# Install nginx config (symlink to the repo file so updates apply immediately)
NGINX_SITE="/etc/nginx/sites-available/atlas"
cp "$APP_DIR/nginx_atlas.conf" "$NGINX_SITE"
# Patch the tile root paths to the actual install location
sed -i "s|/atlas_data/atlas-control/static|$APP_DIR/static|g" "$NGINX_SITE"

ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/atlas
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable --now nginx
# enable --now is a no-op when nginx is already running — reload so config
# changes shipped by an update actually take effect.
systemctl reload-or-restart nginx
log "Nginx configured (HTTPS :443 → Flask :5000)"

# ════════════════════════════════════════════════════════════════════════════
# 10.  SYSTEMD SERVICES
# ════════════════════════════════════════════════════════════════════════════
section "Systemd Services"

# Generate service files from the repo templates by substituting placeholders
sed "s|ATLAS_USER|$ATLAS_USER|g; s|APP_DIR|$APP_DIR|g" \
    "$APP_DIR/atlas-control.service" > /etc/systemd/system/atlas-control.service

sed "s|OLLAMA_USER|$OLLAMA_SERVICE_USER|g; s|OLLAMA_GROUP|$OLLAMA_SERVICE_GROUP|g; s|OLLAMA_DATA_DIR|$OLLAMA_DATA_DIR|g" \
    "$APP_DIR/ollama.service" > /etc/systemd/system/ollama.service

systemctl daemon-reload
systemctl enable atlas-control ollama
log "Services enabled: atlas-control, ollama"

# Web-app-triggered updates: install the root-owned launcher the Flask app
# invokes via sudo.  It starts install.sh --update as a transient systemd
# unit so the update survives atlas-control restarting itself mid-update.
sed "s|__APP_DIR__|$APP_DIR|g" "$APP_DIR/atlas_update_launcher.sh" > /usr/local/sbin/atlas-update
chown root:root /usr/local/sbin/atlas-update
chmod 755 /usr/local/sbin/atlas-update
SUDOERS_UPDATE="/etc/sudoers.d/atlas-update"
echo "$ATLAS_USER ALL=(ALL) NOPASSWD: /usr/local/sbin/atlas-update" > "$SUDOERS_UPDATE"
chmod 440 "$SUDOERS_UPDATE"
visudo -c -f "$SUDOERS_UPDATE" > /dev/null
log "Update launcher installed (/usr/local/sbin/atlas-update — used by the web UI)"
log "Atlas UPS defaults: I2C bus 7, address 0x41 (Waveshare UPS Power Module C)"

# ════════════════════════════════════════════════════════════════════════════
# 11.  UDEV HARDWARE DEVICE RULES
# ════════════════════════════════════════════════════════════════════════════
section "Hardware Device Rules"

cp "$APP_DIR/99-atlas-devices.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger
log "udev rules installed (Meshtastic → /dev/meshtastic, GPS → /dev/gps)"

# ════════════════════════════════════════════════════════════════════════════
# 12.  FIRST-RUN INITIALIZATION
# ════════════════════════════════════════════════════════════════════════════
section "First-Run Initialization"

# Generate Flask secret key
SECRET="$APP_DIR/data/.secret_key"
if [[ ! -f "$SECRET" ]]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > "$SECRET"
    chmod 600 "$SECRET"
    chown "$ATLAS_USER:$ATLAS_USER" "$SECRET"
    log "Flask secret key generated"
fi

# Seed a default hotspot config so the WiFi→hotspot failover works out of the
# box.  Without a saved SSID + >=8-char password, _start_hotspot_with_saved_config
# refuses to start the AP, leaving Atlas unreachable when it can't join a LAN.
# Only create it if absent so we never clobber the user's chosen credentials.
HOTSPOT_CFG="$APP_DIR/hotspot_config.json"
if [[ ! -f "$HOTSPOT_CFG" ]]; then
    printf '{"ssid": "Atlas-Hotspot", "password": "AtlasControl"}\n' > "$HOTSPOT_CFG"
    chmod 600 "$HOTSPOT_CFG"
    chown "$ATLAS_USER:$ATLAS_USER" "$HOTSPOT_CFG"
    log "Default hotspot seeded (SSID: Atlas-Hotspot / pass: AtlasControl — change in the app)"
else
    log "Hotspot config already present — leaving as-is"
fi

# Initialize SQLite database
sudo -u "$ATLAS_USER" "$VENV/bin/python3" -c "
import sys; sys.path.insert(0, '$APP_DIR')
import database; database.init_db()
print('Database initialized')
"
log "SQLite database initialized"

sudo -u "$ATLAS_USER" "$VENV/bin/python3" -c "
import sys; sys.path.insert(0, '$APP_DIR')
import database
s = database.get_app_settings()
updates = {}
if s.get('serial_port') in (None, '', '/dev/ttyACM1'):
    updates['serial_port'] = 'AUTO'
if s.get('gps_port') in (None, '', '/dev/ttyACM0'):
    updates['gps_port'] = 'AUTO'
if updates:
    database.set_app_settings(updates)
    print(f'Updated legacy port defaults: {updates}')
else:
    print('Port defaults already normalized')
"
log "Serial port settings normalized"

sudo -u "$ATLAS_USER" "$VENV/bin/python3" -c "
import sys; sys.path.insert(0, '$APP_DIR')
import database
s = database.ai_get_settings()
updates = {}
if s.get('model') in (None, '', 'llama3.2:3b', 'qwen3:4b', 'qwen2.5:3b', 'qwen3.5:4b'):
    updates['model'] = 'qwen3.5:2b'
    # Qwen3-family sampling: old low-temp values cause repetition loops
    updates['temperature'] = '0.7'
    updates['top_p'] = '0.8'
    updates['top_k'] = '20'
    updates['num_gpu'] = '-1'
# Stale 2048-token context truncates the system prompt once RAG + history land
if s.get('num_ctx') == '2048':
    updates['num_ctx'] = '4096'
# Migrate the embedder to qwen3-embedding:0.6b. nomic (768-dim) and qwen3
# (1024-dim) vectors are incompatible, so any change wipes stored doc
# embeddings — the app re-embeds them on next startup.
if s.get('embed_model') in (None, '', 'nomic-embed-text'):
    if s.get('embed_model') != 'qwen3-embedding:0.6b':
        updates['embed_model'] = 'qwen3-embedding:0.6b'
        conn = database.get_db()
        n = conn.execute('UPDATE ai_documents SET embedding=NULL').rowcount
        conn.commit()
        print(f'Cleared {n} stale doc embedding(s) for re-embed')
if updates:
    database.ai_set_settings(updates)
    print(f'Updated AI defaults: {updates}')
else:
    print('AI defaults already normalized')
"
log "AI settings normalized"

# Fix all ownership
chown -R "$ATLAS_USER:$ATLAS_USER" "$APP_DIR"

# Start services (restart so an update re-run replaces the running code)
systemctl restart ollama || true
if systemctl is-active --quiet ollama; then
    info "Waiting for Ollama API ..."
    for _ in {1..30}; do
        if curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done

    if curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
        log "Ollama API is responding"
        for model in "$OLLAMA_CHAT_MODEL" "$OLLAMA_EMBED_MODEL"; do
            if ollama show "$model" >/dev/null 2>&1; then
                log "Ollama model present: $model"
            else
                info "Pulling Ollama model: $model"
                # Never fatal: an off-grid box can't pull, and a failed pull
                # must not abort the update before services restart.
                if ollama pull "$model"; then
                    log "Ollama model ready: $model"
                else
                    warn "Could not pull $model (offline?) — Ray needs it; pull manually when online"
                fi
            fi
        done
    else
        warn "Ollama service started but API did not become ready; skipping model pull"
    fi
else
    warn "Ollama service did not start; skipping model pull"
fi

systemctl restart atlas-control
log "Services started"

# Record the installed version — re-runs of install.sh use this marker to
# offer the quick-update path, and the app can surface it as a version string.
ATLAS_REV="$(sudo -u "$ATLAS_USER" git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "${ATLAS_REV} $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$APP_DIR/.atlas_installed"
chown "$ATLAS_USER:$ATLAS_USER" "$APP_DIR/.atlas_installed"

# ════════════════════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLD}${GRN}══════════════════════════════════════════════════════════${NC}"
if [[ "$INSTALL_MODE" == "update" ]]; then
    echo -e "${BLD}${GRN}  Atlas Control updated to ${ATLAS_REV}!${NC}"
else
    echo -e "${BLD}${GRN}  Atlas Control installation complete! (${ATLAS_REV})${NC}"
fi
echo -e "${GRN}══════════════════════════════════════════════════════════${NC}"
echo ""
LAN_ACCESS_IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -Ev '^(10\.13\.13\.|127\.)' | head -1 || true)
[[ -z "${LAN_ACCESS_IP:-}" ]] && LAN_ACCESS_IP="127.0.0.1"
echo -e "  ${BLD}Data storage :${NC} $APP_DIR"
USED=$(df -h "$ATLAS_DATA" | tail -1 | awk '{print $3"/"$2" used"}')
echo -e "  ${BLD}Disk usage   :${NC} $USED"
echo ""
echo -e "  ${CYN}Access Atlas Control:${NC}"
echo -e "    On your network : ${BLD}https://atlas.local${NC}"
echo -e "    Direct LAN IP   : ${BLD}https://${LAN_ACCESS_IP}${NC}"
echo -e "    Locally         : ${BLD}https://localhost${NC}"
echo ""
echo -e "  ${CYN}Service management:${NC}"
echo -e "    sudo systemctl status  atlas-control"
echo -e "    sudo systemctl restart atlas-control"
echo -e "    sudo journalctl -u atlas-control -f"
echo ""
echo -e "  ${YLW}First visit:${NC} browser will warn about the self-signed certificate."
echo -e "  Click 'Advanced' → 'Proceed to site' — required once per device."
echo -e "  If ${BLD}atlas.local${NC} does not resolve on your phone or laptop, use the direct IP above."
echo ""
if [[ "$INSTALL_MODE" == "full" && "${STATE_INPUT,,}" == "none" ]]; then
    echo -e "  ${YLW}Routing data not installed.${NC} Add states:"
    echo -e "    sudo $APP_DIR/install.sh --full   (re-run and choose states)"
    echo ""
fi
echo -e "  ${CYN}Updating later:${NC} ${BLD}sudo $APP_DIR/install.sh --update${NC}"
echo -e "  pulls the latest Atlas Control release and restarts — data, maps,"
echo -e "  and settings are preserved."
echo ""

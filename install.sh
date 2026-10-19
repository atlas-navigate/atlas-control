#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║              ATLAS CONTROL — UNIT INSTALLER                              ║
# ║              Jetson Orin Nano (ARM64) · Ubuntu 22.04 / 24.04             ║
# ║                                                                          ║
# ║  The technician's step after the data payload is on /atlas_data          ║
# ║  (tools/manufacturing/atlas-provision.sh runs it):                       ║
# ║                                                                          ║
# ║  Usage:  sudo ./install.sh --bundle DIR [--labels DIR] [--dev-key PUB]   ║
# ║            --bundle DIR   a signed Atlas release (atlas-release.json,    ║
# ║                           its .sig and the bundle)                       ║
# ║            --labels DIR   where to write this unit's label files         ║
# ║            --dev-key PUB  developer unit: trust this key, keep SSH,      ║
# ║                           skip retail lock-down (never for sold units)   ║
# ║                                                                          ║
# ║  What this does (every data step is skipped when its data is present):   ║
# ║    1.  /atlas_data storage and system packages                            ║
# ║    2.  Python environment for the dataset builders                       ║
# ║    3.  Ollama, OSRM, basemap, topo, cities/trails/places, state routing  ║
# ║    4.  The Atlas software from the signed release, through its update   ║
# ║        engine: services, nginx, firewall, udev, mDNS — no git, no sudo   ║
# ║    5.  This unit's own identity (name, TLS key, hotspot, setup code)     ║
# ║        and its printable label                                           ║
# ║    6.  Retail lock-down (tools/manufacturing/finalize-retail.sh)         ║
# ║                                                                          ║
# ║  Software updates never use this script: Atlas installs signed releases ║
# ║  itself (Settings → Software Update).                                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -euo pipefail
# A bare `set -e` death is easy to miss in a long log; report it. The [✗]
# marker is what atlas-provision.sh and a technician look for.
set -E
# Subshell filter: $(...) substitution failures fire ERR too but don't kill
# the script — only report the top-level failure that actually aborts.
trap '(( BASH_SUBSHELL == 0 )) && echo -e "\033[0;31m[✗]\033[0m install.sh aborted (line $LINENO: $BASH_COMMAND)" >&2 || true' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# Ray's models (Qwen2.5 3B, Qwen3.5 4B and the embedder), the Ollama version
# they need and the build that suits this hardware all come from the release's
# ai_models.json, read through model_profiles.py (sections 5 and 12).

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

# Datasets belong to the setup account; data/ logs/ updates/ cache/ belong to
# the 'atlas' service account once Atlas is installed — never take them back.
# The service account must still be able to read the datasets: osrm-extract
# writes each graph's .fileIndex as 0700, and with it unreadable osrm-routed
# exits at once and every route falls back to a straight line.
own_datasets() {
    if id -u atlas >/dev/null 2>&1; then
        find "$APP_DIR" \( -path "$APP_DIR/data" -o -path "$APP_DIR/logs" -o -path "$APP_DIR/updates" \
             -o -path "$APP_DIR/cache" \) -prune -o -exec chown -h "$ATLAS_USER:$ATLAS_USER" {} +
    else
        chown -R "$ATLAS_USER:$ATLAS_USER" "$APP_DIR"
    fi
    if [[ -d "$APP_DIR/osrm-data" ]]; then
        chmod -R a+rX "$APP_DIR/osrm-data"
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

# ── Options ─────────────────────────────────────────────────────────────────
BUNDLE=""
LABELS_DIR=""
DEV_KEY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle)  BUNDLE="${2:-}"; shift 2 ;;
        --labels)  LABELS_DIR="${2:-}"; shift 2 ;;
        --dev-key) DEV_KEY="${2:-}"; shift 2 ;;
        --full|--fresh) shift ;;
        --update|update)
            die "Atlas updates itself from signed releases (Settings → Software Update);
    install.sh only sets up units. On a developer unit use tools/dev/deploy." ;;
        -h|--help)
            sed -n '3,30p' "$SCRIPT_DIR/install.sh" | sed 's/^# ║ \{0,2\}//; s/ *║$//'
            exit 0 ;;
        *) die "Unknown option: $1  (try --help)" ;;
    esac
done
[[ -n "$BUNDLE" && -f "$BUNDLE/atlas-release.json" ]] \
    || die "Give the signed Atlas release to install:  sudo ./install.sh --bundle DIR"
BUNDLE="$(cd "$BUNDLE" && pwd)"
[[ -z "$DEV_KEY" || -f "$DEV_KEY" ]] || die "Developer key not found: $DEV_KEY"

# ── Must run as root ────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run with sudo:  sudo ./install.sh"
# The setup account (admin): owns the datasets and the dataset builders.
# Atlas itself runs as the unprivileged 'atlas' account created in step 4.
ATLAS_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [[ -z "$ATLAS_USER" ]] || ! getent passwd "$ATLAS_USER" > /dev/null; then
    ATLAS_USER="$(stat -c %U /atlas_data/atlas-control 2>/dev/null || echo ubuntu)"
fi
ATLAS_HOME="$(getent passwd "$ATLAS_USER" | cut -d: -f6)" \
    || die "Cannot resolve home directory for user '$ATLAS_USER'"
# systemd-run units have no $HOME; tools like the ollama CLI panic without it.
export HOME="${HOME:-/root}"

echo -e "${BLD}${CYN}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║          ATLAS CONTROL — INSTALLER                  ║"
echo "  ║          Offline GPS Nav + Mesh Communications      ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Release            : ${BLD}$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$BUNDLE/atlas-release.json")${NC}"
echo -e "  Unit type          : ${BLD}$([[ -n "$DEV_KEY" ]] && echo "developer" || echo "retail")${NC}"
echo -e "  Installing for user: ${BLD}${ATLAS_USER}${NC}"
echo -e "  Source directory   : ${BLD}${SCRIPT_DIR}${NC}"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# 1.  NVMe DETECTION & STORAGE SETUP
# ════════════════════════════════════════════════════════════════════════════
section "Storage"

ATLAS_DATA="/atlas_data"

# Atlas data (maps, routing, DBs) lives under /atlas_data. On current Jetson
# builds the OS and this data share a single NVMe, so /atlas_data is just a
# directory on the root filesystem — nothing to detect, partition, format, or
# mount. (Earlier builds put the OS on a microSD card and Atlas data on a
# SEPARATE NVMe mounted at /atlas_data; those installs keep working unchanged
# because we honor an existing mount and never reformat anything.)
RECOMMENDED_FREE_GB=350

if mountpoint -q "$ATLAS_DATA" 2>/dev/null; then
    ATLAS_DEV=$(findmnt -n -o SOURCE "$ATLAS_DATA")
    log "/atlas_data is a separate mount on $ATLAS_DEV — using it as-is"
else
    mkdir -p "$ATLAS_DATA"
    log "Using $ATLAS_DATA on the root filesystem ($(findmnt -n -o SOURCE / 2>/dev/null || echo '/'))"
fi

# Free-space advisory only (never blocks): the full dataset — basemap + all-state
# OSRM + topo/DBs + scratch — is large, but a partial state selection needs far
# less, so we warn rather than refuse.
FREE_GB=$(df -BG --output=avail "$ATLAS_DATA" 2>/dev/null | tail -1 | tr -dc '0-9')
if [[ -n "$FREE_GB" && "$FREE_GB" -lt "$RECOMMENDED_FREE_GB" ]]; then
    warn "Only ${FREE_GB} GB free on $ATLAS_DATA (~${RECOMMENDED_FREE_GB} GB recommended for the full basemap + all 50 states)."
    warn "You can still proceed — install fewer states when prompted, or free space and re-run."
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

# The Atlas software comes only from the signed release (step 4, below) and
# runs from /opt/atlas. This directory holds Atlas's state and datasets; the
# dataset builders further down run from here when data must be (re)built,
# so the kit's copies of them are placed alongside the data.
SRC_DIR="$SCRIPT_DIR"
[[ -f "$SRC_DIR/app.py" ]] || die "install.sh must sit in an Atlas release kit or checkout (app files beside it)."

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
              --exclude='/data/' --exclude='/updates/' --exclude='/cache/' \
              --exclude='atlas.db' \
              --exclude='hotspot_config.json' \
              --exclude='osrm_active.json' \
              "$SRC_DIR/" "$APP_DIR/"
    log "Dataset builders copied"
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

own_datasets
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

# Only fetch what is missing: a re-run (or a unit whose packages came with the
# OS image) needs no network here.
MISSING_PKGS=()
for p in "${PKGS[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING_PKGS+=("$p")
done
if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    info "Installing packages: ${MISSING_PKGS[*]} ..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PKGS[@]}" > /dev/null 2>&1
    log "System packages installed"
else
    log "System packages already present"
fi

for cmd in rsync nmcli osmium smartctl systemctl bluetoothctl; do
    command_exists "$cmd" || die "Required command missing after apt install: $cmd"
done
log "Required system commands verified"

# Device access (serial, I2C, GPIO, Bluetooth), Wi-Fi control and drive
# health belong to the 'atlas' service account and the root helper that the
# release installs in step 4 — the setup account gets no sudo rules and no
# device groups, and the hostname comes from this unit's own identity.

# mDNS hygiene — by default avahi advertises on EVERY interface, including
# docker0 (172.17.0.1): a phone that resolves Atlas's name then gets an
# unreachable address and the connection silently fails. Deny that bridge
# rather than pinning one Wi-Fi interface name (a unit whose module enumerates
# differently would silently stop being discoverable), and use IPv4 so clients
# don't latch onto a rotating privacy address. use-ipv6=no alone still
# publishes AAAA records over IPv4; publish-aaaa-on-ipv4=no stops that, and
# with it the re-registration on every address rotation that can push avahi
# into a name conflict (atlas-2.local). The DNS-SD advert itself
# (_atlas._tcp with this unit's id and key pin) comes with the release.
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
sed -i 's/^[#[:space:]]*use-ipv6=.*/use-ipv6=no/' "$AVAHI_CONF"
if grep -q '^[#[:space:]]*publish-aaaa-on-ipv4=' "$AVAHI_CONF"; then
    sed -i -E 's/^[#[:space:]]*publish-aaaa-on-ipv4=.*/publish-aaaa-on-ipv4=no/' "$AVAHI_CONF"
else
    sed -i '/^\[publish\]/a publish-aaaa-on-ipv4=no' "$AVAHI_CONF"
fi
sed -i -E 's/^[#[:space:]]*allow-interfaces=.*/#allow-interfaces=/' "$AVAHI_CONF"
if grep -q '^[#[:space:]]*deny-interfaces=' "$AVAHI_CONF"; then
    sed -i -E 's/^[#[:space:]]*deny-interfaces=.*/deny-interfaces=docker0/' "$AVAHI_CONF"
else
    sed -i '/^\[server\]/a deny-interfaces=docker0' "$AVAHI_CONF"
fi
systemctl enable avahi-daemon
# A restart, not a reload: avahi only re-reads its service files on reload,
# so the settings above would otherwise wait for the next boot.
systemctl restart avahi-daemon || true
log "avahi-daemon enabled (IPv4, every interface but docker0)"

systemctl enable --now bluetooth
log "bluetooth service enabled"

# ════════════════════════════════════════════════════════════════════════════
# 3b.  SWAP / MEMORY HEADROOM  (zram + persistent disk swap)
# ════════════════════════════════════════════════════════════════════════════
# The Orin Nano shares 8 GB of RAM between CPU and GPU. The heaviest steps —
# osrm-extract/partition/customize on the big states (CA/TX/FL) and the places
# index — plus long-range routing at runtime drive it into the OOM killer.
# zram (compressed RAM) helps but is NOT extra capacity, so we set up BOTH a
# zram device AND a real disk swapfile HERE, before any heavy provisioning
# (OSRM build in §6, state processing in §8).
section "Swap / Memory Headroom"

# ── zram (compressed RAM, high priority) ─────────────────────────────────────
# The helper is idempotent and installed to a fixed path, so this is safe to
# re-run on every install/update. Non-fatal: a kernel without the zram module
# must not abort the install.
install -m 755 "$APP_DIR/zram-swap.sh" /usr/local/sbin/atlas-zram
cp "$APP_DIR/zram-swap.service" /etc/systemd/system/zram-swap.service
cat > /etc/sysctl.d/99-zram.conf <<'SYS'
# Prefer pushing anonymous pages to (fast, compressed) zram over dropping page
# cache, and reclaim VFS metadata caches (dentries/inodes) more eagerly.
# swappiness=100 is what protects the mmap'd OSRM graphs: they are file-backed
# page cache, so biasing reclaim toward anonymous memory keeps a warm routing
# graph resident. vfs_cache_pressure only governs dentry/inode reclaim, not
# those data pages. Measured against the acceptance test in 3febdd0 — do not
# retune without re-measuring on the 7.3 GB box.
vm.swappiness=100
vm.vfs_cache_pressure=200
SYS
systemctl daemon-reload
if systemctl enable --now zram-swap.service 2>/dev/null; then
    sysctl -p /etc/sysctl.d/99-zram.conf >/dev/null 2>&1 || true
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
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$ATLAS_SWAPFILE"; then
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
            # Persist across reboots at low priority (below zram). Idempotent.
            if ! grep -qF "$ATLAS_SWAPFILE" /etc/fstab 2>/dev/null; then
                printf '%s none swap sw,pri=10 0 0\n' "$ATLAS_SWAPFILE" >> /etc/fstab
            fi
            log "Persistent disk swap active + in /etc/fstab (${ATLAS_DISK_SWAP_GB} GB, priority 10)"
        else
            warn "Could not set up disk swapfile at $ATLAS_SWAPFILE — continuing (big-state OSRM may OOM)"
            swapoff "$ATLAS_SWAPFILE" 2>/dev/null || true
            rm -f "$ATLAS_SWAPFILE"
        fi
    else
        warn "Not enough free space on $ATLAS_DATA for a ${ATLAS_DISK_SWAP_GB} GB swapfile (need ~$(( ATLAS_DISK_SWAP_GB + 5 )) GB) — skipping disk swap"
    fi
fi

# ════════════════════════════════════════════════════════════════════════════
# 4.  PYTHON VIRTUAL ENVIRONMENT
# ════════════════════════════════════════════════════════════════════════════
section "Python Environment (dataset builders)"
# Only the dataset builders below use this environment. Atlas itself runs
# from the release's own environment, built offline from its signed wheels.

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

# ════════════════════════════════════════════════════════════════════════════
# 5–8.  PROVISIONING
#       Ollama · OSRM build · map data · state routing data — each skipped
#       when the payload already holds it.
# ════════════════════════════════════════════════════════════════════════════
STATE_INPUT="keep"

# ════════════════════════════════════════════════════════════════════════════
# 5.  OLLAMA (AI / LLM)
# ════════════════════════════════════════════════════════════════════════════
section "Ollama AI Engine"

# Every model Ray offers must run on this Ollama (Qwen3.5 needs 0.17.1+), and
# Ollama needs its GPU runners in /usr/local/lib/ollama — a bare binary
# (as older payload drives staged) silently runs Ray on the CPU.
OLLAMA_MIN="$(python3 -I "$SRC_DIR/model_profiles.py" --min-ollama)"
ollama_version() { ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }
version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

OLLAMA_OK=false
if command -v ollama &>/dev/null; then
    if [[ ! -d /usr/local/lib/ollama ]]; then
        warn "Ollama is installed without its GPU runners (/usr/local/lib/ollama) — reinstalling"
    elif ! version_ge "$(ollama_version)" "$OLLAMA_MIN"; then
        warn "Ollama $(ollama_version) is older than $OLLAMA_MIN, which Ray's models need — upgrading"
    else
        log "Ollama $(ollama_version) with GPU runners (Ray's models need $OLLAMA_MIN or newer)"
        OLLAMA_OK=true
    fi
fi
if ! $OLLAMA_OK; then
    info "Downloading and installing Ollama (needs Internet) ..."
    if curl -fsSL https://ollama.com/install.sh | OLLAMA_INSTALL_DIR=/usr/local/bin sh; then
        log "Ollama $(ollama_version) installed"
    elif command -v ollama &>/dev/null; then
        warn "Could not update Ollama (offline?). Ray keeps working with the models this Ollama can run;"
        warn "re-run install.sh when online to enable every model (needs Ollama $OLLAMA_MIN+)."
    else
        die "Ollama is required and could not be installed. Connect to the Internet, or stage it on the payload drive."
    fi
fi
# Service file is written in section 10 alongside atlas-control.service

# ════════════════════════════════════════════════════════════════════════════
# 6.  OSRM ROUTING ENGINE (native build from source)
# ════════════════════════════════════════════════════════════════════════════
section "OSRM Routing Engine"

# Install order of preference:
#   1. Already on PATH        → nothing to do
#   2. Pre-built binaries      → drop them into /usr/local/bin (seconds)
#   3. Build from source       → setup_native_osrm.sh (20–40 min on Jetson)
#
# Pre-built native binaries staged on the data drive (e.g.
# /atlas_data/osrm-native-bin/osrm-*) let us skip the from-source build
# entirely. They're dynamically linked against system Boost/TBB, which the
# System Dependencies section above already installed (libboost-all-dev,
# libtbb-dev), so a straight copy into PATH is all that's needed.
#
# All FOUR tools are required, not just osrm-routed: processing a state needs
# osrm-extract / osrm-partition / osrm-customize (section 8), and osrm-routed
# only serves the result. A prebuilt dir that stages just osrm-routed is
# therefore incomplete — fall through to the source build, which produces the
# whole toolset.
OSRM_PREBUILT_DIR="$ATLAS_DATA/osrm-native-bin"
OSRM_TOOLS=(osrm-routed osrm-extract osrm-partition osrm-customize)

_osrm_tools_on_path() {
    local t
    for t in "${OSRM_TOOLS[@]}"; do command -v "$t" &>/dev/null || return 1; done
}
_osrm_prebuilt_complete() {
    local t
    for t in "${OSRM_TOOLS[@]}"; do [[ -x "$OSRM_PREBUILT_DIR/$t" ]] || return 1; done
}

if _osrm_tools_on_path; then
    log "OSRM tools already installed: $(osrm-routed --version 2>/dev/null | head -1)"
elif _osrm_prebuilt_complete; then
    info "Installing pre-built OSRM binaries from $OSRM_PREBUILT_DIR ..."
    # `install -m 755 <src>... <dir>` copies each osrm-* binary into a PATH
    # directory and sets its mode to rwxr-xr-x (world-executable) in one step.
    install -m 755 "$OSRM_PREBUILT_DIR"/osrm-* /usr/local/bin/
    ldconfig 2>/dev/null || true   # refresh linker cache for the new binaries
    hash -r                        # forget any cached "not found" for osrm-*
    if _osrm_tools_on_path; then
        log "OSRM binaries installed from prebuilt: $(osrm-routed --version 2>/dev/null | head -1)"
    else
        die "Prebuilt OSRM install failed — one or more of ${OSRM_TOOLS[*]} not on PATH.
    Check that $OSRM_PREBUILT_DIR/osrm-* are executables for this arch and that
    their shared-library deps resolve (ldd $OSRM_PREBUILT_DIR/osrm-routed)."
    fi
else
    if compgen -G "$OSRM_PREBUILT_DIR/osrm-*" >/dev/null 2>&1; then
        warn "Prebuilt OSRM dir incomplete (need all of: ${OSRM_TOOLS[*]}) — building from source"
    fi
    info "Building OSRM from source (this takes 20–40 minutes on Jetson) ..."
    bash "$APP_DIR/setup_native_osrm.sh" --no-cleanup-prompt
    log "OSRM routing engine built and installed"
fi

# ── OSRM routing profiles (car.lua + shared lib/) ────────────────────────────
# Both routing profiles depend on OSRM's shared Lua profile library:
#   • car.lua is loaded directly from $OSRM_PROFILES_DIR/car.lua (see CAR_LUA,
#     section 8) and require()s lib/* relative to itself.
#   • hiking.lua require()s the same lib/* via its package.path (which points at
#     $OSRM_PROFILES_DIR).
# A from-source `make install` drops these in place, but the prebuilt-binary
# path does not — so install the version-matched copy bundled in the repo
# whenever the system location is missing. Bundling keeps the install
# self-contained and immune to how the binaries were obtained.
OSRM_PROFILES_DIR="/usr/local/share/osrm/profiles"
if [[ ! -f "$OSRM_PROFILES_DIR/car.lua" || ! -d "$OSRM_PROFILES_DIR/lib" ]]; then
    info "Installing OSRM routing profiles to $OSRM_PROFILES_DIR ..."
    mkdir -p "$OSRM_PROFILES_DIR/lib"
    cp "$APP_DIR/osrm-profiles/car.lua"     "$OSRM_PROFILES_DIR/car.lua"
    cp "$APP_DIR/osrm-profiles/lib/"*.lua   "$OSRM_PROFILES_DIR/lib/"
    log "OSRM profiles installed (car.lua + shared lib/)"
else
    log "OSRM profiles already present ($OSRM_PROFILES_DIR)"
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

# ── 7b. Vector basemap (world z0–6 + US z7–15, ~20 GB) ───────────────────────
BASEMAP="$TILES_DIR/map.pmtiles"
if [[ -f "$BASEMAP" ]]; then
    BSIZE=$(du -sh "$BASEMAP" | cut -f1)
    log "Vector basemap already present ($BSIZE) — skipping download"
else
    # Verify the resolved URL is reachable BEFORE the ~20 GB extract so a
    # stale fallback key (Protomaps prunes old builds) or being offline fails
    # fast with a clear message instead of a half-built archive.
    if ! curl -sfI --max-time 30 "$BASEMAP_URL" >/dev/null 2>&1; then
        die "Basemap URL not reachable: $BASEMAP_URL
    The Protomaps build may have been pruned, or the machine is offline.
    Find a current key at $PROTOMAPS_METADATA_URL, update PROTOMAPS_FALLBACK_KEY
    at the top of this script (or restore connectivity), then re-run."
    fi
    # Only the byte ranges for the world overview and the US are fetched, not
    # the ~127 GB planet (setup_basemap.sh, basemap-region-us.geojson).
    info "Building map.pmtiles (~20 GB: world z0–6 + US detail) ..."
    info "Source: $BASEMAP_URL"
    sudo -u "$ATLAS_USER" bash "$APP_DIR/setup_basemap.sh" "$BASEMAP_URL" "$BASEMAP"
    log "Vector basemap ready ($(du -sh "$BASEMAP" | cut -f1))"
fi

# ── 7b. Noto Sans glyph PBFs (required for map label rendering) ──────────────
# The font directory holds per-weight Unicode-range PBF files, not TTF files.
# The map style requests the "Noto Sans Regular" stack (templates/index.html),
# so gate specifically on THAT weight being complete — counting all weights
# together let a fully-downloaded Bold mask a missing/partial Regular and leave
# the map with unlabeled features. Regular has 256 ranges; allow one of slack.
# download_fonts.sh is idempotent (skips ranges already on disk) and re-verifies
# the primary weight itself before returning success.
PRIMARY_FONT_DIR="$APP_DIR/static/fonts/Noto Sans Regular"
PRIMARY_FONT_COUNT=$(find "$PRIMARY_FONT_DIR" -name "*.pbf" 2>/dev/null | wc -l || true)
if [[ $PRIMARY_FONT_COUNT -lt 255 ]]; then
    info "Downloading map font glyphs (~8 MB) ..."
    sudo -u "$ATLAS_USER" bash "$APP_DIR/download_fonts.sh"
    log "Font glyphs downloaded"
else
    log "Font glyphs already present (Noto Sans Regular: $PRIMARY_FONT_COUNT ranges)"
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
# The raw z/x/y cache (~25 GB) only feeds setup_topo_pmtiles.sh; the map
# reads topo.pmtiles.
if [[ -f "$TOPO_PMTILES" ]]; then
    rm -rf "$TOPO_TILES"
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
    # Only osrm-customize reads these (~30% of a graph, ~32 GB across all
    # states); osrm-routed never does. Rebuilding a state starts from extract.
    rm -f "$state_dir"/region.osrm.{ebg,enw,cnbg,cnbg_to_ebg,turn_penalties_index}
    touch "$state_dir/.processed"
    chown -R "$ATLAS_USER:$ATLAS_USER" "$state_dir"
    chmod -R a+rX "$state_dir"     # osrm-extract writes .fileIndex 0700
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

# ════════════════════════════════════════════════════════════════════════════
# 8a.  NPS TRAILS / TRAILHEADS DATABASE
# ════════════════════════════════════════════════════════════════════════════
# Must run AFTER the state .osm.pbf downloads above — download_nps_trails.py's
# OSM extraction (build_from_osm) globs osrm-data/*.osm.pbf for trail/trailhead
# geometry. Running it earlier (as this used to, back in §7 Map Data) finds no
# PBFs yet on a fresh install, so it silently skips OSM extraction and leaves
# the trails/trailheads tables empty forever — nps_units/park_boundaries still
# populate fine since those come from a live NPS API call, so the DB file looks
# non-empty and the "already present" guard below then permanently hides the
# gap. Park metadata itself doesn't depend on state selection, so this still
# runs even when STATE_INPUT was "none" (OSM extraction then finds nothing to
# extract and app.py geocoder falls back to trailheads.db as before).
section "NPS Trails / Trailheads Database"

DATA_DIR="$APP_DIR/static/data"
mkdir -p "$DATA_DIR"
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
# 8b.  OFFLINE PLACES / GEOCODING INDEX  (address + POI search)
# ════════════════════════════════════════════════════════════════════════════
# Build static/data/places.db from the state .osm.pbf extracts just downloaded,
# so users can type a store / street address / POI name and route to it
# (Google-Maps-style). build_places_index.py reads osrm-data/*.osm.pbf — the same
# files OSRM routes on; _process_state keeps the originals. build_places_safe.sh
# makes the heavy build safe on 8 GB: it adds swap, pauses Ray (ollama), and
# memory-caps the build so it can't freeze the box. Idempotent + non-fatal, so a
# long or interrupted build never aborts the install.
section "Offline Places / Geocoding Index"

PLACES_DB="$APP_DIR/static/data/places.db"
if [[ -s "$PLACES_DB" ]]; then
    log "Places index already present ($(du -sh "$PLACES_DB" | cut -f1)) — skipping"
elif ! compgen -G "$OSRM_DIR/*.osm.pbf" >/dev/null 2>&1; then
    warn "No state .pbf files present — skipping places index (install states first)"
else
    info "Building offline address/POI search index (memory-safe; this can take a while) ..."
    if bash "$APP_DIR/build_places_safe.sh"; then
        log "Places index built ($(du -sh "$PLACES_DB" 2>/dev/null | cut -f1)) — 'type a place/address' search enabled"
    else
        warn "Places index build did not finish — resume later: sudo bash $APP_DIR/build_places_safe.sh --resume"
    fi
fi


# ════════════════════════════════════════════════════════════════════════════
# 8c.  OFFLINE GEOCODER FALLBACK DATA  (GeoNames)
# ════════════════════════════════════════════════════════════════════════════
# Ray's reverse geocoder has three tiers. Tier 1 (static/data/us_cities.db, §7d)
# is US-only, so these two files are the ONLY source of any non-US place name,
# and the ZIP centroids beat city centroids badly in sparse terrain (on Mt
# Rainier: "Longmire, WA" 12 km vs "Tacoma, WA" 68 km).
#
# Nothing in this repo fetched them until now, so both tiers silently loaded
# zero rows on every install and Ray hallucinated city names from bare lat/lon.
#
# Non-fatal by design — these are fallback tiers; us_cities.db keeps the primary
# path working, so a failed download must never abort an install or an update.
section "Offline Geocoder Data (GeoNames)"

if [[ ! -s "$APP_DIR/data/US.txt" || ! -s "$APP_DIR/data/cities5000.txt" ]]; then
    info "Downloading GeoNames geocoder data (~6 MB) ..."
    if sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_geonames.py" \
            --data-dir "$APP_DIR/data"; then
        log "Geocoder fallback data ready"
    else
        warn "GeoNames download failed — Ray keeps US city geocoding via"
        warn "us_cities.db, but international coordinates will not resolve."
        warn "Re-run later with: $VENV/bin/python3 $APP_DIR/download_geonames.py"
    fi
else
    log "Geocoder fallback data present ($(du -sh "$APP_DIR/data/US.txt" | cut -f1) + $(du -sh "$APP_DIR/data/cities5000.txt" | cut -f1))"
fi

# ════════════════════════════════════════════════════════════════════════════
# 8d.  ROUTING MEMORY MODE  (osrm-routed --mmap)
# ════════════════════════════════════════════════════════════════════════════
# routing_node.py launches osrm-routed with --mmap=1 so a state graph is
# file-backed page cache (~42 MB RSS) instead of ~1.1 GB of anonymous RAM. That
# is what lets Ray stay resident during a long-distance route instead of being
# evicted for it: the unload in app.py is now a pressure valve that only fires
# below ~800 MB free, not the default path. A binary without the flag exits
# immediately on every launch, _port_ready times out, and route() falls back to
# a straight line — a well-formed response, so it presents as "routing is
# broken" rather than as a missing command-line option.
#
# §6's first two branches (already on PATH / prebuilt binaries from
# $OSRM_PREBUILT_DIR) accept any vintage without a version check, so an older
# osrm-routed can land here.
#
# Probe --help rather than comparing version strings: the binary itself is the
# authority on what it accepts, and the check costs milliseconds.
#
# Capture --help into a variable instead of piping it into grep. We run under
# `set -o pipefail` (top of file), so `osrm-routed --help | grep -q --mmap`
# reports failure whenever EITHER side fails — and plenty of CLIs print help
# then exit non-zero. That would read a perfectly capable binary as lacking the
# flag and start a needless 20–40 min rebuild.
# v5.27.1 happens to exit 0, but this check exists for binaries we have not seen.
_osrm_has_mmap() {
    local help
    help="$(osrm-routed --help 2>&1 || true)"
    grep -q -- '--mmap' <<<"$help"
}

section "Routing Memory Mode (OSRM --mmap)"

OSRM_MMAP_OK=true
if ! command_exists osrm-routed; then
    warn "osrm-routed not on PATH — skipping --mmap capability check"
elif _osrm_has_mmap; then
    log "OSRM supports --mmap — Ray and long-distance routing can run together"
else
    warn "Installed osrm-routed predates --mmap — rebuilding from source (20–40 min) ..."
    # Guarded: unlike §6 (where no OSRM exists at all and dying is correct), a
    # working-but-older binary is already installed here. Under `set -e` + the
    # ERR trap an unguarded build failure would abort the whole install and mark
    # the update failed — worse than continuing in high-memory mode.
    if bash "$APP_DIR/setup_native_osrm.sh" --no-cleanup-prompt; then
        hash -r   # forget the cached path to the old binary (mirrors §6)
        if _osrm_has_mmap; then
            log "OSRM rebuilt with --mmap support"
        else
            OSRM_MMAP_OK=false
            warn "Rebuilt osrm-routed still lacks --mmap — long-distance routes may"
            warn "degrade to straight lines while Ray is loaded."
        fi
    else
        OSRM_MMAP_OK=false
        warn "OSRM rebuild failed — keeping the existing binary and continuing."
        warn "Routing stays in high-memory mode; see the summary at the end."
    fi
fi

# ════════════════════════════════════════════════════════════════════════════
# 10.  OLLAMA SERVICE
# ════════════════════════════════════════════════════════════════════════════
section "Ollama Service"

# Atlas's own units, nginx site, firewall, udev and mDNS files come from the
# signed release (step 4); Ollama is part of the base system.
sed "s|OLLAMA_USER|$OLLAMA_SERVICE_USER|g; s|OLLAMA_GROUP|$OLLAMA_SERVICE_GROUP|g; s|OLLAMA_DATA_DIR|$OLLAMA_DATA_DIR|g" \
    "$APP_DIR/ollama.service" > /etc/systemd/system/ollama.service

systemctl daemon-reload
systemctl enable ollama
log "Service enabled: ollama"

# ════════════════════════════════════════════════════════════════════════════
# 11.  UDEV HARDWARE DEVICE RULES
# ════════════════════════════════════════════════════════════════════════════
section "Hardware Device Rules"
# The udev rules (radio/GPS symlinks, group dialout, mode 0660) come from the
# signed release in step 4.

# ── 11b. Jetson Orin UART1 DMA fix (40-pin header /dev/ttyTHS1) ───────────────
# JetPack 6.2.2+ has a serial-tegra RX-DMA kernel bug that returns received
# UART bytes as 0x00 on /dev/ttyTHS1, breaking any radio wired to header pins
# 8/10 (for Atlas: the Heltec V4 mesh link). The fix is a device-tree overlay
# that deletes the UEFI-injected dmas/dma-names properties so the driver falls
# back to PIO mode. See jetson-orin-uart/README.md for background/attribution.
UART_FIX_DTS="$APP_DIR/jetson-orin-uart/disable-uart1-dma.dts"
UART_FIX_DTBO="/boot/disable-uart1-dma.dtbo"
EXTLINUX_CONF="/boot/extlinux/extlinux.conf"
if [[ -f /etc/nv_tegra_release && -d /proc/device-tree/bus@0/serial@3100000 \
      && -f "$UART_FIX_DTS" && -f "$EXTLINUX_CONF" ]]; then
    if grep -qF "$UART_FIX_DTBO" "$EXTLINUX_CONF"; then
        log "Jetson UART1 DMA fix already active in extlinux.conf"
    else
        command -v dtc > /dev/null 2>&1 || apt-get install -y device-tree-compiler > /dev/null 2>&1 || true
        if command -v dtc > /dev/null 2>&1 \
           && dtc -@ -I dts -O dtb -o "$UART_FIX_DTBO" "$UART_FIX_DTS" 2> /dev/null; then
            cp "$EXTLINUX_CONF" "${EXTLINUX_CONF}.bak.$(date +%Y%m%d%H%M%S)"
            UART_FIX_DEFAULT="$(awk '/^[[:space:]]*DEFAULT[[:space:]]/{print $2; exit}' "$EXTLINUX_CONF")"
            UART_FIX_STANZA=""
            if [[ -n "$UART_FIX_DEFAULT" ]]; then
            UART_FIX_STANZA="$(awk -v lbl="$UART_FIX_DEFAULT" '
                $1=="LABEL" { found = ($2==lbl) }
                found && NF==0 { exit }
                found { print }' "$EXTLINUX_CONF")"
            fi
            if [[ -n "$UART_FIX_STANZA" ]]; then
                # Clone the default stanza as a new UARTFix entry; the previous
                # default stays in the boot menu as a fallback.
                UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                    | sed 's|^[[:space:]]*LABEL[[:space:]].*|LABEL UARTFix|' \
                    | sed 's|MENU LABEL.*|MENU LABEL Atlas UART DMA fix|')"
                if ! echo "$UART_FIX_STANZA" | grep -qE '^[[:space:]]*FDT[[:space:]]'; then
                    # Overlays need an explicit FDT; only safe to guess when
                    # exactly one kernel dtb exists (true for stock devkits).
                    UART_FIX_FDT="$(find /boot/dtb -maxdepth 1 -name 'kernel_*.dtb' 2> /dev/null | head -1 || true)"
                    UART_FIX_NDTB="$(find /boot/dtb -maxdepth 1 -name 'kernel_*.dtb' 2> /dev/null | wc -l || true)"
                    if [[ "$UART_FIX_NDTB" -eq 1 && -n "$UART_FIX_FDT" ]]; then
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "\|^[[:space:]]*LINUX[[:space:]]|a\\	FDT $UART_FIX_FDT")"
                    else
                        warn "Ambiguous FDT ($UART_FIX_NDTB kernel dtbs) — skipping UART1 DMA fix; see jetson-orin-uart/README.md"
                        UART_FIX_STANZA=""
                    fi
                fi
                if [[ -n "$UART_FIX_STANZA" ]]; then
                    if echo "$UART_FIX_STANZA" | grep -qE '^[[:space:]]*OVERLAYS[[:space:]]'; then
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "s|^\([[:space:]]*OVERLAYS[[:space:]].*\)$|\1,$UART_FIX_DTBO|")"
                    else
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "\|^[[:space:]]*FDT[[:space:]]|a\\	OVERLAYS $UART_FIX_DTBO")"
                    fi
                    printf '\n%s\n' "$UART_FIX_STANZA" >> "$EXTLINUX_CONF"
                    sed -i "s|^\([[:space:]]*DEFAULT[[:space:]]\{1,\}\).*|\1UARTFix|" "$EXTLINUX_CONF"
                    warn "Jetson UART1 DMA fix installed — REBOOT required before the 40-pin UART mesh radio works"
                fi
            else
                warn "Could not parse extlinux.conf default stanza — skipping UART1 DMA fix"
            fi
        else
            warn "dtc unavailable or overlay compile failed — skipping UART1 DMA fix"
        fi
    fi
fi

# ════════════════════════════════════════════════════════════════════════════
# 12.  OLLAMA MODELS
# ════════════════════════════════════════════════════════════════════════════
section "Ollama Models"

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
        # Every model Ray offers, in the build that suits this unit's memory
        # (e.g. Qwen3.5 4B as Q4_K_M on an 8 GB Orin Nano), plus the embedder.
        # A unit sold off-grid can never download one later, so all of them
        # ship. The owner's choice of model (Settings → Ray) is never changed.
        mapfile -t RAY_MODELS < <(python3 -I "$SRC_DIR/model_profiles.py" --preinstall)
        MODELS_MISSING=()
        for model in "${RAY_MODELS[@]}"; do
            if ollama show "$model" >/dev/null 2>&1; then
                log "Ollama model present: $model"
            else
                info "Pulling Ollama model: $model"
                # Never fatal: an off-grid box can't pull, and a failed pull
                # must not abort the install before services start.
                if ollama pull "$model"; then
                    log "Ollama model ready: $model"
                else
                    warn "Could not pull $model (offline?)"
                    MODELS_MISSING+=("$model")
                fi
            fi
        done
        if (( ${#MODELS_MISSING[@]} )); then
            warn "Missing Ray models: ${MODELS_MISSING[*]} — put them on the payload drive, or re-run install.sh online."
        fi
    else
        warn "Ollama service started but API did not become ready; skipping model pull"
    fi
else
    warn "Ollama service did not start; skipping model pull"
fi


# ════════════════════════════════════════════════════════════════════════════
# 13.  ATLAS SOFTWARE  (the signed release, through its update engine)
# ════════════════════════════════════════════════════════════════════════════
section "Atlas Software (signed release)"

BUNDLE_SEQ=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["seq"])' "$BUNDLE/atlas-release.json")
if [[ -e /opt/atlas/current ]]; then
    INSTALLED_SEQ=$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("seq", 0))
except Exception: print(0)' /var/lib/atlas-update/installed.json)
    if (( INSTALLED_SEQ >= BUNDLE_SEQ )); then
        log "This release (or a newer one) is already installed"
    else
        # Already on the signed layout: a normal verified update, with the
        # engine's health check and automatic rollback.
        info "Handing the release to Atlas's update engine ..."
        READY="$APP_DIR/updates/ready"
        install -d -o atlas -g atlas -m 2770 "$READY"
        rm -f "$READY"/*
        install -o atlas -g atlas -m 0640 "$BUNDLE"/atlas-release.json "$BUNDLE"/atlas-release.json.sig \
            "$BUNDLE"/*.tar.xz "$READY/"
        /usr/bin/python3 -I - <<'PY'
import json, socket, sys, time
def status():
    try:
        return json.load(open("/run/atlas-update/status.json"))
    except Exception:
        return {}
# The status file still shows the previous update's result until the engine
# picks this one up: only a result with a new transaction id counts.
before = status().get("txid")
s = socket.socket(socket.AF_UNIX)
s.connect("/run/atlas/helper.sock")
s.sendall(b'{"verb": "update.apply", "args": {}}\n')
s.recv(4096)
last = None
for _ in range(1800):
    st = status()
    line = f"{st.get('stage')}: {st.get('message') or ''}"
    if line != last:
        print("   ", line, flush=True)
        last = line
    if st.get("txid") != before and st.get("engine_state") == "IDLE" and \
            st.get("stage") in ("success", "rolled_back", "failed", "cancelled", "interrupted"):
        sys.exit(0 if st["stage"] == "success" else 1)
    time.sleep(1)
sys.exit("timed out waiting for the update engine")
PY
        log "Release installed by the update engine"
    fi
else
    # First install on this unit: service account, engine, this unit's own
    # identity, the release, system files — verified and health-checked, and
    # undone automatically if the new Atlas does not come up healthy.
    /usr/bin/python3 -I "$SRC_DIR/tools/migrate/migrate.py" apply --bundle "$BUNDLE" --provision \
        ${DEV_KEY:+--dev-key "$DEV_KEY"}
fi
systemctl start atlas-firewall.service || warn "Firewall did not start — check: journalctl -u atlas-firewall"
own_datasets

# ════════════════════════════════════════════════════════════════════════════
# 14.  IDENTITY & LABEL
# ════════════════════════════════════════════════════════════════════════════
section "Identity & Label"

UNIT_NAME=$(/usr/local/lib/atlas/bin/atlas-identity show | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
UNIT_HOST=$(/usr/local/lib/atlas/bin/atlas-identity show | python3 -c 'import json,sys; print(json.load(sys.stdin)["hostname"])')
log "This unit: $UNIT_NAME ($UNIT_HOST.local)"
if [[ -n "$LABELS_DIR" ]]; then
    /usr/local/lib/atlas/bin/atlas-identity label-json \
        | /opt/atlas/current/venv/bin/python3 "$SRC_DIR/tools/manufacturing/make_label.py" --out "$LABELS_DIR" >/dev/null
    log "Label files written to $LABELS_DIR/ (print $UNIT_NAME.png and attach it to this unit)"
else
    info "No --labels DIR: print the label later with"
    echo "    sudo /usr/local/lib/atlas/bin/atlas-identity label-json | /opt/atlas/current/venv/bin/python3 $SRC_DIR/tools/manufacturing/make_label.py --out DIR"
fi

# ════════════════════════════════════════════════════════════════════════════
# 15.  RETAIL LOCK-DOWN
# ════════════════════════════════════════════════════════════════════════════
section "Retail Lock-down"

if [[ -n "$DEV_KEY" ]]; then
    warn "Developer unit — not locked down (SSH stays on, developer key trusted)"
else
    bash "$SRC_DIR/tools/manufacturing/finalize-retail.sh"
fi

# ════════════════════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLD}${GRN}══════════════════════════════════════════════════════════${NC}"
echo -e "${BLD}${GRN}  $UNIT_NAME is ready.${NC}"
echo -e "${GRN}══════════════════════════════════════════════════════════${NC}"
echo ""
USED=$(df -h "$ATLAS_DATA" | tail -1 | awk '{print $3"/"$2" used"}')
echo -e "  ${BLD}Data storage :${NC} $APP_DIR  ($USED)"
echo ""
echo -e "  ${CYN}First use (owner):${NC} scan the label's app QR code with the Atlas app,"
echo -e "  or join the Wi-Fi printed on the label and open ${BLD}https://$UNIT_HOST.local${NC}"
echo -e "  (the browser warns once about Atlas's own certificate)."
echo ""
if [[ "${STATE_INPUT,,}" == "none" ]]; then
    echo -e "  ${YLW}Routing data not installed.${NC} Add states by re-running install.sh."
    echo ""
fi
# ${OSRM_MMAP_OK:-true} because `set -u` is on and early paths never reach §8d.
if [[ "${OSRM_MMAP_OK:-true}" == "false" ]]; then
    echo -e "  ${YLW}Routing is in high-memory mode.${NC} This osrm-routed predates --mmap,"
    echo -e "  so each state graph costs ~1.1 GB of RAM instead of ~42 MB. Long routes"
    echo -e "  can degrade to a straight line while Ray is loaded. Re-run install.sh"
    echo -e "  after removing the prebuilt OSRM binaries to rebuild them (20–40 min)."
    echo ""
fi
if [[ ! -f "$APP_DIR/static/data/places.db" ]]; then
    echo -e "  ${YLW}Offline address/POI search not built yet.${NC} To build it (one-time, heavy):"
    echo -e "    sudo bash $APP_DIR/build_places_safe.sh"
    echo ""
fi
echo -e "  ${CYN}Updates:${NC} Atlas installs signed releases itself — Settings → Software Update."
echo ""
if [[ -n "${RAY_MODELS[*]:-}" ]]; then
    echo -e "  ${CYN}Ray's models:${NC} $(python3 -I "$SRC_DIR/model_profiles.py" --summary | awk '{print $1" "$2}' | paste -sd, - | sed 's/,/, /g')"
    echo -e "  (each tuned for this unit automatically; the owner picks one in Settings → Ray)."
    echo ""
fi

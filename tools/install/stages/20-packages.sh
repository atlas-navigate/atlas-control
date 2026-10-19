# 20 — The distribution packages Atlas and its dataset builders need.
section "System Packages"

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
    retry 3 15 apt-get update -qq || die "apt-get update failed — connect to the Internet and re-run."
    export DEBIAN_FRONTEND=noninteractive
    retry 3 15 apt-get install -y -qq "${MISSING_PKGS[@]}" > /dev/null \
        || die "Could not install: ${MISSING_PKGS[*]} — see the apt output above, then re-run."
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
# release installs (stage 95) — the setup account gets no sudo rules and no
# device groups, and the hostname comes from this unit's own identity.
#
# avahi's own settings (IPv4 only, every interface but docker0, no host
# details) and the _atlas._tcp advert come with the release, so every update
# can correct them too (system/files.json "settings").
systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon did not start — check: journalctl -u avahi-daemon"
systemctl enable --now bluetooth >/dev/null 2>&1 || warn "bluetooth did not start — check: journalctl -u bluetooth"
log "avahi-daemon and bluetooth enabled"

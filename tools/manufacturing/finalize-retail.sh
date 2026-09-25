#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  FINALIZE RETAIL — last step before an Atlas leaves the production line  ║
# ║                                                                          ║
# ║  Usage:  sudo finalize-retail.sh [--check]                               ║
# ║            --check   only report; change nothing                         ║
# ║                                                                          ║
# ║  A sold Atlas has no universal way in: no shared password, no SSH, no    ║
# ║  sudo rights for the service. The owner uses it through pairing only.    ║
# ║    - login accounts from first-boot setup get their password locked     ║
# ║      (same technician password on every unit = universal credential)     ║
# ║    - SSH is switched off and masked                                      ║
# ║    - the setup account leaves the atlas group (no reading owner data)    ║
# ║    - mDNS is not pinned to one Wi-Fi interface name, hides host details ║
# ║    - checks: no sudo rules, firewall on, app only on loopback           ║
# ║  Developer units (/etc/atlas/dev-mode) are left alone.                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -Eeuo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; BLD=$'\033[1m'; NC=$'\033[0m'
log()  { echo -e "${GRN}[✓]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
do_() { if (( CHECK_ONLY )); then echo "    would run: $*"; else "$@"; fi; }

[[ $EUID -eq 0 ]] || die "Must run as root:  sudo $0"
if [[ -e /etc/atlas/dev-mode ]]; then
    warn "Developer unit (/etc/atlas/dev-mode): not locking it down."
    exit 0
fi
[[ -e /opt/atlas/current ]] || die "Atlas is not installed on the signed layout yet — run install.sh first."

FAIL=0
bad() { warn "$*"; FAIL=1; }

# ── Accounts ─────────────────────────────────────────────────────────────────
while IFS=: read -r user _ uid _ _ _ shell; do
    (( uid >= 1000 && uid < 60000 )) || continue
    [[ "$shell" == */nologin || "$shell" == */false ]] && continue
    status=$(passwd -S "$user" 2>/dev/null | awk '{print $2}')
    if [[ "$status" == "P" ]]; then
        do_ passwd -l "$user" >/dev/null
        log "Password login locked for $user"
    else
        log "$user has no usable password"
    fi
    if id -nG "$user" | tr ' ' '\n' | grep -qx atlas; then
        do_ gpasswd -d "$user" atlas >/dev/null
        log "$user removed from the atlas group"
    fi
done < /etc/passwd

# ── SSH ──────────────────────────────────────────────────────────────────────
for unit in ssh.socket ssh.service sshd.service; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1 && systemctl cat "$unit" >/dev/null 2>&1; then
        do_ systemctl disable --now "$unit" >/dev/null 2>&1 || true
        do_ systemctl mask "$unit" >/dev/null 2>&1 || true
    fi
done
log "SSH disabled and masked"

# ── mDNS ─────────────────────────────────────────────────────────────────────
AVAHI_CONF=/etc/avahi/avahi-daemon.conf
if [[ -f "$AVAHI_CONF" ]]; then
    if (( ! CHECK_ONLY )); then
        cp -n "$AVAHI_CONF" "$AVAHI_CONF.atlas-orig" || true
        # Pinning allow-interfaces to one Wi-Fi name silently stops discovery
        # on a unit whose module enumerates differently; deny the container
        # bridge instead, and publish no workstation/host details.
        sed -i -E 's/^[#[:space:]]*allow-interfaces=.*/#allow-interfaces=/' "$AVAHI_CONF"
        sed -i -E 's/^[#[:space:]]*deny-interfaces=.*/deny-interfaces=docker0/' "$AVAHI_CONF"
        grep -q '^deny-interfaces=' "$AVAHI_CONF" || sed -i '/^\[server\]/a deny-interfaces=docker0' "$AVAHI_CONF"
        sed -i -E 's/^[#[:space:]]*publish-workstation=.*/publish-workstation=no/; s/^[#[:space:]]*publish-hinfo=.*/publish-hinfo=no/' "$AVAHI_CONF"
        systemctl try-restart avahi-daemon.service || true
    fi
    log "mDNS: every interface but docker0, no host details published"
fi

# ── Checks ───────────────────────────────────────────────────────────────────
ls /etc/sudoers.d/atlas-* >/dev/null 2>&1 && bad "sudo rules for Atlas still present: $(ls /etc/sudoers.d/atlas-*)"
[[ -z "$(sudo -l -U atlas 2>/dev/null | grep -E '^\s+\(')" ]] || bad "the atlas account has sudo rights"
systemctl is-enabled --quiet atlas-firewall.service || bad "firewall is not enabled"
systemctl is-active --quiet atlas-firewall.service || bad "firewall is not running"
[[ "$(systemctl show -p User --value atlas-control.service)" == "atlas" ]] || bad "atlas-control does not run as the atlas account"
if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|[^0-9])(0\.0\.0\.0|\*|\[::\]):5000$'; then
    bad "the app port 5000 is reachable from the network"
fi
[[ -s /etc/atlas/identity/device.json ]] || bad "no device identity"

if (( FAIL )); then
    die "Not ready for retail — fix the items above."
fi
if (( ! CHECK_ONLY )); then
    date -u +%Y-%m-%dT%H:%M:%SZ > /etc/atlas/retail
    chmod 644 /etc/atlas/retail
fi
log "${BLD}Ready for retail.${NC}"

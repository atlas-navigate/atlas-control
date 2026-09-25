#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ATLAS MIRROR — full rootfs backup of THIS unit (disaster recovery)     ║
# ║                                                                          ║
# ║  Usage:  sudo /opt/atlas-imaging/atlas-mirror.sh                        ║
# ║                                                                          ║
# ║  This is INSURANCE, not the deployment mechanism. It captures the hand-  ║
# ║  tweaks that live outside /atlas_data — WiFi profiles in /etc/netplan,   ║
# ║  custom sysctls, anything you changed by hand — which the deployment     ║
# ║  payload deliberately does not carry. Use atlas-capture.sh to build      ║
# ║  images for units you sell.                                              ║
# ║                                                                          ║
# ║  Re-running is a cheap rsync delta.                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -Eeuo pipefail

BACKUP_UUID="9b6fe19b-34c1-434f-8e6e-c87cb1f370c4"
BACKUP_MNT="/mnt/atlas-backup"
MIRROR="$BACKUP_MNT/mirror-full"

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; BLD=$'\033[1m'; NC=$'\033[0m'
log()  { echo -e "${GRN}[✓]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Must run as root:  sudo $0"

BACKUP_DEV="/dev/disk/by-uuid/$BACKUP_UUID"
[[ -e "$BACKUP_DEV" ]] || die "Backup drive not attached."
EXISTING_MNT=$(findmnt -n -o TARGET --source "$(readlink -f "$BACKUP_DEV")" | head -1)
if [[ -n "$EXISTING_MNT" ]]; then
    BACKUP_MNT="$EXISTING_MNT"
else
    mkdir -p "$BACKUP_MNT"
    mount "$BACKUP_DEV" "$BACKUP_MNT" || die "Mount failed."
fi
MIRROR="$BACKUP_MNT/mirror-full"
log "Mirror destination: $MIRROR"
mkdir -p "$MIRROR"

STOPPED=()
trap '[[ ${#STOPPED[@]} -gt 0 ]] && systemctl start "${STOPPED[@]}" 2>/dev/null || true' EXIT
for svc in atlas-control ollama; do
    systemctl is-active --quiet "$svc" && { systemctl stop "$svc"; STOPPED+=("$svc"); }
done
sync

# -x confines the copy to the root filesystem; -H preserves hardlinks;
# -AX preserves ACLs and xattrs, which carry file capabilities on binaries.
rsync -aHAXx --numeric-ids --delete --info=progress2 --human-readable \
    --exclude='/proc/***' --exclude='/sys/***' --exclude='/dev/***' \
    --exclude='/run/***'  --exclude='/tmp/***' --exclude='/mnt/***' \
    --exclude='/media/***' --exclude='/lost+found' \
    --exclude='/atlas_data/atlas-swap.swap' \
    --exclude='/var/log/***' --exclude='/var/tmp/***' \
    --exclude='/var/cache/apt/archives/*.deb' \
    --exclude='/home/*/.cache/***' --exclude='/root/.cache/***' \
    --exclude='/swapfile' \
    / "$MIRROR/"

cat > "$BACKUP_MNT/MIRROR-INFO.txt" <<EOF
Full rootfs mirror of $(hostname)
Taken    : $(date -Is)
L4T      : $(dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null)
Kernel   : $(uname -r)
Note     : Disaster recovery for THIS device only. Excludes the 32 GB swapfile
           (recreate with: fallocate -l 32G /atlas_data/atlas-swap.swap &&
            chmod 600 ... && mkswap ... && swapon ...).
           For building units to sell, use payload/ + atlas-provision.sh.
EOF
sync
log "${BLD}Mirror complete: $(du -sh "$MIRROR" | cut -f1) at $MIRROR${NC}"

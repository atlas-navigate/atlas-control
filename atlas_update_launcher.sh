#!/usr/bin/env bash
# Atlas Control update launcher — installed by install.sh as
# /usr/local/sbin/atlas-update (root-owned) with a NOPASSWD sudoers rule so
# the web app can trigger software updates.
#
# Runs `install.sh --update` as a transient systemd unit (atlas-update.service)
# rather than as a child of the web app: the update restarts atlas-control
# partway through, which would kill any updater still attached to its process
# tree.  Output streams to logs/update.log, which /api/update/status tails.
set -euo pipefail

APP_DIR="__APP_DIR__"   # substituted by install.sh
LOG="$APP_DIR/logs/update.log"

[[ -f "$APP_DIR/install.sh" ]] || { echo "no install.sh at $APP_DIR" >&2; exit 1; }

if systemctl is-active --quiet atlas-update.service; then
    echo "update already running"
    exit 0
fi
systemctl reset-failed atlas-update.service 2>/dev/null || true

mkdir -p "$APP_DIR/logs"
: > "$LOG"
chmod 644 "$LOG"

exec systemd-run --unit=atlas-update --collect --quiet \
    bash -c "bash '$APP_DIR/install.sh' --update >> '$LOG' 2>&1"

#!/bin/bash
# Atlas Control — install udev rules, update service, clear stale port settings, restart
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ATLAS_USER="${SUDO_USER:-$(logname 2>/dev/null || echo ubuntu)}"

echo "Installing udev device rules..."
sudo cp "$SCRIPT_DIR/99-atlas-devices.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "Regenerating atlas-control.service from template..."
sed "s|ATLAS_USER|$ATLAS_USER|g; s|APP_DIR|$SCRIPT_DIR|g" \
  "$SCRIPT_DIR/atlas-control.service" | sudo tee /etc/systemd/system/atlas-control.service >/dev/null
echo "Atlas UPS defaults in service: I2C bus 7, address 0x41 (Waveshare UPS Power Module C)"

echo "Clearing stale port settings from database..."
"$SCRIPT_DIR/venv/bin/python3" -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
import database as db
db.init_db()
db.set_app_settings({'serial_port': 'AUTO', 'gps_port': 'AUTO'})
print('DB ports reset to AUTO')
"

echo "Reloading systemd and restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart atlas-control

sleep 3
echo ""
echo "Devices:"
ls -la /dev/meshtastic /dev/gps /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true

echo ""
echo "Service command:"
systemctl show atlas-control --property=ExecStart | grep -o 'argv\[\]=.*' | head -1

echo ""
echo "UPS environment:"
systemctl show atlas-control --property=Environment | sed 's/^Environment=//'

echo ""
echo "Recent logs:"
journalctl -u atlas-control -n 25 --no-pager

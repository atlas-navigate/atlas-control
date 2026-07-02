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
ls -la /dev/meshtastic /dev/gps /dev/ttyACM* /dev/ttyUSB* /dev/ttyTHS* 2>/dev/null || true

echo ""
echo "Heltec V4 mesh radio (USB-C = /dev/meshtastic, or 40-pin UART pins 8/10 = /dev/ttyTHS1):"
if [ -e /dev/meshtastic ]; then
  echo "  USB: /dev/meshtastic present"
fi
"$SCRIPT_DIR/venv/bin/python3" -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
import mesh_manager as mm
found = mm._scan_meshtastic_uart(exclude=[None])
print('  UART: meshtastic radio on ' + ', '.join(found) if found
      else '  UART: no meshtastic radio answering on /dev/ttyTHS* (check wiring pins 8=TX/10=RX/GND + Serial module PROTO @115200 on the Heltec)')
" 2>/dev/null || echo "  UART: probe skipped (mesh_manager import failed)"

echo ""
echo "I2C GPS (u-blox DDC @ 0x42; 40-pin header pins 3/5 = bus 7):"
for b in 7 1; do
  if command -v i2cdetect >/dev/null 2>&1 && [ -e "/dev/i2c-$b" ]; then
    if i2cdetect -y -r "$b" 2>/dev/null | grep -q ' 42'; then
      echo "  bus $b: GPS detected at 0x42"
    fi
  fi
done

echo ""
echo "Service command:"
systemctl show atlas-control --property=ExecStart | grep -o 'argv\[\]=.*' | head -1

echo ""
echo "UPS environment:"
systemctl show atlas-control --property=Environment | sed 's/^Environment=//'

echo ""
echo "Recent logs:"
journalctl -u atlas-control -n 25 --no-pager

#!/bin/bash
# Allow atlas-control to read NVMe temperature/health via smartctl without a password prompt.
# Run once with: sudo bash setup_nvme_temp.sh

set -e

SUDOERS_FILE="/etc/sudoers.d/atlas-smartctl"
USER_NAME="${SUDO_USER:-jrfleetwood}"

echo "Creating sudoers entry for smartctl..."
echo "$USER_NAME ALL=(ALL) NOPASSWD: /usr/sbin/smartctl" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE"
echo "  ✓ /etc/sudoers.d/atlas-smartctl created"

echo "Restarting atlas-control..."
systemctl restart atlas-control
echo "  ✓ Service restarted"

echo ""
echo "Done. NVMe temperature and health will now appear in the System page."

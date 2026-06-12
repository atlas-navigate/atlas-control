#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Atlas Control — HTTPS + DNS setup
# Run once as root:  sudo bash setup_https.sh
#
# What this does:
#   1. Installs nginx + dnsmasq
#   2. Places the self-signed TLS cert so nginx can serve HTTPS
#   3. Configures nginx as an HTTPS reverse proxy in front of Flask
#   4. Configures dnsmasq (on the VPN interface) to resolve 'atlas' and
#      'atlas.local' for VPN clients
#   5. Sets systemd startup ordering so everything comes up in order
#
# After running this:
#   On your LAN  →  https://atlas.local   (mDNS, no port needed)
#   Over VPN     →  https://atlas         (dnsmasq, no port needed)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run as root — sudo bash setup_https.sh" >&2
  exit 1
fi

ATLAS_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_SRC="$ATLAS_DIR/cert.pem"
KEY_SRC="$ATLAS_DIR/key.pem"
SSL_DIR="/etc/ssl/atlas"
VPN_IP="10.13.13.1"

echo "==> Atlas Control HTTPS + DNS Setup"
echo "    Atlas dir : $ATLAS_DIR"
echo ""

# ── 1. Install nginx and dnsmasq ──────────────────────────────────────────────
echo "--> Installing nginx and dnsmasq..."
apt-get update -qq
apt-get install -y nginx dnsmasq

# ── 2. Place TLS certificate ──────────────────────────────────────────────────
echo "--> Installing TLS certificate..."
mkdir -p "$SSL_DIR"

if [[ ! -f "$CERT_SRC" || ! -f "$KEY_SRC" ]]; then
  echo "    cert.pem / key.pem not found in $ATLAS_DIR — generating..."
  LAN_IP=$(hostname -I | tr ' ' '\n' | grep -v '^10\.13\.13\.' | grep -v '^127\.' | grep -v '^172\.' | head -1)
  [[ -z "$LAN_IP" ]] && LAN_IP="192.168.0.1"
  HOTSPOT_IP="10.42.0.1"
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY_SRC" -out "$CERT_SRC" -days 3650 \
    -subj "/CN=atlas" \
    -addext "subjectAltName=DNS:atlas,DNS:atlas.local,DNS:localhost,IP:${VPN_IP},IP:${HOTSPOT_IP},IP:${LAN_IP},IP:127.0.0.1" \
    2>/dev/null
  echo "    Certificate generated for: atlas, atlas.local, ${LAN_IP}, ${VPN_IP}, ${HOTSPOT_IP}"
fi

cp "$CERT_SRC" "$SSL_DIR/cert.pem"
cp "$KEY_SRC"  "$SSL_DIR/key.pem"
chmod 600 "$SSL_DIR/key.pem"
chmod 644 "$SSL_DIR/cert.pem"

# ── 3. Configure nginx ────────────────────────────────────────────────────────
echo "--> Configuring nginx..."

cat > /etc/nginx/sites-available/atlas <<'EOF'
# WebSocket upgrade map
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# Redirect plain HTTP → HTTPS
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 301 https://$host$request_uri;
}

# HTTPS — proxy to Flask on 127.0.0.1:5000
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name atlas atlas.local _;

    ssl_certificate     /etc/ssl/atlas/cert.pem;
    ssl_certificate_key /etc/ssl/atlas/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 32M;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;

        # WebSocket / Socket.IO upgrade
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # Pass real client IP to Flask
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Long timeouts for WebSocket keep-alive
        proxy_read_timeout  86400s;
        proxy_send_timeout  86400s;
        proxy_connect_timeout 10s;
    }
}
EOF

# Enable the site, disable nginx default
ln -sf /etc/nginx/sites-available/atlas /etc/nginx/sites-enabled/atlas
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx
systemctl restart nginx
echo "    nginx active — HTTPS proxy on :443 → Flask :5000"

# ── 4. Configure dnsmasq for VPN DNS ─────────────────────────────────────────
echo "--> Configuring dnsmasq for VPN DNS..."

cat > /etc/dnsmasq.d/atlas-vpn.conf <<EOF
# Atlas Control — VPN DNS
# Binds to the WireGuard interface only.
# Resolves 'atlas' and 'atlas.local' for VPN clients so they can use
# https://atlas or https://atlas.local instead of an IP address.
bind-interfaces
interface=wg0
listen-address=${VPN_IP}
# Forward unknown queries upstream (internet DNS for VPN clients)
server=8.8.8.8
server=8.8.4.4
# Friendly hostname resolution
address=/atlas/${VPN_IP}
address=/atlas.local/${VPN_IP}
EOF

# Ensure dnsmasq starts after WireGuard so the wg0 interface exists
DROPIN="/etc/systemd/system/dnsmasq.service.d"
mkdir -p "$DROPIN"
cat > "$DROPIN/after-wg.conf" <<'EOF'
[Unit]
After=wg-quick@wg0.service
EOF

systemctl daemon-reload
systemctl enable dnsmasq
systemctl restart dnsmasq 2>/dev/null || true
echo "    dnsmasq active — VPN clients resolve atlas / atlas.local → ${VPN_IP}"

# ── 5. Ensure atlas-control starts after nginx ────────────────────────────────
ATLAS_DROPIN="/etc/systemd/system/atlas-control.service.d"
mkdir -p "$ATLAS_DROPIN"
cat > "$ATLAS_DROPIN/after-nginx.conf" <<'EOF'
[Unit]
After=nginx.service
EOF
systemctl daemon-reload
systemctl restart atlas-control

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "==> HTTPS + DNS setup complete!"
echo ""
echo "    On your WiFi / LAN:"
echo "      https://atlas.local      ← works on all devices, no port needed"
echo ""
echo "    Over WireGuard VPN:"
echo "      https://atlas            ← works after connecting VPN, no port needed"
echo ""
echo "    First visit: browser will warn about the self-signed cert."
echo "    Click 'Advanced' → 'Proceed' (Chrome) or 'Accept the Risk' (Firefox)."
echo "    On iPhone: tap 'Show Details' → 'visit this website' → 'Visit Website'."
echo "    Do this once per device — geolocation will then work."
echo ""

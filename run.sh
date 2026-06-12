#!/bin/bash
# ═══════════════════════════════════════════════════
# Atlas Control — Launch Script
# ═══════════════════════════════════════════════════
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Default args
PORT="AUTO"
HOST="0.0.0.0"
WEB_PORT=5000
EXTRA_ARGS=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift 2;;
        --host) HOST="$2"; shift 2;;
        --web-port) WEB_PORT="$2"; shift 2;;
        --demo) EXTRA_ARGS="$EXTRA_ARGS --demo"; shift;;
        *) EXTRA_ARGS="$EXTRA_ARGS $1"; shift;;
    esac
done

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Atlas Control                          ║"
echo "  ║   http://$HOST:$WEB_PORT                 ║"
echo "  ║   Device: $PORT                          ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

python3 app.py --port "$PORT" --host "$HOST" --web-port "$WEB_PORT" $EXTRA_ARGS

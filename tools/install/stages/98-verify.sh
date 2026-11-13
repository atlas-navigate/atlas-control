# 98 — Verify: does this unit now match its release?
# The same read-only report (atlas-engine check) a technician or a later
# support session gets: system files and settings, services, power mode,
# kernel settings, swap, Ollama's settings and version, every model, Ray on
# the GPU, the Atlas button, firewall and open ports, lock-down, identity,
# storage.
section "Verify"

ENGINE_BIN="$R/usr/local/lib/atlas/bin/atlas-engine"
if "$ENGINE_BIN" check; then
    VERIFY_OK=true
    log "This unit matches its release"
else
    VERIFY_OK=false
    echo -e "${RED}[✗]${NC} This unit does not match its release — each [FAIL] line above names the fix." >&2
fi
if (( ${#MODELS_MISSING[@]} )); then
    VERIFY_OK=false
    echo -e "${RED}[✗]${NC} Ray is missing: ${MODELS_MISSING[*]} — re-run install.sh online, or from a payload drive that has them." >&2
fi

# The Atlas button: the first owner pairs with it, and holding it 15 s resets
# Atlas. A unit without it can't be set up the intended way.
# (Standard library and python3-smbus only, so the system Python runs it.)
BUTTON_SRC="$R/opt/atlas/current/status_button.py"
[[ -f "$BUTTON_SRC" ]] || BUTTON_SRC="$SRC_DIR/status_button.py"
set +e
BUTTON_OUT=$("$SYS_PY" -I "$BUTTON_SRC" --probe 2>&1)
BUTTON_RC=$?
set -e
case "$BUTTON_RC" in
    0) log "Atlas button found ($BUTTON_OUT)" ;;
    3) VERIFY_OK=false
       echo -e "${RED}[✗]${NC} The Atlas button is on header pins 3/5 (I2C bus 7, 400 kHz), where it stops responding." >&2
       echo "    Move its D wire to pin 27 and C to pin 28 (+ to pin 1, − to pin 6), then re-run: sudo ./install.sh --bundle DIR --only verify" >&2 ;;
    *) if [[ -n "$DEV_KEY" ]]; then
           warn "No Atlas button found — owners pair with it and hold it to reset Atlas (wiring: docs/MANUFACTURING.md)"
       else
           VERIFY_OK=false
           echo -e "${RED}[✗]${NC} No Atlas button found — this unit must not ship without it (wiring: docs/MANUFACTURING.md)." >&2
           [[ -n "$BUTTON_OUT" ]] && echo "    $BUTTON_OUT" >&2
       fi ;;
esac

# 98 — Verify: does this unit now match its release?
# The same read-only report (atlas-engine check) a technician or a later
# support session gets: system files and settings, services, power mode,
# kernel settings, swap, Ollama's settings and version, every model, Ray on
# the GPU, firewall and open ports, lock-down, identity, storage.
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

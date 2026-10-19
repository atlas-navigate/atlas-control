# Shared helpers for install.sh and its stages (tools/install/stages/*.sh).
# Sourced, never run. Every stage is idempotent: it checks what is already in
# place and only does what is missing, so install.sh can be re-run at any time
# (after a failure, on a configured unit, after an update) without harm.
#
# R is the filesystem prefix for every system path: empty on a unit. Tests
# (tests/test_installer.py) set ATLAS_ROOT to a sandbox and put stub commands
# first on PATH, so the whole installer runs without touching the machine —
# the same arrangement the update engine's tests use.
R="${ATLAS_ROOT:-}"
R="${R%/}"
# The distribution's Python 3.12 runs the release's own tools (migrate,
# handoff, model_profiles); tests substitute a recording stub.
SYS_PY="${ATLAS_SYS_PYTHON:-/usr/bin/python3}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GRN}[✓]${NC} $*"; }
info() { echo -e "${CYN}[→]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

section() {
    echo ""
    echo -e "${BLD}${CYN}══ $* ══${NC}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# retry ATTEMPTS FIRST_DELAY_S CMD... — for network steps (apt, downloads,
# model pulls); the delay doubles after each failure.
retry() {
    local attempts=$1 delay=$2 i
    shift 2
    for (( i = 1; ; i++ )); do
        "$@" && return 0
        (( i >= attempts )) && return 1
        warn "  attempt $i/$attempts failed — retrying in ${delay}s"
        sleep "$delay"
        delay=$(( delay * 2 ))
    done
}

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

# kit_sysfiles SRC... — install these of the release kit's system files now,
# for the steps that need them before the release (swap for the dataset
# builds, Ollama's settings for the model pulls). They are rendered and placed
# exactly as the update engine does, so the release later finds them correct
# and updates keep owning them. Prints each destination it changed.
kit_sysfiles() {
    ATLAS_ROOT="${R:-/}" "$SYS_PY" -I "$SRC_DIR/tools/install/kit_sysfile.py" "$SRC_DIR" "$@"
}

# Datasets belong to the setup account; data/ logs/ updates/ cache/ belong to
# the 'atlas' service account once Atlas is installed — never take them back.
# The service account must still be able to read the datasets: osrm-extract
# writes each graph's .fileIndex as 0700, and with it unreadable osrm-routed
# exits at once and every route falls back to a straight line.
own_datasets() {
    if id -u atlas >/dev/null 2>&1; then
        find "$APP_DIR" \( -path "$APP_DIR/data" -o -path "$APP_DIR/logs" -o -path "$APP_DIR/updates" \
             -o -path "$APP_DIR/cache" \) -prune -o -exec chown -h "$ATLAS_USER:$ATLAS_USER" {} +
    else
        chown -R "$ATLAS_USER:$ATLAS_USER" "$APP_DIR"
    fi
    if [[ -d "$APP_DIR/osrm-data" ]]; then
        chmod -R a+rX "$APP_DIR/osrm-data"
    fi
}

# json_field FILE KEY — one top-level field of a JSON file.
json_field() {
    "$SYS_PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$1" "$2"
}

# US states → Geofabrik extract names (stage 80; install.sh checks --states).
declare -A GF=(
    [AL]=alabama         [AK]=alaska          [AZ]=arizona
    [AR]=arkansas        [CA]=california       [CO]=colorado
    [CT]=connecticut     [DE]=delaware         [FL]=florida
    [GA]=georgia         [HI]=hawaii           [ID]=idaho
    [IL]=illinois        [IN]=indiana          [IA]=iowa
    [KS]=kansas          [KY]=kentucky         [LA]=louisiana
    [ME]=maine           [MD]=maryland         [MA]=massachusetts
    [MI]=michigan        [MN]=minnesota        [MS]=mississippi
    [MO]=missouri        [MT]=montana          [NE]=nebraska
    [NV]=nevada          [NH]=new-hampshire    [NJ]=new-jersey
    [NM]=new-mexico      [NY]=new-york         [NC]=north-carolina
    [ND]=north-dakota    [OH]=ohio             [OK]=oklahoma
    [OR]=oregon          [PA]=pennsylvania     [RI]=rhode-island
    [SC]=south-carolina  [SD]=south-dakota     [TN]=tennessee
    [TX]=texas           [UT]=utah             [VT]=vermont
    [VA]=virginia        [WA]=washington       [WV]=west-virginia
    [WI]=wisconsin       [WY]=wyoming          [DC]=district-of-columbia
)


# normalize_states TEXT — "all", "none", "keep", or the abbreviations in TEXT
# (spaces or commas, any case) upper-cased; fails on an unknown state.
normalize_states() {
    local text="${1//,/ }" word out=()
    case "${text,,}" in
        all|none|keep) echo "${text,,}"; return 0 ;;
    esac
    for word in $text; do
        word="${word^^}"
        [[ -v GF[$word] ]] || { echo "$word"; return 1; }
        out+=("$word")
    done
    [[ ${#out[@]} -gt 0 ]] || { echo "(empty)"; return 1; }
    echo "${out[*]}"
}

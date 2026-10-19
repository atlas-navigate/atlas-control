#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║              ATLAS CONTROL — UNIT INSTALLER                              ║
# ║              Jetson Orin Nano (ARM64) · Ubuntu 24.04 (JetPack 7)         ║
# ║                                                                          ║
# ║  The technician's step after the data payload is on /atlas_data          ║
# ║  (tools/manufacturing/atlas-provision.sh runs it):                       ║
# ║                                                                          ║
# ║  Usage:  sudo ./install.sh --bundle DIR [options]                        ║
# ║    --bundle DIR    a signed Atlas release (atlas-release.json, its .sig  ║
# ║                    and the bundle)                                       ║
# ║    --labels DIR    where to write this unit's label files                ║
# ║    --dev-key PUB   developer unit: trust this key, keep SSH, skip retail ║
# ║                    lock-down (never for sold units)                      ║
# ║    --states LIST   routing data: all | keep | none | "MD VA ..."         ║
# ║                    (default: ask on a terminal, else keep)               ║
# ║    --yes           never ask anything (unattended)                       ║
# ║    --only STAGES   run only these stages, e.g. 90,98 or models,verify    ║
# ║    --skip STAGES   run all but these (preflight and summary always run   ║
# ║                    unless named here)                                    ║
# ║    --log FILE      also write the output here (default:                  ║
# ║                    /var/log/atlas-install/install-<time>.log)            ║
# ║                                                                          ║
# ║  Stages (tools/install/stages/, in order; each skips what is in place,  ║
# ║  so a re-run — after a failure, or on a working unit — is safe):         ║
# ║    00 preflight   platform, OS, Python, release signature                ║
# ║    10 storage     /atlas_data and the dataset builders                   ║
# ║    20 packages    system packages (only the missing ones)                ║
# ║    30 swap        zram + disk swap (the release's own files)             ║
# ║    40 builder-venv  the dataset builders' Python environment             ║
# ║    50 ollama      Ollama (the tested version) + Atlas's settings for it  ║
# ║    55 uart        Jetson UART1 DMA fix for the 40-pin mesh radio         ║
# ║    60 osrm        routing engine                                         ║
# ║    70 maps        basemap, fonts, topo, cities                           ║
# ║    80 routing     state routing graphs                                   ║
# ║    85 places      trails, address search, geocoder data                  ║
# ║    90 models      every model Ray offers                                 ║
# ║    95 release     the Atlas software, through its update engine: first   ║
# ║                   install, update, or re-apply of the installed release  ║
# ║    96 identity    this unit's name and printable label                   ║
# ║    97 retail      lock-down (tools/manufacturing/finalize-retail.sh)     ║
# ║    98 verify      atlas-engine check: the unit matches its release       ║
# ║    99 summary                                                            ║
# ║                                                                          ║
# ║  Software updates never use this script: Atlas installs signed releases ║
# ║  itself (Settings → Software Update). docs/INSTALL.md has the details.  ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -euo pipefail
# A bare `set -e` death is easy to miss in a long log; report it with the
# stage it happened in. The [✗] marker is what atlas-provision.sh and a
# technician look for. Subshell filter: $(...) substitution failures fire ERR
# too but don't kill the script — only report the failure that aborts.
set -E
CURRENT_STAGE="startup"
trap '(( BASH_SUBSHELL == 0 )) && echo -e "\033[0;31m[✗]\033[0m install.sh stopped in stage $CURRENT_STAGE (${BASH_SOURCE[0]##*/} line $LINENO: $BASH_COMMAND) — fix the cause and re-run; finished steps are skipped" >&2 || true' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR"
[[ -f "$SRC_DIR/tools/install/lib.sh" && -f "$SRC_DIR/app.py" ]] \
    || { echo "install.sh must sit in an Atlas release kit or checkout (tools/install/ and the app beside it)." >&2; exit 1; }
# shellcheck source=tools/install/lib.sh
source "$SRC_DIR/tools/install/lib.sh"

# ── Options ─────────────────────────────────────────────────────────────────
BUNDLE=""
LABELS_DIR=""
DEV_KEY=""
STATES=""
ASSUME_YES=""
ONLY=""
SKIP=""
INSTALL_LOG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle)  BUNDLE="${2:-}"; shift 2 ;;
        --labels)  LABELS_DIR="${2:-}"; shift 2 ;;
        --dev-key) DEV_KEY="${2:-}"; shift 2 ;;
        --states)  STATES="${2:-}"; shift 2 ;;
        --yes|-y)  ASSUME_YES=1; shift ;;
        --only)    ONLY="${2:-}"; shift 2 ;;
        --skip)    SKIP="${2:-}"; shift 2 ;;
        --log)     INSTALL_LOG="${2:-}"; shift 2 ;;
        --full|--fresh) shift ;;          # accepted for old runbooks; every run is both
        --update|update)
            die "Atlas updates itself from signed releases (Settings → Software Update);
    install.sh only sets up units. On a developer unit use tools/dev/deploy." ;;
        -h|--help)
            sed -n '3,46p' "$SCRIPT_DIR/install.sh" | sed 's/^# ║ \{0,2\}//; s/ *║$//'
            exit 0 ;;
        *) die "Unknown option: $1  (try --help)" ;;
    esac
done
[[ -n "$BUNDLE" && -f "$BUNDLE/atlas-release.json" ]] \
    || die "Give the signed Atlas release to install:  sudo ./install.sh --bundle DIR"
BUNDLE="$(cd "$BUNDLE" && pwd)"
[[ -z "$DEV_KEY" || -f "$DEV_KEY" ]] || die "Developer key not found: $DEV_KEY"
if [[ -n "$STATES" ]]; then
    STATES="$(normalize_states "$STATES")" \
        || die "Unknown state: $STATES (use abbreviations such as MD VA, or all / keep / none)"
fi

# ── Must run as root (a test sandbox, ATLAS_ROOT, runs as anyone) ───────────
[[ $EUID -eq 0 || -n "$R" ]] || die "Run with sudo:  sudo ./install.sh --bundle DIR"
# The setup account (admin): owns the datasets and the dataset builders.
# Atlas itself runs as the unprivileged 'atlas' account the release creates.
ATLAS_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [[ -z "$ATLAS_USER" || "$ATLAS_USER" == "root" ]] || ! getent passwd "$ATLAS_USER" > /dev/null; then
    ATLAS_USER="$(stat -c %U "$R/atlas_data/atlas-control" 2>/dev/null || echo ubuntu)"
fi
ATLAS_HOME="$(getent passwd "$ATLAS_USER" | cut -d: -f6)" \
    || die "Cannot resolve home directory for user '$ATLAS_USER'"
# systemd-run units have no $HOME; tools like the ollama CLI panic without it.
export HOME="${HOME:-/root}"

ATLAS_DATA="$R/atlas_data"
APP_DIR="$ATLAS_DATA/atlas-control"
OLLAMA_DATA_DIR="$ATLAS_DATA/ollama"
VENV="$APP_DIR/venv"
RECOMMENDED_FREE_GB=250
# Set by the stages that produce them; defaults for stages left out.
RAY_MODELS=()
MODELS_MISSING=()
OSRM_MMAP_OK=true
ROUTING_STATES=""
VERIFY_OK=true
UNIT_NAME=""
UNIT_HOST=""

# ── Log: everything on screen also goes to a root-only file ─────────────────
LOG_DIR="$R/var/log/atlas-install"
INSTALL_LOG="${INSTALL_LOG:-$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$LOG_DIR" "$(dirname "$INSTALL_LOG")"
chmod 700 "$LOG_DIR"
( umask 077; : >> "$INSTALL_LOG" )
# The last 10 install logs are kept.
find "$LOG_DIR" -maxdepth 1 -name 'install-*.log' -printf '%T@ %p\n' \
    | sort -rn | tail -n +11 | cut -d' ' -f2- | xargs -r rm -f
exec > >(tee -a "$INSTALL_LOG") 2>&1

echo -e "${BLD}${CYN}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║          ATLAS CONTROL — INSTALLER                  ║"
echo "  ║          Offline GPS Nav + Mesh Communications      ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Release            : ${BLD}$(json_field "$BUNDLE/atlas-release.json" version)${NC}"
echo -e "  Unit type          : ${BLD}$([[ -n "$DEV_KEY" ]] && echo "developer" || echo "retail")${NC}"
echo -e "  Installing for user: ${BLD}${ATLAS_USER}${NC}"
echo -e "  Source directory   : ${BLD}${SRC_DIR}${NC}"
echo -e "  Started            : ${BLD}$(date '+%Y-%m-%d %H:%M:%S')${NC}  (log: ${INSTALL_LOG#$R})"
echo ""

# ── Stages ──────────────────────────────────────────────────────────────────
# A stage is named by its number, its name or both: 90, models, 90-models.
_stage_in() {
    local stage="$1" list="${2//,/ }" word num="${1%%-*}" name="${1#*-}"
    for word in $list; do
        [[ "$word" == "$stage" || "$word" == "$num" || "$word" == "$name" ]] && return 0
    done
    return 1
}
STAGES=()
for f in "$SRC_DIR"/tools/install/stages/[0-9][0-9]-*.sh; do
    STAGES+=("$(basename "$f" .sh)")
done
for word in ${ONLY//,/ } ${SKIP//,/ }; do
    found=""
    for stage in "${STAGES[@]}"; do _stage_in "$stage" "$word" && found=1; done
    [[ -n "$found" ]] || die "No such stage: $word  (stages: ${STAGES[*]})"
done

for stage in "${STAGES[@]}"; do
    if _stage_in "$stage" "$SKIP"; then
        info "Stage $stage skipped (--skip)"
        continue
    fi
    if [[ -n "$ONLY" ]] && ! _stage_in "$stage" "$ONLY" && [[ "$stage" != 00-* && "$stage" != 99-* ]]; then
        continue
    fi
    CURRENT_STAGE="$stage"
    STAGE_START=$SECONDS
    # shellcheck source=/dev/null
    source "$SRC_DIR/tools/install/stages/$stage.sh"
    echo "    (stage $stage: $(( SECONDS - STAGE_START )) s)"
done

#!/usr/bin/env bash
# ============================================================
# Atlas Control — OSRM Native Reprocessor
#
# Reprocesses all state OSRM data using the locally installed
# v5.27.1 native binaries (replaces v6-format data that the
# native binary cannot read).
#
# All 50 state PBF files must be present at:
#   /atlas_data/atlas-control/osrm-data/<state>.osm.pbf
#
# Usage:
#   bash reprocess_osrm.sh                  # all states
#   bash reprocess_osrm.sh maryland         # one state only
#   bash reprocess_osrm.sh --force          # overwrite even if .processed exists
#   bash reprocess_osrm.sh maryland --force
#
# Estimated time: ~5-15 min per state on Jetson Orin Nano
# Runs in the background; log output goes to reprocess_osrm.log
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/osrm-data"
STATES_DIR="$DATA_DIR/states"

CAR_LUA="/usr/local/share/osrm/profiles/car.lua"
HIKING_LUA="$SCRIPT_DIR/hiking.lua"

EXTRACT="/usr/local/bin/osrm-extract"
PARTITION="/usr/local/bin/osrm-partition"
CUSTOMIZE="/usr/local/bin/osrm-customize"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

FORCE=false
TARGET_STATE=""

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --*) echo "Unknown option: $arg"; exit 1 ;;
        *) TARGET_STATE="${arg,,}" ;;
    esac
done

# Preflight checks
for BIN in "$EXTRACT" "$PARTITION" "$CUSTOMIZE"; do
    [[ -x "$BIN" ]] || { echo -e "${RED}✗ Missing binary: $BIN${NC}"; exit 1; }
done
for LUA in "$CAR_LUA" "$HIKING_LUA"; do
    [[ -f "$LUA" ]] || { echo -e "${RED}✗ Missing Lua profile: $LUA${NC}"; exit 1; }
done

ALL_STATES=(
    alabama alaska arizona arkansas california colorado connecticut
    delaware florida georgia hawaii idaho illinois indiana iowa
    kansas kentucky louisiana maine maryland massachusetts michigan
    minnesota mississippi missouri montana nebraska nevada
    new-hampshire new-jersey new-mexico new-york north-carolina
    north-dakota ohio oklahoma oregon pennsylvania rhode-island
    south-carolina south-dakota tennessee texas utah vermont
    virginia washington west-virginia wisconsin wyoming
)

[[ -n "$TARGET_STATE" ]] && ALL_STATES=("$TARGET_STATE")

declare -A PROFILE_LUA=(
    [car]="$CAR_LUA"
    [hiking]="$HIKING_LUA"
)

TOTAL=${#ALL_STATES[@]}
DONE=0; FAILED=0
START=$(date +%s)

echo -e "${CYAN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║  Atlas OSRM Reprocessor — v5.27.1 native    ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "States:  ${BOLD}${ALL_STATES[*]}${NC}"
echo -e "Profiles: car, hiking"
echo -e "Force:   $FORCE"
echo ""

process_profile() {
    local STATE="$1" PROFILE="$2"
    local LUA="${PROFILE_LUA[$PROFILE]}"
    local PBF="$DATA_DIR/${STATE}.osm.pbf"
    local PDIR="$STATES_DIR/$STATE/$PROFILE"
    local MARKER="$PDIR/.processed"

    if [[ ! -f "$PBF" ]]; then
        echo -e "  ${RED}✗ PBF missing: $PBF — cannot process $PROFILE${NC}"
        return 1
    fi

    # Detect incompatible v6 data: run osrm-routed briefly, capture output
    local NEEDS_REPROCESS=false
    if [[ "$FORCE" == "true" ]]; then
        NEEDS_REPROCESS=true
    elif [[ ! -f "$MARKER" ]]; then
        NEEDS_REPROCESS=true
    else
        local CHECK_OUT
        CHECK_OUT=$(timeout 3 /usr/local/bin/osrm-routed --algorithm mld \
            --port 59877 --ip 127.0.0.1 "$PDIR/region.osrm" 2>&1 || true)
        if [[ "$CHECK_OUT" == *"incompatible"* ]]; then
            echo -e "  ${YELLOW}⚠ $PROFILE has incompatible v6.0.0 data — reprocessing${NC}"
            NEEDS_REPROCESS=true
        else
            echo -e "  ${GREEN}✓ $PROFILE already valid — skip${NC}"
            return 0
        fi
    fi

    mkdir -p "$PDIR"
    rm -f "$MARKER"
    # Remove old incompatible files
    rm -f "$PDIR"/region.osrm.* 2>/dev/null || true

    local TMPBF="$PDIR/region.osm.pbf"
    echo -e "  ${YELLOW}→ $PROFILE: extract ($(du -sh "$PBF" | cut -f1) PBF)...${NC}"
    cp "$PBF" "$TMPBF"

    "$EXTRACT" -p "$LUA" "$TMPBF" 2>&1 || {
        echo -e "  ${RED}✗ extract failed for $STATE/$PROFILE${NC}"
        rm -f "$TMPBF"
        return 1
    }
    # In v5 osrm-extract writes region.osrm.cnbg (not a literal region.osrm file)
    if [[ ! -f "$PDIR/region.osrm.cnbg" ]]; then
        echo -e "  ${RED}✗ extract produced no output for $STATE/$PROFILE${NC}"
        rm -f "$TMPBF"
        return 1
    fi

    echo -e "  ${YELLOW}→ $PROFILE: partition...${NC}"
    "$PARTITION" "$PDIR/region.osrm" 2>&1 || {
        echo -e "  ${RED}✗ partition failed for $STATE/$PROFILE${NC}"
        rm -f "$TMPBF"
        return 1
    }

    echo -e "  ${YELLOW}→ $PROFILE: customize...${NC}"
    "$CUSTOMIZE" "$PDIR/region.osrm" 2>&1 || {
        echo -e "  ${RED}✗ customize failed for $STATE/$PROFILE${NC}"
        rm -f "$TMPBF"
        return 1
    }

    rm -f "$TMPBF"
    touch "$MARKER"
    echo -e "  ${GREEN}✓ $PROFILE done ($(du -sh "$PDIR" | cut -f1))${NC}"
}

for STATE in "${ALL_STATES[@]}"; do
    echo -e "${BOLD}── $STATE ────────────────────────────────────────${NC}"
    STATE_FAIL=false
    for PROFILE in car hiking; do
        if ! process_profile "$STATE" "$PROFILE"; then
            STATE_FAIL=true
            FAILED=$((FAILED + 1))
        fi
    done
    $STATE_FAIL || DONE=$((DONE + 1))

    ELAPSED=$(( $(date +%s) - START ))
    if [[ $DONE -gt 0 ]]; then
        AVG=$(( ELAPSED / DONE ))
        ETA=$(( (TOTAL - DONE - FAILED) * AVG ))
        echo -e "  ${CYAN}Progress: ${DONE}/${TOTAL} | elapsed ${ELAPSED}s | ETA ~${ETA}s${NC}"
    fi
    echo ""
done

ELAPSED=$(( $(date +%s) - START ))
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}Done! ${DONE}/${TOTAL} states processed in ${ELAPSED}s${NC}"
[[ $FAILED -gt 0 ]] && echo -e "${RED}  ${FAILED} state(s) failed — check output above${NC}"
echo ""
echo "Restart Atlas to load new routing data:"
echo "  sudo systemctl restart atlas-control"
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"

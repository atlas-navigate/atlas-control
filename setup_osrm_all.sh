#!/usr/bin/env bash
# ============================================================
# Atlas Control — Whole-US OSRM Preprocessing
# Processes all 50 states one at a time so they fit in RAM.
# Each state gets its own directory under osrm-data/states/
# and its own Docker container when served.
#
# Usage:
#   bash setup_osrm_all.sh              (process all 50 states)
#   bash setup_osrm_all.sh georgia      (single state)
#   bash setup_osrm_all.sh southeast    (region preset)
#
# This will take many hours — run it overnight or in a tmux session.
# It is safe to Ctrl-C and restart — completed states are skipped.
#
# After this completes, use start_routing.sh to load states for serving.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATES_DIR="$SCRIPT_DIR/osrm-data/states"
SRC_DIR="$SCRIPT_DIR/osrm-data"          # where individual state .pbf files live

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   Atlas Control — Whole-US OSRM Setup        ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Preflight ─────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
    echo -e "${RED}✗ Docker not available.${NC}"; exit 1
fi
echo -e "${GREEN}✓ Docker OK${NC}"
docker pull ghcr.io/project-osrm/osrm-backend:latest -q && echo -e "${GREEN}✓ OSRM image ready${NC}"

# ── State → Geofabrik path ────────────────────────────────────────────────────
declare -A STATES=(
  [alabama]="north-america/us/alabama-latest.osm.pbf"
  [alaska]="north-america/us/alaska-latest.osm.pbf"
  [arizona]="north-america/us/arizona-latest.osm.pbf"
  [arkansas]="north-america/us/arkansas-latest.osm.pbf"
  [california]="north-america/us/california-latest.osm.pbf"
  [colorado]="north-america/us/colorado-latest.osm.pbf"
  [connecticut]="north-america/us/connecticut-latest.osm.pbf"
  [delaware]="north-america/us/delaware-latest.osm.pbf"
  [florida]="north-america/us/florida-latest.osm.pbf"
  [georgia]="north-america/us/georgia-latest.osm.pbf"
  [hawaii]="north-america/us/hawaii-latest.osm.pbf"
  [idaho]="north-america/us/idaho-latest.osm.pbf"
  [illinois]="north-america/us/illinois-latest.osm.pbf"
  [indiana]="north-america/us/indiana-latest.osm.pbf"
  [iowa]="north-america/us/iowa-latest.osm.pbf"
  [kansas]="north-america/us/kansas-latest.osm.pbf"
  [kentucky]="north-america/us/kentucky-latest.osm.pbf"
  [louisiana]="north-america/us/louisiana-latest.osm.pbf"
  [maine]="north-america/us/maine-latest.osm.pbf"
  [maryland]="north-america/us/maryland-latest.osm.pbf"
  [massachusetts]="north-america/us/massachusetts-latest.osm.pbf"
  [michigan]="north-america/us/michigan-latest.osm.pbf"
  [minnesota]="north-america/us/minnesota-latest.osm.pbf"
  [mississippi]="north-america/us/mississippi-latest.osm.pbf"
  [missouri]="north-america/us/missouri-latest.osm.pbf"
  [montana]="north-america/us/montana-latest.osm.pbf"
  [nebraska]="north-america/us/nebraska-latest.osm.pbf"
  [nevada]="north-america/us/nevada-latest.osm.pbf"
  [new-hampshire]="north-america/us/new-hampshire-latest.osm.pbf"
  [new-jersey]="north-america/us/new-jersey-latest.osm.pbf"
  [new-mexico]="north-america/us/new-mexico-latest.osm.pbf"
  [new-york]="north-america/us/new-york-latest.osm.pbf"
  [north-carolina]="north-america/us/north-carolina-latest.osm.pbf"
  [north-dakota]="north-america/us/north-dakota-latest.osm.pbf"
  [ohio]="north-america/us/ohio-latest.osm.pbf"
  [oklahoma]="north-america/us/oklahoma-latest.osm.pbf"
  [oregon]="north-america/us/oregon-latest.osm.pbf"
  [pennsylvania]="north-america/us/pennsylvania-latest.osm.pbf"
  [rhode-island]="north-america/us/rhode-island-latest.osm.pbf"
  [south-carolina]="north-america/us/south-carolina-latest.osm.pbf"
  [south-dakota]="north-america/us/south-dakota-latest.osm.pbf"
  [tennessee]="north-america/us/tennessee-latest.osm.pbf"
  [texas]="north-america/us/texas-latest.osm.pbf"
  [utah]="north-america/us/utah-latest.osm.pbf"
  [vermont]="north-america/us/vermont-latest.osm.pbf"
  [virginia]="north-america/us/virginia-latest.osm.pbf"
  [washington]="north-america/us/washington-latest.osm.pbf"
  [west-virginia]="north-america/us/west-virginia-latest.osm.pbf"
  [wisconsin]="north-america/us/wisconsin-latest.osm.pbf"
  [wyoming]="north-america/us/wyoming-latest.osm.pbf"
)

declare -A REGIONS=(
  [northeast]="maine new-hampshire vermont massachusetts rhode-island connecticut new-york new-jersey pennsylvania delaware maryland"
  [southeast]="virginia west-virginia north-carolina south-carolina georgia florida tennessee alabama mississippi kentucky"
  [midwest]="ohio indiana illinois michigan wisconsin minnesota iowa missouri north-dakota south-dakota nebraska kansas"
  [south]="texas oklahoma arkansas louisiana"
  [mountain]="montana wyoming colorado utah idaho nevada"
  [southwest]="arizona new-mexico"
  [west]="california oregon washington"
  [noncontiguous]="alaska hawaii"
  [all]="alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada new-hampshire new-jersey new-mexico new-york north-carolina north-dakota ohio oklahoma oregon pennsylvania rhode-island south-carolina south-dakota tennessee texas utah vermont virginia washington west-virginia wisconsin wyoming"
)

# ── Status subcommand ─────────────────────────────────────────────────────────
if [[ "${1:-}" == "status" ]]; then
    echo -e "${CYAN}${BOLD}OSRM Data Status${NC}"
    ALL_STATES="${REGIONS[all]}"
    for STATE in $ALL_STATES; do
        PBF="$SRC_DIR/${STATE}.osm.pbf"
        PBF_SIZE=$(stat -c%s "$PBF" 2>/dev/null || echo 0)
        car_ok="✗"; foot_ok="✗"; hike_ok="✗"
        [[ -f "$STATES_DIR/$STATE/car/.processed"    ]] && car_ok="✓"
        [[ -f "$STATES_DIR/$STATE/foot/.processed"   ]] && foot_ok="✓"
        [[ -f "$STATES_DIR/$STATE/hiking/.processed" ]] && hike_ok="✓"
        pbf_info=""
        [[ "$PBF_SIZE" -gt 0 ]] && pbf_info=" (pbf ready)" || pbf_info=" (no pbf)"
        if [[ "$car_ok" == "✓" && "$foot_ok" == "✓" && "$hike_ok" == "✓" ]]; then
            echo -e "  ${GREEN}✓ $STATE — car foot hike${NC}"
        else
            echo -e "  ${YELLOW}  $STATE — car:$car_ok foot:$foot_ok hike:$hike_ok$pbf_info${NC}"
        fi
    done
    exit 0
fi

# ── Profile selection ─────────────────────────────────────────────────────────
# Non-interactive: PROF_CHOICE env var, or --profiles flag, or prompted
if [[ -z "${PROF_CHOICE:-}" ]]; then
    for _arg in "$@"; do
        if [[ "$_arg" == --profiles=* ]]; then
            _pval="${_arg#--profiles=}"
            case "$_pval" in
                car)                PROF_CHOICE=1 ;;
                car+foot+hiking)    PROF_CHOICE=2 ;;
                all)                PROF_CHOICE=3 ;;
            esac
        fi
    done
fi
if [[ -z "${PROF_CHOICE:-}" ]]; then
    echo "Which routing profiles do you need?"
    echo "  1) Driving only"
    echo "  2) Driving + Walking + Hiking  [recommended]"
    echo "  3) All: Driving + Walking + Hiking + Cycling"
    echo ""
    read -rp "Choice [2]: " PROF_CHOICE
fi
PROF_CHOICE="${PROF_CHOICE:-2}"
PROFILES=("car")
[[ "$PROF_CHOICE" -ge 2 ]] && PROFILES+=("foot" "hiking")
[[ "$PROF_CHOICE" -ge 3 ]] && PROFILES+=("bicycle")
echo -e "${YELLOW}→ Profiles: ${PROFILES[*]}${NC}\n"

# ── Resolve target states ─────────────────────────────────────────────────────
TARGET_STATES=()
# Strip --profiles=... from positional args
_POSITIONAL=()
for _arg in "$@"; do
    [[ "$_arg" == --profiles=* ]] || _POSITIONAL+=("$_arg")
done
ARG="${_POSITIONAL[0]:-all}"

if [[ -n "${REGIONS[${ARG,,}]:-}" ]]; then
    read -ra TARGET_STATES <<< "${REGIONS[${ARG,,}]}"
elif [[ -n "${STATES[${ARG,,}]:-}" ]]; then
    TARGET_STATES=("${ARG,,}")
else
    echo -e "${RED}Unknown state or region: $ARG${NC}"
    echo "Valid regions: ${!REGIONS[*]}"
    exit 1
fi

echo -e "Processing ${#TARGET_STATES[@]} state(s): ${TARGET_STATES[*]}\n"

# ── Process each state ────────────────────────────────────────────────────────
mkdir -p "$STATES_DIR"
DONE=0; SKIPPED=0; FAILED=0

process_state() {
    local STATE="$1"
    local PBF_SRC="$SRC_DIR/${STATE}.osm.pbf"

    echo -e "${CYAN}${BOLD}━━━ $STATE ━━━${NC}"

    # Download PBF if not already present or if the file is empty (corrupted download)
    local PBF_SIZE
    PBF_SIZE=$(stat -c%s "$PBF_SRC" 2>/dev/null || echo 0)
    if [[ ! -f "$PBF_SRC" || "$PBF_SIZE" -eq 0 ]]; then
        [[ "$PBF_SIZE" -eq 0 && -f "$PBF_SRC" ]] && echo -e "${YELLOW}  → Re-downloading $STATE (empty file from previous failed download)...${NC}" \
            || echo -e "${YELLOW}  → Downloading $STATE...${NC}"
        local URL="https://download.geofabrik.de/${STATES[$STATE]}"
        rm -f "$PBF_SRC"
        wget -q --show-progress -O "$PBF_SRC" "$URL" || {
            echo -e "${RED}  ✗ Download failed for $STATE${NC}"
            rm -f "$PBF_SRC"
            return 1
        }
    else
        echo -e "${GREEN}  ✓ PBF already downloaded ($(du -sh "$PBF_SRC" | cut -f1))${NC}"
    fi

    for PROFILE in "${PROFILES[@]}"; do
        local PDIR="$STATES_DIR/$STATE/$PROFILE"
        local DONE_MARKER="$PDIR/.processed"

        if [[ -f "$DONE_MARKER" ]]; then
            echo -e "${GREEN}  ✓ $PROFILE already processed — skipping${NC}"
            continue
        fi

        # Clean up any interrupted partial processing (OSRM files without .processed marker)
        if [[ -d "$PDIR" ]] && compgen -G "$PDIR/region.osrm*" > /dev/null 2>&1; then
            echo -e "${YELLOW}  → Cleaning up interrupted $PROFILE processing...${NC}"
            rm -f "$PDIR"/region.osrm* "$PDIR"/region.osm.pbf
        fi

        echo -e "${YELLOW}  → Processing $PROFILE...${NC}"
        mkdir -p "$PDIR"
        cp "$PBF_SRC" "$PDIR/region.osm.pbf"

        # hiking.lua needs to be mounted into the container
        local LUA_MOUNT="" LUA_PATH="/opt/${PROFILE}.lua"
        if [[ "$PROFILE" == "hiking" ]]; then
            LUA_MOUNT="-v ${SCRIPT_DIR}/hiking.lua:/opt/hiking.lua"
            LUA_PATH="/opt/hiking.lua"
        fi

        docker run --rm -v "$PDIR:/data" $LUA_MOUNT \
            ghcr.io/project-osrm/osrm-backend osrm-extract -p "$LUA_PATH" /data/region.osm.pbf \
            2>&1
        [[ ${PIPESTATUS[0]} -ne 0 ]] && echo -e "${RED}  ✗ osrm-extract failed for $STATE/$PROFILE${NC}" && rm -f "$PDIR/region.osm.pbf" && return 1

        docker run --rm -v "$PDIR:/data" \
            ghcr.io/project-osrm/osrm-backend osrm-partition /data/region.osrm \
            2>&1
        [[ ${PIPESTATUS[0]} -ne 0 ]] && echo -e "${RED}  ✗ osrm-partition failed for $STATE/$PROFILE${NC}" && rm -f "$PDIR/region.osm.pbf" && return 1

        docker run --rm -v "$PDIR:/data" \
            ghcr.io/project-osrm/osrm-backend osrm-customize /data/region.osrm \
            2>&1
        [[ ${PIPESTATUS[0]} -ne 0 ]] && echo -e "${RED}  ✗ osrm-customize failed for $STATE/$PROFILE${NC}" && rm -f "$PDIR/region.osm.pbf" && return 1

        rm -f "$PDIR/region.osm.pbf"
        [[ -f "$PDIR/region.osrm" ]] || touch "$PDIR/region.osrm"
        touch "$DONE_MARKER"
        echo -e "${GREEN}  ✓ $PROFILE done ($(du -sh "$PDIR" | cut -f1))${NC}"
    done
}

for STATE in "${TARGET_STATES[@]}"; do
    if [[ -z "${STATES[$STATE]:-}" ]]; then
        echo -e "${RED}✗ Unknown state: $STATE — skipping${NC}"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Check if all profiles already done
    ALL_DONE=true
    for PROFILE in "${PROFILES[@]}"; do
        [[ ! -f "$STATES_DIR/$STATE/$PROFILE/.processed" ]] && ALL_DONE=false && break
    done
    if $ALL_DONE; then
        echo -e "${GREEN}✓ $STATE — all profiles already processed${NC}"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if process_state "$STATE"; then
        DONE=$((DONE + 1))
    else
        FAILED=$((FAILED + 1))
        echo -e "${YELLOW}  Continuing with next state...${NC}"
    fi
    echo ""
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD} Processing complete!${NC}"
echo ""
echo "  Processed : $DONE state(s)"
echo "  Skipped   : $SKIPPED (already done)"
echo "  Failed    : $FAILED"
echo ""
echo "  Data stored in: osrm-data/states/"
echo ""
echo "  Next: start routing for your area:"
echo "    bash start_routing.sh southeast"
echo "    bash start_routing.sh all          (heavy — loads all processed states)"
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"

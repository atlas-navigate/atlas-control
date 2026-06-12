#!/usr/bin/env bash
# ============================================================
# Atlas Control — Offline Navigation Setup
# Downloads and preprocesses OSM data for local OSRM routing.
#
# Usage:
#   bash setup_offline.sh              (interactive)
#   bash setup_offline.sh georgia      (US state, lowercase)
#   bash setup_offline.sh custom https://download.geofabrik.de/...
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/osrm-data"
COMPOSE="$SCRIPT_DIR/docker-compose.osrm.yml"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   Atlas Control — Offline Routing Setup      ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Preflight checks ──────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo -e "${RED}✗ Docker not found.${NC}"
    echo "  Install: sudo apt-get install -y docker.io"
    echo "           sudo systemctl enable --now docker"
    echo "           sudo usermod -aG docker \$USER  (then log out and back in)"
    exit 1
fi
if ! docker info &>/dev/null 2>&1; then
    echo -e "${RED}✗ Cannot reach Docker daemon.${NC}"
    echo "  Run: sudo usermod -aG docker \$USER  then log out/in, or use sudo."
    exit 1
fi
echo -e "${GREEN}✓ Docker OK${NC}"

# osmium-tool is needed to merge multi-state PBF files
HAVE_OSMIUM=false
if command -v osmium &>/dev/null; then
    HAVE_OSMIUM=true
    echo -e "${GREEN}✓ osmium-tool OK${NC}"
else
    echo -e "${YELLOW}⚠ osmium-tool not found (only needed for multi-state regions)${NC}"
    echo "  Install: sudo apt-get install -y osmium-tool"
fi

# ── Pull OSRM image (ARM64 / amd64 auto-detected) ────────────────────────────
echo -e "${YELLOW}→ Pulling ghcr.io/project-osrm/osrm-backend image...${NC}"
docker pull ghcr.io/project-osrm/osrm-backend:latest
echo -e "${GREEN}✓ Image ready${NC}"
echo ""

# ── US state → Geofabrik path map ────────────────────────────────────────────
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

# ── Multi-state region presets ────────────────────────────────────────────────
declare -A REGIONS=(
  [east-coast]="maine new-hampshire vermont massachusetts rhode-island connecticut new-york new-jersey pennsylvania delaware maryland virginia west-virginia north-carolina south-carolina georgia florida"
  [northeast]="maine new-hampshire vermont massachusetts rhode-island connecticut new-york new-jersey pennsylvania delaware maryland"
  [southeast]="virginia west-virginia north-carolina south-carolina georgia florida tennessee alabama mississippi"
  [south-atlantic]="virginia north-carolina south-carolina georgia florida"
  [midwest]="ohio indiana illinois michigan wisconsin minnesota iowa missouri north-dakota south-dakota nebraska kansas"
  [great-lakes]="ohio indiana illinois michigan wisconsin minnesota"
  [southwest]="texas oklahoma new-mexico arizona"
  [west-coast]="california oregon washington"
  [mountain-west]="montana wyoming colorado utah idaho nevada"
  [pacific]="california oregon washington alaska hawaii"
  [us-all]="alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada new-hampshire new-jersey new-mexico new-york north-carolina north-dakota ohio oklahoma oregon pennsylvania rhode-island south-carolina south-dakota tennessee texas utah vermont virginia washington west-virginia wisconsin wyoming"
)

# ── Resolve region ────────────────────────────────────────────────────────────
REGION_STATES=()   # non-empty → multi-state merge mode

if [[ $# -ge 2 && "${1:-}" == "custom" ]]; then
    OSM_URL="$2"
    REGION_NAME="custom"
elif [[ $# -ge 1 && -n "${REGIONS[${1,,}]:-}" ]]; then
    REGION_NAME="${1,,}"
    read -ra REGION_STATES <<< "${REGIONS[$REGION_NAME]}"
elif [[ $# -ge 1 && -n "${STATES[${1,,}]:-}" ]]; then
    REGION_NAME="${1,,}"
    OSM_URL="https://download.geofabrik.de/${STATES[$REGION_NAME]}"
else
    echo "Enter a US state, region preset, or full Geofabrik URL:"
    echo ""
    echo "  Single states : georgia, north-carolina, texas ..."
    echo "  Region presets: east-coast, northeast, southeast, west-coast,"
    echo "                  south-atlantic, midwest, great-lakes, southwest,"
    echo "                  mountain-west, pacific, us-all"
    echo "  Custom URL    : https://download.geofabrik.de/..."
    echo ""
    read -rp "Region: " INPUT
    INPUT="${INPUT,,}"
    if [[ "$INPUT" =~ ^https?:// ]]; then
        OSM_URL="$INPUT"
        REGION_NAME="custom"
    elif [[ -n "${REGIONS[$INPUT]:-}" ]]; then
        REGION_NAME="$INPUT"
        read -ra REGION_STATES <<< "${REGIONS[$INPUT]}"
    elif [[ -n "${STATES[$INPUT]:-}" ]]; then
        REGION_NAME="$INPUT"
        OSM_URL="https://download.geofabrik.de/${STATES[$INPUT]}"
    else
        echo -e "${RED}Unknown region: $INPUT${NC}"
        echo "Full state list: https://download.geofabrik.de/north-america/us.html"
        exit 1
    fi
fi

echo -e "${GREEN}Region: ${BOLD}$REGION_NAME${NC}"
if [[ ${#REGION_STATES[@]} -gt 0 ]]; then
    echo "States: ${REGION_STATES[*]}"
else
    echo "URL:    $OSM_URL"
fi
echo ""

# ── Profile selection ─────────────────────────────────────────────────────────
echo "Which routing profiles do you need?"
echo "  1) Driving only                              (~1 GB processed)"
echo "  2) Driving + Walking + Hiking                (~3 GB processed)  [recommended]"
echo "  3) All: Driving + Walking + Hiking + Cycling (~4 GB processed)"
echo ""
echo "  Walking uses the standard OSRM foot profile (sidewalks, footpaths)."
echo "  Hiking uses a custom trail-preference profile (paths/tracks over roads)."
echo ""
read -rp "Choice [2]: " PROF_CHOICE
PROF_CHOICE="${PROF_CHOICE:-2}"

PROFILES=("car")
[[ "$PROF_CHOICE" -ge 2 ]] && PROFILES+=("foot" "hiking")
[[ "$PROF_CHOICE" -ge 3 ]] && PROFILES+=("bicycle")

echo -e "\n${YELLOW}→ Building profiles: ${PROFILES[*]}${NC}\n"

# ── Download OSM extract ──────────────────────────────────────────────────────
mkdir -p "$DATA_DIR"
PBF="$DATA_DIR/region.osm.pbf"

if [[ ${#REGION_STATES[@]} -gt 0 ]]; then
    # ── Multi-state: download each state and merge ────────────────────────────
    if [[ "$HAVE_OSMIUM" == "false" ]]; then
        echo -e "${RED}✗ osmium-tool is required to merge multi-state regions.${NC}"
        echo "  Install: sudo apt-get install -y osmium-tool"
        exit 1
    fi

    STATE_PBFS=()
    echo -e "${YELLOW}→ Downloading ${#REGION_STATES[@]} state PBF files for '$REGION_NAME'...${NC}"
    for ST in "${REGION_STATES[@]}"; do
        if [[ -z "${STATES[$ST]:-}" ]]; then
            echo -e "${RED}  ✗ Unknown state in region: $ST${NC}"
            exit 1
        fi
        ST_URL="https://download.geofabrik.de/${STATES[$ST]}"
        ST_PBF="$DATA_DIR/${ST}.osm.pbf"
        if [[ -f "$ST_PBF" ]]; then
            echo -e "${GREEN}  ✓ $ST already downloaded ($(du -sh "$ST_PBF" | cut -f1))${NC}"
        else
            echo -e "${YELLOW}  → $ST${NC}"
            wget -q --show-progress -O "$ST_PBF" "$ST_URL"
        fi
        STATE_PBFS+=("$ST_PBF")
    done

    echo ""
    echo -e "${YELLOW}→ Merging ${#REGION_STATES[@]} state files with osmium (this may take a few minutes)...${NC}"
    osmium merge "${STATE_PBFS[@]}" -o "$PBF" --overwrite
    echo -e "${GREEN}✓ Merged PBF: $(du -sh "$PBF" | cut -f1)${NC}"

    echo -e "${YELLOW}→ Removing individual state files to save space...${NC}"
    rm -f "${STATE_PBFS[@]}"
else
    # ── Single state or custom URL ────────────────────────────────────────────
    if [[ -f "$PBF" ]]; then
        echo -e "${YELLOW}OSM file already exists ($(du -sh "$PBF" | cut -f1)). Re-download? [y/N]${NC}"
        read -rp "" REDLY
        if [[ "${REDLY,,}" == "y" ]]; then
            wget -c --show-progress -O "$PBF" "$OSM_URL"
        fi
    else
        echo -e "${YELLOW}→ Downloading OSM extract (this may take a few minutes)...${NC}"
        wget -c --show-progress -O "$PBF" "$OSM_URL"
    fi
fi
echo -e "${GREEN}✓ OSM data: $(du -sh "$PBF" | cut -f1)${NC}\n"

# ── Preprocess each profile ───────────────────────────────────────────────────
PORT=5001
for PROFILE in "${PROFILES[@]}"; do
    PDIR="$DATA_DIR/$PROFILE"
    mkdir -p "$PDIR"

    DONE_MARKER="$PDIR/.processed"
    if [[ -f "$DONE_MARKER" ]]; then
        echo -e "${GREEN}✓ $PROFILE already processed — skipping (delete $DONE_MARKER to redo)${NC}"
        PORT=$((PORT + 1))
        continue
    fi

    echo -e "${YELLOW}→ Processing $PROFILE (5–30 min depending on region size)...${NC}"
    cp "$PBF" "$PDIR/region.osm.pbf"

    # hiking uses our custom hiking.lua; all other profiles use the built-in ones
    if [[ "$PROFILE" == "hiking" ]]; then
        LUA_MOUNT="-v ${SCRIPT_DIR}/hiking.lua:/opt/hiking.lua"
        LUA_PATH="/opt/hiking.lua"
    else
        LUA_MOUNT=""
        LUA_PATH="/opt/${PROFILE}.lua"
    fi

    docker run --rm \
        -v "$PDIR:/data" \
        $LUA_MOUNT \
        ghcr.io/project-osrm/osrm-backend \
        osrm-extract -p "$LUA_PATH" /data/region.osm.pbf

    docker run --rm \
        -v "$PDIR:/data" \
        ghcr.io/project-osrm/osrm-backend \
        osrm-partition /data/region.osrm

    docker run --rm \
        -v "$PDIR:/data" \
        ghcr.io/project-osrm/osrm-backend \
        osrm-customize /data/region.osrm

    # Remove the large intermediate files — only the .osrm.* graph files are needed at runtime
    rm -f "$PDIR/region.osm.pbf"
    touch "$DONE_MARKER"

    echo -e "${GREEN}✓ $PROFILE done ($(du -sh "$PDIR" | cut -f1))${NC}\n"
    PORT=$((PORT + 1))
done

# ── Write docker-compose.osrm.yml ────────────────────────────────────────────
cat > "$COMPOSE" <<'HEADER'
# Atlas Control — Local OSRM Routing
# Start:  docker compose -f docker-compose.osrm.yml up -d
# Stop:   docker compose -f docker-compose.osrm.yml down
# Logs:   docker compose -f docker-compose.osrm.yml logs -f
version: '3.8'
services:
HEADER

PORT=5001
for PROFILE in "${PROFILES[@]}"; do
    SVC="osrm-${PROFILE}"
    cat >> "$COMPOSE" <<SVCBLOCK
  ${SVC}:
    image: ghcr.io/project-osrm/osrm-backend:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:${PORT}:5000"
    volumes:
      - ./osrm-data/${PROFILE}:/data:ro
    command: osrm-routed --algorithm mld --max-table-size 1000 /data/region.osrm

SVCBLOCK
    PORT=$((PORT + 1))
done

echo -e "${GREEN}✓ docker-compose.osrm.yml written${NC}"

# ── Start services ────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}→ Starting OSRM services...${NC}"
cd "$SCRIPT_DIR"
docker compose -f docker-compose.osrm.yml up -d
echo -e "${GREEN}✓ OSRM running${NC}"

# ── Write osrm_active.json so Atlas Control discovers local containers ─────────
ACTIVE_JSON="$SCRIPT_DIR/osrm_active.json"
{
    printf '{\n  "updated": "%s",\n  "states": {\n    "us": {' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    PORT=5001
    FIRST=true
    for PROFILE in "${PROFILES[@]}"; do
        if [[ "$FIRST" == "true" ]]; then FIRST=false; else printf ','; fi
        printf '\n      "%s": %d' "$PROFILE" "$PORT"
        PORT=$((PORT + 1))
    done
    printf '\n    }\n  }\n}\n'
} > "$ACTIVE_JSON"
echo -e "${GREEN}✓ osrm_active.json written — Atlas Control will use local routing${NC}"

# ── Optional systemd auto-start ───────────────────────────────────────────────
echo ""
read -rp "Auto-start OSRM on boot with systemd? [Y/n]: " AUTOSTART
if [[ "${AUTOSTART,,}" != "n" ]]; then
    sudo tee /etc/systemd/system/atlas-osrm.service > /dev/null <<UNIT
[Unit]
Description=Atlas Control OSRM Routing
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=docker compose -f docker-compose.osrm.yml up -d
ExecStop=docker compose -f docker-compose.osrm.yml down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable atlas-osrm
    echo -e "${GREEN}✓ atlas-osrm.service enabled${NC}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD} Setup complete!${NC}"
echo ""
echo " OSRM ports (all on localhost only):"
PORT=5001
for PROFILE in "${PROFILES[@]}"; do
    LABEL="$PROFILE"
    [[ "$PROFILE" == "hiking" ]] && LABEL="hiking (trail-optimized)"
    echo "   $LABEL → localhost:$PORT"
    PORT=$((PORT + 1))
done
echo ""
echo " Atlas Control auto-detects and uses local OSRM."
echo " Run this script again to add more regions or profiles."
echo ""
echo " Next step — download Protomaps tiles:"
echo "   python3 download_tiles.py --bbox <west,south,east,north>"
echo -e "${CYAN}${BOLD}════════════════════════════════════════════════${NC}"

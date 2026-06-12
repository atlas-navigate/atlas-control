#!/usr/bin/env bash
# ============================================================
# Atlas Control — Start OSRM Routing Containers
# Loads processed state graphs into Docker containers and
# writes osrm_active.json so the app knows what's available.
#
# Usage:
#   bash start_routing.sh                   (interactive)
#   bash start_routing.sh southeast
#   bash start_routing.sh georgia florida north-carolina
#   bash start_routing.sh all               (all processed states — RAM heavy)
#   bash start_routing.sh stop              (stop all OSRM containers)
#
# RAM budget: each state container uses ~100–500 MB depending on state size.
# Recommended: load 5–10 states at a time on this hardware (7.4 GB RAM).
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATES_DIR="$SCRIPT_DIR/osrm-data/states"
ACTIVE_JSON="$SCRIPT_DIR/osrm_active.json"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

declare -A REGIONS=(
  [northeast]="maine new-hampshire vermont massachusetts rhode-island connecticut new-york new-jersey pennsylvania delaware maryland"
  [southeast]="virginia west-virginia north-carolina south-carolina georgia florida tennessee alabama mississippi kentucky"
  [midwest]="ohio indiana illinois michigan wisconsin minnesota iowa missouri north-dakota south-dakota nebraska kansas"
  [south]="texas oklahoma arkansas louisiana"
  [mountain]="montana wyoming colorado utah idaho nevada"
  [southwest]="arizona new-mexico"
  [west]="california oregon washington"
  [noncontiguous]="alaska hawaii"
)

# ── Stop all running OSRM containers ─────────────────────────────────────────
stop_all() {
    echo -e "${YELLOW}→ Stopping all atlas-osrm-* containers...${NC}"
    local containers
    containers=$(docker ps -q --filter "name=atlas-osrm-" 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
        echo "$containers" | xargs docker stop
        echo "$containers" | xargs docker rm
        echo -e "${GREEN}✓ Stopped${NC}"
    else
        echo "  (none running)"
    fi
    echo '{}' > "$ACTIVE_JSON"
}

if [[ "${1:-}" == "stop" ]]; then
    stop_all
    exit 0
fi

# ── Resolve target states ─────────────────────────────────────────────────────
ALL_PROCESSED=()
while IFS= read -r -d '' dir; do
    state=$(basename "$(dirname "$dir")")
    ALL_PROCESSED+=("$state")
done < <(find "$STATES_DIR" -name ".processed" -path "*/car/.processed" -print0 2>/dev/null | sort -z -u)
# deduplicate
mapfile -t ALL_PROCESSED < <(printf '%s\n' "${ALL_PROCESSED[@]}" | sort -u)

if [[ ${#ALL_PROCESSED[@]} -eq 0 ]]; then
    echo -e "${RED}✗ No processed states found in $STATES_DIR${NC}"
    echo "  Run: bash setup_osrm_all.sh"
    exit 1
fi

TARGET_STATES=()

if [[ $# -eq 0 ]]; then
    echo "Available processed states:"
    printf '  %s\n' "${ALL_PROCESSED[@]}"
    echo ""
    echo "Region presets: northeast, southeast, midwest, south, mountain, southwest, west, noncontiguous, all"
    echo ""
    read -rp "States or region to load: " INPUT
    set -- $INPUT
fi

for ARG in "$@"; do
    ARG="${ARG,,}"
    if [[ "$ARG" == "all" ]]; then
        TARGET_STATES=("${ALL_PROCESSED[@]}")
        break
    elif [[ -n "${REGIONS[$ARG]:-}" ]]; then
        read -ra REGION_STATES <<< "${REGIONS[$ARG]}"
        TARGET_STATES+=("${REGION_STATES[@]}")
    else
        TARGET_STATES+=("$ARG")
    fi
done

# Deduplicate and filter to only processed states
mapfile -t TARGET_STATES < <(printf '%s\n' "${TARGET_STATES[@]}" | sort -u)
VALID_STATES=()
for STATE in "${TARGET_STATES[@]}"; do
    if [[ -d "$STATES_DIR/$STATE" ]]; then
        VALID_STATES+=("$STATE")
    else
        echo -e "${YELLOW}  ⚠ $STATE not processed — skipping (run setup_osrm_all.sh $STATE)${NC}"
    fi
done

if [[ ${#VALID_STATES[@]} -eq 0 ]]; then
    echo -e "${RED}✗ None of the requested states are processed.${NC}"
    exit 1
fi

echo -e "\nLoading ${#VALID_STATES[@]} state(s): ${VALID_STATES[*]}\n"

# RAM estimate
echo -e "${YELLOW}⚠  RAM note: each state container uses ~100–500 MB."
echo "   Loading ${#VALID_STATES[@]} state(s) × 3 profiles ≈ est. $((${#VALID_STATES[@]} * 300)) MB – $((${#VALID_STATES[@]} * 1500)) MB RAM.${NC}"
echo ""
read -rp "Continue? [Y/n]: " CONFIRM
[[ "${CONFIRM,,}" == "n" ]] && exit 0

# ── Stop existing OSRM containers ────────────────────────────────────────────
stop_all

# ── Start containers ──────────────────────────────────────────────────────────
echo ""
PORT=5001
declare -A ACTIVE_MAP   # state:profile -> port

for STATE in "${VALID_STATES[@]}"; do
    for PROFILE in car foot hiking bicycle; do
        PDIR="$STATES_DIR/$STATE/$PROFILE"
        DONE_MARKER="$PDIR/.processed"
        [[ ! -f "$DONE_MARKER" ]] && continue

        CNAME="atlas-osrm-${STATE}-${PROFILE}"
        echo -e "${YELLOW}  → Starting $STATE/$PROFILE on port $PORT${NC}"

        docker run -d \
            --name "$CNAME" \
            --restart unless-stopped \
            -p "127.0.0.1:${PORT}:5000" \
            -v "$PDIR:/data:ro" \
            ghcr.io/project-osrm/osrm-backend \
            osrm-routed --algorithm mld --max-table-size 1000 /data/region.osrm \
            > /dev/null

        ACTIVE_MAP["${STATE}:${PROFILE}"]=$PORT
        PORT=$((PORT + 1))
    done
done

# ── Write osrm_active.json ────────────────────────────────────────────────────
{
    echo "{"
    echo "  \"updated\": \"$(date -Iseconds)\","
    echo "  \"states\": {"
    FIRST_STATE=true
    for STATE in "${VALID_STATES[@]}"; do
        $FIRST_STATE || echo ","
        FIRST_STATE=false
        echo -n "    \"$STATE\": {"
        FIRST_PROFILE=true
        for PROFILE in car foot hiking bicycle; do
            KEY="${STATE}:${PROFILE}"
            [[ -z "${ACTIVE_MAP[$KEY]:-}" ]] && continue
            $FIRST_PROFILE || echo -n ", "
            FIRST_PROFILE=false
            echo -n "\"$PROFILE\": ${ACTIVE_MAP[$KEY]}"
        done
        echo -n "}"
    done
    echo ""
    echo "  }"
    echo "}"
} > "$ACTIVE_JSON"

echo ""
echo -e "${GREEN}${BOLD}✓ Routing ready${NC}"
echo ""
echo "  Active containers:"
docker ps --filter "name=atlas-osrm-" --format "  {{.Names}}  →  {{.Ports}}" | sed 's/127.0.0.1:/port /'
echo ""
echo "  Config written to: osrm_active.json"
echo "  To stop:  bash start_routing.sh stop"
echo "  To reload: bash start_routing.sh <region>"

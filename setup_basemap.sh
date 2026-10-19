#!/usr/bin/env bash
# ============================================================
# Atlas Control — Build the US basemap (map.pmtiles)
#
# Cuts the Protomaps planet build down to what Atlas uses:
#   • the whole world at z0–6   (~45 MB — countries, major roads/cities)
#   • the US at z7–15           (~20 GB — basemap-region-us.geojson:
#                                lower 48, Alaska + Aleutians, Hawaii, PR/USVI)
# instead of the ~127 GB full planet. Routing, places and topo are US-only,
# so detail outside the US was never reachable from anything else on the box.
#
# SOURCE may be a URL (only the needed byte ranges are downloaded) or a local
# planet .pmtiles. OUTPUT is replaced atomically, so SOURCE may equal OUTPUT.
#
# Usage:
#   bash setup_basemap.sh SOURCE [OUTPUT]
#   bash setup_basemap.sh https://build.protomaps.com/20261001.pmtiles
#   bash setup_basemap.sh static/tiles/map.pmtiles static/tiles/map.pmtiles.new
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PMTILES_CLI="$SCRIPT_DIR/pmtiles"
REGION="$SCRIPT_DIR/basemap-region-us.geojson"
WORLD_MAX_ZOOM=6

SRC="${1:?usage: setup_basemap.sh SOURCE [OUTPUT]}"
OUT="${2:-$SCRIPT_DIR/static/tiles/map.pmtiles}"
WORLD_TMP="$OUT.world.tmp"
US_TMP="$OUT.us.tmp"
OUT_TMP="$OUT.tmp"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

cleanup() { rm -f "$WORLD_TMP" "$US_TMP" "$OUT_TMP"; }
trap cleanup EXIT

[[ -x "$PMTILES_CLI" ]] || { echo -e "${RED}✗ pmtiles CLI not found at $PMTILES_CLI${NC}"; exit 1; }
[[ -f "$REGION" ]]      || { echo -e "${RED}✗ Region file not found: $REGION${NC}"; exit 1; }
mkdir -p "$(dirname "$OUT")"

echo "→ World overview (z0–${WORLD_MAX_ZOOM}) ..."
"$PMTILES_CLI" extract "$SRC" "$WORLD_TMP" --maxzoom="$WORLD_MAX_ZOOM"

echo "→ US detail (z$((WORLD_MAX_ZOOM + 1))+) ..."
"$PMTILES_CLI" extract "$SRC" "$US_TMP" --region="$REGION" --minzoom="$((WORLD_MAX_ZOOM + 1))"

# The two archives are disjoint by zoom, which is what `merge` requires.
echo "→ Merging ..."
"$PMTILES_CLI" merge "$WORLD_TMP" "$US_TMP" "$OUT_TMP"
rm -f "$WORLD_TMP" "$US_TMP"

"$PMTILES_CLI" verify "$OUT_TMP"
mv -f "$OUT_TMP" "$OUT"
echo -e "${GREEN}✓ Basemap ready: $OUT ($(du -sh "$OUT" | cut -f1))${NC}"

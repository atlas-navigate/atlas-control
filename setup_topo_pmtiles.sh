#!/usr/bin/env bash
# ============================================================
# Atlas Control — Convert topo raster tiles → PMTiles
#
# Bundles the tile-per-file raster directory into a single
# PMTiles archive served via HTTP range requests — same
# approach used for the street basemap (map.pmtiles).
#
# Result: static/tiles/topo.pmtiles
#
# Usage:
#   bash setup_topo_pmtiles.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TILE_DIR="$SCRIPT_DIR/static/tiles/topo"
OUT_PMTILES="$SCRIPT_DIR/static/tiles/topo.pmtiles"
TMP_MBTILES="$SCRIPT_DIR/static/tiles/topo_tmp.mbtiles"
PMTILES_CLI="$SCRIPT_DIR/pmtiles"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "Atlas Control — Topo PMTiles Conversion"
echo ""

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ ! -d "$TILE_DIR" ]]; then
    echo -e "${RED}✗ Topo tile directory not found: $TILE_DIR${NC}"
    exit 1
fi

TILE_COUNT=$(find "$TILE_DIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \) | wc -l)
if [[ "$TILE_COUNT" -eq 0 ]]; then
    echo -e "${RED}✗ No raster tiles found in $TILE_DIR${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Found $TILE_COUNT topo tiles${NC}"

if [[ ! -x "$PMTILES_CLI" ]]; then
    echo -e "${RED}✗ pmtiles CLI not found at $PMTILES_CLI${NC}"
    exit 1
fi
echo -e "${GREEN}✓ pmtiles CLI ready${NC}"
echo ""

# Atlas expects the topo cache to be JPEG-only before building PMTiles.
TILE_FORMAT="$(
python3 - "$TILE_DIR" <<'PYEOF'
import os, sys
tile_dir = sys.argv[1]
exts = set()
for root, _, files in os.walk(tile_dir):
    for name in files:
        ext = os.path.splitext(name)[1].lower()
        if ext in (".png", ".jpg", ".jpeg"):
            exts.add(ext)
if not exts:
    print("none")
    raise SystemExit(0)
if ".png" in exts:
    print("has_png")
    raise SystemExit(0)
print("jpg")
PYEOF
)"
if [[ "$TILE_FORMAT" == none ]]; then
    echo -e "${RED}✗ Could not detect raster tile format${NC}"
    exit 1
fi
if [[ "$TILE_FORMAT" == "has_png" ]]; then
    echo -e "${RED}✗ PNG topo tiles found in $TILE_DIR${NC}"
    echo "  Re-run download_topo.py or install.sh to normalize the cache to JPEG."
    exit 1
fi
PMTILES_TILE_TYPE=3
PMTILES_TILE_LABEL="JPEG"
echo -e "${GREEN}✓ Detected raster format: ${PMTILES_TILE_LABEL}${NC}"
echo ""

# ── Step 1: tile directory → MBTiles (sqlite3) ────────────────────────────────
echo "→ Building MBTiles from tile directory..."
rm -f "$TMP_MBTILES"

python3 - "$TILE_DIR" "$TMP_MBTILES" "$TILE_FORMAT" <<'PYEOF'
import os, sys, sqlite3, time

tile_dir = sys.argv[1]
out_path = sys.argv[2]
tile_format = sys.argv[3]
progress_every = 5000

con = sqlite3.connect(out_path)
con.execute("PRAGMA journal_mode=WAL")
con.execute("""
    CREATE TABLE metadata (name TEXT NOT NULL, value TEXT);
""")
con.execute("""
    CREATE TABLE tiles (
        zoom_level  INTEGER NOT NULL,
        tile_column INTEGER NOT NULL,
        tile_row    INTEGER NOT NULL,
        tile_data   BLOB NOT NULL
    );
""")
con.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
con.execute("INSERT INTO metadata VALUES ('name',   'topo')")
con.execute("INSERT INTO metadata VALUES ('format', ?)", (tile_format,))
con.execute("INSERT INTO metadata VALUES ('type',   'overlay')")
con.execute("INSERT INTO metadata VALUES ('version','1.0')")
con.execute("INSERT INTO metadata VALUES ('description','Topo overlay tiles')")

count = 0
last_report = time.time()
zoom_levels = sorted(
    int(name) for name in os.listdir(tile_dir)
    if os.path.isdir(os.path.join(tile_dir, name)) and name.isdigit()
)
min_zoom = min(zoom_levels)
max_zoom = max(zoom_levels)

for z_str in os.listdir(tile_dir):
    z_path = os.path.join(tile_dir, z_str)
    if not os.path.isdir(z_path):
        continue
    z = int(z_str)
    for x_str in os.listdir(z_path):
        x_path = os.path.join(z_path, x_str)
        if not os.path.isdir(x_path):
            continue
        x = int(x_str)
        for fname in os.listdir(x_path):
            if not fname.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
            stem, _ = os.path.splitext(fname)
            y = int(stem)
            # MBTiles uses TMS y (inverted): tile_row = (2^z - 1) - y
            tms_y = (2**z - 1) - y
            with open(os.path.join(x_path, fname), 'rb') as f:
                data = f.read()
            con.execute(
                "INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                (z, x, tms_y, sqlite3.Binary(data))
            )
            count += 1
            if count % progress_every == 0:
                con.commit()
                now = time.time()
                rate = progress_every / max(now - last_report, 0.001)
                print(f"  Imported {count} tiles so far ({rate:.0f}/s)", flush=True)
                last_report = now

# Compute and store bounds from tile coords
min_x = min_y = float('inf')
max_x = max_y = float('-inf')
import math
for row in con.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"):
    z, x, tms_y = row
    y = (2**z - 1) - tms_y
    n = 2**z
    lon_w = x / n * 360 - 180
    lon_e = (x + 1) / n * 360 - 180
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    min_x = min(min_x, lon_w); max_x = max(max_x, lon_e)
    min_y = min(min_y, lat_s); max_y = max(max_y, lat_n)

if min_x != float('inf'):
    bounds = f"{min_x:.6f},{min_y:.6f},{max_x:.6f},{max_y:.6f}"
    center_z = min(14, max_zoom)
    center = f"{(min_x+max_x)/2:.6f},{(min_y+max_y)/2:.6f},{center_z}"
    con.execute("INSERT INTO metadata VALUES ('bounds', ?)", (bounds,))
    con.execute("INSERT INTO metadata VALUES ('center', ?)", (center,))
    con.execute("INSERT INTO metadata VALUES ('minzoom', ?)", (str(min_zoom),))
    con.execute("INSERT INTO metadata VALUES ('maxzoom', ?)", (str(max_zoom),))

con.commit()
con.close()
print(f"  Written {count} tiles")
PYEOF

echo -e "${GREEN}✓ MBTiles built${NC}"

# ── Step 2: MBTiles → PMTiles ─────────────────────────────────────────────────
echo "→ Converting MBTiles to PMTiles..."
"$PMTILES_CLI" convert "$TMP_MBTILES" "$OUT_PMTILES" --force
rm -f "$TMP_MBTILES"

# ── Step 3: Patch PMTiles v3 header ──────────────────────────────────────────
# pmtiles convert does not always write tile_type (byte 91) or min/max zoom
# (bytes 92-93) from MBTiles metadata. Without a correct tile_type the protocol
# handler cannot determine the raster MIME type. Patch the 127-byte header in-place.
echo "→ Patching PMTiles header (tile_type, zoom range)..."
python3 - "$OUT_PMTILES" "$PMTILES_TILE_TYPE" "$PMTILES_TILE_LABEL" <<'PYEOF'
import sys, struct

path = sys.argv[1]
tile_type = int(sys.argv[2])
tile_label = sys.argv[3]
with open(path, 'r+b') as f:
    hdr = bytearray(f.read(127))
    # PMTiles v3 header byte offsets:
    #   88: clustered   89: internal_compression   90: tile_compression
    #   91: tile_type   92: min_zoom   93: max_zoom
    hdr[91] = tile_type
    hdr[90] = 1   # tile_compression = none (PNGs/JPEGs are already compressed)
    # Derive zoom range from existing tiles; only update if header values look wrong
    if hdr[92] == 0 and hdr[93] == 0:
        import os, re
        tile_dir = os.path.join(os.path.dirname(path), 'topo')
        zooms = set()
        if os.path.isdir(tile_dir):
            for entry in os.scandir(tile_dir):
                if entry.is_dir() and entry.name.isdigit():
                    zooms.add(int(entry.name))
        if zooms:
            hdr[92] = min(zooms)
            hdr[93] = max(zooms)
            print(f"  zoom range patched: {min(zooms)}–{max(zooms)}")
    f.seek(0)
    f.write(bytes(hdr))
print(f"  tile_type patched: {tile_type} ({tile_label})")
PYEOF

SIZE=$(du -sh "$OUT_PMTILES" | cut -f1)
echo -e "${GREEN}✓ topo.pmtiles created ($SIZE) → static/tiles/topo.pmtiles${NC}"
echo ""
echo "Restart atlas-control for the change to take effect:"
echo "  sudo systemctl restart atlas-control"
echo "  # or: bash run.sh"

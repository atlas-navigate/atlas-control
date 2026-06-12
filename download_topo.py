#!/usr/bin/env python3
"""
Download USGS National Map topo tiles for offline use.

Tiles are stored as static/tiles/topo/{z}/{x}/{y}.{ext}. The source
currently serves JPEG tiles, but Atlas accepts either PNG or JPEG and
serves the correct content type at request time.

USGS URL pattern: .../USGSTopo/MapServer/tile/{z}/{y}/{x}
Note: USGS uses y/x order (row/col), not the standard x/y.

Usage:
    python3 download_topo.py [options]

    --states  AK CA ...    Two-letter state codes (default: all lower-48 + AK + HI)
    --bbox    W S E N      Custom bounding box (overrides --states)
    --min-zoom INT         Minimum zoom level (default: 8)
    --max-zoom INT         Maximum zoom level (default: 13)
    --workers INT          Parallel download threads (default: 8)
    --yes                  Skip confirmation prompt
    --rebuild-pmtiles      Run setup_topo_pmtiles.sh after download completes
    --output-dir PATH      Tile output directory (default: ./static/tiles/topo)
"""

import argparse
import io
import math
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# State bounding boxes (WGS-84: west, south, east, north)
# ---------------------------------------------------------------------------
STATE_BBOXES = {
    "AL": (-88.47, 30.14, -84.89, 35.01),
    "AK": (-179.15, 51.21, -129.97, 71.35),
    "AZ": (-114.82, 31.33, -109.04, 37.00),
    "AR": (-94.62, 33.00, -89.64, 36.50),
    "CA": (-124.41, 32.53, -114.13, 42.01),
    "CO": (-109.06, 36.99, -102.04, 41.00),
    "CT": (-73.73, 40.99, -71.79, 42.05),
    "DE": (-75.79, 38.45, -75.05, 39.84),
    "FL": (-87.63, 24.52, -80.03, 31.00),
    "GA": (-85.61, 30.36, -80.84, 35.00),
    "HI": (-160.25, 18.91, -154.81, 22.24),
    "ID": (-117.24, 41.99, -111.04, 49.00),
    "IL": (-91.51, 36.97, -87.02, 42.51),
    "IN": (-88.10, 37.77, -84.78, 41.76),
    "IA": (-96.64, 40.37, -90.14, 43.50),
    "KS": (-102.05, 36.99, -94.59, 40.00),
    "KY": (-89.57, 36.50, -81.96, 39.15),
    "LA": (-94.04, 28.93, -88.82, 33.02),
    "ME": (-71.08, 43.06, -66.95, 47.46),
    "MD": (-79.49, 37.91, -74.99, 39.72),
    "MA": (-73.51, 41.24, -69.93, 42.89),
    "MI": (-90.42, 41.70, -82.41, 48.19),
    "MN": (-97.24, 43.50, -89.49, 49.38),
    "MS": (-91.65, 30.18, -88.10, 35.01),
    "MO": (-95.77, 35.99, -89.10, 40.61),
    "MT": (-116.05, 44.36, -104.04, 49.00),
    "NE": (-104.05, 40.00, -95.31, 43.00),
    "NV": (-120.00, 35.00, -114.04, 42.00),
    "NH": (-72.56, 42.70, -70.61, 45.31),
    "NJ": (-75.56, 38.93, -73.89, 41.36),
    "NM": (-109.05, 31.33, -103.00, 37.00),
    "NY": (-79.76, 40.50, -71.86, 45.01),
    "NC": (-84.32, 33.84, -75.46, 36.59),
    "ND": (-104.05, 45.93, -96.55, 49.00),
    "OH": (-84.82, 38.40, -80.52, 41.98),
    "OK": (-103.00, 33.62, -94.43, 37.00),
    "OR": (-124.57, 41.99, -116.46, 46.24),
    "PA": (-80.52, 39.72, -74.69, 42.27),
    "RI": (-71.86, 41.15, -71.12, 42.02),
    "SC": (-83.35, 32.05, -78.54, 35.22),
    "SD": (-104.06, 42.48, -96.44, 45.94),
    "TN": (-90.31, 34.98, -81.65, 36.68),
    "TX": (-106.65, 25.84, -93.51, 36.50),
    "UT": (-114.05, 37.00, -109.04, 42.00),
    "VT": (-73.44, 42.73, -71.46, 45.02),
    "VA": (-83.68, 36.54, -75.24, 39.47),
    "WA": (-124.73, 45.54, -116.92, 49.00),
    "WV": (-82.64, 37.20, -77.72, 40.64),
    "WI": (-92.89, 42.49, -86.25, 47.08),
    "WY": (-111.06, 40.99, -104.05, 45.00),
    "DC": (-77.12, 38.79, -76.91, 38.99),
}

US48 = [s for s in STATE_BBOXES if s not in ("AK", "HI")]
ALL_STATES = list(STATE_BBOXES.keys())

USGS_URL = (
    "https://basemap.nationalmap.gov/arcgis/rest/services/"
    "USGSTopo/MapServer/tile/{z}/{y}/{x}"
)

HEADERS = {
    "User-Agent": "Atlas-Control/1.0 (offline topo downloader)",
    "Accept": "image/jpeg,image/png,image/*",
}

PNG_MAGIC = b"\x89PNG"
JPEG_MAGIC = b"\xff\xd8\xff"


# ---------------------------------------------------------------------------
# Tile math
# ---------------------------------------------------------------------------

def lon_to_tile_x(lon: float, z: int) -> int:
    return int((lon + 180.0) / 360.0 * (1 << z))


def lat_to_tile_y(lat: float, z: int) -> int:
    lat_r = math.radians(lat)
    return int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
               / 2.0 * (1 << z))


def tiles_for_bbox(west, south, east, north, z):
    """Yield (z, x, y) for every tile covering the bbox at zoom z."""
    x_min = lon_to_tile_x(west, z)
    x_max = lon_to_tile_x(east, z)
    y_min = lat_to_tile_y(north, z)   # north → smaller y
    y_max = lat_to_tile_y(south, z)   # south → larger y
    n = 1 << z
    x_min = max(0, min(x_min, n - 1))
    x_max = max(0, min(x_max, n - 1))
    y_min = max(0, min(y_min, n - 1))
    y_max = max(0, min(y_max, n - 1))
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            yield z, x, y


def collect_tiles(bboxes, min_zoom, max_zoom):
    """Return a deduplicated list of (z, x, y) tuples."""
    seen = set()
    for bbox in bboxes:
        for z in range(min_zoom, max_zoom + 1):
            for tile in tiles_for_bbox(*bbox, z):
                seen.add(tile)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------

def download_tile(z, x, y, out_dir: Path, retries=3):
    """
Download one tile.  Returns (z, x, y, status) where status is one of:
  'ok'       — downloaded
  'skip'     — already cached locally
  'blank'    — server returned a missing tile (404 / 204)
  'error'    — failed after retries
    """
    dest_dir = out_dir / str(z) / str(x)
    existing_jpg = dest_dir / f"{y}.jpg"
    existing_jpeg = dest_dir / f"{y}.jpeg"
    if existing_jpg.exists() and existing_jpg.stat().st_size > 0:
        return z, x, y, "skip"
    if existing_jpeg.exists() and existing_jpeg.stat().st_size > 0:
        existing_jpeg.rename(existing_jpg)
        return z, x, y, "skip"

    dest_dir.mkdir(parents=True, exist_ok=True)

    def _classify_image(data: bytes):
        if len(data) >= 4 and data[:4] == PNG_MAGIC:
            return "png"
        if len(data) >= 3 and data[:3] == JPEG_MAGIC:
            return "jpg"
        return None

    def _cleanup_stale():
        for ext in ("png", "jpg", "jpeg"):
            stale = dest_dir / f"{y}.{ext}"
            if stale.exists():
                stale.unlink()

    url = USGS_URL.format(z=z, y=y, x=x)

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            img_ext = _classify_image(data)
            if not img_ext:
                # Not a valid raster tile — likely an HTML/API error page
                return z, x, y, "error"
            _cleanup_stale()
            dest = dest_dir / f"{y}.jpg"
            if img_ext == "png":
                with Image.open(io.BytesIO(data)) as img:
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    elif img.mode == "L":
                        img = img.convert("RGB")
                    img.save(dest, format="JPEG", quality=92, optimize=True)
            else:
                with open(dest, "wb") as f:
                    f.write(data)
            return z, x, y, "ok"
        except urllib.error.HTTPError as e:
            if e.code in (404, 204):
                return z, x, y, "blank"
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))

    return z, x, y, "error"


# ---------------------------------------------------------------------------
# Progress bar (no external deps)
# ---------------------------------------------------------------------------

class Progress:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.ok = 0
        self.skip = 0
        self.blank = 0
        self.err = 0
        self._start = time.time()
        self._last_print = 0

    def update(self, status):
        self.done += 1
        if status == "ok":
            self.ok += 1
        elif status == "skip":
            self.skip += 1
        elif status == "blank":
            self.blank += 1
        else:
            self.err += 1
        now = time.time()
        if now - self._last_print >= 1.0 or self.done == self.total:
            self._print()
            self._last_print = now

    def _print(self):
        elapsed = time.time() - self._start
        rate = self.done / elapsed if elapsed > 0 else 0
        pct = 100 * self.done / self.total if self.total else 0
        eta = (self.total - self.done) / rate if rate > 0 else 0
        bar_w = 30
        filled = int(bar_w * self.done / self.total) if self.total else 0
        bar = "#" * filled + "-" * (bar_w - filled)
        sys.stdout.write(
            f"\r[{bar}] {pct:5.1f}%  {self.done}/{self.total}"
            f"  dl={self.ok} cached={self.skip} blank={self.blank} err={self.err}"
            f"  {rate:.1f}/s  ETA {int(eta)}s   "
        )
        sys.stdout.flush()
        if self.done == self.total:
            print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Download USGS topo tiles for Atlas Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--states", nargs="+", metavar="XX",
        help="Two-letter state codes (default: all 50 + DC)",
    )
    p.add_argument(
        "--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
        help="Custom bounding box (overrides --states)",
    )
    p.add_argument("--min-zoom", type=int, default=8, metavar="INT")
    p.add_argument("--max-zoom", type=int, default=13, metavar="INT")
    p.add_argument("--workers", type=int, default=8, metavar="INT")
    p.add_argument("--yes", action="store_true", help="Skip confirmation")
    p.add_argument(
        "--rebuild-pmtiles", action="store_true",
        help="Run setup_topo_pmtiles.sh after download",
    )
    p.add_argument(
        "--output-dir", default=None, metavar="PATH",
        help="Tile output directory (default: <script_dir>/static/tiles/topo)",
    )
    return p.parse_args()


def main():
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    out_dir = Path(args.output_dir) if args.output_dir else script_dir / "static" / "tiles" / "topo"

    # Build bounding box list
    if args.bbox:
        bboxes = [tuple(args.bbox)]
        region_desc = f"custom bbox {args.bbox}"
    else:
        states = [s.upper() for s in args.states] if args.states else ALL_STATES
        unknown = [s for s in states if s not in STATE_BBOXES]
        if unknown:
            print(f"Unknown state codes: {', '.join(unknown)}", file=sys.stderr)
            sys.exit(1)
        bboxes = [STATE_BBOXES[s] for s in states]
        region_desc = ", ".join(states) if len(states) <= 10 else f"{len(states)} states"

    print(f"Region   : {region_desc}")
    print(f"Zoom     : {args.min_zoom}–{args.max_zoom}")
    print(f"Output   : {out_dir}")
    print(f"Workers  : {args.workers}")
    print()

    tiles = collect_tiles(bboxes, args.min_zoom, args.max_zoom)
    total = len(tiles)

    # Estimate size: ~25 KB/tile average for USGS topo at typical zooms
    size_est_gb = total * 25_000 / 1e9
    print(f"Tiles    : {total:,}  (~{size_est_gb:.1f} GB estimated)")
    print()

    if not args.yes:
        ans = input("Proceed? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    progress = Progress(total)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_tile, z, x, y, out_dir): (z, x, y)
            for z, x, y in tiles
        }
        for fut in as_completed(futures):
            try:
                _, _, _, status = fut.result()
            except Exception:
                status = "error"
            progress.update(status)

    print(
        f"\nDone. {progress.ok} downloaded, {progress.skip} cached, "
        f"{progress.blank} blank, {progress.err} errors."
    )

    if args.rebuild_pmtiles:
        import subprocess
        script = script_dir / "setup_topo_pmtiles.sh"
        print(f"\nRunning {script} ...")
        subprocess.run(["bash", str(script)], check=True)


if __name__ == "__main__":
    main()

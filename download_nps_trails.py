#!/usr/bin/env python3
"""
Atlas Control — Full US National Parks Trail & Feature Database Builder
Ground-up rewrite of download_trailheads.py

This script builds a comprehensive offline trail database from two sources:

  1. OSM PBF files (already on disk for OSRM routing):
       - Trail LINES with geometry for map rendering
       - Trailheads, peaks, springs, viewpoints, campsites, shelters
       - National park / protected area boundaries
       Uses 'osmium' which must be installed (apt install osmium-tool)

  2. NPS ArcGIS REST API (requires internet ONCE to download):
       - Official NPS trail lines for all National Parks
       - Park unit boundaries
       - Trailheads from NPS directly
       Downloads ~20 MB of compressed GeoJSON; stored offline permanently

Output: static/data/nps_trails.db
  Tables:
    trails         — trail line geometries + attributes for routing overlay
    trailheads     — point features (backward compatible with old trailheads.db)
    park_boundaries — polygon outlines of all NPS units
    nps_units       — metadata about each national park unit

The legacy static/data/trailheads.db is also updated so existing geocoding
in app.py continues to work without modification.

Usage:
    python3 download_nps_trails.py                # full build (OSM + NPS API)
    python3 download_nps_trails.py --osm-only     # skip NPS API download
    python3 download_nps_trails.py --nps-only     # skip OSM extraction
    python3 download_nps_trails.py --no-progress  # quiet mode

Requirements:
    osmium-tool    (apt install osmium-tool)
    Python 3.8+    (stdlib only: urllib, json, sqlite3)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
PBF_DIR     = SCRIPT_DIR / "osrm-data"
DATA_DIR    = SCRIPT_DIR / "static" / "data"
DB_PATH     = DATA_DIR / "nps_trails.db"
LEGACY_DB   = DATA_DIR / "trailheads.db"   # kept for backward compat

# NPS ArcGIS REST API endpoints
# April 4, 2026 validation:
# - The former NPS_Trails service returns ArcGIS {"error":{"message":"Invalid URL"}}
# - The park-boundary service below still responds correctly.
# Atlas therefore uses verified OSM extraction for trail geometry and keeps the
# live NPS boundary feed for park metadata/boundaries.
NPS_TRAILS_URL    = None
NPS_UNITS_URL     = (
    "https://services1.arcgis.com/fBc8EJBxQRMcHlei/ArcGIS/rest/services/"
    "NPS_Land_Resources_Division_Boundary_and_Tract_Data_Service/"
    "FeatureServer/2/query"
)
NPS_TRAILHEADS_URL = None

# Batch size for paginated NPS API downloads
NPS_BATCH_SIZE = 2000

# OSM tags to extract
OSM_NODE_TAGS = [
    "highway=trailhead",
    "natural=peak",
    "natural=saddle",
    "natural=spring",
    "natural=waterfall",
    "natural=cave_entrance",
    "natural=volcano",
    "tourism=viewpoint",
    "tourism=camp_site",
    "tourism=wilderness_hut",
    "tourism=alpine_hut",
    "amenity=shelter",
    "leisure=picnic_table",
    "amenity=water_point",
    "amenity=toilets",
    "emergency=phone",
]

OSM_WAY_TAGS = [
    "highway=path",
    "highway=footway",
    "highway=bridleway",
    "highway=track",
    "route=hiking",
    "route=foot",
]

FEAT_TYPE_MAP = {
    "highway=trailhead": "trailhead",
    "natural=peak": "peak",
    "natural=saddle": "pass",
    "natural=spring": "spring",
    "natural=waterfall": "waterfall",
    "natural=cave_entrance": "cave",
    "natural=volcano": "volcano",
    "tourism=viewpoint": "viewpoint",
    "tourism=camp_site": "campsite",
    "tourism=wilderness_hut": "shelter",
    "tourism=alpine_hut": "shelter",
    "amenity=shelter": "shelter",
    "amenity=water_point": "water",
    "amenity=toilets": "toilet",
    "emergency=phone": "emergency_phone",
    "highway=path": "trail",
    "highway=footway": "trail",
    "highway=bridleway": "trail",
    "highway=track": "trail",
}


def log(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


# ── Database schema ───────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS trails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT,
    park_code    TEXT,
    park_name    TEXT,
    trail_type   TEXT,
    surface      TEXT,
    difficulty   TEXT,
    uses         TEXT,
    length_m     REAL,
    geometry_json TEXT NOT NULL,
    bbox_west    REAL,
    bbox_south   REAL,
    bbox_east    REAL,
    bbox_north   REAL,
    source       TEXT    -- 'nps' or 'osm'
);

CREATE INDEX IF NOT EXISTS idx_trails_bbox
    ON trails (bbox_west, bbox_south, bbox_east, bbox_north);
CREATE INDEX IF NOT EXISTS idx_trails_name
    ON trails (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_trails_park
    ON trails (park_code);

CREATE TABLE IF NOT EXISTS trailheads (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    feat_type TEXT,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    ele_m     REAL,
    state     TEXT,
    park_code TEXT,
    source    TEXT
);

CREATE INDEX IF NOT EXISTS idx_th_name ON trailheads (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_th_latlon ON trailheads (lat, lon);

CREATE TABLE IF NOT EXISTS park_boundaries (
    park_code    TEXT PRIMARY KEY,
    park_name    TEXT,
    state        TEXT,
    category     TEXT,
    geometry_json TEXT,
    bbox_west    REAL,
    bbox_south   REAL,
    bbox_east    REAL,
    bbox_north   REAL
);

CREATE TABLE IF NOT EXISTS nps_units (
    park_code    TEXT PRIMARY KEY,
    full_name    TEXT,
    designation  TEXT,
    states       TEXT,
    description  TEXT,
    lat          REAL,
    lon          REAL
);
"""


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.commit()
    return con


# ── OSM extraction ────────────────────────────────────────────────────────────

def _state_name(pbf_name: str) -> str:
    stem = pbf_name.replace(".osm.pbf", "")
    if stem == "region":
        return "Southwest"
    return " ".join(w.capitalize() for w in stem.split("-"))


def _bbox_from_coords(coords) -> tuple:
    """Return (west, south, east, north) from a list of [lon, lat] coords."""
    if not coords:
        return None, None, None, None
    lons = [c[0] for c in coords if len(c) >= 2]
    lats = [c[1] for c in coords if len(c) >= 2]
    if not lons:
        return None, None, None, None
    return min(lons), min(lats), max(lons), max(lats)


def _line_length_m(coords) -> float:
    total = 0.0
    import math
    R = 6_371_000.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i][0], coords[i][1]
        lon2, lat2 = coords[i + 1][0], coords[i + 1][1]
        p1, p2 = math.radians(lat1), math.radians(lat2)
        a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
             + math.cos(p1) * math.cos(p2)
             * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
        total += R * 2 * math.asin(max(0.0, a) ** 0.5)
    return total


def _osmium_extract(pbf: Path, node_tags: list, way_tags: list,
                    out_dir: Path, quiet=False) -> tuple:
    """
    Run osmium to extract nodes (points) and ways (lines) from a PBF.
    Returns (node_geojsonseq_path, way_geojsonseq_path).
    """
    node_filter = "n/" + ",".join(node_tags)
    way_filter  = "w/" + ",".join(way_tags)

    node_pbf   = out_dir / (pbf.stem + "_nodes.osm.pbf")
    way_pbf    = out_dir / (pbf.stem + "_ways.osm.pbf")
    node_json  = out_dir / (pbf.stem + "_nodes.geojsonseq")
    way_json   = out_dir / (pbf.stem + "_ways.geojsonseq")

    def run_osmium(args):
        cmd = ["osmium"] + args
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            return True
        except subprocess.CalledProcessError as e:
            if not quiet:
                print(f"    osmium error: {e.stderr.decode()[:200]}", flush=True)
            return False
        except FileNotFoundError:
            print("ERROR: osmium not found. Install: apt install osmium-tool", flush=True)
            sys.exit(1)

    ok = True
    # Extract nodes
    if run_osmium(["tags-filter", str(pbf), node_filter,
                   "-o", str(node_pbf), "--overwrite", "--no-progress"]):
        run_osmium(["export", str(node_pbf), "-f", "geojsonseq",
                    "-o", str(node_json), "--overwrite"])
    else:
        ok = False

    # Extract ways (trail lines)
    if run_osmium(["tags-filter", str(pbf), way_filter,
                   "-o", str(way_pbf), "--overwrite", "--no-progress"]):
        run_osmium(["export", str(way_pbf), "-f", "geojsonseq",
                    "-o", str(way_json), "--overwrite"])
    else:
        ok = False

    # Cleanup temp PBFs
    for p in (node_pbf, way_pbf):
        p.unlink(missing_ok=True)

    return node_json, way_json


def _load_nodes_from_geojsonseq(path: Path, state_name: str):
    """Yield (name, feat_type, lat, lon, ele_m, state_name) from a GeoJSON-seq file."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                feat = json.loads(line)
                geo = feat.get("geometry", {})
                if geo.get("type") != "Point":
                    continue
                lon, lat = geo["coordinates"][:2]
                props = feat.get("properties", {})
                name = (props.get("name") or props.get("name:en") or props.get("ref") or "").strip()
                if not name:
                    continue
                ele = None
                try:
                    ele = float(props.get("ele", 0) or 0) or None
                except (ValueError, TypeError):
                    pass
                # Map tags to feat_type
                feat_type = "trail_feature"
                for tag, ftype in FEAT_TYPE_MAP.items():
                    k, v = tag.split("=", 1)
                    if props.get(k) == v:
                        feat_type = ftype
                        break
                yield name, feat_type, lat, lon, ele, state_name
            except Exception:
                continue


def _load_ways_from_geojsonseq(path: Path, state_name: str):
    """
    Yield trail dicts from a GeoJSON-seq file.
    Returns dicts suitable for inserting into the trails table.
    """
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                feat = json.loads(line)
                geo = feat.get("geometry", {})
                if geo.get("type") not in ("LineString", "MultiLineString"):
                    continue
                coords = geo.get("coordinates", [])
                if not coords:
                    continue
                # Flatten MultiLineString
                if geo["type"] == "MultiLineString":
                    all_coords = [c for seg in coords for c in seg]
                else:
                    all_coords = coords

                props    = feat.get("properties", {})
                name     = (props.get("name") or props.get("ref") or "").strip() or None
                surface  = props.get("surface", "")
                trail_type = "trail"
                w, s, e, n = _bbox_from_coords(all_coords)
                if w is None:
                    continue
                length_m = _line_length_m(all_coords)
                if length_m < 5:   # skip micro-segments
                    continue

                geo_json = json.dumps({"type": geo["type"], "coordinates": coords})
                yield {
                    "name":          name,
                    "park_code":     None,
                    "park_name":     state_name,
                    "trail_type":    trail_type,
                    "surface":       surface,
                    "difficulty":    props.get("sac_scale", ""),
                    "uses":          props.get("foot", "yes"),
                    "length_m":      round(length_m, 1),
                    "geometry_json": geo_json,
                    "bbox_west":     w,
                    "bbox_south":    s,
                    "bbox_east":     e,
                    "bbox_north":    n,
                    "source":        "osm",
                }
            except Exception:
                continue


def build_from_osm(con: sqlite3.Connection, quiet=False):
    """Extract trail lines and points from all OSM PBF files on disk."""
    pbf_files = sorted(PBF_DIR.glob("*.osm.pbf"))
    if not pbf_files:
        log("No .osm.pbf files found — skipping OSM extraction", quiet)
        return

    log(f"Extracting from {len(pbf_files)} PBF files…", quiet)
    th_rows    = []
    trail_rows = []
    seen_th    = set()
    seen_trail = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for pbf in pbf_files:
            state_name = _state_name(pbf.name)
            if not quiet:
                print(f"  {state_name}…", end="", flush=True)

            node_json, way_json = _osmium_extract(
                pbf, OSM_NODE_TAGS, OSM_WAY_TAGS, tmp, quiet=quiet)

            n_nodes = n_ways = 0
            for row in _load_nodes_from_geojsonseq(node_json, state_name):
                key = (row[0].lower(), round(row[2], 2), round(row[3], 2))
                if key not in seen_th:
                    seen_th.add(key)
                    th_rows.append((*row, None, "osm"))  # add park_code, source
                    n_nodes += 1

            for trail in _load_ways_from_geojsonseq(way_json, state_name):
                key = (trail.get("name") or "", round(trail["bbox_west"], 2),
                       round(trail["bbox_south"], 2))
                if key not in seen_trail:
                    seen_trail.add(key)
                    trail_rows.append(trail)
                    n_ways += 1

            if not quiet:
                print(f" {n_nodes} pts, {n_ways} trails")

            # Clean up temp files to save space
            node_json.unlink(missing_ok=True)
            way_json.unlink(missing_ok=True)

    # Bulk insert
    con.executemany(
        "INSERT INTO trailheads (name,feat_type,lat,lon,ele_m,state,park_code,source) "
        "VALUES (?,?,?,?,?,?,?,?)", th_rows)
    con.executemany(
        "INSERT INTO trails "
        "(name,park_code,park_name,trail_type,surface,difficulty,uses,length_m,"
        " geometry_json,bbox_west,bbox_south,bbox_east,bbox_north,source) "
        "VALUES (:name,:park_code,:park_name,:trail_type,:surface,:difficulty,:uses,"
        ":length_m,:geometry_json,:bbox_west,:bbox_south,:bbox_east,:bbox_north,:source)",
        trail_rows)
    con.commit()
    log(f"OSM: {len(th_rows)} points, {len(trail_rows)} trail segments inserted", quiet)


# ── NPS API download ──────────────────────────────────────────────────────────

def _nps_get(url: str, params: dict, retries=3) -> dict:
    """HTTP GET to NPS ArcGIS REST API, return parsed JSON."""
    if not url:
        return {}
    params.setdefault("f", "json")
    params.setdefault("outSR", "4326")
    full_url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": "AtlasControl/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                if isinstance(data, dict) and data.get("error"):
                    raise RuntimeError(
                        f"ArcGIS error for {url}: {data['error'].get('message', 'unknown error')}"
                    )
                return data
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return {}


def _count_nps_features(url: str) -> int:
    if not url:
        return 0
    try:
        data = _nps_get(url, {"where": "1=1", "returnCountOnly": "true"})
        return data.get("count", 0)
    except Exception:
        return 0


def download_nps_trails(con: sqlite3.Connection, quiet=False):
    """Download all NPS trail lines from the NPS ArcGIS REST API."""
    if not NPS_TRAILS_URL:
        log("Skipping NPS ArcGIS trail download — no verified trail endpoint is configured.", quiet)
        return
    log("Downloading NPS trails from NPS ArcGIS API…", quiet)

    total = _count_nps_features(NPS_TRAILS_URL)
    if total == 0:
        log("  Could not count NPS trails (offline?). Skipping NPS download.", quiet)
        return

    log(f"  {total} NPS trail features to download…", quiet)
    inserted = 0
    offset   = 0

    while offset < total:
        try:
            data = _nps_get(NPS_TRAILS_URL, {
                "where":            "1=1",
                "outFields":        "TRLNAME,TRLALTNAMES,TRLSURFACE,TRLUSE,"
                                    "TRLCLASS,TRLSTATUS,PARKNAME,STATE,UNITCODE",
                "returnGeometry":   "true",
                "resultOffset":     str(offset),
                "resultRecordCount": str(NPS_BATCH_SIZE),
                "geometryType":     "esriGeometryPolyline",
            })
        except Exception as e:
            log(f"  NPS API error at offset {offset}: {e}", quiet)
            break

        features = data.get("features", [])
        if not features:
            break

        rows = []
        for feat in features:
            attrs = feat.get("attributes", {})
            geom  = feat.get("geometry", {})
            paths = geom.get("paths", [])
            if not paths:
                continue

            # paths is a list of polylines; take the first or merge
            all_coords = [[pt[0], pt[1]] for path in paths for pt in path]
            if len(all_coords) < 2:
                continue

            w, s, e, n = _bbox_from_coords(all_coords)
            if w is None:
                continue

            length_m = _line_length_m(all_coords)
            geo_type = "MultiLineString" if len(paths) > 1 else "LineString"
            geo_json = json.dumps({"type": geo_type,
                                   "coordinates": paths if len(paths) > 1 else paths[0]})

            name       = (attrs.get("TRLNAME") or attrs.get("TRLALTNAMES") or "").strip() or None
            park_code  = (attrs.get("UNITCODE") or "").strip() or None
            park_name  = (attrs.get("PARKNAME") or "").strip() or None
            surface    = (attrs.get("TRLSURFACE") or "").strip()
            uses       = (attrs.get("TRLUSE") or "").strip()
            difficulty = (attrs.get("TRLCLASS") or "").strip()
            state      = (attrs.get("STATE") or "").strip()

            rows.append((name, park_code, park_name, "trail", surface,
                         difficulty, uses, round(length_m, 1),
                         geo_json, w, s, e, n, "nps"))

        if rows:
            con.executemany(
                "INSERT INTO trails "
                "(name,park_code,park_name,trail_type,surface,difficulty,uses,length_m,"
                " geometry_json,bbox_west,bbox_south,bbox_east,bbox_north,source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            inserted += len(rows)

        offset += len(features)
        if not quiet:
            pct = min(100, offset * 100 // total)
            print(f"\r  {offset}/{total} ({pct}%) — {inserted} inserted    ",
                  end="", flush=True)

        if not data.get("exceededTransferLimit", False) and len(features) < NPS_BATCH_SIZE:
            break

    if not quiet:
        print()
    log(f"NPS trails: {inserted} trail segments downloaded", quiet)


def download_nps_units(con: sqlite3.Connection, quiet=False):
    """Download NPS park unit boundaries and metadata."""
    log("Downloading NPS park unit boundaries…", quiet)

    total = _count_nps_features(NPS_UNITS_URL)
    if total == 0:
        log("  Could not reach NPS Units API. Skipping.", quiet)
        return

    offset   = 0
    inserted = 0
    while offset < total:
        try:
            data = _nps_get(NPS_UNITS_URL, {
                "where":             "1=1",
                "outFields":         "UNIT_CODE,UNIT_NAME,UNIT_TYPE,STATE",
                "returnGeometry":    "true",
                "resultOffset":      str(offset),
                "resultRecordCount": str(NPS_BATCH_SIZE),
            })
        except Exception as e:
            log(f"  NPS Units API error at offset {offset}: {e}", quiet)
            break

        features = data.get("features", [])
        if not features:
            break

        for feat in features:
            attrs = feat.get("attributes", {})
            geom  = feat.get("geometry", {})
            rings = geom.get("rings", [])

            code  = (attrs.get("UNIT_CODE") or "").strip()
            name  = (attrs.get("UNIT_NAME") or "").strip()
            utype = (attrs.get("UNIT_TYPE") or "").strip()
            state = (attrs.get("STATE") or "").strip()

            if not code:
                continue

            geo_json = None
            w = s = e = n = None
            if rings:
                all_pts = [pt for ring in rings for pt in ring]
                w, s, e, n = _bbox_from_coords(all_pts)
                geo_json = json.dumps({"type": "MultiPolygon",
                                       "coordinates": [[ring] for ring in rings]})

            try:
                con.execute(
                    "INSERT OR REPLACE INTO park_boundaries "
                    "(park_code,park_name,state,category,geometry_json,"
                    " bbox_west,bbox_south,bbox_east,bbox_north) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, name, state, utype, geo_json, w, s, e, n))
                con.execute(
                    "INSERT OR REPLACE INTO nps_units "
                    "(park_code,full_name,designation,states,description,lat,lon) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (code, name, utype, state, "",
                     (s + n) / 2 if s and n else None,
                     (w + e) / 2 if w and e else None))
                inserted += 1
            except Exception:
                continue

        con.commit()
        offset += len(features)
        if not data.get("exceededTransferLimit", False) and len(features) < NPS_BATCH_SIZE:
            break

    log(f"NPS units: {inserted} park boundaries downloaded", quiet)


# ── Legacy trailheads.db ──────────────────────────────────────────────────────

def _write_legacy_db(src_con: sqlite3.Connection, quiet=False):
    """
    Write/update legacy trailheads.db from the new database so existing
    app.py geocoding code continues to work without modification.
    """
    LEGACY_DB.parent.mkdir(parents=True, exist_ok=True)
    if LEGACY_DB.exists():
        LEGACY_DB.unlink()
    leg = sqlite3.connect(str(LEGACY_DB))
    leg.executescript("""
        CREATE TABLE trailheads (
            name      TEXT NOT NULL,
            feat_type TEXT,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            ele_m     REAL,
            state     TEXT
        );
        CREATE INDEX idx_th_name ON trailheads (name COLLATE NOCASE);
    """)
    rows = src_con.execute(
        "SELECT name, feat_type, lat, lon, ele_m, state FROM trailheads"
    ).fetchall()
    leg.executemany("INSERT INTO trailheads VALUES (?,?,?,?,?,?)", rows)
    leg.commit()
    n = leg.execute("SELECT COUNT(*) FROM trailheads").fetchone()[0]
    leg.close()
    log(f"Legacy trailheads.db written: {n} features", quiet)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build NPS trails + trailheads database")
    parser.add_argument("--osm-only",    action="store_true", help="Skip NPS API download")
    parser.add_argument("--nps-only",    action="store_true", help="Skip OSM PBF extraction")
    parser.add_argument("--no-progress", action="store_true", help="Suppress progress output")
    args = parser.parse_args()
    quiet = args.no_progress

    log("Atlas — NPS Trails Database Builder", quiet)
    log(f"Output: {DB_PATH}", quiet)
    log("", quiet)

    t0  = time.time()
    con = _open_db(DB_PATH)

    if not args.nps_only:
        if not (PBF_DIR / "").exists():
            log(f"WARNING: {PBF_DIR} not found — skipping OSM extraction", quiet)
        else:
            build_from_osm(con, quiet=quiet)

    if not args.osm_only:
        try:
            download_nps_trails(con, quiet=quiet)
            download_nps_units(con, quiet=quiet)
        except KeyboardInterrupt:
            log("\nInterrupted during NPS download — OSM data still saved", quiet)

    # Summary
    th_n     = con.execute("SELECT COUNT(*) FROM trailheads").fetchone()[0]
    trail_n  = con.execute("SELECT COUNT(*) FROM trails").fetchone()[0]
    park_n   = con.execute("SELECT COUNT(*) FROM park_boundaries").fetchone()[0]
    con.close()

    size_mb = DB_PATH.stat().st_size / 1_048_576
    elapsed = time.time() - t0

    log("", quiet)
    log(f"Done in {elapsed:.0f}s. Database: {DB_PATH} ({size_mb:.1f} MB)", quiet)
    log(f"  {th_n:,} trailhead/peak/feature points", quiet)
    log(f"  {trail_n:,} trail line segments", quiet)
    log(f"  {park_n:,} park boundaries", quiet)

    # Write legacy DB for backward compat
    con2 = sqlite3.connect(str(DB_PATH))
    _write_legacy_db(con2, quiet=quiet)
    con2.close()

    log("", quiet)
    log("Restart atlas-control to enable offline trail search and map overlay.", quiet)


if __name__ == "__main__":
    main()

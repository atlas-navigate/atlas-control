#!/usr/bin/env python3
"""
Atlas Control — Offline Trailhead & Trail Database Builder

Extracts trailheads, trail access points, mountain peaks, and named hiking
routes from the OSM state PBF files you already downloaded for OSRM, then
writes a SQLite database at static/data/trailheads.db for offline geocoding.

Requires: osmium (already installed at /usr/bin/osmium)
Runtime: ~1-5 minutes depending on number of states

Usage:
    python3 download_trailheads.py
"""
import os, sys, subprocess, sqlite3, json, tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PBF_DIR    = SCRIPT_DIR / "osrm-data"
DB_PATH    = SCRIPT_DIR / "static" / "data" / "trailheads.db"

# OSM tags we want to capture as searchable trail/park features
# Format: key=value pairs for osmium tags-filter
OSMIUM_FILTER_TAGS = [
    # Trailheads and access points
    "highway=trailhead",
    # Named footpaths and hiking paths
    "highway=path",
    "highway=footway",
    "highway=bridleway",
    "highway=track",
    # Natural features (peaks, saddles, passes, lakes, etc.)
    "natural=peak",
    "natural=saddle",
    "natural=spring",
    "natural=waterfall",
    "natural=cave_entrance",
    "natural=volcano",
    # Park and protected area boundaries
    "boundary=national_park",
    "boundary=protected_area",
    "leisure=nature_reserve",
    # Tourism features
    "tourism=camp_site",
    "tourism=viewpoint",
    "tourism=wilderness_hut",
    "tourism=alpine_hut",
    # Named hiking routes (relations)
    "route=hiking",
    "route=foot",
]


def osmium_extract_geojson(pbf_path: Path, tags: list, out_path: Path):
    """Use osmium to filter nodes with given tags and export as GeoJSON."""
    # Build tag filter expression - join with space means OR
    tag_expr = " ".join(tags)

    # We export only nodes (n/) for trailheads/peaks, and relations (r/) for routes
    # Note: osmium tags-filter outputs an OSM file, then we convert to GeoJSON
    tmp_osm = out_path.with_suffix(".osm.pbf")
    try:
        subprocess.run(
            ["osmium", "tags-filter",
             str(pbf_path),
             "n/" + ",".join(tags),   # nodes with any of these tags
             "-o", str(tmp_osm),
             "--overwrite", "--no-progress"],
            check=True, capture_output=True)

        subprocess.run(
            ["osmium", "export",
             str(tmp_osm),
             "-f", "geojsonseq",
             "-o", str(out_path),
             "--overwrite"],
            check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"    osmium error: {e.stderr.decode()[:200]}")
        return False
    finally:
        tmp_osm.unlink(missing_ok=True)
    return True


def parse_geojsonseq(path: Path):
    """Parse GeoJSON sequence file, yield (name, lat, lon, tags_dict)."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                feat = json.loads(line)
                geo  = feat.get("geometry", {})
                if geo.get("type") != "Point":
                    continue
                lon, lat = geo["coordinates"][:2]
                props = feat.get("properties", {})
                name = (props.get("name") or
                        props.get("name:en") or
                        props.get("ref")).strip() if (
                    props.get("name") or props.get("name:en") or props.get("ref")
                ) else None
                if name:
                    yield name, lat, lon, props
            except Exception:
                continue


def feature_type(props: dict) -> str:
    """Return a human-readable feature type string."""
    if props.get("highway") == "trailhead":
        return "trailhead"
    if props.get("natural") == "peak":
        return "peak"
    if props.get("natural") == "waterfall":
        return "waterfall"
    if props.get("natural") == "spring":
        return "spring"
    if props.get("natural") == "cave_entrance":
        return "cave"
    if props.get("natural") in ("saddle", "pass"):
        return "pass"
    if props.get("tourism") == "viewpoint":
        return "viewpoint"
    if props.get("tourism") in ("camp_site",):
        return "campsite"
    if props.get("tourism") in ("alpine_hut", "wilderness_hut"):
        return "shelter"
    if props.get("boundary") in ("national_park", "protected_area"):
        return "park"
    if props.get("leisure") == "nature_reserve":
        return "nature_reserve"
    if props.get("highway") in ("path", "footway", "track", "bridleway"):
        return "trail"
    return "trail_feature"


def build_db(rows):
    """Write SQLite trailhead database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE trailheads (
            name       TEXT NOT NULL,
            feat_type  TEXT,
            lat        REAL NOT NULL,
            lon        REAL NOT NULL,
            ele_m      REAL,
            state      TEXT
        );
        CREATE INDEX idx_th_name ON trailheads (name COLLATE NOCASE);
    """)
    cur.executemany(
        "INSERT INTO trailheads VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    n = cur.execute("SELECT COUNT(*) FROM trailheads").fetchone()[0]
    con.close()
    return n


def state_from_pbf(pbf_name: str) -> str:
    """Convert 'new-mexico.osm.pbf' → 'New Mexico'."""
    stem = pbf_name.replace(".osm.pbf", "")
    if stem == "region":
        return "Southwest"   # combined regional PBF
    return " ".join(w.capitalize() for w in stem.split("-"))


def main():
    pbf_files = sorted(PBF_DIR.glob("*.osm.pbf"))
    if not pbf_files:
        print("No .osm.pbf files found in", PBF_DIR)
        print("Run download_tiles.py or setup_osrm_all.sh first.")
        sys.exit(1)

    print(f"Found {len(pbf_files)} state PBF files.")
    print(f"Output: {DB_PATH}")
    print()

    all_rows = []
    seen = set()   # (name_lower, round_lat2, round_lon2) dedup

    with tempfile.TemporaryDirectory() as tmpdir:
        for pbf in pbf_files:
            state_name = state_from_pbf(pbf.name)
            print(f"  Processing {state_name}...", end="", flush=True)
            out_geojson = Path(tmpdir) / (pbf.stem + ".geojsonseq")

            ok = osmium_extract_geojson(pbf, OSMIUM_FILTER_TAGS, out_geojson)
            if not ok:
                print(" skipped (osmium error)")
                continue

            count = 0
            for name, lat, lon, props in parse_geojsonseq(out_geojson):
                key = (name.lower(), round(lat, 2), round(lon, 2))
                if key in seen:
                    continue
                seen.add(key)
                ele = props.get("ele")
                try:
                    ele = float(ele) if ele else None
                except (ValueError, TypeError):
                    ele = None
                ftype = feature_type(props)
                all_rows.append((name, ftype, lat, lon, ele, state_name))
                count += 1

            print(f" {count} features")

    print()
    print(f"Writing database ({len(all_rows)} total features)...")
    n = build_db(all_rows)
    size_kb = DB_PATH.stat().st_size // 1024
    print(f"Done. {n} features in {DB_PATH} ({size_kb} KB)")
    print()
    print("Restart atlas-control to enable offline trail search.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Atlas Control — Offline City Geocoder Setup
Downloads GeoNames cities1000 dataset (cities with population > 1000),
filters to USA, and builds a SQLite database at static/data/us_cities.db.

Run once while you have internet access. Works fully offline afterwards.

Usage:
    python3 download_cities.py                       # skip if the DB exists
    python3 download_cities.py --force               # rebuild regardless
    python3 download_cities.py --output /path/db     # explicit destination
"""
import argparse, os, sys, zipfile, sqlite3, urllib.request
from pathlib import Path

SCRIPT_DIR      = Path(__file__).parent
DEFAULT_DB_PATH = SCRIPT_DIR / "static" / "data" / "us_cities.db"
ZIP_URL         = "https://download.geonames.org/export/dump/cities1000.zip"

# US state abbreviation code → full state name
_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "Washington DC",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}


def download_and_build(db_path=DEFAULT_DB_PATH):
    DB_PATH = Path(db_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("Downloading GeoNames cities1000 dataset...")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Emit only on a 10% change — install.sh pipes this into a log file,
        # where a per-8KB-block carriage-return spinner becomes 1300 junk lines.
        last_pct = [-1]

        def _progress(count, block, total):
            if total <= 0:
                return
            pct = min(100, count * block * 100 // total)
            if pct // 10 != last_pct[0] // 10:
                last_pct[0] = pct
                print(f"  {pct}% ({total // 1024} KB total)", flush=True)

        urllib.request.urlretrieve(ZIP_URL, tmp_path, reporthook=_progress)

        print("Parsing cities data (US only)...")
        rows = []
        with zipfile.ZipFile(tmp_path) as zf:
            with zf.open("cities1000.txt") as f:
                for line in f:
                    try:
                        parts = line.decode("utf-8").rstrip("\n").split("\t")
                        if len(parts) < 15:
                            continue
                        country = parts[8]
                        if country != "US":
                            continue
                        name       = parts[1]
                        ascii_name = parts[2]
                        alt_names  = parts[3]   # comma-separated
                        lat        = float(parts[4])
                        lon        = float(parts[5])
                        state_code = parts[10]  # e.g. "TX"
                        population = int(parts[14]) if parts[14] else 0
                        state_name = _STATE_NAMES.get(state_code, state_code)
                        rows.append((name, ascii_name, alt_names, lat, lon,
                                     state_code, state_name, population))
                    except Exception:
                        continue

        print(f"  Found {len(rows)} US cities.")

        print(f"Building SQLite database: {DB_PATH}")
        if DB_PATH.exists():
            DB_PATH.unlink()

        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.executescript("""
            CREATE TABLE cities (
                name       TEXT NOT NULL,
                ascii_name TEXT NOT NULL,
                alt_names  TEXT,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                state_code TEXT,
                state_name TEXT,
                population INTEGER DEFAULT 0
            );
            CREATE INDEX idx_ascii ON cities (ascii_name COLLATE NOCASE);
            CREATE INDEX idx_name  ON cities (name COLLATE NOCASE);
        """)
        cur.executemany(
            "INSERT INTO cities VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

        size_kb = DB_PATH.stat().st_size // 1024
        print(f"  Done! Database size: {size_kb} KB")
        print(f"  Restart atlas-control to enable offline geocoding.")

    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    # argparse rather than a bare script: install.sh has always passed
    # `--output "$DATA_DIR/us_cities.db"`, which this script silently ignored —
    # harmless only because the hardcoded default happened to resolve to the
    # same path. Change DATA_DIR in install.sh and the DB lands somewhere the
    # app never reads.
    ap = argparse.ArgumentParser(
        description="Build the offline US city geocoding database from GeoNames.")
    ap.add_argument("--output", default=str(DEFAULT_DB_PATH),
                    help="destination SQLite file")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if the database already exists")
    args = ap.parse_args()

    out = Path(args.output)
    if out.exists() and not args.force:
        # Never prompt. This runs under `systemd-run` (via atlas-update) with no
        # stdin, where input() raises EOFError -> non-zero exit -> `set -e`
        # aborts the whole update.
        print(f"Database already exists: {out} ({out.stat().st_size // 1024} KB)")
        print("Use --force to rebuild.")
        sys.exit(0)
    download_and_build(out)

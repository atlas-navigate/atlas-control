#!/usr/bin/env python3
"""
Atlas Control — GeoNames fallback tiers for the offline reverse geocoder.

Fetches the two datasets ai_manager's reverse geocoder reads as tiers 2 and 3:

  data/US.txt          GeoNames US postal codes  (41 k ZIP centroids)
  data/cities5000.txt  GeoNames world cities     (69 k cities, population >= 5000)

Tier 1 (static/data/us_cities.db, built by download_cities.py) is US-only, so
cities5000.txt is the ONLY source of any non-US place name, and the ZIP
centroids are markedly better than city centroids in sparse terrain — on Mt
Rainier the ZIP tier says "Longmire, WA" (12 km) where a population-weighted
city lookup says "Tacoma, WA" (68 km).

Neither file was provisioned by anything in the repo until now, so both tiers
silently loaded zero rows on every install.

IMPORTANT — the US URL is export/**zip**/US.zip, not export/dump/US.zip. Both
exist and both contain a file called "US.txt", but they are different datasets
with different column orders. The dump variant is the full gazetteer, where
column 9 is 'cc2' rather than latitude; feeding it to _load_us_zips() makes
float() raise on every row, which that loader swallows, leaving the tier empty
with no error. That is the exact silent-failure mode this script exists to
prevent, which is why every download is validated against the real schema
before it is allowed to replace a good file.

Run once with internet access; works fully offline afterwards.

Usage:
    python3 download_geonames.py                 # skip files that already exist
    python3 download_geonames.py --force         # re-download regardless
    python3 download_geonames.py --data-dir DIR  # default: <script dir>/data
"""
import argparse
import os
import sys
import tempfile
import zipfile
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

# (member name, url, min expected rows, min columns, index that must parse as a float)
DATASETS = {
    "US.txt": {
        "url": "https://download.geonames.org/export/zip/US.zip",
        "min_rows": 30000,      # actual ~41,490
        "min_cols": 11,
        "float_col": 9,         # latitude
        "label": "US postal-code centroids",
    },
    "cities5000.txt": {
        "url": "https://download.geonames.org/export/dump/cities5000.zip",
        "min_rows": 50000,      # actual ~69,525
        "min_cols": 15,
        "float_col": 4,         # latitude
        "label": "world cities (population >= 5000)",
    },
}


_last_pct = [-1]


def _progress(count, block, total):
    # Only emit on a 10% change: install.sh pipes this to a log file, where a
    # per-8KB-block carriage-return spinner becomes hundreds of junk lines.
    if total <= 0:
        return
    pct = min(100, count * block * 100 // total)
    if pct // 10 != _last_pct[0] // 10:
        _last_pct[0] = pct
        print(f"  {pct}% ({total // 1024} KB total)", flush=True)


def _validate(path, spec):
    """Confirm the extracted file really is the dataset the loaders expect.

    An HTTP 200 proves nothing — the wrong-but-same-named GeoNames file returns
    200 and parses to zero usable rows. Check the schema, not the transfer.
    """
    rows = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < spec["min_cols"]:
                continue
            try:
                float(parts[spec["float_col"]])
            except (ValueError, IndexError):
                continue
            rows += 1
    if rows < spec["min_rows"]:
        raise ValueError(
            f"only {rows} usable rows (expected >= {spec['min_rows']}). "
            f"Wrong dataset or changed schema — refusing to install it.")
    return rows


def fetch(member, spec, data_dir, force=False):
    dest = data_dir / member
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"{member}: present ({dest.stat().st_size // 1024} KB) — skipping")
        return True

    print(f"{member}: downloading {spec['label']} ...")
    print(f"  {spec['url']}")
    tmp_zip = None
    tmp_out = dest.with_suffix(dest.suffix + ".tmp")
    _last_pct[0] = -1
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_zip = tmp.name
        urllib.request.urlretrieve(spec["url"], tmp_zip, reporthook=_progress)

        with zipfile.ZipFile(tmp_zip) as zf:
            if member not in zf.namelist():
                raise ValueError(
                    f"archive does not contain {member} (has: {zf.namelist()})")
            # Extract beside the target, validate, and only then move into place,
            # so a bad download can never replace a working file.
            with zf.open(member) as src, open(tmp_out, "wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)

        rows = _validate(tmp_out, spec)
        os.replace(tmp_out, dest)
        print(f"  OK — {rows} usable rows, {dest.stat().st_size // 1024} KB → {dest}")
        return True

    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        if tmp_out.exists():
            tmp_out.unlink()
        return False
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            os.unlink(tmp_zip)


def main():
    ap = argparse.ArgumentParser(
        description="Download GeoNames fallback datasets for the offline reverse geocoder.")
    ap.add_argument("--data-dir", default=str(SCRIPT_DIR / "data"),
                    help="directory to write US.txt and cities5000.txt into")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the files already exist")
    ap.add_argument("--only", choices=sorted(DATASETS),
                    help="fetch just one dataset")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    wanted = [args.only] if args.only else list(DATASETS)
    ok = all(fetch(m, DATASETS[m], data_dir, args.force) for m in wanted)

    if ok:
        print("\nDone. Restart atlas-control to pick up the new geocoder tiers.")
    else:
        # Non-zero so a caller can warn, but these are fallback tiers — the
        # primary us_cities.db path keeps working without them, so install.sh
        # must warn rather than abort on this.
        print("\nOne or more datasets failed. The primary geocoder "
              "(static/data/us_cities.db) still works; international coverage "
              "will be unavailable.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

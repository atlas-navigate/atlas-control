#!/usr/bin/env python3
"""Build an offline address + POI geocoding index → static/data/places.db.

100% offline. Source data is the OSM .pbf state extracts already on the box
(osrm-data/*.pbf — the same files OSRM routes on). For each state we use the
locally-installed `osmium` CLI to filter out address points and POIs, export
them to line-delimited GeoJSON, and load them into a SQLite FTS5 index that
/api/nav/geocode searches. No network access is used.

On the 8 GB Jetson, run it through build_places_safe.sh, which pauses Ray and
caps memory so the build can't freeze the box. Direct usage:
    python3 build_places_index.py                 # every osrm-data/*.pbf
    python3 build_places_index.py virginia maryland   # named states only
    python3 build_places_index.py --resume        # continue an interrupted build
    python3 build_places_index.py --vacuum        # also compact (heavy; optional)

The build writes to static/data/places.db.tmp and atomically renames on
success, so a running service keeps using the old index until the new one is
complete. The geocoder opens places.db read-only per query, so the new index
goes live without a restart.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PBF_DIR = os.path.join(HERE, "osrm-data")
OUT_DB = os.path.join(HERE, "static", "data", "places.db")
TMP_DB = OUT_DB + ".tmp"
REBUILD_DB = OUT_DB + ".rebuild"   # clean target used by --resume salvage
TMP_DIR = os.path.join(HERE, "static", "data", ".places_build")

# OSM tag keys whose presence makes an object worth indexing. Addresses come
# from addr:housenumber; the rest are the POI families people search for.
FILTER_TAGS = [
    "addr:housenumber",
    "amenity", "shop", "tourism", "leisure",
    "office", "healthcare", "craft", "place",
]

# Coarse category used by "<thing> near me" search in the geocode endpoint.
_AMENITY_COARSE = {
    "fuel": "fuel", "charging_station": "fuel",
    "restaurant": "food", "fast_food": "food", "cafe": "food",
    "food_court": "food", "bar": "food", "pub": "food", "biergarten": "food",
    "ice_cream": "food",
    "bank": "finance", "atm": "finance", "bureau_de_change": "finance",
    "hospital": "health", "clinic": "health", "doctors": "health",
    "pharmacy": "health", "dentist": "health", "veterinary": "health",
    "nursing_home": "health", "social_facility": "health",
    "police": "emergency", "fire_station": "emergency",
    "school": "education", "university": "education", "college": "education",
    "library": "education", "kindergarten": "education",
    "place_of_worship": "worship",
    "toilets": "facility", "drinking_water": "facility", "shower": "facility",
    "parking": "facility", "post_office": "facility", "townhall": "facility",
    "community_centre": "facility", "shelter": "facility", "fountain": "facility",
    "fuel_station": "fuel",
    "cinema": "attraction", "theatre": "attraction", "arts_centre": "attraction",
}
_GROCERY_SHOPS = {
    "supermarket", "convenience", "greengrocer", "general", "grocery",
    "butcher", "bakery", "deli", "farm",
}
_LODGING_TOURISM = {
    "hotel", "motel", "guest_house", "hostel", "camp_site", "caravan_site",
    "chalet", "apartment", "wilderness_hut", "alpine_hut",
}


def classify(props):
    """Return (kind, category) for an OSM feature's tag dict."""
    a = props.get("amenity")
    if a and a != "yes":
        return a, _AMENITY_COARSE.get(a, "amenity")
    s = props.get("shop")
    if s and s != "yes":
        return s, ("grocery" if s in _GROCERY_SHOPS else "shop")
    t = props.get("tourism")
    if t and t != "yes":
        return t, ("lodging" if t in _LODGING_TOURISM else "attraction")
    le = props.get("leisure")
    if le and le != "yes":
        return le, "leisure"
    o = props.get("office")
    if o and o != "yes":
        return o, "office"
    h = props.get("healthcare")
    if h and h != "yes":
        return h, "health"
    c = props.get("craft")
    if c and c != "yes":
        return c, "shop"
    p = props.get("place")
    if p and p != "yes":
        return p, "place"
    if props.get("addr:housenumber"):
        return "address", "address"
    return "poi", "other"


def centroid(geom, _cap=256):
    """Representative (lat, lon) for any GeoJSON geometry.

    Averages up to _cap vertices — a sample is plenty for a "navigate here"
    point and keeps big polygons (parks, lakes) from costing real time.
    """
    coords = []

    def rec(x):
        if len(coords) >= _cap:
            return
        if isinstance(x, (list, tuple)):
            if (len(x) == 2 and isinstance(x[0], (int, float))
                    and isinstance(x[1], (int, float))):
                coords.append((x[0], x[1]))
            else:
                for y in x:
                    if len(coords) >= _cap:
                        break
                    rec(y)

    rec(geom.get("coordinates"))
    if not coords:
        return None
    n = len(coords)
    return (sum(c[1] for c in coords) / n, sum(c[0] for c in coords) / n)


def state_label(stem):
    """'district-of-columbia' -> 'District of Columbia'."""
    minor = {"of", "and"}
    words = stem.replace("_", "-").split("-")
    return " ".join(w if w in minor else w.capitalize() for w in words)


def _build_pragmas(con):
    # Tuned for a bulk build on the box's 8 GB unified memory, which is SHARED
    # with the GPU + Ollama (~3.8 GB resident). temp_store=FILE (not MEMORY)
    # keeps the CREATE INDEX sort and VACUUM copy off the heap; SQLITE_TMPDIR
    # (set in main) points those temp files at the big disk. cache_size is held
    # to 64 MB so this connection's page cache can't balloon the heap — peak
    # build RSS stays well under 1 GB so it coexists with Ray instead of
    # freezing the Tegra (unified-memory exhaustion hard-hangs the box; the
    # kernel never gets to run a clean OOM-kill).
    con.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=FILE;
        PRAGMA cache_size=-65536;
    """)


def init_db(con):
    _build_pragmas(con)
    con.execute("""
        CREATE TABLE places(
            id      INTEGER PRIMARY KEY,
            name    TEXT,
            kind    TEXT,
            category TEXT,
            addr    TEXT,
            city    TEXT,
            state   TEXT,
            lat     REAL,
            lon     REAL
        )
    """)
    # One row per fully-loaded state extract. Written only after that state's
    # places are committed, so --resume can skip the states already done and
    # rerun just the remaining osmium extracts (the long, RAM-touching phase).
    con.execute("""
        CREATE TABLE IF NOT EXISTS loaded_states(
            stem TEXT PRIMARY KEY,
            n    INTEGER,
            ts   REAL
        )
    """)


def load_state(con, pbf_path):
    stem = os.path.basename(pbf_path).split(".")[0]
    label = state_label(stem)
    os.makedirs(TMP_DIR, exist_ok=True)
    filtered = os.path.join(TMP_DIR, stem + ".poi.pbf")
    nodeidx = os.path.join(TMP_DIR, stem + ".nodeidx")

    # 1) Filter addresses + POIs out of the full state extract.
    # Nodes + ways only. Relations (giant multipolygon coastlines/forests/lakes)
    # cost huge time to assemble in `osmium export` and are not searchable POIs.
    filt_args = ["osmium", "tags-filter", "--overwrite", "-o", filtered, pbf_path]
    for tag in FILTER_TAGS:
        filt_args += [f"n/{tag}", f"w/{tag}"]
    subprocess.run(filt_args, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2) Export to line-delimited GeoJSON on stdout and stream it in.
    # -i sparse_file_array spills the way→node-location index to a disk file
    # instead of the default flex_mem (heap). On a state with millions of
    # referenced nodes that index is the build's largest single allocation, and
    # keeping it off the unified-memory heap is what stops `osmium export` from
    # tipping the box into a freeze. The index file is removed below.
    exp = subprocess.Popen(
        ["osmium", "export", filtered, "-i", "sparse_file_array," + nodeidx,
         "-f", "jsonseq", "-x", "print_record_separator=false", "-o", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1 << 20)

    batch = []
    kept = 0
    INSERT = ("INSERT INTO places(name,kind,category,addr,city,state,lat,lon) "
              "VALUES(?,?,?,?,?,?,?,?)")
    for line in exp.stdout:
        line = line.lstrip("\x1e").strip()
        if not line:
            continue
        try:
            feat = json.loads(line)
        except ValueError:
            continue
        props = feat.get("properties") or {}
        name = props.get("name") or props.get("brand") or props.get("operator")
        hn = props.get("addr:housenumber")
        street = props.get("addr:street")
        # Keep only things a person could search for: a name, or a street address.
        if not name and not (hn and street):
            continue
        cen = centroid(feat.get("geometry") or {})
        if not cen:
            continue
        kind, category = classify(props)
        addr = f"{hn} {street}".strip() if (hn and street) else (street or "")
        city = (props.get("addr:city") or props.get("addr:town")
                or props.get("addr:village") or "")
        disp = name or addr
        if not disp:
            continue
        batch.append((name or "", kind, category, addr, city, label,
                      cen[0], cen[1]))
        if len(batch) >= 5000:
            con.executemany(INSERT, batch)
            kept += len(batch)
            batch = []
    if batch:
        con.executemany(INSERT, batch)
        kept += len(batch)
    exp.wait()
    con.execute("INSERT OR REPLACE INTO loaded_states(stem, n, ts) VALUES(?,?,?)",
                (stem, kept, time.time()))
    con.commit()                           # checkpoint: this state is fully done
    for tmp in (filtered, nodeidx):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return kept


def _mem_avail_mb():
    """System-wide available RAM in MB (so phase logs show real headroom)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def build_indexes(con, total, vacuum=False):
    """Build the FTS5 search index + lookup indexes over the loaded places.

    The FTS5 insert is the memory-critical step: SQLite buffers every pending
    term in a RAM hash until the transaction commits, so a single INSERT…SELECT
    over tens of millions of rows OOMs an 8 GB box. We insert in id-ranged
    chunks and commit each one, which flushes that hash to an on-disk segment
    and keeps peak memory flat regardless of row count.

    Every phase prints free RAM so a hang shows exactly where (and how close to
    the edge) the box was — the previous build crashed silently right here, in
    the index phase, with no log line to pin it down.
    """
    print(f"Building FTS5 index over {total:,} places… "
          f"(free {_mem_avail_mb()} MB)", flush=True)
    con.execute("""
        CREATE VIRTUAL TABLE places_fts USING fts5(
            name, addr, city, kind,
            content='places', content_rowid='id', tokenize='unicode61'
        )
    """)
    row = con.execute("SELECT MIN(id), MAX(id) FROM places").fetchone()
    lo, hi = (row[0], row[1]) if row and row[0] is not None else (1, 0)
    CHUNK = 200_000
    start, chunk_no = lo, 0
    while start <= hi:
        end = min(start + CHUNK - 1, hi)
        con.execute(
            "INSERT INTO places_fts(rowid, name, addr, city, kind) "
            "SELECT id, name, addr, city, kind FROM places "
            "WHERE id BETWEEN ? AND ?", (start, end))
        con.commit()                       # flush pending terms → on-disk segment
        chunk_no += 1
        if chunk_no % 25 == 0:
            print(f"  fts {end:,}/{hi:,}", flush=True)
        start = end + 1

    # Consolidate the per-chunk segments for fast queries. The single-shot
    # ('optimize') command rewrites the WHOLE index in one transaction and was
    # the build's worst memory spike over tens of millions of rows. Bounded
    # ('merge', N) does the same consolidation incrementally: each call merges
    # ~N pages then returns, and we commit between calls so peak memory stays
    # flat. Repeat until a call reports no rows changed (nothing left to merge).
    print(f"  merging fts segments… (free {_mem_avail_mb()} MB)", flush=True)
    for _ in range(200):
        before = con.total_changes
        con.execute("INSERT INTO places_fts(places_fts, rank) VALUES('merge', 64)")
        con.commit()
        if con.total_changes == before:    # merge was a no-op → fully consolidated
            break

    # CREATE INDEX sorts ~30M rows; temp_store=FILE + SQLITE_TMPDIR (set in main)
    # spill the sort runs to the big disk so the sorter's RAM stays bounded by
    # the 64 MB page cache. Commit after each so neither is held in one txn.
    print(f"Building lookup indexes… (free {_mem_avail_mb()} MB)", flush=True)
    con.execute("CREATE INDEX idx_places_cat_lat ON places(category, lat)")
    con.commit()
    con.execute("CREATE INDEX idx_places_lat ON places(lat)")
    con.commit()

    if vacuum:
        # VACUUM rewrites the ENTIRE multi-GB database in a single operation —
        # the heaviest step in the build and a prime suspect for the silent
        # freeze. A freshly-built, append-only DB has almost no free pages to
        # reclaim, so it's off by default; pass --vacuum only to squeeze size.
        print(f"Compacting database (VACUUM)… (free {_mem_avail_mb()} MB)",
              flush=True)
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.commit()


def _target_pbfs(targets):
    pbfs = sorted(f for f in os.listdir(PBF_DIR) if f.endswith(".osm.pbf"))
    if targets:
        want = {t.lower() for t in targets}
        pbfs = [f for f in pbfs if f.split(".")[0].lower() in want]
        if not pbfs:
            sys.exit(f"No matching .pbf for: {', '.join(targets)}")
    return pbfs


def _load_states(con, pbfs, done_stems):
    """osmium-load every target pbf whose state isn't already checkpointed."""
    for i, fn in enumerate(pbfs, 1):
        stem = fn.split(".")[0]
        if stem in done_stems:
            print(f"[{i}/{len(pbfs)}] {fn}: skip (already loaded)", flush=True)
            continue
        st = time.time()
        try:
            n = load_state(con, os.path.join(PBF_DIR, fn))
        except subprocess.CalledProcessError as e:
            print(f"[{i}/{len(pbfs)}] {fn}: FAILED ({e})", flush=True)
            continue
        print(f"[{i}/{len(pbfs)}] {fn}: {n:,} places ({time.time()-st:.0f}s)",
              flush=True)


def _resume_into_fresh(pbfs):
    """Salvage an interrupted build.

    A crash mid-build (e.g. a memory freeze during the FTS phase) leaves the
    old .tmp with an intact `places` table but a half-written, often un-droppable
    FTS index — which is why the previous in-place '--resume' could dead-end at
    "looks corrupt, rerun from scratch" and throw away the long osmium phase.
    Instead we copy the already-loaded `places` (and the per-state checkpoints)
    into a clean DB, never touching the corrupt FTS, then load only the states
    that hadn't finished. Returns the open fresh connection, or None if the old
    .tmp can't be salvaged (caller falls back to a full build).
    """
    try:
        src = sqlite3.connect(f"file:{TMP_DB}?mode=ro", uri=True)
        done = {r[0] for r in src.execute("SELECT stem FROM loaded_states")}
        have = src.execute("SELECT count(*) FROM places").fetchone()[0]
        src.close()
    except sqlite3.DatabaseError as e:
        print(f"--resume: {TMP_DB} not salvageable ({e}); doing a full build.",
              flush=True)
        return None
    if not done or not have:
        print(f"--resume: {TMP_DB} has no checkpointed states; full build.",
              flush=True)
        return None

    if os.path.exists(REBUILD_DB):
        os.remove(REBUILD_DB)
    con = sqlite3.connect(REBUILD_DB)
    init_db(con)
    print(f"Resuming: copying {have:,} places from {len(done)} finished states "
          f"out of the interrupted build…", flush=True)
    con.execute("ATTACH DATABASE ? AS src", (TMP_DB,))
    con.execute("INSERT INTO main.places "
                "SELECT id,name,kind,category,addr,city,state,lat,lon "
                "FROM src.places")
    con.execute("INSERT OR REPLACE INTO main.loaded_states "
                "SELECT stem,n,ts FROM src.loaded_states")
    con.commit()
    con.execute("DETACH DATABASE src")
    _load_states(con, pbfs, done)
    return con


def main():
    if not shutil.which("osmium"):
        sys.exit("osmium CLI not found — install osmium-tool first.")
    argv = sys.argv[1:]
    resume = "--resume" in argv
    vacuum = "--vacuum" in argv
    targets = [a for a in argv if not a.startswith("-")]

    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    # Keep SQLite's own temp files (index sort, VACUUM copy) on the big disk
    # rather than a small/tmpfs /tmp, so memory-safe temp_store=FILE has room.
    os.environ.setdefault("SQLITE_TMPDIR", os.path.dirname(OUT_DB))

    t0 = time.time()
    pbfs = _target_pbfs(targets)

    con = None
    build_path = TMP_DB
    if resume and os.path.exists(TMP_DB):
        con = _resume_into_fresh(pbfs)
        if con is not None:
            build_path = REBUILD_DB
    if con is None:                    # fresh build (default, or resume fell back)
        if resume:
            print(f"--resume: no {TMP_DB} to resume; doing a full build.",
                  flush=True)
        if os.path.exists(TMP_DB):
            os.remove(TMP_DB)
        con = sqlite3.connect(TMP_DB)
        init_db(con)
        _load_states(con, pbfs, set())
        build_path = TMP_DB

    total = con.execute("SELECT count(*) FROM places").fetchone()[0]
    if not total:
        sys.exit("No places loaded — nothing to index.")
    build_indexes(con, total, vacuum=vacuum)
    con.close()

    os.replace(build_path, OUT_DB)
    if build_path != TMP_DB and os.path.exists(TMP_DB):
        os.remove(TMP_DB)              # drop the salvaged interrupted build
    size_mb = os.path.getsize(OUT_DB) / 1e6
    try:
        os.rmdir(TMP_DIR)
    except OSError:
        pass
    print(f"Done: {total:,} places → {OUT_DB} ({size_mb:.0f} MB) "
          f"in {time.time()-t0:.0f}s", flush=True)
    print("Restart atlas-control to use the new index.", flush=True)


if __name__ == "__main__":
    main()

# 85 — Trails, the offline address/POI index and the geocoder fallback data.
# NPS trails / trailheads database:
# Must run AFTER the state .osm.pbf downloads above — download_nps_trails.py's
# OSM extraction (build_from_osm) globs osrm-data/*.osm.pbf for trail/trailhead
# geometry. Running it earlier (as this used to, with the map data) finds no
# PBFs yet on a fresh install, so it silently skips OSM extraction and leaves
# the trails/trailheads tables empty forever — nps_units/park_boundaries still
# populate fine since those come from a live NPS API call, so the DB file looks
# non-empty and the "already present" guard below then permanently hides the
# gap. Park metadata itself doesn't depend on state selection, so this still
# runs even when no states were selected (OSM extraction then finds nothing to
# extract and app.py geocoder falls back to trailheads.db as before).
section "NPS Trails / Trailheads Database"

DATA_DIR="$APP_DIR/static/data"
if [[ ! -s "$DATA_DIR/nps_trails.db" ]]; then
    info "Downloading NPS trails database ..."
    if sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_nps_trails.py"; then
        log "NPS trails database ready"
    elif sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_trailheads.py"; then
        log "Legacy trailheads database ready"
    else
        die "Failed to build trail database"
    fi
else
    log "NPS trails database present ($(du -sh "$DATA_DIR/nps_trails.db" | cut -f1))"
fi

# ── Offline places / geocoding index (address + POI search) ─────────────────
# Build static/data/places.db from the state .osm.pbf extracts just downloaded,
# so users can type a store / street address / POI name and route to it
# (Google-Maps-style). build_places_index.py reads osrm-data/*.osm.pbf — the same
# files OSRM routes on; _process_state keeps them for this. build_places_safe.sh
# makes the heavy build safe on 8 GB: it adds swap, pauses Ray (ollama), and
# memory-caps the build so it can't freeze the box. Idempotent + non-fatal, so a
# long or interrupted build never aborts the install.
section "Offline Places / Geocoding Index"

PLACES_DB="$APP_DIR/static/data/places.db"
if [[ -s "$PLACES_DB" ]]; then
    log "Places index already present ($(du -sh "$PLACES_DB" | cut -f1)) — skipping"
elif ! compgen -G "$OSRM_DIR/*.osm.pbf" >/dev/null 2>&1; then
    warn "No state .pbf files present — skipping places index (install states first)"
else
    info "Building offline address/POI search index (memory-safe; this can take a while) ..."
    if bash "$APP_DIR/build_places_safe.sh"; then
        log "Places index built ($(du -sh "$PLACES_DB" 2>/dev/null | cut -f1)) — 'type a place/address' search enabled"
    else
        warn "Places index build did not finish — resume later: sudo bash $APP_DIR/build_places_safe.sh --resume"
    fi
fi
# Only the builders above (OSRM, trails, places) read the state extracts
# (~11 GB); nothing at runtime does. Kept while the places build still needs
# them for --resume; a state added later downloads its extract again.
if [[ -s "$PLACES_DB" ]]; then
    rm -f "$OSRM_DIR"/*.osm.pbf
fi

# ── Offline geocoder fallback data (GeoNames) ───────────────────────────────
# Ray's reverse geocoder has three tiers. Tier 1 (static/data/us_cities.db, stage 70)
# is US-only, so these two files are the ONLY source of any non-US place name,
# and the ZIP centroids beat city centroids badly in sparse terrain (on Mt
# Rainier: "Longmire, WA" 12 km vs "Tacoma, WA" 68 km).
#
# Nothing in this repo fetched them until now, so both tiers silently loaded
# zero rows on every install and Ray hallucinated city names from bare lat/lon.
#
# Non-fatal by design — these are fallback tiers; us_cities.db keeps the primary
# path working, so a failed download must never abort an install or an update.
section "Offline Geocoder Data (GeoNames)"

if [[ ! -s "$APP_DIR/data/US.txt" || ! -s "$APP_DIR/data/cities5000.txt" ]]; then
    info "Downloading GeoNames geocoder data (~6 MB) ..."
    if sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_geonames.py" \
            --data-dir "$APP_DIR/data"; then
        log "Geocoder fallback data ready"
    else
        warn "GeoNames download failed — Ray keeps US city geocoding via"
        warn "us_cities.db, but international coordinates will not resolve."
        warn "Re-run later with: $VENV/bin/python3 $APP_DIR/download_geonames.py"
    fi
else
    log "Geocoder fallback data present ($(du -sh "$APP_DIR/data/US.txt" | cut -f1) + $(du -sh "$APP_DIR/data/cities5000.txt" | cut -f1))"
fi

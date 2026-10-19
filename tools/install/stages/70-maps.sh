# 70 — Map data: basemap, label fonts, topographic overlay, US cities.
section "Map Data"

TILES_DIR="$APP_DIR/static/tiles"

# Protomaps publishes a basemap build daily. The latest is resolved from its
# metadata only when the basemap must be built (a re-run needs no network);
# if the lookup fails, the last verified-good build below is used.
PROTOMAPS_METADATA_URL="https://build-metadata.protomaps.dev/builds.json"
# Last-resort key. Protomaps prunes old daily builds, so this WILL 404
# eventually — refresh it periodically. The basemap step HEAD-checks the
# resolved URL and aborts with a clear message rather than downloading a dead
# link into map.pmtiles.
PROTOMAPS_FALLBACK_KEY="20260608.pmtiles"

resolve_latest_protomaps_url() {
    local key
    key="$("$SYS_PY" -I - "$PROTOMAPS_METADATA_URL" <<'PYEOF'
import json, sys, urllib.request
req = urllib.request.Request(sys.argv[1], headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        builds = json.load(r)
    if isinstance(builds, list) and builds:
        print(builds[-1].get("key", ""))
except Exception:
    pass
PYEOF
)"
    echo "https://build.protomaps.com/${key:-$PROTOMAPS_FALLBACK_KEY}"
}

# ── PMTiles CLI (go-pmtiles, ARM64 binary) ───────────────────────────────────
PMTILES_BIN="$APP_DIR/pmtiles"
if [[ -x "$PMTILES_BIN" ]]; then
    log "pmtiles CLI already present"
else
    info "Downloading pmtiles CLI ..."
    # Fetch the latest release tag from GitHub, fall back to known-good version
    PMTILES_VER=$(curl -sfL "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest" \
        | "$SYS_PY" -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null \
        || echo "v1.22.3")
    PMTILES_URL="https://github.com/protomaps/go-pmtiles/releases/download/${PMTILES_VER}/go-pmtiles_${PMTILES_VER#v}_Linux_arm64.tar.gz"
    info "  Version: $PMTILES_VER"
    retry 3 10 bash -o pipefail -c 'curl -fsSL "$1" | tar -xz -C "$2" pmtiles' _ "$PMTILES_URL" "$APP_DIR" \
        || die "Could not download the pmtiles CLI ($PMTILES_URL) — connect to the Internet and re-run."
    chmod +x "$PMTILES_BIN"
    chown "$ATLAS_USER:$ATLAS_USER" "$PMTILES_BIN"
    log "pmtiles CLI installed ($PMTILES_VER)"
fi

# ── Vector basemap (world z0–6 + US z7–15, ~20 GB) ───────────────────────────
BASEMAP="$TILES_DIR/map.pmtiles"
if [[ -f "$BASEMAP" ]]; then
    log "Vector basemap already present ($(du -sh "$BASEMAP" | cut -f1)) — skipping download"
else
    BASEMAP_URL="$(resolve_latest_protomaps_url)"
    log "Using Protomaps basemap: ${BASEMAP_URL##*/}"
    # Verify the resolved URL is reachable BEFORE the ~20 GB extract so a
    # stale fallback key (Protomaps prunes old builds) or being offline fails
    # fast with a clear message instead of a half-built archive.
    if ! curl -sfI --max-time 30 "$BASEMAP_URL" >/dev/null 2>&1; then
        die "Basemap URL not reachable: $BASEMAP_URL
    The Protomaps build may have been pruned, or the machine is offline.
    Find a current key at $PROTOMAPS_METADATA_URL, update PROTOMAPS_FALLBACK_KEY
    in tools/install/stages/70-maps.sh (or restore connectivity), then re-run."
    fi
    # Only the byte ranges for the world overview and the US are fetched, not
    # the ~127 GB planet (setup_basemap.sh, basemap-region-us.geojson).
    info "Building map.pmtiles (~20 GB: world z0–6 + US detail) ..."
    info "Source: $BASEMAP_URL"
    sudo -u "$ATLAS_USER" bash "$APP_DIR/setup_basemap.sh" "$BASEMAP_URL" "$BASEMAP"
    log "Vector basemap ready ($(du -sh "$BASEMAP" | cut -f1))"
fi

# ── Noto Sans glyph PBFs (required for map label rendering) ──────────────────
# The font directory holds per-weight Unicode-range PBF files, not TTF files.
# The map style requests the "Noto Sans Regular" stack (templates/index.html),
# so gate specifically on THAT weight being complete — counting all weights
# together let a fully-downloaded Bold mask a missing/partial Regular and leave
# the map with unlabeled features. Regular has 256 ranges; allow one of slack.
# download_fonts.sh is idempotent (skips ranges already on disk) and re-verifies
# the primary weight itself before returning success.
PRIMARY_FONT_DIR="$APP_DIR/static/fonts/Noto Sans Regular"
PRIMARY_FONT_COUNT=$(find "$PRIMARY_FONT_DIR" -name "*.pbf" 2>/dev/null | wc -l || true)
if [[ $PRIMARY_FONT_COUNT -lt 255 ]]; then
    info "Downloading map font glyphs (~8 MB) ..."
    sudo -u "$ATLAS_USER" bash "$APP_DIR/download_fonts.sh"
    log "Font glyphs downloaded"
else
    log "Font glyphs already present (Noto Sans Regular: $PRIMARY_FONT_COUNT ranges)"
fi

# ── Topographic overlay (topo.pmtiles) ───────────────────────────────────────
# Built once from a downloaded z8–13 JPEG tile cache (~25 GB), which is then
# deleted: the map reads only topo.pmtiles.
TOPO_PMTILES="$TILES_DIR/topo.pmtiles"
TOPO_TILES="$TILES_DIR/topo"
TOPO_MIN_ZOOM=8
TOPO_MAX_ZOOM=13
if [[ -f "$TOPO_PMTILES" ]]; then
    log "Topographic PMTiles archive present ($(du -sh "$TOPO_PMTILES" | cut -f1)) — skipping"
else
    sudo -u "$ATLAS_USER" mkdir -p "$TOPO_TILES"
    # A sufficiently complete cache at the target max zoom (an interrupted
    # earlier run) is built from as it is; download_topo.py skips tiles it has.
    TARGET_ZOOM_COUNT=$(find "$TOPO_TILES/$TOPO_MAX_ZOOM" \( -name "*.jpg" -o -name "*.jpeg" \) 2>/dev/null | wc -l || true)
    if [[ $TARGET_ZOOM_COUNT -lt 1000 ]]; then
        info "Downloading topo tiles (z${TOPO_MIN_ZOOM}–${TOPO_MAX_ZOOM}, all states, ~5–15 GB) ..."
        sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_topo.py" \
            --min-zoom "$TOPO_MIN_ZOOM" --max-zoom "$TOPO_MAX_ZOOM" \
            --output-dir "$TOPO_TILES" \
            --workers 20 --yes
        log "Topo tiles downloaded"
    else
        log "Topo tile cache present (z${TOPO_MAX_ZOOM}: $TARGET_ZOOM_COUNT tiles) — building from it"
    fi
    info "Building topo.pmtiles ..."
    sudo -u "$ATLAS_USER" bash "$APP_DIR/setup_topo_pmtiles.sh"
    log "topo.pmtiles built ($(du -sh "$TOPO_PMTILES" | cut -f1))"
fi
if [[ -f "$TOPO_PMTILES" ]]; then
    rm -rf "$TOPO_TILES"
fi

# ── US cities database ───────────────────────────────────────────────────────
DATA_DIR="$APP_DIR/static/data"
mkdir -p "$DATA_DIR"
if [[ ! -s "$DATA_DIR/us_cities.db" ]]; then
    info "Downloading US cities database ..."
    sudo -u "$ATLAS_USER" "$VENV/bin/python3" "$APP_DIR/download_cities.py" \
        --output "$DATA_DIR/us_cities.db"
    log "US cities database ready"
else
    log "US cities database present ($(du -sh "$DATA_DIR/us_cities.db" | cut -f1))"
fi

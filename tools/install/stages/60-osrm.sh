# 60 — OSRM, the routing engine (tools, profiles, --mmap support).
section "OSRM Routing Engine"

# Install order of preference:
#   1. Already on PATH        → nothing to do
#   2. Pre-built binaries      → drop them into /usr/local/bin (seconds)
#   3. Build from source       → setup_native_osrm.sh (20–40 min on Jetson)
#
# Pre-built native binaries staged on the data drive (e.g.
# /atlas_data/osrm-native-bin/osrm-*) let us skip the from-source build
# entirely. They're dynamically linked against system Boost/TBB, which stage 20
# already installed (libboost-all-dev,
# libtbb-dev), so a straight copy into PATH is all that's needed.
#
# All FOUR tools are required, not just osrm-routed: processing a state needs
# osrm-extract / osrm-partition / osrm-customize (stage 80), and osrm-routed
# only serves the result. A prebuilt dir that stages just osrm-routed is
# therefore incomplete — fall through to the source build, which produces the
# whole toolset.
OSRM_PREBUILT_DIR="$ATLAS_DATA/osrm-native-bin"
OSRM_TOOLS=(osrm-routed osrm-extract osrm-partition osrm-customize)

_osrm_tools_on_path() {
    local t
    for t in "${OSRM_TOOLS[@]}"; do command -v "$t" &>/dev/null || return 1; done
}
_osrm_prebuilt_complete() {
    local t
    for t in "${OSRM_TOOLS[@]}"; do [[ -x "$OSRM_PREBUILT_DIR/$t" ]] || return 1; done
}

if _osrm_tools_on_path; then
    log "OSRM tools already installed: $(osrm-routed --version 2>/dev/null | head -1)"
elif _osrm_prebuilt_complete; then
    info "Installing pre-built OSRM binaries from $OSRM_PREBUILT_DIR ..."
    # `install -m 755 <src>... <dir>` copies each osrm-* binary into a PATH
    # directory and sets its mode to rwxr-xr-x (world-executable) in one step.
    install -m 755 "$OSRM_PREBUILT_DIR"/osrm-* "$R/usr/local/bin/"
    ldconfig 2>/dev/null || true   # refresh linker cache for the new binaries
    hash -r                        # forget any cached "not found" for osrm-*
    if _osrm_tools_on_path; then
        log "OSRM binaries installed from prebuilt: $(osrm-routed --version 2>/dev/null | head -1)"
    else
        die "Prebuilt OSRM install failed — one or more of ${OSRM_TOOLS[*]} not on PATH.
    Check that $OSRM_PREBUILT_DIR/osrm-* are executables for this arch and that
    their shared-library deps resolve (ldd $OSRM_PREBUILT_DIR/osrm-routed)."
    fi
else
    if compgen -G "$OSRM_PREBUILT_DIR/osrm-*" >/dev/null 2>&1; then
        warn "Prebuilt OSRM dir incomplete (need all of: ${OSRM_TOOLS[*]}) — building from source"
    fi
    info "Building OSRM from source (this takes 20–40 minutes on Jetson) ..."
    bash "$APP_DIR/setup_native_osrm.sh" --no-cleanup-prompt
    log "OSRM routing engine built and installed"
fi

# ── OSRM routing profiles (car.lua + shared lib/) ────────────────────────────
# Both routing profiles depend on OSRM's shared Lua profile library:
#   • car.lua is loaded directly from $OSRM_PROFILES_DIR/car.lua (see CAR_LUA,
#     stage 80) and require()s lib/* relative to itself.
#   • hiking.lua require()s the same lib/* via its package.path (which points at
#     $OSRM_PROFILES_DIR).
# A from-source `make install` drops these in place, but the prebuilt-binary
# path does not — so install the version-matched copy bundled in the repo
# whenever the system location is missing. Bundling keeps the install
# self-contained and immune to how the binaries were obtained.
OSRM_PROFILES_DIR="$R/usr/local/share/osrm/profiles"
if [[ ! -f "$OSRM_PROFILES_DIR/car.lua" || ! -d "$OSRM_PROFILES_DIR/lib" ]]; then
    info "Installing OSRM routing profiles to $OSRM_PROFILES_DIR ..."
    mkdir -p "$OSRM_PROFILES_DIR/lib"
    cp "$APP_DIR/osrm-profiles/car.lua"     "$OSRM_PROFILES_DIR/car.lua"
    cp "$APP_DIR/osrm-profiles/lib/"*.lua   "$OSRM_PROFILES_DIR/lib/"
    log "OSRM profiles installed (car.lua + shared lib/)"
else
    log "OSRM profiles already present ($OSRM_PROFILES_DIR)"
fi

# ── --mmap (routing memory mode) ─────────────────────────────────────────────
# routing_node.py launches osrm-routed with --mmap=1 so a state graph is
# file-backed page cache (~42 MB RSS) instead of ~1.1 GB of anonymous RAM. That
# is what lets Ray stay resident during a long-distance route instead of being
# evicted for it: the unload in app.py is now a pressure valve that only fires
# below ~800 MB free, not the default path. A binary without the flag exits
# immediately on every launch, _port_ready times out, and route() falls back to
# a straight line — a well-formed response, so it presents as "routing is
# broken" rather than as a missing command-line option.
#
# The first two branches above (already on PATH / prebuilt binaries from
# $OSRM_PREBUILT_DIR) accept any vintage without a version check, so an older
# osrm-routed can land here. Checked before any state is processed, so a
# rebuilt binary is the one that builds the graphs.
#
# Probe --help rather than comparing version strings: the binary itself is the
# authority on what it accepts, and the check costs milliseconds.
#
# Capture --help into a variable instead of piping it into grep. We run under
# `set -o pipefail` (install.sh), so `osrm-routed --help | grep -q --mmap`
# reports failure whenever EITHER side fails — and plenty of CLIs print help
# then exit non-zero. That would read a perfectly capable binary as lacking the
# flag and start a needless 20–40 min rebuild.
# v5.27.1 happens to exit 0, but this check exists for binaries we have not seen.
_osrm_has_mmap() {
    local help
    help="$(osrm-routed --help 2>&1 || true)"
    grep -q -- '--mmap' <<<"$help"
}

OSRM_MMAP_OK=true
if ! command_exists osrm-routed; then
    warn "osrm-routed not on PATH — skipping --mmap capability check"
elif _osrm_has_mmap; then
    log "OSRM supports --mmap — Ray and long-distance routing can run together"
else
    warn "Installed osrm-routed predates --mmap — rebuilding from source (20–40 min) ..."
    # Guarded: unlike the build above (no OSRM at all, so dying is correct), a
    # working-but-older binary is already installed here. Under `set -e` + the
    # ERR trap an unguarded build failure would abort the whole install —
    # worse than continuing in high-memory mode.
    if bash "$APP_DIR/setup_native_osrm.sh" --no-cleanup-prompt; then
        hash -r   # forget the cached path to the old binary
        if _osrm_has_mmap; then
            log "OSRM rebuilt with --mmap support"
        else
            OSRM_MMAP_OK=false
            warn "Rebuilt osrm-routed still lacks --mmap — long-distance routes may"
            warn "degrade to straight lines while Ray is loaded."
        fi
    else
        OSRM_MMAP_OK=false
        warn "OSRM rebuild failed — keeping the existing binary and continuing."
        warn "Routing stays in high-memory mode; see the summary at the end."
    fi
fi

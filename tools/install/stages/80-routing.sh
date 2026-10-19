# 80 — Offline routing data for the US states (car + hiking graphs).
section "Routing Data (US States)"

# Routing profiles to process per state (car always included; hiking optional)
ROUTING_PROFILES=("car" "hiking")

OSRM_DIR="$APP_DIR/osrm-data"
CAR_LUA="$R/usr/local/share/osrm/profiles/car.lua"
HIKE_LUA="$APP_DIR/hiking.lua"

_process_state() {
    local abbr="$1"
    local profile="$2"   # car | hiking
    local gf_name="${GF[$abbr]}"
    local state_dir="$OSRM_DIR/states/${gf_name}/${profile}"
    local pbf="$OSRM_DIR/${gf_name}.osm.pbf"

    # Already processed?
    [[ -f "$state_dir/.processed" ]] && return 0

    mkdir -p "$state_dir"

    # Download .pbf — also re-download if a previous attempt left a 0-byte file
    local pbf_size
    pbf_size=$(stat -c%s "$pbf" 2>/dev/null || echo 0)
    if [[ ! -f "$pbf" || "$pbf_size" -eq 0 ]]; then
        [[ "$pbf_size" -eq 0 && -f "$pbf" ]] && rm -f "$pbf"
        info "  Downloading ${abbr} (${gf_name}) ..."
        if ! retry 3 30 wget -q --show-progress \
                -O "${pbf}.tmp" \
                "https://download.geofabrik.de/north-america/us/${gf_name}-latest.osm.pbf"; then
            warn "  Download failed for ${abbr} — skipping"
            rm -f "${pbf}.tmp"
            return 1
        fi
        mv "${pbf}.tmp" "$pbf"
        chown "$ATLAS_USER:$ATLAS_USER" "$pbf"
    fi

    # Choose Lua profile
    local lua_file
    if [[ "$profile" == "hiking" ]]; then
        lua_file="$HIKE_LUA"
    else
        lua_file="$CAR_LUA"
    fi

    info "  Processing ${abbr}/${profile} ..."
    cp "$pbf" "$state_dir/region.osm.pbf"

    local extract_log="$state_dir/osrm-extract.log"
    local partition_log="$state_dir/osrm-partition.log"
    local customize_log="$state_dir/osrm-customize.log"

    if ! osrm-extract -p "$lua_file" "$state_dir/region.osm.pbf" > /dev/null 2>"$extract_log"; then
        warn "  osrm-extract failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$extract_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$extract_log"

    if ! osrm-partition "$state_dir/region.osrm" > /dev/null 2>"$partition_log"; then
        warn "  osrm-partition failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$partition_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$partition_log"

    if ! osrm-customize "$state_dir/region.osrm" > /dev/null 2>"$customize_log"; then
        warn "  osrm-customize failed for ${abbr}/${profile} — skipping"
        sed -n '1,120p' "$customize_log" >&2
        rm -f "$state_dir/region.osm.pbf"
        return 1
    fi
    rm -f "$customize_log"

    rm -f "$state_dir/region.osm.pbf"
    # Only osrm-customize reads these (~30% of a graph, ~32 GB across all
    # states); osrm-routed never does. Rebuilding a state starts from extract.
    rm -f "$state_dir"/region.osrm.{ebg,enw,cnbg,cnbg_to_ebg,turn_penalties_index}
    touch "$state_dir/.processed"
    chown -R "$ATLAS_USER:$ATLAS_USER" "$state_dir"
    chmod -R a+rX "$state_dir"     # osrm-extract writes .fileIndex 0700
    log "  ${abbr}/${profile} ready"
}

# Which states: --states, else asked on a terminal, else (unattended) the
# states this unit already has — a payload-provisioned unit carries them all,
# and a re-run never starts hours of downloads nobody asked for.
_states_present() {
    local abbr
    for abbr in "${!GF[@]}"; do
        if [[ -d "$OSRM_DIR/states/${GF[$abbr]}" ]]; then echo "$abbr"; fi
    done | sort
}
mapfile -t PRESENT < <(_states_present)
if [[ -z "$STATES" ]]; then
    if [[ -t 0 && -z "$ASSUME_YES" ]]; then
        DEFAULT_STATES=$([[ ${#PRESENT[@]} -gt 0 ]] && echo keep || echo all)
        echo ""
        echo -e "  Atlas Control supports offline routing for all 50 US states."
        echo -e "  Processing all states takes ${YLW}many hours${NC} and ~110 GB of storage."
        echo ""
        echo -e "  Options:"
        echo -e "    ${BLD}all${NC}    — All 50 states + DC (full national coverage)"
        echo -e "    ${BLD}keep${NC}   — The ${#PRESENT[@]} states already here (finish any incomplete one)"
        echo -e "    ${BLD}STATE${NC}  — Space-separated abbreviations, e.g: MD VA WV PA"
        echo -e "    ${BLD}none${NC}   — Skip now; add states later by re-running install.sh"
        echo ""
        read -rp "  States to install [$DEFAULT_STATES]: " STATES || true
        STATES="${STATES:-$DEFAULT_STATES}"
        STATES="$(normalize_states "$STATES")" || die "Unknown state: $STATES (use abbreviations such as MD VA, or all / keep / none)"
    else
        STATES=keep
    fi
fi

case "$STATES" in
    none) STATE_LIST=() ;;
    keep) STATE_LIST=("${PRESENT[@]}") ;;
    all)  mapfile -t STATE_LIST < <(printf '%s\n' "${!GF[@]}" | sort) ;;
    *)    read -ra STATE_LIST <<<"$STATES" ;;
esac

if [[ ${#STATE_LIST[@]} -eq 0 ]]; then
    warn "No routing data to install (--states $STATES). Add states later: sudo ./install.sh --bundle DIR --states \"MD VA\""
else
    info "Routing data for ${#STATE_LIST[@]} state(s): ${STATE_LIST[*]}"
    for abbr in "${STATE_LIST[@]}"; do
        for profile in "${ROUTING_PROFILES[@]}"; do
            _process_state "$abbr" "$profile" || warn "  ${abbr}/${profile} failed — continuing"
        done
    done
    log "State routing data processing complete"
fi
ROUTING_STATES=$(_states_present | wc -l)

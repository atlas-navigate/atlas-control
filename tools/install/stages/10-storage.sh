# 10 — /atlas_data and the dataset builders.
section "Storage"

# Atlas data (maps, routing, DBs) lives under /atlas_data. On current Jetson
# builds the OS and this data share a single NVMe, so /atlas_data is just a
# directory on the root filesystem — nothing to detect, partition, format, or
# mount. (Earlier builds put the OS on a microSD card and Atlas data on a
# SEPARATE NVMe mounted at /atlas_data; those installs keep working unchanged
# because we honor an existing mount and never reformat anything.)
if mountpoint -q "$ATLAS_DATA" 2>/dev/null; then
    log "/atlas_data is a separate mount on $(findmnt -n -o SOURCE "$ATLAS_DATA") — using it as-is"
else
    mkdir -p "$ATLAS_DATA"
    log "Using /atlas_data on the root filesystem ($(findmnt -n -o SOURCE / 2>/dev/null || echo '/'))"
fi
mkdir -p "$APP_DIR"
chown "$ATLAS_USER:$ATLAS_USER" "$ATLAS_DATA" "$APP_DIR"

# The Atlas software comes only from the signed release (stage 95) and runs
# from /opt/atlas. This directory holds Atlas's state and datasets; the
# dataset builders run from here when data must be (re)built, so the kit's
# copies of them are placed alongside the data.
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
    info "Copying the dataset builders to $APP_DIR ..."
    # Excludes protect everything created at runtime or by later install
    # steps from being clobbered:
    #   venv/ osrm-data/ tiles/        — rebuilt/downloaded artifacts
    #   data/ updates/ cache/          — the owner's state (service account)
    #   atlas.db / hotspot_config.json — legacy files with per-unit content
    #   osrm_active.json / logs        — runtime state
    rsync -a --exclude='.git/' \
              --exclude='venv/' --exclude='__pycache__/' \
              --exclude='*.pyc' --exclude='*.log' --exclude='logs/' \
              --exclude='osrm-data/' --exclude='static/tiles/' \
              --exclude='/data/' --exclude='/updates/' --exclude='/cache/' \
              --exclude='atlas.db' \
              --exclude='hotspot_config.json' \
              --exclude='osrm_active.json' \
              "$SRC_DIR/" "$APP_DIR/"
    log "Dataset builders copied"
else
    log "Running from the install location — no copy needed"
fi

# A fresh checkout can carry stale SQLite WAL/SHM files without their
# database; they would corrupt the brand-new database on first run.
for stale in "$APP_DIR/data/meshtastic.db-wal" "$APP_DIR/data/meshtastic.db-shm"; do
    if [[ -f "$stale" && ! -f "$APP_DIR/data/meshtastic.db" ]]; then
        rm -f "$stale"
        warn "Removed stale $(basename "$stale") (no matching database)"
    fi
done
# Left by the git-based updater that signed releases replaced.
rm -f "$APP_DIR/logs/update.log"

mkdir -p "$APP_DIR/data" \
         "$APP_DIR/static/fonts" \
         "$APP_DIR/static/tiles" \
         "$APP_DIR/static/data" \
         "$APP_DIR/osrm-data/states" \
         "$APP_DIR/logs" \
         "$OLLAMA_DATA_DIR/models"
own_datasets
chmod 755 "$ATLAS_DATA" "$OLLAMA_DATA_DIR" "$OLLAMA_DATA_DIR/models"

# ~/atlas-control → /atlas_data/atlas-control for the setup account.
LINK="$R$ATLAS_HOME/atlas-control"
if [[ "$APP_DIR" != "$LINK" && -d "$R$ATLAS_HOME" ]]; then
    if [[ -L "$LINK" && "$(readlink "$LINK")" == "${APP_DIR#$R}" ]]; then
        :
    else
        if [[ -L "$LINK" || -f "$LINK" ]]; then
            rm -f "$LINK"
        elif [[ -d "$LINK" ]]; then
            mv "$LINK" "${LINK}.bak"
        fi
        ln -s "${APP_DIR#$R}" "$LINK"
        chown -h "$ATLAS_USER:$ATLAS_USER" "$LINK"
        log "Symlink: ~/atlas-control → /atlas_data/atlas-control"
    fi
fi

# 40 — Python environment for the dataset builders.
# Only the dataset builders (stages 70–85) use this environment, and of them
# only the topo downloader needs anything beyond the standard library
# (requirements-builders.txt). Atlas itself runs from its release's own
# environment, built offline from the release's signed wheels.
section "Python Environment (dataset builders)"

VENV="$APP_DIR/venv"
if [[ ! -x "$VENV/bin/python3" ]]; then
    info "Creating virtual environment ..."
    sudo -u "$ATLAS_USER" "$SYS_PY" -m venv --system-site-packages "$VENV"
fi
# Already satisfied (a re-run, an offline unit, a developer checkout): no pip.
if sudo -u "$ATLAS_USER" "$VENV/bin/python3" -c 'import PIL' 2>/dev/null; then
    log "Builder environment ready"
else
    info "Installing the builders' Python packages ..."
    retry 3 10 sudo -u "$ATLAS_USER" "$VENV/bin/pip" install -q -r "$SRC_DIR/requirements-builders.txt" \
        || die "Could not install the dataset builders' packages (offline?) — connect and re-run."
    sudo -u "$ATLAS_USER" "$VENV/bin/python3" -c 'import PIL' \
        || die "The builder environment still cannot import Pillow"
    log "Builder environment ready"
fi

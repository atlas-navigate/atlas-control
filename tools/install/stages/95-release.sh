# 95 — The Atlas software: the signed release, through its update engine.
section "Atlas Software (signed release)"

ENGINE_BIN="$R/usr/local/lib/atlas/bin/atlas-engine"
BUNDLE_SEQ=$(json_field "$BUNDLE/atlas-release.json" seq)
if [[ -e "$R/opt/atlas/current" ]]; then
    INSTALLED_SEQ=$("$SYS_PY" -I -c 'import json,sys
try: print(int(json.load(open(sys.argv[1])).get("seq", 0)))
except Exception: print(0)' "$R/var/lib/atlas-update/installed.json")
    if (( INSTALLED_SEQ >= BUNDLE_SEQ )); then
        log "This release (or a newer one) is already installed"
        # Put the installed release's system files, settings and power mode
        # back where something (a package upgrade, a hand edit) moved them.
        ENGINE_VER=$("$ENGINE_BIN" version 2>/dev/null || echo 0)
        if (( ENGINE_VER >= 4 )); then
            "$ENGINE_BIN" reapply \
                || die "Could not re-apply the installed release's system files (see above) — nothing was left half-changed."
            log "System files, settings and power mode match the installed release"
        else
            warn "The installed update engine ($ENGINE_VER) cannot re-apply its files; the next update does."
        fi
    else
        # Already on the signed layout: a normal verified update, with the
        # engine's health check and automatic rollback.
        info "Handing the release to Atlas's update engine ..."
        "$SYS_PY" -I "$SRC_DIR/tools/release/handoff.py" "$BUNDLE" --state "$APP_DIR" \
            || die "The update engine did not install this release — Atlas still runs the version it had
    (details above; journalctl -u atlas-update-engine). Fix the cause and re-run install.sh."
        log "Release installed by the update engine"
    fi
else
    # First install on this unit: service account, engine, this unit's own
    # identity, the release, system files — verified and health-checked, and
    # undone automatically if the new Atlas does not come up healthy.
    "$SYS_PY" -I "$SRC_DIR/tools/migrate/migrate.py" apply --bundle "$BUNDLE" --provision \
        ${DEV_KEY:+--dev-key "$DEV_KEY"} \
        || die "The first install did not complete and was undone (details above). Fix the cause and re-run install.sh."
fi
systemctl start atlas-firewall.service || warn "Firewall did not start — check: journalctl -u atlas-firewall"
own_datasets

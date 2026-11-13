# 96 — This unit's identity, and its record for support. No label ships with
# Atlas: the Wi-Fi is named after its address and the code in that name is the
# password until the owner sets their own.
section "Identity"

IDENTITY_BIN="$R/usr/local/lib/atlas/bin/atlas-identity"
IDENTITY_JSON="$("$IDENTITY_BIN" show)" || die "This unit has no identity — run: sudo $IDENTITY_BIN ensure"
UNIT_NAME=$("$SYS_PY" -c 'import json,sys; print(json.load(sys.stdin)["name"])' <<<"$IDENTITY_JSON")
UNIT_HOST=$("$SYS_PY" -c 'import json,sys; print(json.load(sys.stdin)["hostname"])' <<<"$IDENTITY_JSON")
log "This unit: $UNIT_NAME ($UNIT_HOST.local)"
if [[ -n "$LABELS_DIR" ]]; then
    "$IDENTITY_BIN" label-json \
        | "$R/opt/atlas/current/venv/bin/python3" "$SRC_DIR/tools/manufacturing/make_label.py" --out "$LABELS_DIR" >/dev/null
    log "Unit record written to $LABELS_DIR/ (for support; nothing is printed)"
else
    info "Support can show this unit's facts later with: sudo /usr/local/lib/atlas/bin/atlas-identity label"
fi

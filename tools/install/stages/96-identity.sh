# 96 — This unit's identity and its printable label.
section "Identity & Label"

IDENTITY_BIN="$R/usr/local/lib/atlas/bin/atlas-identity"
IDENTITY_JSON="$("$IDENTITY_BIN" show)" || die "This unit has no identity — run: sudo $IDENTITY_BIN ensure"
UNIT_NAME=$("$SYS_PY" -c 'import json,sys; print(json.load(sys.stdin)["name"])' <<<"$IDENTITY_JSON")
UNIT_HOST=$("$SYS_PY" -c 'import json,sys; print(json.load(sys.stdin)["hostname"])' <<<"$IDENTITY_JSON")
log "This unit: $UNIT_NAME ($UNIT_HOST.local)"
if [[ -n "$LABELS_DIR" ]]; then
    "$IDENTITY_BIN" label-json \
        | "$R/opt/atlas/current/venv/bin/python3" "$SRC_DIR/tools/manufacturing/make_label.py" --out "$LABELS_DIR" >/dev/null
    log "Label files written to $LABELS_DIR/ (print $UNIT_NAME.png and attach it to this unit)"
else
    info "No --labels DIR: print the label later with"
    echo "    sudo /usr/local/lib/atlas/bin/atlas-identity label-json | /opt/atlas/current/venv/bin/python3 $SRC_DIR/tools/manufacturing/make_label.py --out DIR"
fi

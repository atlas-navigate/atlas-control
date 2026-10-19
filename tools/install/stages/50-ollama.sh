# 50 — Ollama, the engine Ray's models run on.
section "Ollama AI Engine"

# Every model Ray offers must run on this Ollama (Qwen3.5 needs 0.17.1+), and
# Ollama needs its GPU runners in /usr/local/lib/ollama — a bare binary (as
# older payload drives staged) silently runs Ray on the CPU. A unit that needs
# one gets exactly the version Ray was measured on (ai_models.json "ollama"),
# not whatever is newest that day. atlas-provision.sh installs the payload
# drive's copy before this runs, so a production unit downloads nothing.
OLLAMA_MIN="$("$SYS_PY" -I "$SRC_DIR/model_profiles.py" --min-ollama)"
OLLAMA_TESTED="$("$SYS_PY" -I "$SRC_DIR/model_profiles.py" --tested-ollama)"
ollama_version() { ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }
_install_ollama() {
    curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION="$OLLAMA_TESTED" OLLAMA_INSTALL_DIR=/usr/local/bin sh
}

OLLAMA_OK=false
if command_exists ollama; then
    if [[ ! -d "$R/usr/local/lib/ollama" ]]; then
        warn "Ollama is installed without its GPU runners (/usr/local/lib/ollama) — reinstalling"
    elif ! version_ge "$(ollama_version)" "$OLLAMA_MIN"; then
        warn "Ollama $(ollama_version) is older than $OLLAMA_MIN, which Ray's models need — upgrading"
    elif [[ "$(ollama_version)" == "$OLLAMA_TESTED" ]]; then
        log "Ollama $(ollama_version) with GPU runners (the version Ray is tested with)"
        OLLAMA_OK=true
    else
        # Runs every model; replacing a working Ollama is the owner's update's job.
        warn "Ollama $(ollama_version) with GPU runners — Ray is tested with $OLLAMA_TESTED (kept: it runs every model)"
        OLLAMA_OK=true
    fi
fi
if ! $OLLAMA_OK; then
    info "Downloading and installing Ollama $OLLAMA_TESTED (needs Internet) ..."
    if retry 3 15 _install_ollama; then
        hash -r
        log "Ollama $(ollama_version) installed"
    elif command_exists ollama; then
        warn "Could not update Ollama (offline?). Ray keeps working with the models this Ollama can run;"
        warn "re-run install.sh when online to enable every model (needs Ollama $OLLAMA_MIN+)."
    else
        die "Ollama is required and could not be installed. Connect to the Internet, or stage it on the payload drive."
    fi
fi

# Its own unprivileged account (as Ollama's installer makes it), never the
# setup account: Ollama listens on the network stack and runs model code.
if ! id -u ollama >/dev/null 2>&1; then
    useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
    log "Created the 'ollama' service account"
fi
for g in video render; do
    if getent group "$g" >/dev/null && ! id -nG ollama | tr ' ' '\n' | grep -qx "$g"; then
        usermod -aG "$g" ollama          # the GPU device nodes
    fi
done
chown -R "ollama:$(id -gn ollama)" "$OLLAMA_DATA_DIR"

# The base unit (account, binary, model directory), then the release's own
# settings and sandbox (the same drop-in every update installs) — in place
# before the model pulls, so even the first load runs the way Atlas runs it.
UNIT="$R/etc/systemd/system/ollama.service"
NEW_UNIT="$(sed "s|OLLAMA_USER|ollama|g; s|OLLAMA_GROUP|$(id -gn ollama)|g; s|OLLAMA_DATA_DIR|${OLLAMA_DATA_DIR#$R}|g" "$SRC_DIR/ollama.service")"
OLLAMA_CHANGED=""
if [[ ! -f "$UNIT" ]] || [[ "$(cat "$UNIT")" != "$NEW_UNIT" ]]; then
    printf '%s\n' "$NEW_UNIT" > "$UNIT"
    OLLAMA_CHANGED=1
fi
[[ -z "$(kit_sysfiles ollama/atlas.conf)" ]] || OLLAMA_CHANGED=1
systemctl daemon-reload
systemctl enable ollama >/dev/null 2>&1
if [[ -n "$OLLAMA_CHANGED" ]] || ! systemctl is-active --quiet ollama; then
    systemctl restart ollama || warn "Ollama did not start — check: journalctl -u ollama"
fi
log "Ollama service enabled"

# 90 — Ray's models: every one Ray offers, in the build that suits this unit.
section "Ollama Models"

# Already running models stay loaded (a re-run on a working unit must not
# make Ray reload); only a stopped Ollama is started.
systemctl is-active --quiet ollama || systemctl restart ollama || true
RAY_MODELS=()
MODELS_MISSING=()
if systemctl is-active --quiet ollama; then
    info "Waiting for Ollama API ..."
    for _ in {1..30}; do
        curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1 && break
        sleep 2
    done

    if curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
        log "Ollama API is responding"
        # Every model Ray offers, in the build that suits this unit's memory
        # (e.g. Qwen3.5 4B as Q4_K_M on an 8 GB Orin Nano), plus the embedder.
        # A unit sold off-grid can never download one later, so all of them
        # ship. The owner's choice of model (Settings → Ray) is never changed.
        mapfile -t RAY_MODELS < <("$SYS_PY" -I "$SRC_DIR/model_profiles.py" --preinstall)
        for model in "${RAY_MODELS[@]}"; do
            if ollama show "$model" >/dev/null 2>&1; then
                log "Ollama model present: $model"
            else
                info "Pulling Ollama model: $model"
                # Never fatal here: an off-grid box can't pull; stage 98 reports
                # what is missing and the install ends as failed.
                if retry 3 20 ollama pull "$model"; then
                    log "Ollama model ready: $model"
                else
                    warn "Could not pull $model (offline?)"
                    MODELS_MISSING+=("$model")
                fi
            fi
        done
        if (( ${#MODELS_MISSING[@]} )); then
            warn "Missing Ray models: ${MODELS_MISSING[*]} — put them on the payload drive, or re-run install.sh online."
        fi
    else
        warn "Ollama service started but API did not become ready; skipping model pull"
    fi
else
    warn "Ollama service did not start; skipping model pull"
fi

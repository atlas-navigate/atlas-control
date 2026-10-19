# 97 — Retail lock-down (tools/manufacturing/finalize-retail.sh).
section "Retail Lock-down"

if [[ -n "$DEV_KEY" ]]; then
    warn "Developer unit — not locked down (SSH stays on, developer key trusted)"
else
    bash "$SRC_DIR/tools/manufacturing/finalize-retail.sh" || die "Retail lock-down failed (see above) — this unit must not ship."
fi

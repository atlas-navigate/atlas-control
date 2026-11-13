# 99 — Summary.
echo ""
if [[ "${VERIFY_OK:-true}" == "true" ]]; then
    echo -e "${BLD}${GRN}══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLD}${GRN}  ${UNIT_NAME:-Atlas} is ready.${NC}"
    echo -e "${GRN}══════════════════════════════════════════════════════════${NC}"
else
    echo -e "${BLD}${RED}══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLD}${RED}  ${UNIT_NAME:-Atlas} is installed but NOT ready — see the [✗] lines above.${NC}"
    echo -e "${RED}══════════════════════════════════════════════════════════${NC}"
fi
echo ""
USED=$(df -h "$ATLAS_DATA" | tail -1 | awk '{print $3"/"$2" used"}')
echo -e "  ${BLD}Data storage :${NC} ${APP_DIR#$R}  ($USED)"
echo -e "  ${BLD}Install log  :${NC} ${INSTALL_LOG#$R}"
echo ""
if [[ -n "${UNIT_HOST:-}" ]]; then
    echo -e "  ${CYN}First use (owner):${NC} join the Wi-Fi ${BLD}$UNIT_HOST.local${NC} (password: the code in"
    echo -e "  that name), open the Atlas app or ${BLD}https://$UNIT_HOST.local${NC}, press the Atlas button and"
    echo -e "  pick the colour its light shows. That device becomes the primary user; it allows"
    echo -e "  every later device, and Atlas asks it to set its own Wi-Fi password."
    echo -e "  Holding the button for 15 s (light turns red) resets Atlas to factory settings."
    echo ""
fi
if [[ -z "$ROUTING_STATES" ]]; then
    ROUTING_STATES=$(find "$APP_DIR/osrm-data/states" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l || true)
fi
if [[ "$ROUTING_STATES" -eq 0 ]]; then
    echo -e "  ${YLW}Routing data not installed.${NC} Add states: sudo ./install.sh --bundle DIR --states \"MD VA\""
    echo ""
fi
# ${OSRM_MMAP_OK:-true}: stages skipped with --only/--skip never set it.
if [[ "${OSRM_MMAP_OK:-true}" == "false" ]]; then
    echo -e "  ${YLW}Routing is in high-memory mode.${NC} This osrm-routed predates --mmap,"
    echo -e "  so each state graph costs ~1.1 GB of RAM instead of ~42 MB. Long routes"
    echo -e "  can degrade to a straight line while Ray is loaded. Re-run install.sh"
    echo -e "  after removing the prebuilt OSRM binaries to rebuild them (20–40 min)."
    echo ""
fi
if [[ ! -f "$APP_DIR/static/data/places.db" ]]; then
    echo -e "  ${YLW}Offline address/POI search not built yet.${NC} To build it (one-time, heavy):"
    echo -e "    sudo bash ${APP_DIR#$R}/build_places_safe.sh"
    echo ""
fi
echo -e "  ${CYN}Updates:${NC} Atlas installs signed releases itself — Settings → Software Update."
echo -e "  ${CYN}Check :${NC} sudo /usr/local/lib/atlas/bin/atlas-engine check   (re-run install.sh to repair)"
echo ""
if [[ ${#RAY_MODELS[@]} -gt 0 ]]; then
    echo -e "  ${CYN}Ray's models:${NC} $("$SYS_PY" -I "$SRC_DIR/model_profiles.py" --summary | awk '{print $1" "$2}' | paste -sd, - | sed 's/,/, /g')"
    echo -e "  (each tuned for this unit automatically; the owner picks one in Settings → Ray)."
    echo ""
fi
[[ "${VERIFY_OK:-true}" == "true" ]] || exit 1

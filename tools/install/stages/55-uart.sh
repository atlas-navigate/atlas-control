# 55 — Jetson Orin UART1 DMA fix (40-pin header /dev/ttyTHS1).
section "Hardware: UART1 DMA fix"

# JetPack 6.2.2+ has a serial-tegra RX-DMA kernel bug that returns received
# UART bytes as 0x00 on /dev/ttyTHS1, breaking any radio wired to header pins
# 8/10 (for Atlas: the Heltec V4 mesh link). The fix is a device-tree overlay
# that deletes the UEFI-injected dmas/dma-names properties so the driver falls
# back to PIO mode. See jetson-orin-uart/README.md for background/attribution.
UART_FIX_DTS="$APP_DIR/jetson-orin-uart/disable-uart1-dma.dts"
UART_FIX_DTBO="/boot/disable-uart1-dma.dtbo"          # as named in extlinux.conf
EXTLINUX_CONF="$R/boot/extlinux/extlinux.conf"
if [[ -f "$R/etc/nv_tegra_release" && -d "$R/proc/device-tree/bus@0/serial@3100000" \
      && -f "$UART_FIX_DTS" && -f "$EXTLINUX_CONF" ]]; then
    if grep -qF "$UART_FIX_DTBO" "$EXTLINUX_CONF"; then
        log "Jetson UART1 DMA fix already active in extlinux.conf"
    else
        command -v dtc > /dev/null 2>&1 || apt-get install -y device-tree-compiler > /dev/null 2>&1 || true
        if command -v dtc > /dev/null 2>&1 \
           && dtc -@ -I dts -O dtb -o "$R$UART_FIX_DTBO" "$UART_FIX_DTS" 2> /dev/null; then
            cp "$EXTLINUX_CONF" "${EXTLINUX_CONF}.bak.$(date +%Y%m%d%H%M%S)"
            UART_FIX_DEFAULT="$(awk '/^[[:space:]]*DEFAULT[[:space:]]/{print $2; exit}' "$EXTLINUX_CONF")"
            UART_FIX_STANZA=""
            if [[ -n "$UART_FIX_DEFAULT" ]]; then
            UART_FIX_STANZA="$(awk -v lbl="$UART_FIX_DEFAULT" '
                $1=="LABEL" { found = ($2==lbl) }
                found && NF==0 { exit }
                found { print }' "$EXTLINUX_CONF")"
            fi
            if [[ -n "$UART_FIX_STANZA" ]]; then
                # Clone the default stanza as a new UARTFix entry; the previous
                # default stays in the boot menu as a fallback.
                UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                    | sed 's|^[[:space:]]*LABEL[[:space:]].*|LABEL UARTFix|' \
                    | sed 's|MENU LABEL.*|MENU LABEL Atlas UART DMA fix|')"
                if ! echo "$UART_FIX_STANZA" | grep -qE '^[[:space:]]*FDT[[:space:]]'; then
                    # Overlays need an explicit FDT; only safe to guess when
                    # exactly one kernel dtb exists (true for stock devkits).
                    UART_FIX_FDT="$(find "$R/boot/dtb" -maxdepth 1 -name 'kernel_*.dtb' 2> /dev/null | head -1 || true)"
                    UART_FIX_NDTB="$(find "$R/boot/dtb" -maxdepth 1 -name 'kernel_*.dtb' 2> /dev/null | wc -l || true)"
                    UART_FIX_FDT="${UART_FIX_FDT#$R}"
                    if [[ "$UART_FIX_NDTB" -eq 1 && -n "$UART_FIX_FDT" ]]; then
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "\|^[[:space:]]*LINUX[[:space:]]|a\\	FDT $UART_FIX_FDT")"
                    else
                        warn "Ambiguous FDT ($UART_FIX_NDTB kernel dtbs) — skipping UART1 DMA fix; see jetson-orin-uart/README.md"
                        UART_FIX_STANZA=""
                    fi
                fi
                if [[ -n "$UART_FIX_STANZA" ]]; then
                    if echo "$UART_FIX_STANZA" | grep -qE '^[[:space:]]*OVERLAYS[[:space:]]'; then
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "s|^\([[:space:]]*OVERLAYS[[:space:]].*\)$|\1,$UART_FIX_DTBO|")"
                    else
                        UART_FIX_STANZA="$(echo "$UART_FIX_STANZA" \
                            | sed "\|^[[:space:]]*FDT[[:space:]]|a\\	OVERLAYS $UART_FIX_DTBO")"
                    fi
                    printf '\n%s\n' "$UART_FIX_STANZA" >> "$EXTLINUX_CONF"
                    sed -i "s|^\([[:space:]]*DEFAULT[[:space:]]\{1,\}\).*|\1UARTFix|" "$EXTLINUX_CONF"
                    warn "Jetson UART1 DMA fix installed — REBOOT required before the 40-pin UART mesh radio works"
                fi
            else
                warn "Could not parse extlinux.conf default stanza — skipping UART1 DMA fix"
            fi
        else
            warn "dtc unavailable or overlay compile failed — skipping UART1 DMA fix"
        fi
    fi
else
    log "UART1 DMA fix not needed here (no Jetson 40-pin UART1, or no boot menu to change)"
fi

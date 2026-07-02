# Jetson Orin UART1 DMA fix (40-pin header, /dev/ttyTHS1)

JetPack 6.2.2+ (L4T r36.4.4 / r36.5) has a kernel bug in the `serial-tegra`
driver's RX DMA path: UEFI injects `dmas`/`dma-names` properties into the
`serial@3100000` device-tree node at boot, and the driver then returns the
DMA'd portion of every received burst as `0x00` bytes. Only the tail bytes
that arrive via the FIFO-timeout/PIO path survive. Any device wired to header
pins 8/10 (for Atlas: the Heltec V4 mesh radio) is unusable until fixed.

The fix is this device-tree overlay, which deletes the injected DMA properties
so the driver falls back to interrupt/PIO mode, where the bug does not occur.
After boot, `dmesg | grep 3100000` should show `RX in PIO mode`.

`install.sh` (repo root) detects affected Jetsons, compiles
`disable-uart1-dma.dts` with `dtc`, installs it to `/boot`, and adds a
`UARTFix` boot entry to `/boot/extlinux/extlinux.conf` (the previous default
entry is kept as a fallback in the boot menu). A reboot is required after
first install.

The overlay source is derived from the MIT-licensed
[jetsonhacks/jetson-orin-uart](https://github.com/jetsonhacks/jetson-orin-uart)
(Copyright (c) 2026 JetsonHacks) — see LICENSE in that repository. Background:
[NVIDIA forums: "DMA on /dev/ttyTHS1 corrupts receiving data"](https://forums.developer.nvidia.com/t/dma-on-dev-ttyths1-corrupts-receiving-data/369191).

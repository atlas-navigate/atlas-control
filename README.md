# Atlas Control

Offline GPS navigation and mesh communications for the Jetson Orin Nano cyberdeck.

Runs 100% offline — no internet required after installation.

## Features

- Offline vector maps (Protomaps, 18 GB US basemap)
- Offline topographic overlay (USGS, z8–z13)
- Turn-by-turn routing for all 50 states (OSRM, car + hiking profiles)
- Meshtastic mesh radio integration
- GPS with Kalman filtering and dead reckoning
- AI chat assistant (Ollama, runs locally on Jetson GPU)
- Phone tracker check-in
- Offline city and NPS trail search
- Native mobile clients with Bluetooth bootstrap into the local Atlas hotspot and API

## Hardware

- Jetson Orin Nano (ARM64, Ubuntu 22.04)
- NVMe drive (500 GB+ recommended — maps + routing data is large)
- u-blox GPS receiver
- Heltec V4 Meshtastic radio
- Optional: Waveshare UPS module with INA219 telemetry

### Heltec V4 mesh radio: USB-C or 40-pin UART

The radio works over USB-C out of the box (udev symlinks it to
`/dev/meshtastic`). To free the USB port it can instead be wired to the
Jetson 40-pin header (see `heltec-v4-pinmap.webp`):

| Jetson (40-pin)      | Heltec V4 (header J2)   |
| -------------------- | ----------------------- |
| pin 8 (UART1 TX)     | J2 pin 5 (`U0RXD`)      |
| pin 10 (UART1 RX)    | J2 pin 6 (`U0TXD`)      |
| GND (pin 6/9)        | J2 pin 1 (GND)          |
| 5V (pin 2/4) or USB  | J2 pin 2 (5V in)        |

Two one-time setup steps for the UART path:

1. **Radio:** the ESP32-S3 serves the Meshtastic client API over native USB
   only, so the Serial module must be told to serve it on the header pins
   (run once, over USB):

   ```bash
   meshtastic --port /dev/ttyACM0 \
     --set serial.enabled true --set serial.mode PROTO \
     --set serial.rxd 44 --set serial.txd 43 --set serial.baud BAUD_115200
   ```

   The device commits config slowly — verify with `--get serial` after it
   reboots (expect `enabled: True`, `mode: 2`).

2. **Jetson:** JetPack 6.2.2+ has a kernel bug that corrupts UART RX on
   `/dev/ttyTHS1` (DMA'd bytes arrive as `0x00`). `install.sh` detects
   affected devices and installs the fix automatically (a reboot is
   required); see `jetson-orin-uart/README.md`.

`mesh_manager.py` finds the radio on either transport automatically (AUTO
order: USB symlink → probed UART → USB scan). If both are connected at once
(e.g. USB used purely for power), pin the UART explicitly:
`PUT /api/settings {"serial_port": "/dev/ttyTHS1"}`, then restart the
service.

## Installation

Requires internet access once to download map data. After that, fully offline.

```bash
git clone https://github.com/atlas-navigate/atlas-control.git
cd atlas-control
sudo ./install.sh
```

The installer will:
1. Detect and mount an NVMe drive at `/atlas_data`
2. Install system dependencies and Python environment
3. Install Ollama (local AI engine)
4. Build OSRM routing engine from source
5. Download the PMTiles CLI tool
6. Download the vector basemap (~18 GB)
7. Download map font glyphs (~8 MB)
8. Download topographic tiles and build topo.pmtiles
9. Download US cities and NPS trails databases
10. Download and process OSRM routing data for chosen states
11. Configure nginx with HTTPS and rate limiting
12. Install systemd services

On first visit, your browser will warn about the self-signed certificate — click **Advanced → Proceed**. Required once per device.

## Updating

The same script updates an existing installation:

```bash
sudo /atlas_data/atlas-control/install.sh --update
```

This pulls the latest release, refreshes Python dependencies, configs, and services, then restarts Atlas — your database, settings, maps, and routing data are preserved. It runs without prompts, so it's safe to automate. Re-running `install.sh` with no flags on an installed system offers the same quick update; use `--full` to force the complete install flow (e.g. to add more state routing data).

You can also update from the web app: **Settings → Software Update → Check for Updates** scans GitHub for new releases, shows the changelog, and installs with one click while streaming progress.

## Access

| Network | URL |
|---------|-----|
| Hotspot / LAN | `https://atlas.local` or `https://<device-ip>` |
| Direct IP | `https://<device-ip>` |

If `atlas.local` does not resolve on your client device, use the direct device IP instead.

## Mobile

Native Android and iOS clients live in [`mobile/`](mobile/README.md).

They use Bluetooth Low Energy for first contact, read a compact Atlas bootstrap manifest from the Jetson, then switch to the Atlas hotspot and local HTTP API for Meshtastic, GPS, navigation, and AI features.

## Service management

```bash
sudo systemctl status  atlas-control
sudo systemctl restart atlas-control
sudo journalctl -u atlas-control -f
```

## UPS battery telemetry

Atlas will show a host battery badge in the top bar when it can read either:

- Linux `power_supply` battery/UPS telemetry, or
- a Waveshare-style INA219 UPS on I2C.

For the Waveshare UPS Power Module C used on this Atlas build, the installed service
defaults to I2C bus `7` and address `0x41`. Those defaults are set in
[`atlas-control.service`](/atlas_data/atlas-control/atlas-control.service:18).

Atlas also auto-detects the older Waveshare generic UPS profile on bus `1` / address `0x42`,
but if your hardware differs from either profile, override `ATLAS_UPS_I2C_BUS` and
`ATLAS_UPS_I2C_ADDR` in the service file, then reload systemd and restart Atlas.

## License

See [LICENSE](LICENSE).

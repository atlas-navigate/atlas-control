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

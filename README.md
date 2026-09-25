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
- Native Android and iOS apps: pair once from the label QR code, pinned TLS, automatic rediscovery
- No accounts or passwords; signed, self-healing software updates

## Hardware

- Jetson Orin Nano (ARM64, Ubuntu 22.04 / JetPack 6 or Ubuntu 24.04 / JetPack 7)
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
order: USB symlink if present, otherwise probed UART, then USB scan). If both
are connected at once (e.g. USB used purely for power), pin the UART
explicitly: `PUT /api/settings {"serial_port": "/dev/ttyTHS1"}` — the change
applies live, no restart needed (`"AUTO"` switches back).

## Installation

Production units are built from a payload drive plus one script, and each
leaves the line with its own identity and label — see
[docs/MANUFACTURING.md](docs/MANUFACTURING.md). The short version, on a
freshly flashed Jetson with the payload drive attached:

```bash
sudo /media/$USER/atlas-backup/stage/atlas-imaging/atlas-provision.sh
```

That copies the maps and datasets, then runs `install.sh --bundle <signed release>`:
system packages, the Atlas software through its update engine (no git, no
sudo rules), the unit's own identity and label, and retail lock-down.
Building the datasets from scratch (a golden unit) runs the same
`install.sh` with no payload and needs Internet for a day or two.

A unit installed the old way moves to the new layout with
`sudo tools/migrate/atlas-migrate apply --bundle <release>` (and back with
`… rollback`).

## Updating

Atlas installs signed releases itself: **Settings → Software Update** checks
while Atlas is online (every six hours, or on demand), shows what's new,
downloads with resume, and installs with a live progress bar. Every release
is verified with Ed25519 before it is installed, health-checked afterwards,
and rolled back automatically if the new version does not come up healthy —
including after a power cut. See [docs/UPDATES.md](docs/UPDATES.md) and, for
publishing releases, [docs/RELEASES.md](docs/RELEASES.md).

## Access

There are no accounts or passwords. The first time, pair the phone or
browser with Atlas using the label (QR code or setup code), Atlas's own
Wi-Fi, or "Allow" on a device that is already paired — see
[docs/PAIRING.md](docs/PAIRING.md).

| Network | Address |
|---------|---------|
| Atlas's Wi-Fi or your network | `https://atlas-xxxx.local` (on the label) |
| Atlas's Wi-Fi | `https://10.42.0.1` |

Browsers warn once about Atlas's own (self-signed) certificate.

## Mobile

Native Android and iOS apps live in [`mobile/`](mobile/README.md). They pair
by scanning the label, pin the unit's TLS key, find Atlas by its device id
(mDNS, the LAN beacon or its hotspot) after any address change, and keep
working through updates.

## Security and privacy

How Atlas keeps strangers, web pages, other radios and bad updates out, and
what it keeps about the owner: [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md).

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
[`system/systemd/atlas-control.service`](system/systemd/atlas-control.service).

Atlas also auto-detects the older Waveshare generic UPS profile on bus `1` / address `0x42`,
but if your hardware differs from either profile, set `ATLAS_UPS_I2C_BUS` and
`ATLAS_UPS_I2C_ADDR` in a drop-in (`sudo systemctl edit atlas-control`) and restart Atlas.

## License

See [LICENSE](LICENSE).

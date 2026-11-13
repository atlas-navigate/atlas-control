# Atlas Control

Offline GPS navigation and mesh communications for the Jetson Orin Nano cyberdeck.

Runs 100% offline — no internet required after installation.

**Owners:** the [Atlas manual](MANUAL.md) shows how to connect to Atlas and use
every page, with screenshots.

## Features

- Offline vector maps (Protomaps, 18 GB US basemap)
- Offline topographic overlay (USGS, z8–z13)
- Turn-by-turn routing for all 50 states (OSRM, car + hiking profiles)
- Meshtastic mesh radio integration
- GPS with Kalman filtering and dead reckoning
- Ray, the AI assistant (Ollama, on the Jetson GPU): Qwen3.5 4B (standard) or Qwen2.5 3B, each tuned for the unit automatically
- Phone tracker check-in
- Offline city and NPS trail search
- Native Android and iOS apps: pair once with the Atlas button, pinned TLS, automatic rediscovery
- No accounts or passwords; signed, self-healing software updates

## Hardware

- Jetson Orin Nano (ARM64, Ubuntu 24.04 / JetPack 7; installs refuse anything else)
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

## Setting up Atlas units

There are three ways to put Atlas on a Jetson. Each one installs a **signed
release** (see [docs/RELEASES.md](docs/RELEASES.md)) and gives the unit its
own identity: name, address, Wi-Fi, setup code and TLS key.

| Way | Use it for | Needs |
|---|---|---|
| [Production drive](#production-units-the-golden-drive) | Units you sell or hand to someone | A golden unit, a 500 GB+ drive, a release signed with the production key |
| [From GitHub](#installing-from-github) | A single unit, no drive | Internet, about 250 GB and a day or two to download the maps and models |
| [Developer drive](#a-developer-golden-drive) or `--dev-key` | Units you develop and test on | The developer key |

Every unit needs JetPack 7 (L4T R39.2, Ubuntu 24.04) flashed to its NVMe with
the `admin` account, and the Atlas button on header pins 27/28
([wiring](docs/MANUFACTURING.md#wiring-the-40-pin-header)). The full
references are [docs/MANUFACTURING.md](docs/MANUFACTURING.md) and
[docs/INSTALL.md](docs/INSTALL.md).

### Production units: the golden drive

A **golden unit** is a developer unit with every dataset built (maps,
routing for all states, places) and every Ray model pulled. Capturing it
copies those datasets, about 155 GB, onto a drive together with the signed
release. Each new unit is then a copy from that drive plus one script.

**1. Prepare the drive (once per drive).** This erases it, so check the
device name with `lsblk` first. The name `atlas-ctrl-prod` is what the
scripts look for. A drive name holds at most 16 characters.

```bash
sudo wipefs -a /dev/sdX
sudo parted -s /dev/sdX mklabel gpt mkpart atlas-ctrl-prod ext4 0% 100%
sudo mkfs.ext4 -L atlas-ctrl-prod /dev/sdX1
```

**2. Capture (once per release), on the golden unit.** First update the
golden unit to the release you are capturing (Settings → Software Update),
then:

```bash
cd /atlas_data/atlas-control
python3 -I tools/release/fetch_release.py ~/atlas-release/0.1.3 --tag v0.1.3
sudo tools/manufacturing/atlas-capture.sh --release ~/atlas-release/0.1.3
```

- **Signing:** the release must carry the production signature. `fetch_release.py`
  refuses one that doesn't, and so does the capture's retail check.
- **What it writes:** maps, routing, places, Ray's models, the Ollama GPU
  runners, the signed release and its own provisioning script.
- **What it never copies:** the golden unit's messages, chats, paired devices,
  Wi-Fi networks, passwords, identity or logs.
- **How it should end:** `retail units trust release 0.1.3`, then
  `Payload ready`. Re-running it only copies what changed.

**3. Provision each unit.** On a freshly flashed Jetson, plug in the drive
and run:

```bash
sudo /media/$USER/atlas-ctrl-prod/stage/atlas-imaging/atlas-provision.sh
```

It takes 45–60 minutes:

1. It copies the datasets and runs `install.sh` from the drive's release
   (packages, the Atlas software, Ollama).
2. It gives the unit its own identity.
3. It locks the unit down for retail: SSH off, no desktop, setup account
   locked.
4. It checks everything with `atlas-engine check`, the button included.

It ends with **`Atlas-XXXXXXXXX is ready.`**; anything else lists what
failed. A run that stops can be run again, and finished steps are skipped.

**4. Check it, then ship it unclaimed.**

1. Reboot. The button glows dim white.
2. From a phone, join the Wi-Fi `atlas-xxxxxxxxx.local`; the password is
   the code in that name.
3. Pair: press the button and pick its colour, then skip the Wi-Fi password
   prompt. Check that the map, a route and Ray work.
4. Hold the button for 15 seconds so the unit forgets you. Its light goes
   red, then green, then dim white.

No label ships with Atlas. Each unit's record (its setup code, for support)
is written to `/root/atlas-labels/` and, when the drive is writable (opened
by the desktop), to the drive's `labels/` folder. Keep those records with
the factory's own: support needs a unit's setup code if its button fails.

### Installing from GitHub

Without a drive, a unit installs the newest published release straight from
GitHub Releases and downloads its datasets from the Internet:

```bash
git clone https://github.com/atlas-navigate/atlas-control.git
cd atlas-control
sudo ./install.sh --from-github            # or a given release: --from-github v0.1.3
```

- **Before anything downloads:** the signature is checked against the
  production keys in the checkout.
- **Datasets:** maps, routing graphs and Ray's models then come from the
  Internet. Plan on many hours and about 250 GB.
- **Routing:** on a terminal it asks which states to build; `--states "MD VA"`
  or `--states all` answers in advance, and `--yes` never asks.
- **Updates:** the unit keeps updating from GitHub Releases afterwards.

> **This makes a production unit.** At the end it turns SSH off and stops
> the desktop from the next boot. If you are working over SSH, or want to
> keep developing on the unit, add `--dev-key` (see below).

### Developer and production units

The same software runs on both. A unit is a developer unit when it was
installed with `--dev-key` (it has `/etc/atlas/dev-mode`); every other unit
is a production (retail) unit. Never ship a developer unit.

| | Production unit | Developer unit |
|---|---|---|
| Made with | `atlas-provision.sh` or `install.sh` | the same, plus `--dev-key dev-release.pub` |
| Releases it installs | signed with the production key | the production key **or** the developer key, so `tools/dev/deploy` installs your working copy |
| SSH | off (masked); port 22 refused by the firewall | on; port 22 open |
| Desktop | boots headless | keeps the desktop |
| Setup account (`admin`) | password locked, no access to Atlas's root helper | unchanged; members of the `atlas` group may use the helper |
| Docker, containerd, snapd, ModemManager | disabled and masked | left as they are (Docker's network is trusted) |
| Update downloads | `https://` only | plain `http://` allowed, for a local test server |
| Button missing at install | a failure: "must not ship" | a warning |
| `atlas-engine check` | also checks the lock-down | skips the lock-down |
| Test tools | — | `tools/dev/acceptance.py`; update fault injection |

What is **the same** on both:
- **Pairing:** by the button, or with the setup code.
- **Wi-Fi:** the starting password is the code in the Wi-Fi name, and Atlas
  asks the owner to change it.
- **New-unit settings:** Qwen3.5 4B, imperial units, Ray chats kept 30 days.
- **Updates:** signed, with automatic rollback.
- **Factory reset:** Settings → Factory reset, or holding the button for 15
  seconds, really erases everything personal. That means messages, Ray chats,
  locations, settings, saved Wi-Fi networks, Bluetooth pairings and connected
  devices. Since 0.1.1 a developer unit no longer only pretends.

Holding the button on your own developer unit wipes it too, so don't hold it
for 15 seconds unless you mean it.

### A developer golden drive

A developer drive makes developer units, for a test bench or a team. It
differs from the production drive in three ways:
- **Its name:** use `atlas-ctrl-dev`, so it is never mistaken for the
  production drive.
- **Its release:** it may carry a release signed only with the developer key.
- **Each unit:** is provisioned with `--dev-key`.

```bash
# Once per drive (erases it):
sudo wipefs -a /dev/sdX
sudo parted -s /dev/sdX mklabel gpt mkpart atlas-ctrl-dev ext4 0% 100%
sudo mkfs.ext4 -L atlas-ctrl-dev /dev/sdX1

# On the golden unit: capture a release (a published one, or your own build
# signed with the developer key), marked as a developer drive:
sudo tools/manufacturing/atlas-capture.sh --release ~/atlas-release/0.1.3 --drive atlas-ctrl-dev --dev

# Put the developer PUBLIC key on the drive (never the .key file). On a
# developer unit it is in /etc/atlas/dev-keys/:
sudo cp /etc/atlas/dev-keys/dev.pub /media/$USER/atlas-ctrl-dev/dev-release.pub
```

On each developer unit:

```bash
D=/media/$USER/atlas-ctrl-dev
sudo $D/stage/atlas-imaging/atlas-provision.sh --drive atlas-ctrl-dev --dev-key $D/dev-release.pub
```

The unit gets everything a production unit gets except the lock-down: it
keeps SSH and the desktop, and trusts the developer key. To build and sign
your own developer release, see [docs/RELEASES.md](docs/RELEASES.md). On a
developer unit, `sg atlas -c tools/dev/deploy` builds the working copy,
signs it with the developer key and installs it through the update engine.

A unit installed the old way (git checkout, sudo rules) moves to this layout
with `sudo tools/migrate/atlas-migrate apply --bundle <release>`, and back
with `… rollback`.

## Updating

Atlas installs signed releases itself: **Settings → Software Update** checks
while Atlas is online (every six hours, or on demand), shows what's new,
downloads with resume, and installs with a live progress bar. Every release
is verified with Ed25519 before it is installed, health-checked afterwards,
and rolled back automatically if the new version does not come up healthy —
including after a power cut. See [docs/UPDATES.md](docs/UPDATES.md) and, for
publishing releases, [docs/RELEASES.md](docs/RELEASES.md).

## Access

There are no accounts or passwords. Join Atlas's Wi-Fi, which is named
after its address (`atlas-xxxxxxxxx.local`; the password is the code in that
name), open that address, press the Atlas button and pick the colour its
light shows. The first device becomes the primary user, which then allows
every later device ("Allow") — see [docs/PAIRING.md](docs/PAIRING.md).

| Network | Address |
|---------|---------|
| Atlas's Wi-Fi or your network | `https://atlas-xxxxxxxxx.local` (the Wi-Fi name; also in Settings → Local Device Address) |
| Atlas's Wi-Fi | `https://10.42.0.1` |

Browsers warn once about Atlas's own (self-signed) certificate.

Every unit has its own setup code and key; its Wi-Fi starts with the code in
its name as the password, and Atlas asks the owner to set their own right
after pairing. Pairing survives
software updates, reboots and address changes; only removing a device, a
factory reset or a new identity ends it.

## Ray's models

Ray → Settings → Model switches between Qwen3.5 4B (standard) and
Qwen2.5 3B (quickest). Atlas picks each model's build, context window and sampling for
its hardware, switches without a restart, and remembers the choice through
reboots and updates. Measurements and the expert overrides:
[docs/AI_MODELS.md](docs/AI_MODELS.md).

## Mobile

Native Android and iOS apps live in [`mobile/`](mobile/README.md). They pair
with the Atlas button, pin the unit's TLS key, find Atlas by its device id
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

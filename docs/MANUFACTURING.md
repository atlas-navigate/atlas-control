# Manufacturing and provisioning

Building a unit is a copy from the payload drive plus one script. Each unit
leaves the line with its own identity, the signed release installed, and
nothing another unit could use against it. No label ships with Atlas (owner
decision, 2026-11-12): its Wi-Fi is named after its address, and the code in
that name is the password until the owner sets their own.

## Once per release: the payload drive

The production payload drive is an ext4 drive labelled `atlas-control-prod`,
500 GB or larger: the payload is about 155 GB, and capture wants 200 GB free
every time it runs. Prepare a new drive once. This erases it, so check the
device name with `lsblk` first:

```bash
sudo wipefs -a /dev/sdX
sudo parted -s /dev/sdX mklabel gpt mkpart atlas-control-prod ext4 0% 100%
sudo mkfs.ext4 -L atlas-control-prod /dev/sdX1
```

On the golden unit (a developer unit with all datasets built, updated to the
release being captured), with the drive attached, fetch that release from
GitHub Releases and capture:

```bash
cd /atlas_data/atlas-control
python3 -I tools/release/fetch_release.py ~/atlas-release/1.2.0 --tag v1.2.0
sudo tools/manufacturing/atlas-capture.sh --release ~/atlas-release/1.2.0 --drive atlas-control-prod
```

`--release` is a signed release directory (see [RELEASES.md](RELEASES.md)).
`--drive` is the drive's label; `atlas-control-prod` is the default. Capture
refuses a release this unit's update engine does not trust
(`atlas-engine verify`), then writes to the drive:

| Path | Contents |
|---|---|
| `payload/` | `/atlas_data`: maps, routing graphs, datasets, Ray's models (~150 GB) |
| `payload/osrm-native-bin/` | prebuilt OSRM binaries |
| `stage/ollama` | the Ollama binary |
| `stage/ollama-lib/` | its GPU runners (`/usr/local/lib/ollama`, ~2 GB) — without them Ray runs on the CPU |
| `stage/release/` | the signed release every new unit installs |
| `stage/kit/` | that same release unpacked (minus wheels): `install.sh`, tools, dataset builders |
| `stage/atlas-imaging/` | `atlas-provision.sh` and the other imaging scripts, from that same release |
| `MANIFEST.txt` | L4T version, release, sizes, date |

**Production or developer drive**: a new retail unit trusts only the
production keys in the release's own keyring. Capture's final checks run the
same check that unit's `install.sh` starts with, and fail on a release signed
only with a developer key; such a drive would be refused on the line, unit
after unit. A drive for developer units takes `--dev` and its own label
(`--drive atlas-backup --dev`), and units provisioned from it need
`atlas-provision.sh --dev-key PUB`.

**Never on the drive**: the golden unit's database (mesh history,
conversations, paired devices, security log), Wi-Fi hints, hotspot password,
Flask secret, database copies, downloaded updates, caches, the source
checkout or worktrees. Its identity lives in `/etc/atlas`, outside the
payload. Excluded files already on the drive from an older capture are
deleted (`--delete-excluded`), and capture's final checks fail if any of
them is present.

**Ray's models**: every model in the release's `ai_models.json`, in the
build for this hardware class, must be on the golden unit before capture:
Qwen2.5 3B, Qwen3.5 4B and the embedder. Pull any that are missing:

```bash
python3 -I model_profiles.py --preinstall | xargs -n1 ollama pull
```

Capture checks each one is in `payload/ollama/models`, and fails otherwise.
A unit sold off-grid can't download one later. Capture on a unit of the same
memory class as the units being built (8 GB Orin Nano → Q4_K_M builds).
Ollama must be recent enough for every model (`model_profiles.py --min-ollama`,
0.17.1 for Qwen3.5). Capture the version Ray was measured on
(`model_profiles.py --tested-ollama`, 0.32.1): a unit whose Ollama is missing
or too old gets exactly that version from `install.sh` when online, and
`atlas-engine check` warns about any other.

`atlas-mirror.sh` is a separate full-rootfs backup of the golden unit for
disaster recovery — it contains that unit's own secrets and is never used to
build units.

## Wiring the 40-pin header

Every unit has three I2C devices on the header. The Atlas button must be on
pins 27/28: the bus at pins 3/5 runs at 400 kHz, and the button (DFRobot
DFR0991) stops responding there until it is power-cycled.

| Device | Wire → header pin | Bus (address) |
|---|---|---|
| **Atlas button** (DFR0991, Gravity cable) | D (SDA) → **27**, C (SCL) → **28**, + → **1** (3.3 V), − → **6** (GND) | i2c-1, 100 kHz (`0x2a`) |
| **GPS** (NEO-M8U) | SDA → **3**, SCL → **5**, 3.3 V → **17**, GND → **9** | i2c-7, 400 kHz (`0x42`) |
| **UPS** (INA219) | SDA → 3, SCL → 5 (shared with the GPS) | i2c-7 (`0x41`) |

Never put the button on pins 3/5. Check the wiring before the payload run:

```bash
sudo i2cdetect -y -r 1      # 2a (the button)
sudo i2cdetect -y -r 7      # 41 (UPS), 42 (GPS)
```

Never probe bus 4 (the power-management chip). `install.sh` checks the button
(stage 98): missing or on pins 3/5 is a `[✗]` on a retail unit ("must not
ship"), a warning on a developer unit. After install,
`python3 /opt/atlas/current/status_button.py --flash` shows each pairing colour
on the button, so the line can confirm the LED and its colours.

## Per unit

1. Flash JetPack (L4T R39.2) to the NVMe and complete first-boot setup with
   the `admin` account (see the BSP README for `l4t_initrd_flash.sh`).
2. Attach the payload drive and run:

   ```bash
   sudo /media/$USER/atlas-control-prod/stage/atlas-imaging/atlas-provision.sh
   ```

   It finds the `atlas-control-prod` drive by its label (`--drive LABEL` for
   another drive).

   It copies the payload (45–60 min), installs the staged binaries and runs
   the kit's `install.sh --bundle stage/release --yes --states keep`
   (unattended: no prompts, only the routing states the payload brought),
   which:
   - checks first, in seconds, that this is an Orin on Ubuntu 24.04 with
     Python 3.12 and that the release is signed by a trusted key — nothing
     is changed otherwise;
   - installs system packages and skips every dataset already present;
   - installs the Atlas release through its update engine: the `atlas`
     service account, root helper, units, nginx, firewall, udev and mDNS
     files, a health check, and automatic rollback on failure;
   - checks Ollama is new enough for every model and has its GPU runners,
     and that every model is present (pulling any missing when online);
   - gives the unit its **own identity**: `Atlas-XXXXXXXXX` name,
     `atlas-xxxxxxxxx` hostname, TLS key and certificate, hotspot name (its
     `.local` address, e.g. `atlas-k7m2qx9wp.local`) and password (the code
     in that name, `k7m2qx9wp`), setup code;
   - writes the unit's **record** for support
     (`/root/atlas-labels/Atlas-XXXX.{png,svg,txt}`, copied to the drive's
     `labels/` when it is writable): the name, the Wi-Fi name (= address),
     "press the button, pick the colour", the setup code and the Atlas app QR
     code. It is not printed, and it never shows the Wi-Fi password;
   - **locks the unit down for retail** (`finalize-retail.sh`): setup-account
     passwords locked, SSH disabled and masked, the setup account removed
     from the `atlas` group, boots headless (`multi-user.target`, from the
     next boot), Docker, containerd, snapd and ModemManager disabled and
     masked; then it checks there are no sudo rules, the firewall is on, the
     app is on loopback only and the release's mDNS settings are in place;
   - **verifies** the unit with `atlas-engine check`: system files and
     settings match the release, services running, power mode MAXN_SUPER,
     kernel settings, swap, Ollama's settings and every model, the firewall
     and open ports, lock-down, identity, storage; then that the Atlas button
     answers on pins 27/28. Any `[FAIL]` ends the run with exit status 1 and
     "installed but NOT ready".

   Everything on screen is also in `/var/log/atlas-install/install-<time>.log`
   (root only; the last 10 are kept). A run that stops can be re-run as it
   is: finished steps are skipped. [INSTALL.md](INSTALL.md) has the stages
   and options.
3. Reboot. Keep the unit records (`labels/` on the drive) with the factory's
   records: they hold each unit's setup code (mode 0600), support's only way
   to pair a unit whose button has failed.

## Verifying a finished unit

- The log shows "already present — skipping" for every dataset and no
  per-state OSRM processing.
- `sudo /usr/local/lib/atlas/bin/atlas-engine check` → no `[FAIL]` (install.sh
  ends with it; after the first reboot, `--infer` also has Ray answer once and
  reports its speed); `sudo finalize-retail.sh --check` → "Ready for retail."
- The button glows dim white (unclaimed). From a phone on the unit's Wi-Fi
  (`atlas-xxxxxxxxx.local`, password `xxxxxxxxx`): open the Atlas app → Connect
  → press the button → pick the colour → paired, the light blinks green then
  goes off, and Atlas asks to set its own Wi-Fi password (skip it); map loads, a
  cross-state route returns (following roads, not a straight line), Ray
  answers. Then Settings → Factory reset (or hold the button 15 s) so the unit
  ships unclaimed.
- `find /atlas_data/atlas-control/osrm-data ! -perm -o=r | wc -l` → `0`. The
  app's service account must read the routing graphs; `osrm-extract` writes
  each `.fileIndex` as 0700, and `install.sh` makes them readable. An
  unreadable graph makes `osrm-routed` exit at once and the app logs
  "cannot read region.osrm.fileIndex".
- `ss -ltn` shows nothing on `*:5000`; `https://atlas-xxxxxxxxx.local` shows
  the pairing screen to an unpaired browser.

## Unique per unit, even from a cloned image

The identity is never on the payload drive. It is created on each unit by
`install.sh`, from the operating system's secure random source. It holds the
setup code, TLS key, device id and proxy secret. None of these is shared
between units: there is no default setup code.

The unit's name code is derived from the module serial. It appears as:
- the name (Atlas-K7M2QX9WP);
- the `.local` hostname (`atlas-k7m2qx9wp.local`);
- the Wi-Fi name, which is that `.local` address.

So a reflashed module keeps its printed name, and a different module never
gets it. Units made since 2026-11 use 9 characters from a keyed permutation of
the numeric serial (`derive_name_v2`, scheme `serial-v2`): a bijection over
29⁹ values, so two units can **never** share a name. A serial that isn't a
number below 29⁹ falls back to a 9-character hash (`hmac9`, recorded in
`device.json`); only those need the fleet registry's duplicate check (bump
`name_salt` with `identity.create(salt=1)`). Units made earlier keep their
6-character name.

The hotspot **password** starts as the code in the Wi-Fi name (owner decision,
2026-11-12), so nothing needs printing. It is public: anyone who sees the
Wi-Fi name knows it. See [PAIRING.md](PAIRING.md) for what that means for
security, and for compliance in markets that restrict default passwords.

If a line ever flashes a whole disk image instead, cloned from a finished
unit, `atlas-firstboot.service` still catches it. It runs
`atlas-identity ensure` on every boot, before NetworkManager, nginx and Atlas
start:
- the identity records the module's serial number (or the NVMe drive's, if
  the module has none);
- on a different module it makes a new identity and removes the cloned
  hotspot profile;
- the app ends pairings made with the old identity.

Support can read the new identity after that first boot with
`sudo atlas-identity label`.

## The clock and the RTC backup cell

Atlas keeps working offline after power loss (see
[TIMEKEEPING.md](TIMEKEEPING.md)). Accurate offline time from the first second
needs a backup cell on the RTC. On the line, after the first boot: unplug for
a minute, boot, and check that `sudo atlas-engine clock status` shows
`"status": "kept-time"`. `"lost-power"` means the cell is missing or flat.

## Developer units

`atlas-provision.sh --dev-key dev-release.pub` makes a developer unit: it
trusts that key (so `tools/dev/deploy` works), keeps SSH, and skips the
retail lock-down. Never ship one.

## Existing units

A unit installed the old way (git checkout, `User=admin`, sudo rules) moves
to this layout with one command, which can be undone:

```bash
sudo tools/migrate/atlas-migrate apply --bundle dist/<version> [--dev-key …]
sudo tools/migrate/atlas-migrate rollback
```

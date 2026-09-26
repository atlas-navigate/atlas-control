# Manufacturing and provisioning

Building a unit is a copy from the payload drive plus one script. Each unit
leaves the line with its own identity, its own label, the signed release
installed, and nothing another unit could use against it.

## Once per release: the payload drive

On the golden unit (a developer unit with all datasets built), with the
`atlas-backup` drive attached:

```bash
sudo install -d -m755 /opt/atlas-imaging
sudo install -m755 tools/manufacturing/*.sh tools/manufacturing/make_label.py /opt/atlas-imaging/
sudo install -m644 docs/MANUFACTURING.md /opt/atlas-imaging/

sudo /opt/atlas-imaging/atlas-capture.sh --release dist/1.2.0
```

`--release` is a signed release directory (see [RELEASES.md](RELEASES.md)).
Capture refuses a release this unit's update engine does not trust
(`atlas-engine verify`), then writes to the drive:

| Path | Contents |
|---|---|
| `payload/` | `/atlas_data`: maps, routing graphs, datasets, Ray's models (~330 GB) |
| `payload/osrm-native-bin/` | prebuilt OSRM binaries |
| `stage/ollama` | the Ollama binary |
| `stage/ollama-lib/` | its GPU runners (`/usr/local/lib/ollama`, ~2 GB) — without them Ray runs on the CPU |
| `stage/release/` | the signed release every new unit installs |
| `stage/kit/` | that same release unpacked (minus wheels): `install.sh`, tools, dataset builders |
| `stage/atlas-imaging/` | these scripts |
| `MANIFEST.txt` | L4T version, release, sizes, date |

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
0.17.1 for Qwen3.5); `install.sh` upgrades an older staged one when online.

`atlas-mirror.sh` is a separate full-rootfs backup of the golden unit for
disaster recovery — it contains that unit's own secrets and is never used to
build units.

## Per unit

1. Flash JetPack (L4T R39.2) to the NVMe and complete first-boot setup with
   the `admin` account (see the BSP README for `l4t_initrd_flash.sh`).
2. Attach the payload drive and run:

   ```bash
   sudo /media/$USER/atlas-backup/stage/atlas-imaging/atlas-provision.sh
   ```

   It copies the payload (45–60 min), installs the staged binaries and runs
   the kit's `install.sh --bundle stage/release`, which:
   - installs system packages and skips every dataset already present;
   - installs the Atlas release through its update engine: the `atlas`
     service account, root helper, units, nginx, firewall, udev and mDNS
     files, a health check, and automatic rollback on failure;
   - checks Ollama is new enough for every model and has its GPU runners,
     and that every model is present (pulling any missing when online);
   - gives the unit its **own identity**: `Atlas-XXXX` name, `atlas-xxxx`
     hostname, TLS key and certificate, hotspot name (the hotspot password
     is the standard one every unit shares), setup code;
   - writes the **label** (`/root/atlas-labels/Atlas-XXXX.{png,svg,txt}`,
     copied to the drive's `labels/` when it is writable): a Wi-Fi QR code,
     the Atlas app QR code, the name, hotspot name and password, setup code
     and address;
   - **locks the unit down for retail** (`finalize-retail.sh`): setup-account
     passwords locked, SSH disabled and masked, the setup account removed
     from the `atlas` group, mDNS unpinned from one interface name; then it
     checks there are no sudo rules, the firewall is on and the app is on
     loopback only.
3. Print the label and attach it to the unit; the label files hold the setup
   code and hotspot password (mode 0600). Reboot.

## Verifying a finished unit

- The log shows "already present — skipping" for every dataset and no
  per-state OSRM processing.
- `systemctl is-active atlas-control nginx ollama avahi-daemon atlas-firewall`
  → all active; `sudo finalize-retail.sh --check` → "Ready for retail."
- From a phone: scan the label's app QR code in the Atlas app → paired, map
  loads, a cross-state route returns, Ray answers.
- `ss -ltn` shows nothing on `*:5000`; `https://atlas-xxxx.local` shows the
  pairing screen to an unpaired browser.

## Unique per unit, even from a cloned image

The identity is never on the payload drive. It is created on each unit by
`install.sh`, from the operating system's secure random source. It holds the
setup code, TLS key, Wi-Fi name, device id and proxy secret. None of these is
shared between units: there is no default setup code.

The hotspot **password** is the exception: it is the same on every unit (owner
decision, `FACTORY_HOTSPOT_PASSWORD`), so it can be pre-printed on the box and
in the manual. Only the label with the setup code is unit-specific. See
[PAIRING.md](PAIRING.md) for what that means for security, and for
compliance in markets that ban universal default passwords.

If a line ever flashes a whole disk image instead, cloned from a finished
unit, `atlas-firstboot.service` still catches it. It runs
`atlas-identity ensure` on every boot, before NetworkManager, nginx and Atlas
start:
- the identity records the module's serial number (or the NVMe drive's, if
  the module has none);
- on a different module it makes a new identity and removes the cloned
  hotspot profile;
- the app ends pairings made with the old identity.

Print the label after that first boot:
`sudo atlas-identity label-json | make_label.py`.

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

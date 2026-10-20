# Installing Atlas

`install.sh` turns a freshly flashed Jetson into a finished Atlas, and is safe
to run again on one that is already working. On the production line
`atlas-provision.sh` runs it for you ([MANUFACTURING.md](MANUFACTURING.md));
this page covers the installer itself.

Software updates never use it. Atlas installs signed releases itself
(Settings → Software Update, [UPDATES.md](UPDATES.md)).

## Requirements

- A Jetson Orin, flashed with JetPack 7: L4T R39, Ubuntu 24.04, and the
  system Python 3.12. Everything is measured on the Orin Nano 8 GB (Super).
- A signed release: a directory holding `atlas-release.json`, its `.sig` and
  the bundle ([RELEASES.md](RELEASES.md)). The kit `install.sh` belongs to is
  that same release unpacked (`stage/kit/` on a payload drive), or a checkout
  on a developer unit.
- Storage: about 250 GB to build every dataset on the unit. A unit seeded from
  a payload drive needs no extra room.
- Internet is needed only for whatever the unit lacks: packages, Ollama,
  models, datasets.

## Usage

```bash
sudo ./install.sh --bundle DIR [--labels DIR] [--dev-key PUB]
                  [--states all|keep|none|"MD VA …"] [--yes]
                  [--only STAGES] [--skip STAGES] [--log FILE]
```

| Option | Meaning |
|---|---|
| `--bundle DIR` | The signed release to install. Required. |
| `--labels DIR` | Where to write this unit's label files (PNG/SVG/TXT, mode 0600). |
| `--dev-key PUB` | Makes a developer unit: trusts this release key, keeps SSH, skips the retail lock-down. Never use it on a unit that ships. |
| `--states` | Which states get routing data. `keep` means the ones already on the unit (any half-built one is finished). `all` is all 50 plus DC, which takes many hours and ~110 GB. `none` skips routing. A list such as `"MD VA"` or `md,va` names states. Unknown names are refused before any work starts. |
| `--yes` | Never ask anything. Without `--states`, routing defaults to `keep`. |
| `--only`, `--skip` | Run only these stages, or all but these. Give a number, a name, or both, comma-separated (`90,98` or `models,verify`). Preflight and summary always run unless named in `--skip`. Meant for debugging. |
| `--log FILE` | Where to write the log, instead of the default below. |

With no terminal attached (atlas-provision, systemd-run, ssh without `-t`),
the installer never prompts and behaves as if given `--yes`. On a terminal
without `--states`, it asks once which states to install. The default answer
is `keep` if the unit already has routing data, otherwise `all`.

The old `--full` and `--fresh` flags are still accepted and do nothing: every
run does both. `--update` is refused, because updates come from the update
engine.

## Stages

The stages are in `tools/install/stages/` and run in this order. Each one
checks what is already in place and does only what is missing. That is what
makes a re-run safe.

| # | Stage | Does | Skips when |
|---|---|---|---|
| 00 | preflight | Checks aarch64, an Orin board, Ubuntu 24.04 and Python 3.12. Checks the release signature and bundle (`migrate.py check`). Warns if storage is low. Takes seconds and changes nothing. | — |
| 10 | storage | Creates `/atlas_data` and copies the dataset builders there. Makes the `~/atlas-control` link. | Directories and link already present |
| 20 | packages | Installs the distribution packages (apt, with retries). Enables avahi and bluetooth. | Every package installed |
| 30 | swap | Installs zram (the release's `atlas-zram`, `zram-swap.service`, `99-atlas-memory.conf`). Adds a 32 GB disk swapfile and its fstab line. | Files identical; swap active; fstab line present |
| 40 | builder-venv | Creates the dataset builders' Python environment. Its only extra package is Pillow (`requirements-builders.txt`). | `import PIL` works, so no pip and no network |
| 50 | ollama | Installs Ollama if it is missing, too old, or lacks its GPU runners, at exactly the version Ray was tested with (`ai_models.json` → `ollama.tested`). Creates the `ollama` account. Writes the base unit and the release's drop-in. | Version OK and files identical, so Ollama is not restarted |
| 55 | uart | Applies the Jetson UART1 DMA fix for the 40-pin mesh radio (needs a reboot) | Already in `extlinux.conf`, or not applicable |
| 60 | osrm | Installs the OSRM tools: from what is installed, then prebuilt binaries, then a source build. Installs the profiles. Checks `--mmap` support. | Tools present with `--mmap` |
| 70 | maps | Installs the pmtiles CLI, the basemap, label fonts, `topo.pmtiles` and US cities | Each file present |
| 80 | routing | Builds the state routing graphs (see `--states`) | A state's `.processed` marker |
| 85 | places | Builds NPS trails, the address/POI index and the GeoNames fallback | Each database present |
| 90 | models | Pulls every model Ray offers, in this unit's build, plus the embedder (with retries). Never restarts a running Ollama. | Every model present |
| 95 | release | First install: `migrate.py apply --provision`. A newer release goes to the update engine (`tools/release/handoff.py`). The same or an older release triggers `atlas-engine reapply`. | — |
| 96 | identity | Shows the unit name and writes the label | — |
| 97 | retail | Runs `finalize-retail.sh` | Developer unit |
| 98 | verify | Runs `atlas-engine check`. Any `[FAIL]` fails the install. | — |
| 99 | summary | Prints what is ready and what still needs doing. Exit status 1 if verify failed. | — |

## Logs

Everything on screen also goes to `/var/log/atlas-install/install-<time>.log`.
The directory is root only, and the last 10 logs are kept. Each stage ends
with its duration. A failure prints `[✗] install.sh stopped in stage NN-name
(file line N: command)`, and every `die` message names the next step.

## Re-running and repairing

Run the same command again. Finished work is skipped: packages, swap, Ollama,
datasets, models. On a working unit with the same release, a re-run:

- changes no file, except a new log;
- does not restart Ollama, so Ray keeps its model loaded;
- runs `atlas-engine reapply`. That puts back any of the release's system
  files, avahi settings or power mode that something moved (a package
  upgrade, a hand edit), with the same validation and all-or-nothing restore
  as an update.

`sudo /usr/local/lib/atlas/bin/atlas-engine reapply` does that last step on
its own.

## Upgrading

On a unit with Internet access, use Settings → Software Update. To install a
newer release by hand, for example from a USB drive, run `install.sh --bundle
<newer release> --yes --states keep`. Stage 95 hands it to the update engine:
signature check, health check, and automatic rollback. On a developer unit,
use `sg atlas -c tools/dev/deploy`.

## Recovery

| What happened | What to do |
|---|---|
| First install failed in stage 95 | `migrate.py` has already undone it, and the unit runs as before. Fix the cause named in the log, then re-run `install.sh`. |
| An update failed or rolled back | The previous version is running and nothing was lost. The message says which version is running. Try again later, or free some storage first. |
| A unit drifted (`atlas-engine check` shows `[FAIL] files`) | Run `sudo atlas-engine reapply`, or re-run `install.sh`. |
| Undo a migration from the old layout | `sudo tools/migrate/atlas-migrate rollback` |
| The installer stopped mid-way | Re-run the same command. |

## `atlas-engine check`

```bash
sudo /usr/local/lib/atlas/bin/atlas-engine check [--json] [--infer]
```

This is a read-only report on whether the unit matches its installed release.
Each line is `[ok]`, `[warn]` (works, but not as intended), `[FAIL]` or
`[skip]` (can't be checked here, for example because it needs root). The exit
status is 1 if anything failed. It checks:

- **The release:** it is installed and complete. Every system file is
  byte-identical to the release's rendering, with the right mode. The INI
  settings are set, removed files are gone, and the symlinks are in place.
- **Units and services:** the release's units are enabled. atlas-control,
  nginx, ollama, avahi, the firewall, the helper socket and zram are running.
- **Hardware and kernel:** the power mode is the one the release names
  (MAXN_SUPER), skipped on modules without it. The kernel settings from
  `99-atlas-memory.conf` are applied. zram and disk swap are active.
- **Ollama:** the release's settings are in effect (`systemctl show`). The
  version is at least the minimum, with a warning if it isn't the tested
  version. Every model Ray offers is present. The loaded chat model is at
  least 95% on the GPU.
- **Network:** the firewall is active. Nothing listens on the network outside
  the firewall policy, and the app's port 5000 is loopback only.
- **Retail units:** SSH is off and the lock-down has been done. The unit boots
  headless, and Docker, containerd, snapd and ModemManager are off.
- **Identity and storage:** the hostname matches the identity. At least 5 GB
  of storage is free.

`--infer` also asks Ray's loaded model for one short answer, using exactly
the load options the app uses. Nothing is reloaded or moved. It reports
tokens per second, and warns if the model had to reload.

## Developer units

A unit installed with `--dev-key` trusts that key, keeps SSH and is not
locked down. The builder environment (`/atlas_data/atlas-control/venv`) has
only what the dataset builders need. To run the test suite there, install
the app's requirements into it: `venv/bin/pip install -r requirements.txt`.

## What lives where

| Owned by | What |
|---|---|
| The release (`system/files.json`, installed and kept current by every update) | Atlas's units, nginx site and nginx sandbox, firewall policy, udev rules, avahi advert and `avahi-daemon.conf` settings, Ollama drop-in (tuning, llama-server limits, sandbox), journald cap, kernel memory settings, zram helper and unit, power mode |
| `install.sh` | Packages, `/atlas_data`, the disk swapfile, the Ollama binary, account and base unit, OSRM, datasets, models, the UART fix |
| `/etc/atlas` (made per unit, never copied) | Identity, TLS key, setup code, `retail` / `dev-mode` markers |

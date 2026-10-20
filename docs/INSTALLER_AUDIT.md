# Installer audit (2026-10-19)

The question was whether `install.sh --bundle` on a freshly flashed Orin Nano
produces the same Atlas as the development unit running the latest main, and
whether updates keep sold units that way.

The architecture was already right, and is unchanged:

- `install.sh` does the base OS and the datasets.
- The signed release (`system/files.json`, applied by the update engine with
  backup, validation and rollback) owns Atlas's system files.
- `migrate.py` does the first install, and `finalize-retail.sh` does the
  lock-down.

What follows is the list of places where a fresh unit, or an updated one,
would have differed. Each row gives the evidence, the fix and the commit
(all on `main`).

| # | Finding | Evidence | Fix | Commit |
|---|---|---|---|---|
| 1 | Nothing set the power mode. Every Ray figure was measured in MAXN_SUPER, but JetPack's default is 25W (`PM_CONFIG DEFAULT=1`) or whatever the technician picked during OOBE. perf.py boosts clocks only up to the mode's ceiling. | `/etc/nvpmodel.conf`; docs/AI_MODELS.md | `"power_mode": "MAXN_SUPER"` in files.json. The engine finds the mode by name and sets it on install and on every update. It skips modules without that mode and never fails an update. | 3e106cf |
| 2 | An unattended install died at the routing prompt: `read -rp` with no guard under `set -e`, so EOF on stdin aborted the run. | old install.sh:950 | `--states all\|keep\|none\|LIST` and `--yes`. The prompt appears only on a terminal, and the default without one is `keep`. atlas-provision passes `--yes --states keep`. | 3dc1db5 |
| 3 | There was no preflight. Python 3.12 was first checked hours into the run, by migrate.py, and the README claimed Ubuntu 22.04. | old migrate.py:120, README | Stage 00 checks the architecture, the Orin board, Ubuntu 24.04 and Python 3.12. It also verifies the release signature and bundle (`migrate.py check`). Nothing is changed when a check fails. | 3dc1db5 |
| 4 | The builder venv installed the full app stack, checked Bluetooth, imported `mobile_bridge`, and pip-upgraded on every run (network needed even when nothing was missing). | old install.sh:466-540 | `requirements-builders.txt` (Pillow only), and pip is skipped when `import PIL` works | 3dc1db5 |
| 5 | install.sh ignored atlas-helper's reply. A refused update meant a 30-minute wait, and the same wait code was duplicated in tools/dev/deploy. | old install.sh:1296 | One `tools/release/handoff.py`, used by both. A refusal fails at once, and it waits for its own transaction's result. | 3dc1db5 |
| 6 | Some settings were made only by install.sh, so updates never fixed them: the avahi-daemon.conf edits, the Ollama tuning in ollama.service, zram and `99-zram.conf`. This unit still had the old `allow-interfaces=wlP1p1s0,l4tbr0` pin. | `/etc/avahi/avahi-daemon.conf` | All of it moved into the release. Engine 4 adds `settings` (INI key edits), the `sysctl` kind, the zram helper and unit, and an Ollama drop-in that carries all the tuning. Old paths go in `remove`. | 3e106cf |
| 7 | ModemManager was enabled and could probe the radio and GPS ports with AT commands. | `systemctl is-enabled ModemManager` | `ID_MM_DEVICE_IGNORE` on every unit. ModemManager is masked on retail units. | 3e106cf, 22cebbf |
| 8 | Services had loose sandboxes. | `systemd-analyze security`: ollama 9.0, nginx 9.4, atlas-control 7.4 (scored offline, the same way as the "after" column) | Release drop-ins bring them to 6.1, 5.7 and 3.8. Directives that would break the GPU, radio, GPS or hostname changes are left out, and the reasons are written in the unit files. | 3e106cf |
| 9 | Ollama came from `curl ollama.com/install.sh \| sh`, which installs whatever is latest, not the version Ray was tested on. | old install.sh | `ollama.tested` (0.32.1) in ai_models.json becomes `OLLAMA_VERSION`. Ollama is installed only when it is missing, too old or has no GPU runners. `atlas-engine check` warns about any other version. | 3e106cf, 3dc1db5 |
| 10 | Retail units ran the desktop plus unused services: about 138 MB resident and 194 MB swapped, on a unit that shares 8 GB with Ray. | measured on this unit (resident + swap of those processes) | `finalize-retail.sh`: boots to `multi-user.target`; docker, containerd, snapd and ModemManager disabled and masked; `--check` verifies it. Developer units are untouched. | 22cebbf |
| 11 | The engine's diagnostic logs were never pruned, and nothing warned about low disk space. | `/var/lib/atlas-update/diag` | Logs pruned to the newest 20, backups pruned by age. A low-storage flag (under 5 GB free or over 95% used) appears in the storage card, and `check` fails below 1 GB. | 3e106cf, b88c7a8 |
| 12 | Dead code: `run.sh` and `setup_offline.sh` (unreferenced); the topo PNG purge; `STATE_INPUT`; nginx `/tiles/topo/` and the app's topo tile route (for a cache install.sh deletes); `logs/update.log`; and routing_node's Docker fallback (the atlas account can't use Docker). | grep, nginx config | Removed. `--full` and `--fresh` are still accepted for old runbooks. | 29c0511, 3dc1db5 |
| 13 | After a successful update the screen said only "Atlas is up to date". Rollback and failure messages had no next step. | update_manager.py | "✓ Updated to Atlas X" with its notes. Failure and rollback messages say which version is running, that data is unchanged, and what to try. | b88c7a8 |
| 14 | **Found during the audit:** a Qwen3.5 load put 33 of 34 layers on the GPU. llama-server keeps 1 GiB free by default, and the load came up 6 MiB short. On unified memory, moving a layer to the CPU frees nothing. | `/api/ps` 82–83% on the GPU; journal "cannot meet free memory target of 1685 MiB, need to reduce device memory by 6 MiB" | `LLAMA_ARG_FIT_TARGET=256` in the Ollama drop-in. Measured before and after, same benchmark: all 34 layers on the GPU, generation 13.0 → 14.5 tok/s (+11.5%), prompt processing 435 → 445 tok/s (within noise). See AI_MODELS.md. | 3e106cf |
| 15 | **Found by the new installer tests:** the routing stage's `_states_present` returned 1 under `pipefail` (a `[[ ]] && echo` loop piped to `sort`), which would have aborted stage 80 on a unit with no routing data. | test_installer.py | if/then | 3dc1db5 |

Checked and left as they are:

- **Ray's per-request HTTP connections.** Setting up a loopback connection
  costs microseconds, against about 0.4 s of Ollama's own CPU work per
  request, which is already measured and tuned.
- **Model tuning, keep-alive, the prompt-cache cap and the CPU/GPU boost.**
  These were already measured and shipped. The boost now also runs in
  MAXN_SUPER on every unit (#1).
- **Hardware buttons and status LEDs.** None exist in the code, so there was
  nothing to preserve.
- **Cloud models.** Atlas is offline, with no accounts, and the code has
  none.

## Ownership after the fixes

Everything Atlas needs outside `/opt/atlas` is now one of three things:

- **In the release.** Updates keep it current and roll it back, and
  `atlas-engine check` reports any drift.
- **Made by `install.sh` once.** Packages, datasets, the Ollama binary and
  models, the swapfile, the UART fix. A re-run redoes only what is missing.
- **Per unit in `/etc/atlas`.** The identity, never copied.

[INSTALL.md](INSTALL.md) has the full table.

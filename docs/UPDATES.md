# Software updates

Atlas updates itself from signed releases. The owner sees one screen
(Settings → Software Update) and one progress bar; everything that can go
wrong is either retried, reported in plain words, or undone automatically.

## What the owner sees

| Situation | Message |
|---|---|
| No Internet | Atlas is offline. Connect to the Internet to check for updates. |
| Clock not set yet | Waiting for Atlas's clock to synchronise before checking for updates. |
| Connection drops mid-download | Download paused — waiting for Internet connection. (resumes by itself) |
| Download fails verification | Update could not be verified. |
| Not enough storage | There is not enough free storage for this update. |
| Install fails its health check | The update to Atlas Y could not be completed, so Atlas went back to X. Your maps, conversations and settings are unchanged. Try again later; if it happens again, free some storage first. |
| Install refused (e.g. verification) | *(the reason)* Atlas X is still installed. *(same next step)* |
| Success | ✓ Updated to Atlas Y — with that release's "What's new" notes, until another release is found |

While installing, the page (and the phone apps) show "Atlas is installing an
update and restarting." Browsers are served a static status page by nginx
while the app restarts, and reconnect by themselves; the apps show "Update
complete." when Atlas is back.

## Two halves

**The app (unprivileged, `update_manager.py`)** checks for updates at most
every 6 hours while online (and on demand), downloads and pre-verifies:

1. Resolve `…/releases/latest` to its tag once; fetch `atlas-release.json`
   and its `.sig` from that tag. No identifiers are sent — the request is the
   same from every unit.
2. Verify the signature and that `seq` is newer than what is installed.
3. Check free space (bundle × 3 + 1 GiB).
4. Download with HTTP `Range` resume from the stable github.com URL (never the
   expiring redirect), accepting `206` only with a matching `Content-Range`.
   A `.part` sidecar records what is being downloaded, so a restart resumes;
   8 MiB chunk hashes pinpoint and re-fetch a damaged chunk.
5. Publish live progress over Socket.IO (`update_progress`: state, stage,
   version, progress_percent, downloaded/total bytes, bytes per second,
   message, error, can_cancel) — the page draws one bar in place and can be
   refreshed at any time.
6. Hand the verified file to the root engine through `atlas-helper`
   (`update.apply`). The app cannot name paths; the engine reads only the
   fixed hand-off directory.

**The engine (root, `system/atlas_engine`)** installs, as a transaction with
an fsynced journal (`/var/lib/atlas-update/journal.json`):

| State | What happens |
|---|---|
| ACCEPTED | copy the hand-off into root-owned space (no symlinks, size caps) |
| VERIFIED | signature, product/arch, `seq` > installed, bundle hash, engine version, space |
| ENGINE_SWITCH | a newer engine installs itself first (self-test, swap, re-exec) |
| STAGING | safe extraction: regular files and directories only, no `..`, no links, no setuid, size/count caps |
| VENV | Python environment reused or built offline from the release's wheels |
| STAGED | byte-compiled, `.complete` marker, renamed to `releases/<version>` |
| APPLYING_SYSTEM | app stopped; database probed as `atlas`; the release's system files installed and validated (see below) — restored at once on failure; then the release's power mode |
| SWITCHED | `current` → new release, app started, nginx reloaded |
| HEALTH_CHECK | loopback and through nginx with the pinned certificate: version, release path of the running process, database, schema window, 30 s of stability; 180 s budget |
| COMMITTING | `installed.json`, `previous`, keyring upgrade, prune (keeps current, previous, factory) |
| ROLLING_BACK | system files restored, `current` → previous, database restored if the old release cannot read it, restart, re-check |

## The release's system files

`system/files.json` lists everything outside `/opt/atlas` a release owns.
Every update installs it with a backup of what it replaces, validates it, and
restores all of it if anything fails. Only what changed is reloaded.

| Entry | Example | Validated by | Applied by |
|---|---|---|---|
| `files`, kind `systemd` | Atlas's units, `zram-swap.service` | `systemd-analyze verify` | daemon-reload |
| `nginx` | site, snippets | `nginx -t` | reload |
| `nginx-unit` | nginx's sandbox drop-in | `systemd-analyze verify nginx.service` | restart nginx |
| `ollama` | Ollama drop-in: tuning, llama-server limits, sandbox | `systemd-analyze verify ollama.service` | restart Ollama (only if it changed) |
| `firewall` | policy | loaded into an unhooked probe chain | reload |
| `udev` | radio/GPS rules | `udevadm verify` | reload + trigger |
| `avahi` | `_atlas._tcp` advert | XML parse | reload avahi |
| `journald` | log size cap | — | restart journald |
| `sysctl` | `99-atlas-memory.conf` | every line `key = value` | `sysctl --system` |
| `plain` | helper shims, `atlas-zram` | — | — |
| `settings` | keys in `/etc/avahi/avahi-daemon.conf` | parses as INI | restart avahi |
| `remove` | files from older installs (`99-zram.conf`, `/usr/local/sbin/atlas-zram`) | — | backed up, then removed |
| `enable` | units to enable | — | `systemctl enable` |
| `power_mode` | `MAXN_SUPER` | found by name in `/etc/nvpmodel.conf` | `nvpmodel -m <id>` when different; skipped on modules without it; never fails an update |

`settings` edits single keys in place and leaves the rest of a file that
belongs to its package alone. A key keeps its line; duplicates are commented
out. The edit is idempotent, and a missing package is skipped.

Engine 4 is the first to understand `settings`, `sysctl`, `nginx-unit` and
`power_mode`. Older engines install engine 4 first (ENGINE_SWITCH), so a
release that uses them updates any unit.

Two commands work outside an update:

- `atlas-engine check [--json] [--infer]` reports, read-only, whether the
  unit matches its release ([INSTALL.md](INSTALL.md#atlas-engine-check)).
- `atlas-engine reapply` installs the current release's system files again,
  for a unit that drifted. It uses the same validation and restore as an
  update, and refuses while one is running.

Diagnostic logs (`/var/lib/atlas-update/diag`) are pruned to the newest 20.
System-file backups and transaction records are pruned to the newest 3.

## Power loss and crashes

`atlas-update-recover.service` runs at boot before Atlas starts:

- interrupted during ACCEPTED…STAGED → cleaned up; the download is kept and
  the owner can retry with one tap;
- during APPLYING_SYSTEM → system files restored, the old release keeps running;
- after SWITCHED → the boot is counted; more than two boots in the new
  release without passing the health check rolls back; otherwise the health
  check resumes;
- during COMMITTING / ROLLING_BACK → finished (both are idempotent).

If even the previous release cannot come up, the engine falls back to the
`factory` release (up to three attempts); failing that it shows "Atlas needs
attention — Atlas could not restore itself automatically. Restart Atlas to
try again." and tries again on the next start.

## Database migrations

`database.py` versions the schema with `PRAGMA user_version` and additive,
ordered migrations. Before migrating it writes a `VACUUM INTO` copy
(`data/backups/`, last three kept). Each release declares a `compat_floor`;
rolling back to a release below it restores the pre-migration copy.
`embed_format_v` uses "less than" semantics so a rollback never forces the
knowledge base to be re-embedded.

## Developer units

On a unit with `/etc/atlas/dev-mode`, `tools/dev/deploy` builds the current
checkout as a developer release, signs it with the developer key and installs
it through exactly this path.

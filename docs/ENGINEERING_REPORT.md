# Atlas production hardening — engineering report

Branch `production-hardening` (worktree `/atlas_data/atlas-control-wt`), 28 commits on top of
`fab3ef6`: 177 files changed (116 added, 32 modified, 29 deleted), +17.9k / −12.6k lines — most
of the deletions are the two mobile apps' trust-all networking layers, which were rewritten.
Nothing is pushed.

**Status in one paragraph.** Every item in `Atlas_control_updates.txt` is implemented, and all of
it is covered by 207 automated tests plus 4 skipped (and 28 JavaScript cases). The update engine,
migration and rollback were rehearsed in a sandboxed root. The browser page was checked in
headless Firefox, and performance was measured on this unit. What has **not** happened yet is
the on-device half of the acceptance test: this Jetson still runs the legacy layout because the
one-command migration needs `sudo`. The first migration attempt (bundle `…1822`) failed safely:
this kernel cannot load native nftables `ct`/`reject` rules, and it rolled itself back. The fix
is committed and proven more strongly than before (see "Firewall"), and a fresh bundle is
built. The on-device steps (§ "Remaining verification") follow the migration. The two mobile
apps are written but cannot be compiled on this Jetson.

---

## 1. Confirmed vulnerabilities and problems

All re-verified at `fab3ef6` (the review's commit, which was also `HEAD`), plus items found
during the work (marked **new**).

Critical
1. **Root escalation through the updater.** `sudo /usr/local/sbin/atlas-update` ran
   `install.sh --update` as root, and `install.sh` was writable by the app user.
2. **Unsigned updates.** `git pull` from GitHub ran as root with no signature, hash, health
   check or rollback, and a failed pull still reported "updated".
3. **Forged client addresses.** Flask listened on `0.0.0.0:5000` behind an unconditional
   `ProxyFix(x_for=1)`, so a forged `X-Forwarded-For` passed the local-only check and the rate
   limiter. Socket.IO bypassed both `ProxyFix` and `before_request`, so every socket looked
   local.
4. **No CSRF, Origin or Host checks.** Body-less POSTs (factory reset, update apply, hotspot
   stop, Wi-Fi disconnect) could be forged cross-site. `get_json(force=True)` accepted
   `text/plain`. Socket.IO allowed any origin, and any private-resolving host was trusted, which
   enabled DNS rebinding.
5. **Ray `eval()` on model output**, with escapable `{"__builtins__": {}}`, unbounded
   pow/factorial, and a fallback that evaluated any line containing operators. Unfenced mesh text
   and RAG passages could steer it: **radio → code execution**.
6. **Universal credentials.** The hotspot `Atlas-Hotspot/AtlasControl` was seeded and logged,
   the mobile default was `"password"`, and one shared RSA certificate was used everywhere.
7. **Secrets disclosed without authentication.** The hotspot password was exposed via BLE GATT,
   `/api/mobile/bootstrap` and `/api/hotspot/status`. The mesh `wifi_psk` and BT `fixed_pin` were
   exposed via `/api/device/config` and `/api/settings`, and channel PSKs via the share URL.
8. **Unrestricted `NOPASSWD`** for `nmcli` and `smartctl`, a service running as the full-sudo
   `admin` account, and Wi-Fi passwords passed on `nmcli` argv (visible in `/proc` and logged by
   sudo).
9. **Mobile TLS.** Android used a trust-all `TrustManager`, `hostnameVerifier true`, WebView
   `proceed()` for any private IP, cleartext and user CAs. iOS accepted any trust and set
   `NSAllowsArbitraryLoads`. Both preferred `http://…:5000`.
10. **Unsanitised Markdown links** (`javascript:` rendered), and a CSP allowing
    `unsafe-inline`/`unsafe-eval`.
11. **new — Radio-to-browser XSS.** Map popups put other radios' node names into an inline
    `onclick` using `JSON.stringify(name).replace(/"/g,'&quot;')`. The attribute is HTML-decoded
    before it runs, so a node named `&quot;);alert(1);//` (fits Meshtastic's 40 bytes) executed
    script in the Atlas UI.

High / medium
- `safe_calc` was AST-based but unbounded (`9**9**9**9`, `factorial(10**6)`).
- No device identity, pairing, pinning or `_atlas._tcp`. The hostname was fixed as `atlas`; apps
  found Atlas by URL lists only; the mDNS parser took the first A record of any name.
- Every unauthenticated beacon probe ran `nmcli` (an amplification and blocking risk).
- gevent does not patch subprocess, time, select or threading. So `nmcli`, `git`, `tegrastats`,
  `smartctl`, `time.sleep` and `Event.wait` in request greenlets froze the whole server.
- `tegrastats` was spawned per stats refresh (about 1 s of waiting each time), and
  `cpu_percent(0.1)` blocked.
- udev rules were `MODE="0666"`. There was no firewall; `meshtastic.db` was 0644.
- Destructive actions had no server-side confirmation, and deletes had none at all. There was no
  delete-history feature, and factory reset kept documents, calendar, settings and Wi-Fi
  profiles.
- GPS sent three identical Socket.IO events per fix at the receiver's full rate. Positions were
  written at 1 row/s (86,400 per day while parked), and coordinates were logged at INFO.
- Explain stored 4-dp GPS and appended coordinates to saved answers. The iOS tracker posted every
  10 m and never stopped.
- Embeddings were 5.3 MB of JSON re-parsed every 2 minutes, scored one list at a time (~49 ms per
  query). The model list was fetched per embed under the lock, which could `/api/pull` for up to
  30 minutes while holding it.
- The knowledge map was ≈67 M pure-Python mult-adds per request on the event loop, measured at a
  **6.0 s stall of every client**.
- One Ollama lock was held for a whole stream, and the calculator agent bypassed it.
- **The RAM valve was dead.** `ai_manager.available_ram_mb()` was called on the instance and the
  AttributeError swallowed, so Ray was never unloaded when routing needed memory.
- There was no token estimation. `_LIVE_DATA_KEYWORDS` substring matching switched RAG off for
  "apply pressure", "recovery position", "altitude sickness", "signal mirror", "frame" (ram) and
  "state" (stat).
- Chat history was loaded whole, with explain blobs and no LIMIT or id tiebreak.
- SQLite used a 64 MB cache and a 256 MB mmap per thread connection on a 7 MB database. There was
  no schema versioning, and `embed_format_v` equality would re-wipe embeddings on rollback.
- nginx had no HTTP/2 and accepted any Host; headers were not inherited into locations. Babel
  (2.8 MB) was shipped, the bundle was un-hashed, and every script was render-blocking.
- The device clock can boot at 1969, which affects certificate dates, cookie expiry and update
  TLS.
- **new** — The Tegra kernel lacks `NFT_CT` and `NFT_REJECT`; the first firewall design failed
  on the device.
- **new** — Legacy scripts that re-created privileges: `setup_devices.sh` rewrote the unit back to
  `User=admin`, and `setup_nvme_temp.sh` added a `NOPASSWD` smartctl rule.
- **new** — Found during Phase F: a revoked device's live socket kept receiving data.
- **new** — Found during Phase H: `sysfiles` "removed" symlinks were restored as 0777 regular
  files, and nginx's default site clashed with Atlas's `default_server` on fresh units.

**Outdated original findings:** none. `HEAD` was `fab3ef6`, and every finding reproduced. Two
were worse than stated: Socket.IO bypassed the checks entirely, and the calculator fallback made
radio text a code path.

## 2. What changed

### Security (`request_guard.py`, `pairing.py`, `device_identity.py`, `system/`)
- **Entry point.** nginx is the only listener (80→443, HTTP/2, the per-device certificate). Flask
  is on `127.0.0.1:5000`, and loopback :5000 is dropped for every account except root, www-data
  and atlas.
- **`EdgeMiddleware`** is outermost, so it also covers `/socket.io/`:
  - proxy headers are trusted only from loopback with the per-device `X-Atlas-Proxy` secret, and
    are stripped otherwise;
  - Host allowlist: IP literals, own names and owner-added names;
  - cross-site checks via `Sec-Fetch-Site`/Origin/Referer;
  - JSON-only bodies;
  - a per-client `X-Atlas-CSRF` HMAC for cookie clients;
  - the pairing gate.
- **Pairing** (see §4). Unpaired clients get only the pairing screen and public identity.
- **No sudo.** Root actions go through the socket-activated `atlas-helper`:
  - fixed verb table, `SO_PEERCRED` check, validated arguments;
  - Wi-Fi secrets are written into root-only NetworkManager keyfiles, never argv;
  - the service runs as `atlas` with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
    `PrivateTmp`, and writes only `data/ logs/ updates/ cache/`.
- **Firewall.** An iptables-nft `ATLAS-INPUT` chain plus a native nft loopback table (policy in
  `system/firewall/policy.json`). SSH is allowed only on developer units. The rules are proven
  loadable by committing them into an unhooked probe chain (not `--test`, which only parses)
  before any install, in migration preflight and in engine validation.
- **udev:** `GROUP=dialout MODE=0660`.
- **Calculator:** `safe_math.py`, a strict AST allowlist with limits on length, nesting, nodes,
  steps, integer bits and factorial. It is shared by the UI calculator, Ray and the calculator
  agent; there is no `eval` anywhere.
- **Untrusted content:** `untrusted.py` neutralises and fences mesh, map, reference and owner
  text with a per-request boundary, and the system prompt treats fenced text as data. React
  renders everything, `atlasSafeHref` handles links, map popups use data attributes plus
  delegation, and the prebuilt page runs under a `script-src 'self'` CSP (no inline script, no
  eval, `object-src 'none'`). All verified in headless Firefox.
- **Secret redaction.** Unauthenticated responses, BLE, beacon, bootstrap and settings no longer
  carry secrets; radio config secrets are write-only.
- **Destructive actions:**
  - factory reset needs `{"confirm":"RESET"}` (typed in the UI);
  - Wi-Fi disconnect, hotspot stop/change and history deletes need `{"confirm":true}`, with one
    shared confirm dialog;
  - chat, document and shared-phone deletes are confirmed in the UI.
- **Legacy removal:** the updater launcher, `setup_https.sh`, the old nginx site, unit template,
  root udev copy, `setup_devices.sh` and `setup_nvme_temp.sh`.

### Privacy (`retention.py`, `audit_log.py`, `sensitive_log.py`)
- **Retention** (Settings → Privacy):
  - conversations until deleted, explain 30 d, location 7 d, shared phones 30 d;
  - applied nightly;
  - "Delete AI History" and "Delete Location History" use `secure_delete` plus a WAL truncate,
    and are tested by scanning the database files for the deleted text.
- **Coordinates:** Ray and explain see ≈1 km or a place name. Coordinate and secret logging is
  redacted or demoted.
- **Security log:** which paired device changed settings, network, pairing or update state —
  never values. Capped at 2000 entries / 180 days.
- **Resale-safe factory reset.** It removes:
  - owner documents, calendar, settings, Ray settings;
  - paired devices, closing their live sockets;
  - saved Wi-Fi networks and hints;
  - downloads and database copies.
  The hotspot returns to the label credentials, and the DB is VACUUMed. It keeps the software,
  maps, built-in reference and identity.

### Performance (measured on this unit; see §9)
- **Stats:**
  - one on-demand `tegrastats` process (1 Hz, 0.1 % CPU, stops after 120 s idle);
  - non-blocking `cpu_percent`; SMART data cached for 10 minutes;
  - stats pushed over Socket.IO to open System/Power pages, with polling only as a fallback and
    never from hidden tabs.
- **`vector_store.py`:** one normalised float32 matrix, rebuilt only when documents change.
  Identical scores, 49 ms → 0.6 ms per query, ~15 MB less RAM.
- **Knowledge map:** built from the same matrix, cached, and computed off the event loop.
  5.7–6.0 s → 10 ms (cold 217 ms), with identical 166 edges.
- **Ollama:**
  - separate generation and embedding slots, shared when RAM < 600 MB;
  - model list cached for 60 s;
  - pulls only at startup warmup, outside any slot;
  - the calculator agent now takes the generation slot.
- **`token_budget.py`:** RAG gets what `num_ctx − num_predict` leaves after the system prompt and
  live data. History keeps the newest turns that fit. It uses a measured 3.8 chars/token (3.3 in
  the estimator), and the prompt token count reported by Ollama is recorded against it.
- **Live-data routing:** matched on whole words, and never disables RAG (live questions take 2
  passages).
- **History:** `LIMIT` + id tiebreak, no explain blobs.
- **SQLite:** 8 MB cache and 64 MB map per connection.
- **GPS:** one `gps_fix` event, ≤2/s with a 5 s heartbeat when parked; track rows every 10 m or
  60 s.
- **Frontend:** every script `defer`, a content-hashed immutable bundle, KaTeX on demand, no babel
  in releases.
- **RAM valve fixed.** `perf_metrics.py` feeds `/api/diagnostics/performance` (paired only, local
  only). `tools/bench/bench.py` does read-only before/after runs.

## 3. Device discovery architecture
- **mDNS:** Avahi advertises `_atlas._tcp` (TXT: device id, TLS key pin, API path) and
  `_https._tcp` on 443, not pinned to one interface name, with docker0 denied.
- **LAN beacon v2** (UDP 5050): replies with id, name, pin, port 443 and HTTPS URLs; the manifest
  is cached for 5 s; replies are rate-limited per source and it never runs `nmcli` per probe.
- **Hotspot:** gateway `10.42.0.1` is always tried. A /24 scan runs on request.
- **Apps:** they remember Atlas by device id + key pin + token, never by address, and confirm any
  sighting with the pinned key. The same id with a different key produces a security warning, not
  a connection.

## 4. Pairing architecture
- **Proofs:**
  - the label setup code (PBKDF2, 5 tries per IP / 20 global per 10 min);
  - the label QR code;
  - a connection over Atlas's hotspot (nginx passes `$server_addr`);
  - approval on a paired device, with a 4-digit match code derived from the SPKI both sides saw.
- **Tokens:** 32-byte random, stored as SHA-256. Browsers get an `HttpOnly; Secure;
  SameSite=Strict` cookie with `Max-Age`; apps keep a bearer token in Keystore/Keychain.
- **Socket.IO:** connect is gated the same way; revoking a device closes its sockets.
- **Limit:** typing the setup code trusts the first key seen (QR or approval avoid this).

## 5. Per-device identity and secrets
`/etc/atlas/identity/` is created at provisioning, or adopted on a migrated unit. It holds:
- `device.json` — id, `Atlas-XXXX` name, `atlas-xxxx` hostname, module serial for clone
  detection;
- an ECDSA P-256 key and certificate (2024–2099, clock-proof);
- `hotspot.json` (random WPA2 password);
- `setup_code` (root only) and its hash;
- `proxy_secret` and the nginx include.

It is never in the payload; cloned storage gets a new identity.

## 6. Mobile connection changes (Android `b04818e`, iOS `3888984`)
- **TLS:** trust-all, cleartext, user CAs and ATS arbitrary loads are removed. SPKI pinning
  applies in the native clients and in the WebView (`onReceivedSslError` / `WKNavigationDelegate`
  compare the leaf SPKI).
- **Onboarding:** QR scan (CameraX + ZXing / AVFoundation, offline), plus discovery via Bonjour,
  beacon v2 and hotspot. Credentials are stored in Keystore / Keychain.
- **Reconnect:** rediscovery by id after IP or network changes. During updates the apps show
  "Atlas is installing an update and restarting." and then "Update complete.".
- **iOS tracker:** opt-in and foreground only, throttled to 30 s / 50 m, and stopped when not
  sharing.
- **Old app builds stop working** once pairing is enforced.

## 7. Updates

### Release signing
- Ed25519 with an offline, encrypted primary key plus an escrowed backup.
- `system/keys/keyring.json` ships empty, so there is no production trust yet.
- The signature covers `ATLAS-MANIFEST-V1\0` + manifest.
- `seq` is the build's epoch seconds, which provides anti-downgrade.
- The keyring upgrades on commit and supports revocation.
- Developer keys are honoured only with `/etc/atlas/dev-mode`.

### Verification
- Signature, product, arch, python, `seq` > installed, and `min_engine`.
- Whole-bundle and 8 MiB per-chunk SHA-256.
- Checked by the app before hand-off, and again by root after copying into root-owned space.

### Progress
- The app states are idle → checking → available → downloading ⇄ paused → verifying → ready →
  installing → up_to_date/failed.
- Engine stages are relayed from `/run/atlas-update/status.json`.
- Socket.IO `update_progress` carries exactly the fields the spec lists. One bar is drawn in
  place; refreshing or closing the page is harmless.
- nginx serves `/update-status.json` and a static page while the app restarts.

### Download resume
- `Range` requests against the stable github.com URL, never the expiring redirect.
- `206` is accepted only with a matching `Content-Range`; a `200` restarts cleanly.
- A `.part` + sidecar pair survives restarts.
- The finished file is hashed chunk by chunk, so any damage — including bytes written before a
  crash — is re-fetched from the first bad 8 MiB chunk (up to 3 repairs), never installed.
- Offline triggers "Download paused — waiting for Internet connection." and automatic resume
  with backoff.

### Installation
- A root engine in `/usr/local/lib/atlas`, root-owned and not writable by `atlas`.
- It keeps a journaled transaction and extracts safely: regular files only, no `..`, links,
  setuid or bombs.
- venvs are built offline from wheels, then `STAGED`.
- System files (units, nginx, firewall, udev, avahi) are installed with validation and immediate
  restore on failure.
- The `current` symlink swap is atomic.

### Health check and rollback
- **Health check** (180 s budget, 30 s stable): version, release path of the running PID,
  database, schema window and restart count, checked both on loopback and via nginx with the
  pinned certificate.
- **Rollback** restores system files and `current`, restores the pre-migration DB copy when the
  target is below `compat_floor`, and re-checks. If the previous release is also unhealthy it
  falls back to `factory` (3 tries), then "Atlas needs attention".

### Power-loss recovery
`atlas-update-recover.service` runs at boot:
- states 1–6 are cleaned up, keeping the download;
- state 7 restores system files;
- states 8–9 count boots and roll back after more than 2;
- 10–11 finish.

Tested with a fault injected at every journal state, in a sandbox.

### Database safety
- `PRAGMA user_version` with ordered additive migrations:
  1. baseline;
  2. pairing tables;
  3. `audit_log` plus indexes on `ai_messages.created_at` and `tracker_devices.last_seen`.
- A `VACUUM INTO` backup is taken before migrating (last 3 kept), and `atlas_meta.compat_floor` is
  recorded.
- `embed_format_v` uses "less than" semantics, so no re-embed happens on rollback.
- No destructive migrations; customer data is preserved.

## 8. Installation, factory provisioning and manufacturing
- **Payload drive.** `tools/manufacturing/atlas-capture.sh --release DIR` stages only a release
  that the golden unit's engine verifies (`atlas-engine verify`), plus its unpacked kit. It strips
  every per-unit file with `--delete-excluded`: DB, hotspot config, Wi-Fi hints, secret, DB
  copies, updates, caches, the source checkout and worktrees.
- **Per unit.** `atlas-provision.sh` copies the payload, then runs the kit's
  `install.sh --bundle stage/release --labels …`, which:
  1. installs packages and skips present datasets;
  2. runs `atlas-migrate --provision`: service account, engine, fresh identity, release, system
     files, health check, auto-rollback;
  3. starts the firewall;
  4. writes the label (Wi-Fi QR + app QR + name/hotspot/setup code/address, files 0600);
  5. runs `finalize-retail.sh`: locks setup-account passwords, masks SSH, removes admin from the
     `atlas` group, unpins mDNS, and checks there are no sudo rules, the firewall is on and :5000
     is on loopback only.

  `install.sh` never pulls code, writes sudo rules, creates a shared certificate or sets a
  default password (enforced by a test).
- **Existing units:** `tools/migrate/atlas-migrate apply|rollback`. Preflight now proves the
  firewall loads before anything changes; a new bundle after an early failure restarts from
  preflight.
- **Release build:** `lock.py` → `build_release.py` → `sign_release.py` (offline) →
  `verify_release.py` → `publish.sh` (draft → upload → download and verify → publish).
  Developer units use `tools/dev/deploy`.

## 9. Tests and results
- `tests/` has 24 modules and 211 test functions, including:
  - request guard, pairing (HTTP + Socket.IO), privacy/retention/confirmations/audit;
  - update manager (resume, pause, corruption, refresh, restart, duplicate start, HTTPS
    required), update engine (signatures, downgrade, tar attacks, space, fault injection at every
    state, recovery, rollback);
  - migration rehearsal (apply, auto-rollback, rollback, reapply, provision, bad signature,
    firewall preflight);
  - system components (helper validation, identity, firewall, sysfiles), safe math, untrusted
    fencing;
  - discovery payloads, vector store, knowledge map, Ray context and token budget, GPS events,
    stats reader and push, frontend page/CSP, manufacturing.
- `tests/js/safe_href.test.js` has 28 cases.
- **Latest run: 207 passed, 4 skipped, 0 failed; JS 28/28.** Two intermittent failures found
  during the work were test defects, and both are fixed: a corruption helper that wrote `X`
  (a no-op 1 time in 256), and a wall-clock assertion.
- **Static checks:**
  - `compileall` clean; `bash -n` clean on every script;
  - pyflakes: no new warnings (3 pre-existing unused locals and a few pre-existing unused
    imports remain on files this branch touched);
  - greps: no `eval`/`exec`, no trust-all TLS or cleartext, no default credentials outside the
    refuse-list, no sudoers writer, no `0.0.0.0` bind.

**Benchmarks** (`tools/bench/bench.py`; before = the live legacy service, after = this branch on a
copy of the unit's database, AI and radio off in both):

| | before | after |
|---|---|---|
| Knowledge map | 6002 ms | 217 ms cold, 4.5 ms cached |
| Slowest other request during a map build | 6036 ms | 119 ms cold, 5.7 ms cached |
| RAG passage scoring (364 passages) | ~49 ms | 0.6 ms |
| Render-blocking scripts on first load | 8 | 0 |
| First-load bytes | 1.96 MB | 1.73 MB (KaTeX on demand) |
| Stats p50 / cold | 2.7 ms / ~1.8 s every expiry | 2.6 ms / ~1 s once, then pushed |
| Server RSS (AI off) | — | 120–124 MB |

## 10. Remaining verification (needs the migration on this unit)
Run once, as you (it prompts for nothing else):
```
sudo /atlas_data/atlas-control-wt/tools/migrate/atlas-migrate apply \
     --bundle /atlas_data/atlas-control-wt/dist/1.0.0-dev.202609252347 \
     --dev-key /home/admin/.config/atlas-dev/dev-release.pub
```
Then, on the device:
- `ss` shows Flask on loopback only, and the firewall chain is live with SSH still reachable
  (dev mode);
- XFF, CSRF, foreign Origin and Host requests are refused;
- pairing with the setup code from `sudo /usr/local/lib/atlas/bin/atlas-identity label-json`;
- Ray/RAG, maps, navigation, GPS and Meshtastic run as `atlas`;
- update end-to-end with `tools/dev/deploy`: network cut mid-download, refresh, a bad signature,
  a deliberately unhealthy build (rollback), kill and reboot at journal states;
- browser reconnect after restart and update;
- the phone apps once built (Android Studio / Xcode).

Until then, items 9–33 of the spec's final verification are verified in tests and sandboxes, not
on this device.

## 11. Remaining risks
- The mobile apps are not compiled here, and old app builds stop working after pairing is
  enforced.
- Browsers show a one-time certificate warning (a per-device self-signed cert; no offline
  alternative for `.local`).
- The NVMe is not encrypted, so the drive can be read if removed.
- Power loss was tested as a simulated crash (and planned sysrq reboots), not bench power cutting.
- **Production keys do not exist yet.** Generate the primary and backup offline, add them to the
  keyring, and ship the first production release before manufacturing. A retail unit cannot
  install anything until then (fail closed).
- The typed-setup-code TOFU gap (§4).
- `finalize-retail.sh` locks setup-account passwords. If units boot to a desktop with autologin,
  that session remains (without sudo); decide whether retail units should boot to
  `multi-user.target`.

## 12. Before manufacturing
1. Generate production keys offline (`tools/release/keygen.py`, primary + escrowed backup) and
   add both public keys to `system/keys/keyring.json`.
2. Merge the branch, then build, sign, verify and publish the first production release
   (`docs/RELEASES.md`).
3. Migrate the golden unit, run the acceptance test on it, then run
   `atlas-capture.sh --release dist/<version>`.
4. Build the Android and iOS apps from `mobile/` and publish them before the units ship.
5. Update `/opt/atlas-imaging` from `tools/manufacturing` (the kit on each drive already comes
   from the release).

## Explicit confirmations
Where a claim is proven in tests or sandboxes but not yet on this device, that is stated.

- **Works fully offline after provisioning:** yes. Updates are the only network feature, and they
  fail gracefully offline.
- **No user accounts:** yes.
- **No device PIN or password:** yes; pairing is a one-time proof of possession.
- **Customers never need SSH, a terminal or GitHub:** yes. SSH is off in retail, updates run
  from the UI, and recovery is automatic.
- **Customers normally don't need the IP address:** yes (label QR, mDNS, beacon, hotspot).
- **Mobile clients discover Atlas automatically:** yes by design and in code. The apps are not
  compiled here.
- **Reconnects after IP or network changes where possible:** yes (rediscovery by id + pin).
  Needs on-device and app verification.
- **Updates start from the Atlas UI:** yes.
- **Downloads show a real live progress bar:** yes (bytes-based, pushed live).
- **Interrupted downloads resume:** yes (HTTP Range, tested against faulty servers).
- **Updates are cryptographically verified:** yes (Ed25519; app and root engine both verify).
- **Root-executed updater code can't be modified by the app user:** yes. The engine, keyring and
  releases are root-owned read-only, and the app has no sudo and `NoNewPrivileges`. Proven in
  sandbox; to be confirmed with `find … ! -user root` after migration.
- **Failed releases roll back automatically:** yes (health check → rollback → factory fallback).
- **Interrupted updates recover without Linux knowledge:** yes (boot recovery unit, one-tap
  retry).
- **Clients reconnect automatically after an update:** yes (browser status page + reconnect; apps
  update-aware).
- **Factory-new units ship without universal secrets:** yes. Per-unit identity, hotspot password,
  setup code and TLS key; nothing per-unit on the payload drive; setup-account passwords locked.

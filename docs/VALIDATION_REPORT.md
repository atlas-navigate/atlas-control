# Validation report: installer modernization (2026-10-19)

This report covers commits 3e106cf, 3dc1db5, 22cebbf, b88c7a8, 29c0511 and
f41a5a7 on `main`. The changes themselves are described in
[INSTALLER_AUDIT.md](INSTALLER_AUDIT.md).

Every result below was run and its output read. Anything not run is listed
under [Not validated](#not-validated) and is not counted as a pass.

## 1. Automated tests: pass

The full suite was run in two halves to fit the unit's free memory, with
`venv/bin/python -m pytest -q -p no:cacheprovider`:

| Run | Result |
|---|---|
| First 19 test files | 240 passed, 4 skipped, 0 failed (79.5 s) |
| Last 19 test files | 209 passed, 0 failed (57.0 s) |
| **Total** | **449 passed, 4 skipped, 0 failed** |

The 4 skips are all in `test_geocode.py`: the GeoNames source files
(`US.txt`, `cities5000.txt`) are not fetched into the temporary test state.
They have nothing to do with this work.

There are 51 new tests in two files:

- **`tests/test_installer.py` (21).** `install.sh` is run for real in an
  `ATLAS_ROOT` sandbox, with stub `systemctl`, `apt-get`, `dpkg`, `ollama`,
  `curl`, `wget` (offline), `nvpmodel`, OSRM and so on, and stdin closed. The
  scenarios:
  - a fresh unattended install, then a re-run whose file snapshot is
    unchanged: no rewrites, no new fstab line, and Ollama not restarted;
  - a newer release, handed to the update engine;
  - a bad signature, the wrong Python and the wrong OS, each stopped in
    preflight before anything changes;
  - an interrupted install that completes when run again;
  - a failed `atlas-engine check`, which fails the install;
  - missing packages, installed with retries;
  - an unknown state, refused before any work, and a states list;
  - `--only` and `--skip`;
  - a retail lock-down failure, which fails the install;
  - `--help`;
  - source checks: every stage parses, the numbering is in order, and no
    unpinned installer or legacy privileges.

  The file also covers `handoff.py` as a real process against a fake
  atlas-helper (success, rollback, refusal, a stale result) and
  `kit_sysfile.py`.
- **`tests/test_conformance.py` (30).**
  - INI settings: they apply, are idempotent, keep their place and restore
    on rollback. A missing avahi package is skipped.
  - The sysctl kind refuses a malformed line and leaves nothing changed.
  - zram and the memory policy move into the release, and the old paths are
    removed.
  - The sandbox drop-ins ship with the release.
  - Power mode is found by name. It switches from the JetPack default,
    skips a module without the mode or a unit that isn't a Jetson, and an
    nvpmodel failure never fails an install.
  - `reapply` puts a drifted file back and waits for a running update.
    Diagnostic logs are pruned.
  - `check` passes a matching unit, names drift with its fix, reports
    listeners outside the policy, checks the power mode and prints JSON.
  - The inference check uses the app's load options, never loads a model
    that is not loaded, and reports a reload.

Two defects were found by these tests and fixed before the commits:

1. **A real installer bug.** In stage 80, `_states_present` returned 1 under
   `pipefail` (a `for …; [[ ]] && echo` loop piped to `sort`). That would
   have aborted the stage on a unit with no routing data yet.
2. **A test-isolation problem.** Once an earlier test file had loaded the app
   and gevent had patched threads, an in-process fake helper thread failed.
   The handoff tests now run `handoff.py` and the fake helper as separate
   processes, which is also how install.sh and deploy run it.

## 2. Checks on this unit before deploying: pass

| Check | Result |
|---|---|
| `atlas-engine check`, repo engine against the unit as it was | 25 ok, 1 warning: Qwen3.5 83% on the GPU (33 of 34 layers) |
| `migrate.py check` on the real bundle (`~/atlas-releases/1.0.0-dev.20261019201402`) | OK with the dev key. Without it: "no release-signing keys are installed", status 1 |
| `systemd-analyze verify` on every rendered release unit | Clean, apart from the zram helper's live path, which did not exist yet (expected before install) |
| `udevadm verify` on the udev rules | Clean |
| Frontend build (`compile_jsx.js`) | Compiles (455 KB) |
| `systemd-analyze security --offline` on the rendered units, HEAD and new | ollama 9.0 → 6.1, nginx 9.4 → 5.7, atlas-control 7.4 → 3.8 |

## 3. Deploy through the real update path: pass

The deploy ran `sg atlas -c tools/dev/deploy`, which builds and signs a
developer release and hands it to the update engine.

- 1.0.0-dev.20261019201208 → **1.0.0-dev.20261020011143**, status
  `success`, exit 0.
- **Engine 3 → 4 self-switch, exercised for real.** The journal shows
  `re-executing engine 4`, and `/usr/local/lib/atlas/engine/current → 4`,
  `previous → 3`.
- The journal shows `power mode MAXN_SUPER: already active`, so nothing
  changed.
- `/api/health` reports the new version and `ok: true`.

## 4. The unit after deploying: pass

`atlas-engine check --infer` gives **31 ok, 0 warnings, 0 failed, 1
skipped** (the retail checks, because this is a developer unit), exit status 0.

- **Files:** the release's 28 files and its avahi settings match. The old
  `/usr/local/sbin/atlas-zram` and `/etc/sysctl.d/99-zram.conf` are gone.
- **Units and services:** all 7 release units are enabled, and atlas-control,
  nginx, ollama, avahi, the firewall, the helper socket and zram are active.
- **Kernel and swap:** MAXN_SUPER. `vm.swappiness = 100` and
  `vm.vfs_cache_pressure = 200`. zram and the disk swapfile are active.
- **Ollama:** 0.32.1, and all 8 drop-in settings are in effect
  (`LLAMA_ARG_FIT_TARGET=256` among them). All 3 models are installed.
- **Ray:** `qwen3.5:4b` is **100% on the GPU**
  (`size_vram == size == 3,142,131,383`). The journal reports `offloaded
  34/34 layers to GPU`. The test answer ran at 14.3 tokens/s.
- **Network:** the firewall is on, only 22, 53, 80 and 443 listen, and port
  5000 is on loopback only.
- **Identity:** Atlas-PJNT answers as `atlas-pjnt.local`, and 244 GB is free.

The live sandbox scores (`systemd-analyze security`) match the offline ones:
ollama 6.1, nginx 5.7, atlas-control 3.8. Ollama ran the benchmark under its
new sandbox, and its journal since the restart has no errors or denied
accesses.

mDNS after the avahi settings, checked through avahi's D-Bus API (avahi-utils
is not installed):

- The `Atlas-PJNT` `_atlas._tcp` service resolves on `wlP1p1s0`
  (100.110.198.218), `l4tbr0` (192.168.55.1) and `lo`, at port 443 with its
  TXT record.
- Nothing is published on `docker0` (`deny-interfaces`). The old
  `allow-interfaces=wlP1p1s0,l4tbr0` pin is commented out.
- Queries over IPv6 get no answer (`use-ipv6=no`), as intended.

## 5. Benchmark: measured, not assumed

The command was `ray_bench.py direct decode --model qwen3.5:4b --runs 5`, the
same before (18:26) and after (21:15) the deploy. It used the same options
(ctx 4096, batch 512, 256 tokens, seed 11) and Ray-sized prompts of 2.3–3.2k
tokens, in MAXN_SUPER. The files are
`tools/bench/results/install-{before,after}-direct-qwen3.5.json`.

| Median of 5 | Before (33/34 layers) | After (34/34) |
|---|---|---|
| Generation | 13.0 tok/s | **14.5 tok/s (+11.5%)** |
| Prompt processing | 435 tok/s | 445 tok/s (+2%, noise) |
| Prompt time, i.e. first token at Ollama | 5.51 s | 5.38 s |
| Whole request | 26.3 s | 24.2 s (−8%) |
| GPU load / CPU | 78% / 14% | 87% / 7.5% |
| Board power | 16.0 W | 16.7 W |
| Hottest zone | 62.5 °C | 63.0 °C |
| Lowest MemAvailable | 705 MB | 509 MB |

- **The gain is generation, not prompt processing.** An earlier version of
  the Ollama drop-in comment and of commit 3e106cf's message said prompts
  were "~3× slower" with 33 layers. That compared against a prompt rate
  measured differently (Ollama's own count, which includes cached tokens).
  The measured A/B above does not support it, and the comment and docs now
  give the measured figures.
- **Memory.** The journal shows the same model, KV and compute buffers in
  both loads, apart from the one 66 MiB layer that moved to the GPU. So
  the lower MemAvailable minimum is not the setting itself. The "before"
  runner had been up for five hours and was partly swapped out, while the
  "after" runner had just loaded. That explanation was not isolated, and
  the kernel log shows no OOM-killer events since the deploy.
- **End-to-end time to first token through Ray's pipeline** (`e2e
  --inproc`) and `models.py` were not re-run, because no baseline was taken
  for them. Of the things measured, nothing in Ray's pipeline changed apart
  from the GPU fit and the Ollama sandbox.
- **Power mode, run by the owner** (`install-25w-direct-qwen3.5.json`, the
  same benchmark at 25W): generation 13.4 tok/s, prompt processing 423
  tok/s, whole request 25.9 s. The GPU averaged 879 MHz and the board drew
  15.5 W. MAXN_SUPER is 8% faster at generating (14.5 tok/s), 5% faster
  at prompts and 7% quicker per request, with the GPU at 1,020 MHz, for
  about 1.2 W more. This is what the release's `power_mode` gives a unit
  that JetPack left at its 25W default.
- **Qwen2.5 3B** was not re-benchmarked. It was already 100% on the GPU, so
  the margin setting doesn't change its placement. Loading it would have
  evicted Ray's current model.

## 6. Acceptance runs on this unit (2026-10-19)

The owner ran `sudo tools/dev/acceptance run` after a real `sudo ./install.sh
--bundle … --dev-key … --yes --states keep` re-run. After that re-run,
`atlas-engine check` showed 30 ok and 0 failed, and Ollama restarted twice.
The second restart was expected: the checkout's Ollama drop-in was one
comment newer than the release that was running.

**Second run, after the fixes below:**
`/var/log/atlas-acceptance/20261019-223514.txt`: **63 passed, 0 failed.**

- The checks that had failed now pass. The tool sees "Updated to Atlas …"
  and the rollback message with both versions.
- Crash recovery at HEALTH_CHECK and COMMITTING now runs and passes. Each
  finished the install (`success`).
- The unit was left on `1.0.0-dev.20261020023355.final`, built from this
  checkout.
- The model section ran on the release the first run had left installed,
  which predates 8042646. So its stored speeds (Qwen2.5 24.6, Qwen3.5 24.3
  tokens/s) are still timed from a one-word answer and are wrong for
  Qwen3.5 (14.5 measured). The installed release has the fix. A model
  switch on it has not been observed yet.

**First run:** `/var/log/atlas-acceptance/20261019-220936.txt`: **57 passed,
3 failed.**

- **Passed:**
  - **Security:** the app's exposure scored 3.8.
  - **Function:** the GPS answered, without a fix indoors. The radio was not
    connected.
  - **Models:** both models are installed with a 4,096-token context.
  - **Download:** a resumed download completed, and the engine verified and
    installed it.
  - **Forged release:** it was refused ("Update could not be verified.").
  - **Unhealthy release:** it rolled back automatically after 254 s, and the
    previous version was serving again in a consistent state.
  - **Faults:** a crash at each state from ACCEPTED through SWITCHED was
    recovered.
  - Pairing, key and AI model survived every scenario, and the unit ended on
    a fresh release.
- **Failed, all in the tool rather than in Atlas:**
  - The download and rollback checks still expected the update messages
    from before b88c7a8. Atlas showed the new ones: "Updated to Atlas …" and
    "The update to Atlas … could not be completed, so Atlas went back to …".
  - The fault section stopped at HEALTH_CHECK. Its release tag
    (`faults-health_check`) is not a valid version, so HEALTH_CHECK and
    COMMITTING did not run.

  The tool now builds its expected messages with functions that a test checks
  against update_manager's real output. Tags are cleaned up and capped so
  they are always valid versions, and a test covers every fault state.

An earlier attempt the same evening had stopped before any release section.
That was caused by two other faults in the tool, both dating from 2026-09-25
and fixed in 959fc7d:

- `git stash create` fails once after `install.sh` touches every file in the
  checkout.
- The tool used an `http://` bundle URL, which the engine refuses.

## Not validated

These were not run, so none of them counts as a pass.

| Item | Why | How to validate |
|---|---|---|
| That an `install.sh` re-run skips every stage | The owner ran it (2026-10-19, before the acceptance runs). Afterwards `atlas-engine check` showed 30 ok and 0 failed. Ollama restarted twice, which was expected (§6). The per-stage output is in the root-only `/var/log/atlas-install/` log, which was not read | Re-run after a deploy, so that the checkout and the release match, and read the log |
| The power-loss reboot test | `acceptance run` passed (63 of 63, [§6](#6-acceptance-runs-on-this-unit-2026-10-19)). The reboot test needs a reboot | `sudo tools/dev/acceptance reboot-test`, then `after-reboot`, then `atlas-engine check` after a plain reboot |
| A fresh flash on a new Orin Nano | Hardware | Follow MANUFACTURING.md on a blank unit. `install.sh` ends with `atlas-engine check` |
| Retail lock-down effects (headless boot, masked services) | Only on a non-dev unit | Covered by sandbox tests and `finalize-retail.sh --check` logic. Not run on hardware |
| Factory reset | Sandbox only, by decision (a real one would unpair this unit) | Existing privacy and pairing tests |
| Phone pairing and mDNS from iOS/Android | Needs phones | Scan the label in each app after the deploy |
| Mesh radio and GPS, including `ID_MM_DEVICE_IGNORE` with ModemManager present | Hardware | Plug in the Heltec and u-blox, then `udevadm info` shows `ID_MM_DEVICE_IGNORE=1` |
| Power-mode switching on hardware | This unit was already MAXN_SUPER | Covered by the sandbox tests above. The A/B step above exercises it for real |

Found in passing and not changed: `/usr/local/lib/atlas/engine/` still holds
engines 1 and 2. The engine keeps `current` and `previous` and never removes
older ones. They take 328 KB and 352 KB, and nothing loads them.

"""install.sh, run for real in an ATLAS_ROOT sandbox; and the tools it shares
with tools/dev/deploy (handoff.py) and the update engine (kit_sysfile.py).

System commands (apt-get, systemctl, swapon, ollama, osrm-*, curl, chown …)
are stubs first on PATH that record their arguments; the release's own tools
run through a stand-in for the system Python that records migrate.py apply /
handoff.py and passes everything else to the real interpreter. stdin is
closed, as under atlas-provision.sh or systemd-run. Datasets are pre-placed,
as a payload drive brings them, so no stage downloads anything.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from engine_sandbox import Sandbox

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER = os.environ.get("USER") or __import__("pwd").getpwuid(os.getuid()).pw_name

STUB = r'''#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0]); args = sys.argv[1:]
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "calls.jsonl"), "a") as f:
    f.write(json.dumps([name] + args) + "\n")
fail = os.environ.get("FAKE_FAIL", "").split(",")
def swaps():
    try:
        return open(os.path.join(state, "swaps")).read().split()
    except OSError:
        return []
if name in fail:
    sys.exit(1)
if name == "systemctl":
    if args[:1] == ["is-active"]:
        print("active")
    elif args[:1] == ["is-enabled"]:
        print("enabled")
    elif args[:1] == ["get-default"]:
        print("multi-user.target")
elif name == "swapon":
    if any(a.startswith("--show") for a in args):
        for s in swaps():
            print(s + (" 3.7G" if "SIZE" in " ".join(args) else ""))
    elif "--priority" in args:
        open(os.path.join(state, "swaps"), "a").write(args[-1].replace(os.environ["ATLAS_ROOT"], "") + "\n")
elif name == "dpkg":
    missing = os.environ.get("FAKE_DPKG_MISSING", "").split(",")
    sys.exit(1 if args[-1] in missing else 0)
elif name == "ollama":
    if args[:1] == ["--version"]:
        print("ollama version is 0.32.1")
elif name == "osrm-routed":
    print("osrm-routed 5.27.1\n  --mmap arg (=0)   Map all files to memory")
elif name == "curl":
    url = next((a for a in args if a.startswith("http")), "")
    sys.exit(0 if "127.0.0.1:11434" in url else 1)     # Ollama answers; the Internet is not there
elif name == "wget":
    sys.exit(4)                                         # offline
elif name == "sudo":
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] == "-u" else 1
    os.execvp(args[i], args[i:])
'''

FAKE_PYTHON = r'''#!/bin/bash
# The system Python 3.12 as install.sh calls it: the release's tools run for
# real, except the ones that would install Atlas (recorded, their effect faked).
echo "$(printf '%q ' python3 "$@")" >> "$FAKE_STATE/python.log"
args=" $* "
R="$ATLAS_ROOT"
if [[ -n "${FAKE_PYV:-}" && "$args" == *version_info* ]]; then echo "$FAKE_PYV"; exit 0; fi
if [[ "$args" == *"migrate.py check"* ]]; then
    [[ -z "${FAKE_UNTRUSTED:-}" ]] || { echo "release not trusted: bad signature" >&2; exit 1; }
    echo "[atlas-migrate] release OK"; exit 0
fi
if [[ "$args" == *"migrate.py apply"* ]]; then
    [[ -z "${FAKE_MIGRATE_FAIL:-}" ]] || { echo "step cutover failed: simulated" >&2; exit 1; }
    mkdir -p "$R/opt/atlas/releases/1.2.3" "$R/var/lib/atlas-update" "$R/usr/local/lib/atlas/bin"
    ln -sfn releases/1.2.3 "$R/opt/atlas/current"
    echo '{"version": "1.2.3", "seq": 5}' > "$R/var/lib/atlas-update/installed.json"
    cp "$FAKE_STATE/atlas-engine" "$FAKE_STATE/atlas-identity" "$R/usr/local/lib/atlas/bin/"
    exit 0
fi
if [[ "$args" == *"handoff.py"* ]]; then echo "    success: Atlas is up to date"; exit 0; fi
exec /usr/bin/python3 "$@"
'''

ENGINE = r'''#!/bin/bash
echo "atlas-engine $*" >> "$FAKE_STATE/calls.jsonl"
case "$1" in
    version) echo 4 ;;
    check) [[ -z "${FAKE_CHECK_FAIL:-}" ]] || { echo "[FAIL] power mode MAXN_SUPER"; exit 1; } ;;
esac
'''

IDENTITY = r'''#!/bin/bash
case "$1" in
    show) echo '{"name": "Atlas-TEST", "hostname": "atlas-test"}' ;;
esac
'''

STUBS = ("systemctl", "apt-get", "dpkg", "swapon", "swapoff", "mkswap", "dd", "sysctl", "modprobe", "ollama",
         "osrm-routed", "osrm-extract", "osrm-partition", "osrm-customize", "curl", "wget", "chown", "useradd",
         "usermod", "rsync", "nmcli", "osmium", "smartctl", "bluetoothctl", "sudo", "dtc", "ldconfig", "sleep")


class Unit:
    """A sandbox that looks like a freshly flashed Orin Nano with a payload drive's datasets."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-install-")
        self.root = os.path.join(self.tmp, "root")
        self.state = os.path.join(self.tmp, "state")
        self.bin = os.path.join(self.tmp, "bin")
        for d in (self.root, self.state, self.bin):
            os.makedirs(d)
        stub = os.path.join(self.bin, "_stub")
        self._write(stub, STUB, 0o755)
        for name in STUBS:
            os.symlink(stub, os.path.join(self.bin, name))
        self.python = os.path.join(self.bin, "_python3")
        self._write(self.python, FAKE_PYTHON, 0o755)
        self._write(os.path.join(self.state, "atlas-engine"), ENGINE, 0o755)
        self._write(os.path.join(self.state, "atlas-identity"), IDENTITY, 0o755)
        self._write(os.path.join(self.state, "swaps"), "/dev/zram0\n")
        r = self.p
        self._write(r("/etc/os-release"), 'ID=ubuntu\nVERSION_ID="24.04"\n')
        self._write(r("/etc/nv_tegra_release"), "# R39 (release), REVISION: 2.1, GCID: 1\n")
        self._write(r("/proc/device-tree/model"), "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super\0")
        self._write(r("/etc/fstab"), "/dev/root / ext4 defaults 0 1\n")
        os.makedirs(r("/usr/local/lib/ollama"))
        os.makedirs(r(f"/home/{USER}"))
        app = r("/atlas_data/atlas-control")
        for rel, data in (("static/tiles/map.pmtiles", "x"), ("static/tiles/topo.pmtiles", "x"),
                          ("static/data/us_cities.db", "x"), ("static/data/nps_trails.db", "x"),
                          ("static/data/places.db", "x"), ("data/US.txt", "x"), ("data/cities5000.txt", "x"),
                          ("osrm-data/states/maryland/car/.processed", ""),
                          ("osrm-data/states/maryland/hiking/.processed", ""),
                          ("atlas-swap.swap", "")):
            self._write(os.path.join(app if not rel.startswith("atlas-swap") else r("/atlas_data"), rel), data)
        for i in range(256):
            self._write(os.path.join(app, "static/fonts/Noto Sans Regular", f"{i * 256}-{i * 256 + 255}.pbf"), "")
        self._write(os.path.join(app, "pmtiles"), "#!/bin/sh\n", 0o755)
        self._write(os.path.join(app, "venv/bin/python3"), "#!/bin/sh\nexit 0\n", 0o755)
        self._write(r("/usr/local/share/osrm/profiles/car.lua"), "")
        os.makedirs(r("/usr/local/share/osrm/profiles/lib"))
        self.bundle = os.path.join(self.tmp, "release")
        self._write(os.path.join(self.bundle, "atlas-release.json"), json.dumps({"version": "1.2.3", "seq": 5}))
        self._write(os.path.join(self.bundle, "atlas-release.json.sig"), "sig")
        self._write(os.path.join(self.bundle, "atlas-1.2.3.tar.xz"), "bundle")
        self.dev_key = os.path.join(self.tmp, "dev.pub")
        self._write(self.dev_key, "key")

    def p(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    @staticmethod
    def _write(path, text, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def run(self, *args, env=None, dev=True):
        cmd = ["bash", os.path.join(REPO, "install.sh"), "--bundle", self.bundle, *args]
        if dev:
            cmd += ["--dev-key", self.dev_key]
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": self.tmp, "SUDO_USER": USER, "LANG": "C.UTF-8",
             "ATLAS_ROOT": self.root, "ATLAS_SYS_PYTHON": self.python, "FAKE_STATE": self.state}
        e.update(env or {})
        open(os.path.join(self.state, "calls.jsonl"), "w").close()
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=e, timeout=300)
        r.calls = [json.loads(ln) if ln.startswith("[") else ln.split()
                   for ln in open(os.path.join(self.state, "calls.jsonl")).read().splitlines()]
        r.out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        return r

    def ran(self, r, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in r.calls)

    def snapshot(self):
        """Every file under the sandbox root but the install logs: path -> (sha256, mode)."""
        out = {}
        for d, _, files in os.walk(self.root):
            if "/var/log/atlas-install" in d:
                continue
            for name in files:
                path = os.path.join(d, name)
                if os.path.islink(path):
                    out[path] = ("link", os.readlink(path))
                    continue
                with open(path, "rb") as f:
                    out[path] = (hashlib.sha256(f.read()).hexdigest(), os.stat(path).st_mode)
        return out

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.u = Unit()
        self.addCleanup(self.u.cleanup)

    def assertOk(self, r):
        self.assertEqual(r.returncode, 0, r.out[-4000:])

    def test_fresh_install_unattended_then_a_rerun_changes_nothing(self):
        u = self.u
        r = u.run()
        self.assertOk(r)
        self.assertNotIn("States to install", r.out)           # no terminal: never a prompt
        self.assertIn("Atlas-TEST is ready.", r.out)
        self.assertTrue(any("migrate.py apply" in ln and "--provision" in ln
                            for ln in open(os.path.join(u.state, "python.log"))))
        # Release-owned files placed early, exactly as the update engine renders them.
        self.assertTrue(os.access(u.p("/usr/local/lib/atlas/bin/atlas-zram"), os.X_OK))
        self.assertIn("ExecStart=/usr/local/lib/atlas/bin/atlas-zram start",
                      open(u.p("/etc/systemd/system/zram-swap.service")).read())
        self.assertIn("vm.swappiness = 100", open(u.p("/etc/sysctl.d/99-atlas-memory.conf")).read())
        self.assertFalse(os.path.exists(u.p("/etc/sysctl.d/99-zram.conf")))
        base = open(u.p("/etc/systemd/system/ollama.service")).read()
        self.assertIn("User=ollama", base)
        self.assertIn('Environment="OLLAMA_MODELS=/atlas_data/ollama/models"', base)
        self.assertNotIn("OLLAMA_KV_CACHE_TYPE", base)         # tuning lives in the release's drop-in
        dropin = open(u.p("/etc/systemd/system/ollama.service.d/atlas.conf")).read()
        self.assertIn("LLAMA_ARG_FIT_TARGET=256", dropin)
        self.assertNotRegex(dropin, r"@[A-Z_]+@")
        self.assertEqual(open(u.p("/etc/fstab")).read().count("/atlas_data/atlas-swap.swap none swap"), 1)
        self.assertTrue(u.ran(r, "systemctl", "enable", "--now", "zram-swap.service"))
        self.assertTrue(u.ran(r, "atlas-engine", "check"))
        self.assertFalse(u.ran(r, "apt-get"))                  # every package already there
        self.assertFalse(u.ran(r, "wget"))                     # payload datasets: nothing downloaded
        logs = os.listdir(u.p("/var/log/atlas-install"))
        self.assertEqual(len(logs), 1)
        self.assertIn("Atlas-TEST is ready.", open(u.p("/var/log/atlas-install/" + logs[0])).read())

        before = u.snapshot()
        r2 = u.run()
        self.assertOk(r2)
        self.assertEqual(u.snapshot(), before)
        self.assertTrue(u.ran(r2, "atlas-engine", "reapply"))  # same release: re-apply, no re-install
        self.assertEqual(sum("migrate.py apply" in ln for ln in open(os.path.join(u.state, "python.log"))), 1)
        self.assertFalse(u.ran(r2, "systemctl", "restart", "ollama"))    # Ray is not reloaded for nothing
        self.assertFalse(u.ran(r2, "sysctl", "-q", "-p"))
        self.assertIn("Persistent disk swap already active", r2.out)
        self.assertEqual(len(os.listdir(u.p("/var/log/atlas-install"))), 2)

    def test_a_newer_release_goes_through_the_update_engine(self):
        u = self.u
        self.assertOk(u.run())
        u._write(os.path.join(u.bundle, "atlas-release.json"), json.dumps({"version": "1.3.0", "seq": 6}))
        r = u.run()
        self.assertOk(r)
        handoffs = [ln for ln in open(os.path.join(u.state, "python.log")) if "handoff.py" in ln]
        self.assertEqual(len(handoffs), 1)
        self.assertIn(u.bundle, handoffs[0])
        self.assertFalse(u.ran(r, "atlas-engine", "reapply"))

    def test_untrusted_release_stops_before_anything_is_changed(self):
        r = self.u.run(env={"FAKE_UNTRUSTED": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("not trusted or is damaged — nothing was changed", r.out)
        self.assertFalse(os.path.exists(self.u.p("/etc/systemd/system/zram-swap.service")))
        self.assertFalse(self.u.ran(r, "systemctl"))
        self.assertFalse(self.u.ran(r, "apt-get"))

    def test_wrong_python_stops_in_preflight(self):
        r = self.u.run(env={"FAKE_PYV": "3.10"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs the system Python 3.12", r.out)
        self.assertFalse(self.u.ran(r, "systemctl"))

    def test_wrong_os_stops_in_preflight(self):
        self.u._write(self.u.p("/etc/os-release"), 'ID=ubuntu\nVERSION_ID="22.04"\n')
        r = self.u.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs Ubuntu 24.04", r.out)

    def test_an_interrupted_install_completes_when_run_again(self):
        u = self.u
        r = u.run(env={"FAKE_MIGRATE_FAIL": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("The first install did not complete and was undone", r.out)
        self.assertNotIn("is ready", r.out)
        r = u.run()
        self.assertOk(r)
        self.assertIn("Atlas-TEST is ready.", r.out)

    def test_a_failed_check_fails_the_install_and_says_so(self):
        r = self.u.run(env={"FAKE_CHECK_FAIL": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not match its release", r.out)
        self.assertIn("installed but NOT ready", r.out)

    def test_missing_packages_are_installed_with_retries(self):
        r = self.u.run(env={"FAKE_DPKG_MISSING": "osmium-tool,sqlite3"})
        self.assertOk(r)
        installs = [c for c in r.calls if c[:2] == ["apt-get", "install"]]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0][-2:], ["osmium-tool", "sqlite3"])

    def test_unknown_state_is_refused_before_any_work(self):
        r = self.u.run("--states", "MD XX")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown state: XX", r.out)
        self.assertEqual(r.calls, [])

    def test_states_list_processes_only_whats_missing(self):
        r = self.u.run("--states", "md,va")
        self.assertOk(r)
        self.assertIn("Routing data for 2 state(s): MD VA", r.out)
        # Maryland is already built; Virginia must be downloaded (offline here: skipped, not fatal).
        self.assertTrue(any(c[0] == "wget" and "virginia-latest.osm.pbf" in c[-1] for c in r.calls))
        self.assertFalse(any(c[0] == "wget" and "maryland" in c[-1] for c in r.calls))
        self.assertIn("Download failed for VA — skipping", r.out)
        self.assertIn("VA/car failed — continuing", r.out)
        self.assertFalse(os.path.exists(self.u.p("/atlas_data/atlas-control/osrm-data/states/virginia/car/.processed")))

    def test_only_and_skip(self):
        r = self.u.run("--only", "models")
        self.assertOk(r)
        self.assertIn("══ Preflight ══", r.out)
        self.assertIn("══ Ollama Models ══", r.out)
        self.assertNotIn("══ System Packages ══", r.out)
        r = self.u.run("--only", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No such stage: nope", r.out)

    def test_a_retail_unit_that_cannot_be_locked_down_does_not_ship(self):
        r = self.u.run(dev=False)          # finalize-retail.sh refuses: the test is not root
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unit type          : retail", r.out)
        self.assertIn("Retail lock-down failed (see above) — this unit must not ship.", r.out)
        self.assertNotIn("is ready", r.out)

    def test_help(self):
        r = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for word in ("--states", "--yes", "98 verify", "55 uart"):
            self.assertIn(word, r.stdout)
        self.assertNotIn("╚", r.stdout)


class InstallerSourceTests(unittest.TestCase):
    FILES = [os.path.join(REPO, "install.sh"), os.path.join(REPO, "tools", "install", "lib.sh")] + sorted(
        os.path.join(REPO, "tools", "install", "stages", f)
        for f in os.listdir(os.path.join(REPO, "tools", "install", "stages")))

    def test_every_stage_parses(self):
        for path in self.FILES:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")

    def test_never_fetches_code_writes_legacy_privileges_or_pipes_an_unpinned_installer(self):
        for path in self.FILES:
            text = open(path).read()
            for bad in ("git pull", "git clone", "sudoers", "sbin/atlas-update", "NOPASSWD", "openssl req",
                        "0666", "avahi-daemon.conf", "/usr/local/sbin/atlas-zram"):
                self.assertNotIn(bad, text, f"{bad} in {path}")
        ollama = open(os.path.join(REPO, "tools", "install", "stages", "50-ollama.sh")).read()
        self.assertIn('OLLAMA_VERSION="$OLLAMA_TESTED"', ollama)

    def test_stage_numbers_are_unique_and_ordered(self):
        names = [os.path.basename(f) for f in self.FILES[2:]]
        nums = [n.split("-")[0] for n in names]
        self.assertEqual(len(nums), len(set(nums)))
        self.assertEqual(names[0], "00-preflight.sh")
        self.assertEqual(names[-1], "99-summary.sh")
        self.assertLess(names.index("97-retail.sh"), names.index("98-verify.sh"))   # check sees the locked unit


HELPER_SERVER = r"""
import json, os, socket, sys
sock_path, reply, status_path, new_status = sys.argv[1:5]
srv = socket.socket(socket.AF_UNIX)
srv.bind(sock_path)
srv.listen(1)
print("ready", flush=True)
conn, _ = srv.accept()
conn.recv(4096)
if new_status:
    tmp = status_path + ".tmp"
    open(tmp, "w").write(new_status)
    os.replace(tmp, status_path)
conn.sendall(reply.encode() + b"\n")
conn.close()
"""


class HandoffTests(unittest.TestCase):
    """tools/release/handoff.py as install.sh and tools/dev/deploy run it: its
    own process, a fake atlas-helper on a UNIX socket, the engine's status file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atlas-handoff-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.status = os.path.join(self.tmp, "status.json")
        self.sock = os.path.join(self.tmp, "helper.sock")
        self.release = os.path.join(self.tmp, "rel")
        for name in ("atlas-release.json", "atlas-release.json.sig", "atlas-1.tar.xz"):
            _write(os.path.join(self.release, name), name)
        self.state = os.path.join(self.tmp, "state")
        _write(os.path.join(self.state, "updates", "ready", "stale"), "old")
        _write(self.status, json.dumps({"txid": "old", "engine_state": "IDLE", "stage": "success"}))

    def handoff(self, reply='{"ok": true}', new_status=None, timeout=10):
        server = subprocess.Popen([sys.executable, "-I", "-c", HELPER_SERVER, self.sock, reply, self.status,
                                   json.dumps(new_status) if new_status else ""],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(server.kill)
        self.assertEqual(server.stdout.readline().strip(), "ready")
        env = dict(os.environ, ATLAS_UPDATE_STATUS=self.status, ATLAS_HELPER_SOCKET=self.sock)
        start = time.monotonic()
        r = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tools", "release", "handoff.py"),
                            self.release, "--state", self.state, "--timeout", str(timeout)],
                           capture_output=True, text=True, env=env, timeout=60)
        r.elapsed = time.monotonic() - start
        return r

    def test_success_waits_for_this_updates_own_result(self):
        r = self.handoff(new_status={"txid": "new", "engine_state": "IDLE", "stage": "success"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, "updates", "ready"))),
                         ["atlas-1.tar.xz", "atlas-release.json", "atlas-release.json.sig"])

    def test_a_rollback_is_a_failure(self):
        r = self.handoff(new_status={"txid": "new", "engine_state": "IDLE", "stage": "rolled_back",
                                     "message": "restored"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("not installed (rolled_back): restored", r.stderr)

    def test_a_refusal_fails_at_once(self):
        r = self.handoff(reply='{"ok": false, "error": "not allowed"}', timeout=600)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused", r.stderr)
        self.assertLess(r.elapsed, 10)

    def test_the_previous_result_does_not_count(self):
        r = self.handoff(timeout=2)            # status still shows the old txid's success
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("timed out", r.stderr)


class KitSysfileTests(unittest.TestCase):
    def test_places_release_files_early_and_identically(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        tool = os.path.join(REPO, "tools", "install", "kit_sysfile.py")
        env = dict(os.environ, ATLAS_ROOT=sb.root)
        out = subprocess.run([sys.executable, "-I", tool, REPO, "bin/atlas-zram", "sysctl/99-atlas-memory.conf"],
                             capture_output=True, text=True, env=env, check=True)
        self.assertEqual(out.stdout.split(), ["/usr/local/lib/atlas/bin/atlas-zram",
                                              "/etc/sysctl.d/99-atlas-memory.conf"])
        out = subprocess.run([sys.executable, "-I", tool, REPO, "bin/atlas-zram"],
                             capture_output=True, text=True, env=env, check=True)
        self.assertEqual(out.stdout, "")
        # the release later finds them already correct
        from atlas_engine import sysfiles
        release = _repo_release(sb.root)
        spec = sysfiles.load_spec(release)
        entry = next(e for e in spec["files"] if e["src"] == "bin/atlas-zram")
        self.assertEqual(open(sb.paths.p(entry["dst"]), "rb").read(), sysfiles.render(release, entry))

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _repo_release(root):
    release = os.path.join(root, "rel")
    shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
    return release


if __name__ == "__main__":
    unittest.main()

"""Manufacturing: per-unit label files, scripts that parse, and a capture
that never copies one unit's secrets onto the next."""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from engine_sandbox import Sandbox
from test_installer import USER, Unit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MFG = os.path.join(REPO, "tools", "manufacturing")
sys.path.insert(0, MFG)


class LabelTests(unittest.TestCase):
    def test_label_from_a_real_identity(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import identity
        with mock.patch.object(identity, "apply_hostname"):
            identity.ensure()
        label = identity.label_payload()
        import make_label
        out = tempfile.mkdtemp()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(label))), \
                mock.patch.object(sys, "argv", ["make_label.py", "--out", out]), redirect_stdout(io.StringIO()):
            self.assertEqual(make_label.main(), 0)
        base = os.path.join(out, label["name"])
        for ext in (".svg", ".png", ".txt"):
            self.assertEqual(stat.S_IMODE(os.stat(base + ext).st_mode), 0o600, ext)   # holds secrets
        svg = open(base + ".svg").read()
        for value in (label["name"], label["setup_code"], label["hotspot_password"], label["hostname"] + ".local"):
            self.assertIn(value, svg)
        self.assertTrue(label["app_qr"].startswith("ATLAS1:" + json.load(
            open(os.path.join(sb.paths.IDENTITY, "device.json")))["device_id"]))
        self.assertEqual(open(base + ".png", "rb").read(8), b"\x89PNG\r\n\x1a\n")


class ScriptTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for path in [os.path.join(REPO, "install.sh")] + \
                [os.path.join(MFG, f) for f in os.listdir(MFG) if f.endswith(".sh")]:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")

    def test_install_script_never_fetches_code_or_writes_legacy_privileges(self):
        text = open(os.path.join(REPO, "install.sh")).read()
        for bad in ("git pull", "git clone", "sudoers", "sbin/atlas-update", "AtlasControl", "NOPASSWD",
                    "openssl req", "0666"):
            self.assertNotIn(bad, text, bad)

    def test_capture_leaves_every_per_unit_secret_behind(self):
        text = open(os.path.join(MFG, "atlas-capture.sh")).read()
        for path in ("/atlas-control/data/meshtastic.db", "/atlas-control/data/.secret_key",
                     "/atlas-control/data/hotspot_config.json", "/atlas-control/data/wifi_hints.json",
                     "/atlas-control/data/backups/", "/atlas-control/updates/", "/atlas-control/cache/"):
            self.assertIn(f"--exclude='{path}", text, path)
        self.assertIn("atlas-engine verify", text)                       # only a trusted release goes out


# The payload drive ($FAKE_DRIVE_DEV) is mounted at $FAKE_DRIVE_MNT; everything
# else is on the NVMe.
FINDMNT = r"""#!/bin/bash
src=""; tgt=""; pos=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) src="$2"; shift 2 ;;
        --target) tgt="$2"; shift 2 ;;
        -o) shift 2 ;;
        -*) shift ;;
        *) pos="$1"; shift ;;
    esac
done
if [[ -n "$src" ]]; then
    [[ "$src" == "$FAKE_DRIVE_DEV" ]] && echo "$FAKE_DRIVE_MNT"
    exit 0
fi
case "${tgt:-$pos}" in "$FAKE_DRIVE_MNT"*) echo "$FAKE_DRIVE_DEV" ;; *) echo /dev/nvme0n1p1 ;; esac
"""

DF = r"""#!/bin/bash
if [[ " $* " == *--output=avail* ]]; then echo " Avail"; echo "  900G"; exit 0; fi
echo "Filesystem     1K-blocks      Used Available Use% Mounted on"
echo "/dev/nvme0n1p1 976000000 100000000 876000000  11% /"
"""

# The golden unit's update engine: the real one from this checkout, rooted at ATLAS_ROOT.
ENGINE = r"""#!/bin/bash
exec /usr/bin/python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from atlas_engine import cli
sys.exit(cli.engine_main(sys.argv[2:]))' "$ATLAS_REPO/system" "$@"
"""

# The golden unit's own state, under atlas-control/: none of it may reach the drive.
SECRETS = ("data/meshtastic.db", "data/meshtastic.db-wal", "data/atlas.db", "data/.secret_key",
           "data/hotspot_config.json", "data/wifi_hints.json", "data/osrm_active.json",
           "data/backups/meshtastic-1.db", "updates/ready/atlas-release.json", "cache/tile.bin",
           "logs/app.log", "key.pem", "cert.pem", "hotspot_config.json", ".git/HEAD")

# The new unit's identity, with the label payload install.sh prints its label from.
IDENTITY = r"""#!/bin/bash
case "$1" in
    show) echo '{"name": "Atlas-TEST", "hostname": "atlas-test"}' ;;
    label-json) cat "$FAKE_STATE/label.json" ;;
esac
"""

LABEL = "atlas-control-prod"


class GoldenDriveTests(unittest.TestCase):
    """The golden drive for real, in ATLAS_ROOT sandboxes as tests/test_installer.py runs
    install.sh: atlas-capture.sh on a golden unit writes the drive, then the drive's own
    atlas-provision.sh seeds a new unit and runs the kit's install.sh. The payload is really
    copied (rsync), the release really verified; system commands are stubs."""

    @classmethod
    def setUpClass(cls):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        sys.path.insert(0, os.path.join(REPO, "system"))
        from atlas_engine import verify
        cls.tmp = tempfile.mkdtemp(prefix="atlas-golden-")
        key = Ed25519PrivateKey.generate()
        cls.pub = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        cls.production = cls._release("production", key, verify, [{"pub": cls.pub, "status": "active"}])
        cls.dev_signed = cls._release("dev-signed", key, verify, [])     # the kit trusts no key
        sandbox = Sandbox()
        try:
            from atlas_engine import identity
            with mock.patch.object(identity, "apply_hostname"):
                identity.ensure()
            cls.label = identity.label_payload()
        finally:
            sandbox.cleanup()
        cls.models = subprocess.run([sys.executable, "-I", os.path.join(REPO, "model_profiles.py"), "--preinstall"],
                                    capture_output=True, text=True, check=True).stdout.split()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _release(cls, name, key, verify, keys):
        """A signed release whose bundle is this checkout's kit, with a keyring of `keys`."""
        out = os.path.join(cls.tmp, name)
        os.makedirs(out)
        tracked = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, text=True,
                                 check=True).stdout.split("\0")
        bundle = "atlas-1.2.3-aarch64.tar.xz"
        with tarfile.open(os.path.join(out, bundle), "w:xz", preset=1) as tar:
            for rel in tracked:
                path = os.path.join(REPO, rel)
                if (not rel or rel.split("/")[0] in ("mobile", "tests", "static", "templates")
                        or rel == "system/keys/keyring.json" or not os.path.isfile(path) or os.path.islink(path)):
                    continue
                tar.add(path, arcname=rel)
            ring = json.dumps({"keyring_version": 2, "keys": keys, "revoked": []}).encode()
            info = tarfile.TarInfo("system/keys/keyring.json")
            info.size = len(ring)
            tar.addfile(info, io.BytesIO(ring))
        data = open(os.path.join(out, bundle), "rb").read()
        manifest = {"format": 1, "product": "atlas-control", "arch": "aarch64", "version": "1.2.3", "seq": 5,
                    "min_engine": 1, "engine_version": 5, "notes": [],
                    "bundle": {"name": bundle, "url": "", "size": len(data),
                               "sha256": hashlib.sha256(data).hexdigest(), "unpacked_size": 64 * 1024 * 1024},
                    "db": {"schema_target": 5, "compat_floor": 0}}
        mbytes = json.dumps(manifest).encode()
        with open(os.path.join(out, "atlas-release.json"), "wb") as f:
            f.write(mbytes)
        with open(os.path.join(out, "atlas-release.json.sig"), "wb") as f:
            f.write(verify.sign_manifest(mbytes, key))
        return out

    def setUp(self):
        self.case = tempfile.mkdtemp(prefix="atlas-drive-")
        self.addCleanup(shutil.rmtree, self.case, True)
        self.drive = os.path.join(self.case, "media", "admin", LABEL)    # where the desktop mounts it
        os.makedirs(self.drive)

    def unit(self):
        u = Unit()
        self.addCleanup(u.cleanup)
        os.unlink(os.path.join(u.bin, "rsync"))                          # payloads are really copied
        for name, text in (("findmnt", FINDMNT), ("df", DF)):
            Unit._write(os.path.join(u.bin, name), text, 0o755)
        return u

    def golden(self):
        """A golden unit: every dataset, Ray's models, the tools outside /atlas_data, and its own secrets."""
        g = self.unit()
        app = g.p("/atlas_data/atlas-control")
        for i in range(50):                                              # 51 states with maryland
            for profile in ("car", "hiking"):
                Unit._write(os.path.join(app, f"osrm-data/states/state{i:02d}/{profile}/.processed"), "")
        for rel in SECRETS:
            Unit._write(os.path.join(app, rel), "this unit's own")
        Unit._write(g.p("/atlas_data/atlas-control-wt/app.py"), "")
        for model in self.models:
            name, tag = model.split(":")
            Unit._write(g.p(f"/atlas_data/ollama/models/manifests/registry.ollama.ai/library/{name}/{tag}"), "{}")
        Unit._write(g.p("/atlas_data/ollama/models/blobs/sha256-0"), "weights")
        for tool in ("routed", "extract", "partition", "customize"):
            Unit._write(g.p(f"/usr/local/bin/osrm-{tool}"), "#!/bin/sh\n", 0o755)
        Unit._write(g.p("/usr/local/lib/ollama/cuda_v12/libggml-cuda.so"), "gpu")
        Unit._write(g.p("/usr/local/lib/atlas/bin/atlas-engine"), ENGINE, 0o755)
        Unit._write(g.p("/usr/local/lib/atlas/keys/keyring.json"),
                    json.dumps({"keyring_version": 1, "keys": [{"pub": self.pub, "status": "active"}]}))
        return g

    def attach(self, u):
        """Plug the drive into u: /dev/disk/by-label/atlas-control-prod."""
        Unit._write(u.p("/dev/sdz1"), "")
        os.makedirs(u.p("/dev/disk/by-label"))
        os.symlink("../../sdz1", u.p(f"/dev/disk/by-label/{LABEL}"))

    def run_script(self, u, script, *args):
        env = {"PATH": f"{u.bin}:/usr/bin:/bin", "HOME": u.tmp, "SUDO_USER": USER, "LANG": "C.UTF-8",
               "ATLAS_ROOT": u.root, "ATLAS_SYS_PYTHON": u.python, "FAKE_STATE": u.state, "ATLAS_REPO": REPO,
               "FAKE_DRIVE_DEV": os.path.realpath(u.p("/dev/sdz1")), "FAKE_DRIVE_MNT": self.drive}
        open(os.path.join(u.state, "calls.jsonl"), "w").close()
        r = subprocess.run(["bash", script, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=env, timeout=600)
        r.out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        r.calls = [json.loads(ln) for ln in open(os.path.join(u.state, "calls.jsonl")) if ln.startswith("[")]
        return r

    def capture(self, g, release, *args):
        return self.run_script(g, os.path.join(MFG, "atlas-capture.sh"), "--release", release, *args)

    def captured(self):
        g = self.golden()
        self.attach(g)
        r = self.capture(g, self.production, "--drive", LABEL)
        self.assertEqual(r.returncode, 0, r.out[-3000:])
        return r

    def provision(self, *args):
        n = self.unit()
        shutil.rmtree(n.p("/atlas_data"))                                # freshly flashed: the payload brings it
        self.attach(n)
        Unit._write(os.path.join(n.state, "atlas-identity"), IDENTITY, 0o755)
        Unit._write(os.path.join(n.state, "label.json"), json.dumps(self.label))
        # The release's venv (the faked install keeps it): prints the label for real.
        Unit._write(n.p("/opt/atlas/releases/1.2.3/venv/bin/python3"), f'#!/bin/sh\nexec {sys.executable} "$@"\n', 0o755)
        r = self.run_script(n, os.path.join(self.drive, "stage/atlas-imaging/atlas-provision.sh"), "--yes",
                            *[a if a != "DEV_KEY" else n.dev_key for a in args])
        r.unit = n
        r.checks = [ln for ln in open(os.path.join(n.state, "python.log")) if "migrate.py check" in ln]
        return r

    def test_capture_writes_a_production_drive_without_the_golden_units_secrets(self):
        r = self.captured()
        self.assertIn("retail units trust release 1.2.3", r.out)
        self.assertIn("Payload ready", r.out)
        payload = os.path.join(self.drive, "payload")
        self.assertTrue(os.path.isfile(os.path.join(payload, "atlas-control/static/tiles/map.pmtiles")))
        for rel in SECRETS:
            self.assertFalse(os.path.exists(os.path.join(payload, "atlas-control", rel)), rel)
        for rel in ("atlas-swap.swap", "atlas-control-wt"):
            self.assertFalse(os.path.exists(os.path.join(payload, rel)), rel)
        self.assertTrue(os.access(os.path.join(payload, "osrm-native-bin/osrm-routed"), os.X_OK))
        stage = os.path.join(self.drive, "stage")
        self.assertTrue(os.path.isfile(os.path.join(stage, "ollama-lib/cuda_v12/libggml-cuda.so")))
        self.assertTrue(os.path.isfile(os.path.join(stage, "kit/install.sh")))
        self.assertTrue(os.path.isfile(os.path.join(stage, "release/atlas-release.json")))
        with open(os.path.join(MFG, "atlas-provision.sh")) as want, \
                open(os.path.join(stage, "atlas-imaging/atlas-provision.sh")) as got:
            self.assertEqual(got.read(), want.read())                     # the release's own, not a stale copy
        self.assertTrue(os.path.isfile(os.path.join(self.drive, "MANUFACTURING.md")))
        manifest = open(os.path.join(self.drive, "MANIFEST.txt")).read()
        self.assertIn("Atlas release      : 1.2.3", manifest)
        self.assertIn(f"Drive              : {LABEL} (production)", manifest)
        self.assertIn("Deploy with: sudo stage/atlas-imaging/atlas-provision.sh   (", manifest)
        self.assertIn(["systemctl", "stop", "atlas-control"], r.calls)
        self.assertIn(["systemctl", "start", "atlas-control", "ollama"], r.calls)    # restored afterwards

        # A re-capture removes anything excluded that an older capture left on the drive.
        stale = os.path.join(payload, "atlas-control/data/.secret_key")
        Unit._write(stale, "older capture")
        g = self.golden()
        self.attach(g)
        r = self.capture(g, self.production)                              # --drive defaults to atlas-control-prod
        self.assertEqual(r.returncode, 0, r.out[-3000:])
        self.assertFalse(os.path.exists(stale))

    def test_a_dev_signed_release_makes_only_a_developer_drive(self):
        g = self.golden()
        self.attach(g)
        r = self.capture(g, self.dev_signed)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("retail units refuse release 1.2.3 (release not trusted: no release-signing keys are "
                      "installed) — FAILED", r.out)
        self.assertIn("do not deploy this payload", r.out)
        r = self.capture(g, self.dev_signed, "--dev")
        self.assertEqual(r.returncode, 0, r.out[-3000:])
        manifest = open(os.path.join(self.drive, "MANIFEST.txt")).read()
        self.assertIn(f"Drive              : {LABEL} (developer units only)", manifest)
        self.assertIn("atlas-provision.sh --dev-key PUB", manifest)

    def test_capture_writes_nothing_without_the_drive(self):
        g = self.golden()
        r = self.capture(g, self.production)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"No drive labelled '{LABEL}' is attached", r.out)
        self.assertEqual(os.listdir(self.drive), [])
        self.assertNotIn(["systemctl", "stop", "atlas-control"], r.calls)

    def test_a_developer_unit_is_provisioned_from_the_drive(self):
        self.captured()
        r = self.provision("--dev-key", "DEV_KEY")
        n = r.unit
        self.assertEqual(r.returncode, 0, r.out[-4000:])
        self.assertIn(f"Payload drive: {n.root}/dev/disk/by-label/{LABEL}", r.out)
        self.assertIn("Atlas-TEST is ready.", r.out)                      # install.sh finished
        self.assertIn("Provisioning complete.", r.out)
        self.assertTrue(os.path.isfile(n.p("/atlas_data/atlas-control/static/tiles/map.pmtiles")))
        for rel in SECRETS:
            self.assertFalse(os.path.exists(n.p(f"/atlas_data/atlas-control/{rel}")), rel)
        self.assertTrue(os.access(n.p("/usr/local/bin/ollama"), os.X_OK))
        self.assertTrue(os.path.isfile(n.p("/usr/local/lib/ollama/cuda_v12/libggml-cuda.so")))
        self.assertTrue(os.path.isfile(n.p("/opt/atlas-imaging/atlas-provision.sh")))
        label = self.label["name"] + ".png"
        self.assertTrue(os.path.isfile(n.p(f"/root/atlas-labels/{label}")))
        self.assertTrue(os.path.isfile(os.path.join(self.drive, "labels", label)))   # a copy for printing
        self.assertTrue(r.checks)
        self.assertTrue(all(f"--bundle {self.drive}/stage/release" in ln for ln in r.checks), r.checks)

    def test_a_retail_unit_from_the_drive_runs_the_retail_install(self):
        self.captured()
        r = self.provision()
        # The sandbox is not root, so the lock-down itself refuses, as in test_installer.
        self.assertEqual(r.returncode, 1, r.out[-4000:])
        self.assertIn("Unit type          : retail", r.out)
        self.assertIn("Retail lock-down failed (see above) — this unit must not ship.", r.out)
        self.assertTrue(r.checks)
        self.assertTrue(all("--dev-key" not in ln for ln in r.checks), r.checks)


if __name__ == "__main__":
    unittest.main()

"""Manufacturing: per-unit label files, scripts that parse, and a capture
that never copies one unit's secrets onto the next."""
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from engine_sandbox import Sandbox

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


if __name__ == "__main__":
    unittest.main()

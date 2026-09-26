"""The on-device acceptance harness's own parts, without root: its release
server really cuts and resumes a download the app is making, and its report
and wait logic behave."""
import base64
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from engine_sandbox import Sandbox

import update_manager

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("acceptance", os.path.join(REPO, "tools", "dev", "acceptance.py"))
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class HarnessServerTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        tree = self.sb.release_tree("2.0.0", 50, extra_files={"payload.bin": base64.b64encode(os.urandom(3_000_000)).decode()})
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub = self.sb.make_bundle(tree, "2.0.0", 50)       # no bundle URL: fetched next to the manifest
        self.server = acceptance.ReleaseServer(bytes_per_second=1_000_000)
        self.addCleanup(self.server.close)
        self.server.serve(pub)
        raw = self.sb.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        keyring = os.path.join(self.sb.root, "keyring.json")
        json.dump({"keys": [{"pub": base64.b64encode(raw).decode()}]}, open(keyring, "w"))
        dev = os.path.join(self.sb.root, "dev-mode")
        open(dev, "w").close()
        for pt in (mock.patch.object(update_manager, "KEYRING", keyring),
                   mock.patch.object(update_manager, "DEV_MODE", dev),
                   mock.patch.object(update_manager, "ENGINE_INSTALLED", os.path.join(self.sb.root, "i.json")),
                   mock.patch.object(update_manager.atlas_paths, "UPDATES_DIR", os.path.join(self.sb.root, "updates"))):
            pt.start(); self.addCleanup(pt.stop)
        self.um = update_manager.UpdateManager(
            releases_url=f"http://127.0.0.1:{acceptance.PORT}", helper_run=lambda v, **a: ("", 0),
            sleep=lambda s: time.sleep(0.2))
        self.um._spawn = lambda fn: threading.Thread(target=fn, daemon=True).start() or \
            mock.Mock(is_alive=lambda: False)

    def wait_state(self, pred, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred(self.um.snapshot()):
                return self.um.snapshot()
            time.sleep(0.1)
        self.fail(f"timed out; state={self.um.snapshot()}")

    def test_internet_cut_pauses_and_the_download_resumes_with_range(self):
        self.assertEqual(self.um.check(user_initiated=True)["state"], "available")
        self.assertTrue(all(r[3] == "Atlas-Updater/1" and "?" not in r[1] for r in self.server.requests))
        self.um.start(install=False)
        self.wait_state(lambda s: (s.get("progress_percent") or 0) >= 25)
        self.server.online = False
        cut = time.time()
        st = self.wait_state(lambda s: s["state"] == "paused")
        self.assertEqual(st["message"], acceptance.MSG_PAUSED)
        time.sleep(1)
        self.server.online = True
        self.wait_state(lambda s: s["state"] == "ready", timeout=120)
        self.assertTrue([r for r in self.server.requests if r[0] > cut and r[2] and r[2] != "bytes=0-"])


class HarnessLogicTests(unittest.TestCase):
    def test_report_marks_failures_and_writes_readable_files(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(acceptance, "REPORT_DIR", d):
            rep = acceptance.Report()
            rep.check("s", "good", True)
            rep.note("s", "info", "x")
            rep.check("s", "bad", False, "why")
            self.assertFalse(rep.write())
        txt = [f for f in os.listdir(d) if f.endswith(".txt")][0]
        text = open(os.path.join(d, txt)).read()
        self.assertIn("1 passed, 1 failed", text)
        self.assertIn("[FAIL] s: bad — why", text)
        self.assertEqual(os.stat(os.path.join(d, txt)).st_mode & 0o777, 0o644)

    def test_waiting_for_a_result_ignores_the_previous_one(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "last_result.json")
        json.dump({"result": "success", "finished": 100.0}, open(path, "w"))
        with mock.patch.object(acceptance, "LAST_RESULT", path), \
                mock.patch.object(acceptance, "engine_idle", return_value=True):
            before = acceptance.last_result()
            with self.assertRaises(TimeoutError):
                acceptance.wait_new_result(before, 0.5, "a new result")      # old success must not count
            json.dump({"result": "rolled_back", "finished": 200.0}, open(path, "w"))
            self.assertEqual(acceptance.wait_new_result(before, 2, "x")["result"], "rolled_back")


if __name__ == "__main__":
    unittest.main()

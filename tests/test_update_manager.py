"""App-side updates: check, resumable download, verification, one job at a
time, and surviving page refreshes / app restarts."""
import base64
import http.server
import importlib.util
import json
import os
import threading
import time
import unittest
from unittest import mock

from engine_sandbox import Sandbox

import update_manager


def _native(cls_name, module):
    try:
        from gevent import monkey
        if monkey.is_module_patched(module):
            return monkey.get_original(module, cls_name)
    except Exception:
        pass
    return getattr(__import__(module), cls_name)


class ReleaseServer:
    """Serves a release directory with Range support and scriptable faults."""

    def __init__(self, directory):
        self.directory = directory
        self.drop_after = None          # bytes to send before cutting the connection (once)
        self.ignore_range = False
        self.corrupt = False
        self.offline = False
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.requests.append((self.path, self.headers.get("Range"), self.headers.get("User-Agent")))
                if server.offline:
                    self.connection.close()
                    return
                path = os.path.join(server.directory, os.path.basename(self.path))
                if not os.path.isfile(path):
                    self.send_error(404)
                    return
                data = open(path, "rb").read()
                if server.corrupt and path.endswith(".tar.xz"):
                    data = data[:100] + bytes([data[100] ^ 0xFF]) + data[101:]   # always a change
                start = 0
                rng = self.headers.get("Range")
                if rng and not server.ignore_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if server.drop_after is not None and path.endswith(".tar.xz"):
                    cut = server.drop_after
                    server.drop_after = None
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)

        ThreadingHTTPServer = http.server.ThreadingHTTPServer
        import socketserver
        # The server runs on a native thread; give it native sockets.
        native_socket = _native("socket", "socket")

        class Server(ThreadingHTTPServer):
            def __init__(self, *a, **kw):
                socketserver.TCPServer.__init__(self, *a, bind_and_activate=False, **kw)
                self.socket = native_socket(self.address_family, self.socket_type)
                self.server_bind()
                self.server_activate()

            def get_request(self):
                # socket.accept() builds the connection from the module-level
                # socket class, which gevent has patched; a gevent socket read
                # that has to wait in this native thread kills the handler
                # ("Cannot switch to a different thread") about 1 time in 300.
                fd, addr = self.socket._accept()
                return native_socket(self.address_family, self.socket_type, fileno=fd), addr

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class UpdateManagerTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        tree = self.sb.release_tree("2.0.0", 50, extra_files={"payload.bin": base64.b64encode(os.urandom(2_400_000)).decode()})
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub = self.sb.make_bundle(tree, "2.0.0", 50)
        self.server = ReleaseServer(pub)
        self.addCleanup(self.server.close)
        raw = self.sb.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        keyring = os.path.join(self.sb.root, "keyring.json")
        json.dump({"keys": [{"pub": base64.b64encode(raw).decode()}]}, open(keyring, "w"))
        dev_mode = os.path.join(self.sb.root, "dev-mode")
        open(dev_mode, "w").close()
        self.helper_calls = []
        patches = [
            mock.patch.object(update_manager, "KEYRING", keyring),
            mock.patch.object(update_manager, "DEV_MODE", dev_mode),
            mock.patch.object(update_manager, "ENGINE_INSTALLED", os.path.join(self.sb.root, "installed.json")),
            mock.patch.object(update_manager, "ENGINE_STATUS", os.path.join(self.sb.root, "status.json")),
            mock.patch.object(update_manager, "ENGINE_RESULT", os.path.join(self.sb.root, "result.json")),
            mock.patch.object(update_manager.atlas_paths, "UPDATES_DIR", os.path.join(self.sb.root, "updates")),
        ]
        for pt in patches:
            pt.start(); self.addCleanup(pt.stop)
        self.events = []
        self.um = self.make()

    def make(self):
        um = update_manager.UpdateManager(
            emit=lambda e, d: self.events.append(d), releases_url=self.server.url,
            helper_run=lambda verb, **a: (self.helper_calls.append(verb) or ("", 0)),
            sleep=lambda s: time.sleep(0.01))
        um._spawn = lambda fn: _Sync(fn)
        return um

    def test_check_finds_a_newer_signed_release_and_sends_no_identifiers(self):
        snap = self.um.check(user_initiated=True)
        self.assertEqual(snap["state"], "available")
        self.assertEqual(snap["version"], "2.0.0")
        self.assertEqual(snap["notes"], ["Test release"])
        for path, _rng, ua in self.server.requests:
            self.assertEqual(ua, update_manager.USER_AGENT)
            self.assertNotIn("?", path)

    def test_up_to_date_when_installed_seq_is_not_lower(self):
        json.dump({"seq": 50}, open(update_manager.ENGINE_INSTALLED, "w"))
        self.assertEqual(self.um.check()["state"], "up_to_date")

    def test_offline_message(self):
        self.server.offline = True
        snap = self.um.check(user_initiated=True)
        self.assertEqual(snap["message"], update_manager.MSG_OFFLINE)

    def test_clock_in_1970_waits_for_time_sync(self):
        self.um.clock = lambda: 0
        self.assertEqual(self.um.check()["message"], update_manager.MSG_NO_TIME)

    def test_forged_manifest_is_not_offered(self):
        path = os.path.join(self.server.directory, "atlas-release.json")
        m = json.load(open(path)); m["version"] = "9.9.9"; json.dump(m, open(path, "w"))
        snap = self.um.check(user_initiated=True)
        self.assertEqual(snap["message"], update_manager.MSG_NOT_VERIFIED)
        self.assertNotEqual(snap["state"], "available")

    def test_download_resumes_after_connection_drop_and_installs(self):
        self.um.check()
        self.server.drop_after = 1_000_000
        snap = self.um.start(install=True)
        self.assertEqual(self.um.state["state"], "installing")
        self.assertEqual(self.helper_calls, ["update.apply"])
        ranges = [r for p, r, _ in self.server.requests if p.endswith(".tar.xz")]
        self.assertEqual(ranges[0], None)
        self.assertTrue(ranges[1].startswith("bytes=") and ranges[1] != "bytes=0-")
        final = os.path.join(self.um.dir, self.um.state["manifest"]["bundle"]["name"])
        self.assertTrue(os.path.exists(final))
        self.assertTrue(any(e["state"] == "paused" and e["message"] == update_manager.MSG_PAUSED
                            for e in self.events))
        pct = [e["progress_percent"] for e in self.events if e["progress_percent"] is not None]
        self.assertEqual(pct, sorted(pct))                  # real, monotonic progress
        del snap

    def test_server_ignoring_range_restarts_cleanly(self):
        self.um.check()
        self.server.drop_after = 500_000
        self.server.ignore_range = True
        self.um.start(install=False)
        self.assertEqual(self.um.state["state"], "ready")

    def test_corrupted_download_is_not_installed(self):
        self.um.check()
        self.server.corrupt = True
        self.um.start(install=True)
        self.assertEqual(self.helper_calls, [], self.um.state)
        self.assertEqual(self.um.state["message"], update_manager.MSG_NOT_VERIFIED, self.um.state)

    def test_duplicate_start_does_not_start_a_second_download(self):
        self.um.check()
        running = mock.Mock(is_alive=lambda: True)
        self.um._job = running
        self.um.start()
        self.assertIs(self.um._job, running)

    def test_page_refresh_or_app_restart_keeps_the_download(self):
        self.um.check()
        self.server.drop_after = 1_200_000
        self.server.offline = False
        self.um._spawn = lambda fn: mock.Mock(is_alive=lambda: False)   # nothing runs yet
        self.um.start(install=False)
        # simulate: app restarted mid-download with a partial file on disk
        final, part, sidecar = self.um._bundle_paths()
        with open(part, "wb") as f:
            f.write(open(os.path.join(self.server.directory, os.path.basename(final)), "rb").read()[:700_000])
        json.dump({"sha256": self.um.state["manifest"]["bundle"]["sha256"],
                   "size": self.um.state["manifest"]["bundle"]["size"]}, open(sidecar, "w"))
        self.um.state["state"] = "downloading"
        self.um._save()
        restarted = self.make()
        self.assertEqual(restarted.state["state"], "paused")
        seen = len(self.server.requests)
        restarted.start(install=False)
        self.assertEqual(restarted.state["state"], "ready")
        ranges = [r for p, r, _ in self.server.requests[seen:] if p.endswith(".tar.xz")]
        self.assertEqual(ranges[0], "bytes=700000-")        # picked up where it stopped

    def test_cancel_discards_partial_download(self):
        self.um.check()
        self.um._spawn = lambda fn: mock.Mock(is_alive=lambda: False)
        self.um.start()
        final, part, sidecar = self.um._bundle_paths()
        open(part, "wb").write(b"partial")
        self.um.cancel()
        self.assertFalse(os.path.exists(part))
        self.assertEqual(self.um.state["state"], "available")

    def test_factory_reset_forgets_downloads_but_never_mid_install(self):
        audits = []
        self.um.audit = lambda action, **d: audits.append((action, d))
        self.um.check()
        self.um.start(install=False)
        self.assertEqual(self.um.state["state"], "ready")
        self.assertEqual(audits, [("update_verified", {"version": "2.0.0"})])
        self.um.state["state"] = "installing"
        self.assertFalse(self.um.forget())
        self.assertTrue(os.listdir(self.um.dir))
        self.um.state["state"] = "ready"
        self.assertTrue(self.um.forget())
        self.assertFalse(os.path.exists(self.um.dir))
        self.assertEqual(self.um.snapshot()["state"], "idle")
        self.assertEqual(self.make().state["state"], "idle")       # persisted

    def test_install_and_outcome_reach_the_security_log(self):
        audits = []
        self.um.audit = lambda action, **d: audits.append(action)
        self.um.check()
        self.um.start(install=True)
        self.um._finish("rolled_back", "The update could not be completed.")
        self.assertEqual(audits, ["update_verified", "update_install_started", "update_rolled_back"])

    def test_result_after_restart_is_reported(self):
        self.um.check()
        self.um.state.update(state="installing")
        json.dump({"engine_state": "IDLE", "stage": "rolled_back"}, open(update_manager.ENGINE_STATUS, "w"))
        json.dump({"result": "rolled_back"}, open(update_manager.ENGINE_RESULT, "w"))
        self.um.reconcile_after_restart()
        self.assertEqual(self.um.state["result"], "rolled_back")
        msg = self.um.state["message"]
        # Which version the owner still has, and what to do next.
        self.assertIn("could not be completed, so Atlas went back to", msg)
        self.assertIn("2.0.0", msg)
        self.assertIn(update_manager.MSG_NEXT_STEP, msg)
        self.assertIsNone(self.um.snapshot()["updated"])

    def test_success_shows_the_installed_version_and_its_notes(self):
        self.um.check()
        notes = self.um.state["manifest"].get("notes", [])
        self.um.state.update(state="installing")
        json.dump({"engine_state": "IDLE", "stage": "success"}, open(update_manager.ENGINE_STATUS, "w"))
        json.dump({"result": "success"}, open(update_manager.ENGINE_RESULT, "w"))
        self.um.reconcile_after_restart()
        snap = self.um.snapshot()
        self.assertEqual(snap["state"], "up_to_date")
        running = update_manager.atlas_version.version()
        self.assertEqual(snap["message"], f"Updated to Atlas {running}.")
        self.assertEqual(snap["updated"]["version"], running)
        self.assertEqual(snap["updated"]["notes"], notes)
        # Still shown after a check that finds nothing newer; restarts keep it.
        self.um.state["result"] = "success"
        self.assertEqual(self.make().snapshot()["updated"]["version"], running)

    def test_the_acceptance_harness_expects_these_messages(self):
        spec = importlib.util.spec_from_file_location(
            "acceptance", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "tools", "dev", "acceptance.py"))
        acceptance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(acceptance)
        running = update_manager.atlas_version.version()
        for result, expected in (("success", acceptance.msg_updated(running)),
                                 ("rolled_back", acceptance.msg_rolled_back("2.0.0", running))):
            self.um.check()
            self.um.state.update(state="installing")
            json.dump({"engine_state": "IDLE", "stage": result}, open(update_manager.ENGINE_STATUS, "w"))
            json.dump({"result": result}, open(update_manager.ENGINE_RESULT, "w"))
            self.um.reconcile_after_restart()
            self.assertTrue(self.um.state["message"].startswith(expected), (result, self.um.state["message"]))

    def test_failure_says_what_is_still_installed(self):
        self.um.check()
        self.um.state.update(state="installing")
        json.dump({"engine_state": "IDLE", "stage": "failed"}, open(update_manager.ENGINE_STATUS, "w"))
        json.dump({"result": "failed", "message": "Update could not be verified."},
                  open(update_manager.ENGINE_RESULT, "w"))
        self.um.reconcile_after_restart()
        running = update_manager.atlas_version.version()
        self.assertEqual(self.um.state["message"],
                         f"Update could not be verified. Atlas {running} is still installed. "
                         f"{update_manager.MSG_NEXT_STEP}")

    def test_https_is_required_outside_developer_mode(self):
        os.unlink(update_manager.DEV_MODE)
        snap = self.um.check(user_initiated=True)
        self.assertNotEqual(snap["state"], "available")


class _Sync:
    """Run a job synchronously (tests)."""

    def __init__(self, fn):
        fn()

    def is_alive(self):
        return False

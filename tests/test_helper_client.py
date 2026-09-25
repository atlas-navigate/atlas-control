"""The app reaches root only through atlas-helper, with passwords in the request."""
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

import atlas_helper


def _native_socket():
    """The test server runs on a native thread, so it must not use a
    gevent-patched socket (app.py monkey-patches socket at import)."""
    try:
        from gevent import monkey
        if monkey.is_module_patched("socket"):
            return monkey.get_original("socket", "socket")
    except Exception:
        pass
    return socket.socket


class FakeHelperServer:
    def __init__(self, reply):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "helper.sock")
        self.requests = []
        self.reply = reply
        self.sock = _native_socket()(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data.split(b"\n")[0])
            self.requests.append(req)
            conn.sendall((json.dumps(self.reply(req)) + "\n").encode())
            conn.close()

    def close(self):
        self.sock.close()


class HelperClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeHelperServer(lambda req: {"ok": True, "rc": 0, "out": "done"})
        self.addCleanup(self.server.close)
        p = mock.patch.object(atlas_helper, "SOCKET_PATH", self.server.path)
        p.start(); self.addCleanup(p.stop)

    def test_round_trip(self):
        self.assertEqual(atlas_helper.run("ping"), ("done", 0))
        self.assertEqual(self.server.requests[-1], {"verb": "ping", "args": {}})

    def test_nmcli_connect_goes_through_helper(self):
        import app
        with mock.patch.object(app.atlas_blocking, "run") as direct:
            out, rc = app._nmcli("dev", "wifi", "connect", "Home", "password", "s3cret-pass", "ifname", "wlan0")
            direct.assert_not_called()
        self.assertEqual(rc, 0)
        req = self.server.requests[-1]
        self.assertEqual(req["verb"], "net.wifi_connect")
        self.assertEqual(req["args"]["password"], "s3cret-pass")
        self.assertEqual(req["args"]["iface"], "wlan0")

    def test_hotspot_and_autoconnect_shapes(self):
        import app
        app._nmcli("dev", "wifi", "hotspot", "ifname", "wlan0", "ssid", "Atlas-7KQ4", "password", "ABCD-EFGH-JKMN")
        self.assertEqual(self.server.requests[-1]["verb"], "net.hotspot_up")
        app._nmcli("con", "modify", "Home", "connection.autoconnect", "no")
        self.assertEqual(self.server.requests[-1]["args"], {"name": "Home", "enabled": False})
        self.assertEqual(app._nmcli("con", "delete", "Hotspot"), ("", 0))

    def test_unrecognised_privileged_calls_are_refused(self):
        import app
        with mock.patch.object(app.atlas_blocking, "run") as direct:
            out, rc = app._nmcli("con", "delete", "Home")
            direct.assert_not_called()
        self.assertNotEqual(rc, 0)

    def test_read_only_queries_run_unprivileged(self):
        import app
        with mock.patch.object(app.atlas_blocking, "run",
                               return_value=mock.Mock(stdout="x", stderr="", returncode=0)) as direct:
            app._nmcli("-t", "-f", "DEVICE,TYPE", "dev")
        self.assertEqual(direct.call_args[0][0][:2], ["nmcli", "-t"])
        self.assertNotIn("sudo", direct.call_args[0][0])


class NoSudoTests(unittest.TestCase):
    def test_app_never_invokes_sudo(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("app.py", "system_stats.py", "ai_manager.py", "mesh_manager.py", "gps_node.py"):
            text = open(os.path.join(root, name)).read()
            self.assertNotRegex(text, r'\[\s*"sudo"|\[\s*\'sudo\'', name)

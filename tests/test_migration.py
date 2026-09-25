"""Rehearse tools/migrate on a sandboxed copy of a legacy Atlas layout:
apply, automatic rollback when the new release is unhealthy, manual
rollback, and re-applying after a rollback."""
import argparse
import base64
import collections
import importlib
import json
import os
import shutil
import sqlite3
import sys
import unittest
from unittest import mock

from engine_sandbox import Sandbox

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "migrate"))

LEGACY_UNIT = "[Service]\nUser=admin\nExecStart=/atlas_data/atlas-control/venv/bin/python3 app.py --host 0.0.0.0\n"
LEGACY_SITE = "# legacy nginx site\n"

PwEntry = collections.namedtuple("PwEntry", "pw_name pw_uid pw_gid")
GrEntry = collections.namedtuple("GrEntry", "gr_name gr_gid")


class MigrationRehearsal(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        p = self.sb.paths
        # A legacy device: code and state in one admin-owned directory.
        app = self.sb.app_state
        os.makedirs(os.path.join(app, "logs"), exist_ok=True)
        os.makedirs(os.path.join(app, "static", "data"), exist_ok=True)
        conn = sqlite3.connect(os.path.join(app, "data", "meshtastic.db"))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE nodes (node_id TEXT)")
        conn.commit(); conn.close()
        ds = sqlite3.connect(os.path.join(app, "static", "data", "nps_trails.db"))
        ds.execute("PRAGMA journal_mode=WAL"); ds.execute("CREATE TABLE t (x)"); ds.commit()
        self._keep_open = ds          # keeps the -wal file present like on the device
        with open(os.path.join(app, "hotspot_config.json"), "w") as f:
            json.dump({"ssid": "Atlas-Hotspot", "password": "my-own-secret-pw"}, f)
        for path, text in (("/etc/systemd/system/atlas-control.service", LEGACY_UNIT),
                           ("/etc/nginx/sites-available/atlas", LEGACY_SITE),
                           ("/etc/sudoers.d/atlas-nmcli", "admin ALL=(ALL) NOPASSWD: /usr/bin/nmcli\n"),
                           ("/etc/sudoers.d/atlas-update", "admin ALL=(ALL) NOPASSWD: /usr/local/sbin/atlas-update\n"),
                           ("/usr/local/sbin/atlas-update", "#!/bin/bash\n")):
            full = p.p(path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)
        os.makedirs(p.p("/etc/nginx/sites-enabled"), exist_ok=True)
        os.symlink("/etc/nginx/sites-available/atlas", p.p("/etc/nginx/sites-enabled/atlas"))

        # Release bundle: a real system/ tree, signed with a developer key.
        tree = self.sb.release_tree("1.0.0", 5)
        shutil.rmtree(os.path.join(tree, "system"))
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(tree, "system"))
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        dev = Ed25519PrivateKey.generate()
        self.bundle = self.sb.make_bundle(tree, "1.0.0", 5, sign_key=dev,
                                          manifest_overrides={"db": {"schema_target": 1, "compat_floor": 0}})
        self.dev_pub = os.path.join(self.sb.root, "dev.pub")
        with open(self.dev_pub, "w") as f:
            f.write(base64.b64encode(dev.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())

        import migrate
        importlib.reload(migrate)
        self.migrate = migrate
        me = os.getuid()
        self.healthy = True
        patches = [
            mock.patch.object(migrate.os, "geteuid", return_value=0),
            mock.patch.object(migrate.os, "chown"),
            mock.patch.object(migrate.pwd, "getpwnam", return_value=PwEntry("atlas", me, me)),
            mock.patch.object(migrate.pwd, "getpwuid", return_value=PwEntry("admin", me, me)),
            mock.patch.object(migrate.grp, "getgrnam", return_value=GrEntry("atlas", me)),
            mock.patch.object(migrate, "RUNNER", self.sb.runner),
            mock.patch.object(self.sb.runner, "run", side_effect=self._run_hook(self.sb.runner.run)),
            mock.patch.object(migrate, "sh", side_effect=self._sh),
            mock.patch.object(migrate.health, "check", side_effect=self._health),
            mock.patch("platform.machine", return_value="aarch64"),
            mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)),
            mock.patch.object(migrate.engine, "install_engine", side_effect=self._install_engine),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)

    def _run_hook(self, original):
        def run(cmd, *a, **kw):
            if cmd[:3] == ["systemctl", "stop", "atlas-control.service"] and self._keep_open:
                self._keep_open.close()          # the legacy app stops: dataset released
                self._keep_open = None
            return original(cmd, *a, **kw)
        return run

    def _install_engine(self, system_dir, version, runner):
        dest = os.path.join(self.sb.paths.ENGINE_DIR, str(version), "atlas_engine")
        shutil.copytree(os.path.join(system_dir, "atlas_engine"), dest, dirs_exist_ok=True)

    def _sh(self, cmd, check=True, **kw):
        import subprocess
        # Drop privilege-changing prefixes; run python probes for real.
        if cmd[0] == "setpriv":
            cmd = cmd[4:]
        if cmd[0] == "runuser":
            cmd = cmd[cmd.index("--") + 1:]
        if cmd[0] in ("/usr/bin/python3", "test"):
            r = subprocess.run(cmd, capture_output=True, text=True)
        else:
            self.sb.runner.calls.append(cmd)
            r = subprocess.CompletedProcess(cmd, 0, "", "")
        if "helper.sock" in " ".join(cmd):
            r = subprocess.CompletedProcess(cmd, 0, '{"ok": true, "pong": true}', "")
        if check and r.returncode != 0:
            raise SystemExit(f"{cmd} failed: {r.stderr}")
        return r

    def _health(self, runner, version, release, **kw):
        from atlas_engine import health
        return health.HealthResult(self.healthy, "fake")

    def apply(self, **kw):
        args = argparse.Namespace(bundle=self.bundle, dev_key=self.dev_pub, rotate_hotspot=False, **kw)
        return self.migrate.apply(args)

    def read(self, path):
        with open(self.sb.paths.p(path)) as f:
            return f.read()

    def test_apply_rollback_reapply(self):
        p = self.sb.paths
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.sb.current_version(), "1.0.0")
        unit = self.read("/etc/systemd/system/atlas-control.service")
        self.assertIn("User=atlas", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertFalse(os.path.exists(p.p("/etc/sudoers.d/atlas-nmcli")))
        self.assertFalse(os.path.exists(p.p("/usr/local/sbin/atlas-update")))
        self.assertTrue(os.path.exists(p.DEV_MODE_FLAG))
        hotspot = json.load(open(os.path.join(p.IDENTITY, "hotspot.json")))
        self.assertEqual(hotspot["password"], "my-own-secret-pw")          # non-default: adopted
        self.assertTrue(os.path.exists(os.path.join(self.sb.app_state, "data", "hotspot_config.json")))
        self.assertFalse(os.path.exists(os.path.join(self.sb.app_state, "static", "data", "nps_trails.db-wal"))
                         and self._journal_mode() == "wal")
        installed = json.load(open(p.INSTALLED))
        self.assertEqual(installed["seq"], 5)
        self.assertEqual(self.apply(), 0)                                   # idempotent

        self.assertEqual(self.migrate.rollback(argparse.Namespace(purge=False)), 0)
        self.assertEqual(self.read("/etc/systemd/system/atlas-control.service"), LEGACY_UNIT)
        self.assertEqual(self.read("/etc/nginx/sites-available/atlas"), LEGACY_SITE)
        self.assertTrue(os.path.exists(p.p("/etc/sudoers.d/atlas-nmcli")))
        self.assertTrue(os.path.exists(p.p("/usr/local/sbin/atlas-update")))
        self.assertFalse(os.path.exists(p.p("/etc/systemd/system/atlas-helper.socket")))

        self.assertEqual(self.apply(), 0)                                   # again after rollback
        self.assertIn("User=atlas", self.read("/etc/systemd/system/atlas-control.service"))

    def _journal_mode(self):
        c = sqlite3.connect(os.path.join(self.sb.app_state, "static", "data", "nps_trails.db"))
        return c.execute("PRAGMA journal_mode").fetchone()[0]

    def test_unhealthy_release_rolls_back_automatically(self):
        self.healthy = False
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.read("/etc/systemd/system/atlas-control.service"), LEGACY_UNIT)
        self.assertTrue(os.path.exists(self.sb.paths.p("/etc/sudoers.d/atlas-update")))

    def test_bad_signature_stops_before_touching_anything(self):
        with open(os.path.join(self.bundle, "atlas-release.json.sig"), "w") as f:
            f.write('{"signatures": []}')
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.read("/etc/systemd/system/atlas-control.service"), LEGACY_UNIT)
        self.assertFalse(os.path.exists(self.sb.paths.OPT))

    def test_default_hotspot_password_is_replaced(self):
        with open(os.path.join(self.sb.app_state, "hotspot_config.json"), "w") as f:
            json.dump({"ssid": "Atlas-Hotspot", "password": "AtlasControl"}, f)
        self.assertEqual(self.apply(), 0)
        new = json.load(open(os.path.join(self.sb.app_state, "data", "hotspot_config.json")))
        self.assertNotEqual(new["password"], "AtlasControl")

"""Update engine: signed install, verification failures, automatic rollback and
recovery from a crash or power cut at every step of the transaction."""
import json
import os
import sqlite3
import tarfile
import unittest

from engine_sandbox import Sandbox


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.install_initial("1.0.0", seq=1)
        self.sb.healthy_versions = {"1.0.0", "1.1.0"}

    def offer(self, version="1.1.0", seq=2, **kw):
        tree = self.sb.release_tree(version, seq, **{k: v for k, v in kw.items()
                                                   if k in ("system_marker", "schema_target", "extra_files")})
        pub = self.sb.make_bundle(tree, version, seq, **{k: v for k, v in kw.items()
                                                         if k in ("tamper", "members", "unpacked", "sign_key",
                                                                  "manifest_overrides")})
        self.sb.hand_off(pub)
        return pub

    def assert_unchanged(self):
        self.assertEqual(self.sb.current_version(), "1.0.0")
        self.assertIn("v1.0.0", self.sb.read("/etc/systemd/system/atlas-control.service"))
        self.assertIn("v1.0.0", self.sb.read("/etc/nginx/sites-available/atlas"))


class HappyPathTests(EngineTestCase):
    def test_signed_release_installs_and_commits(self):
        self.offer()
        self.assertEqual(self.sb.engine().run(), "done")
        self.assertEqual(self.sb.current_version(), "1.1.0")
        self.assertEqual(os.path.basename(os.path.realpath(self.sb.paths.PREVIOUS)), "1.0.0")
        self.assertIn("v1.1.0", self.sb.read("/etc/systemd/system/atlas-control.service"))
        nginx = self.sb.read("/etc/nginx/sites-available/atlas")
        self.assertIn(f"state={self.sb.app_state}", nginx)
        self.assertTrue(os.path.islink(self.sb.paths.p("/etc/nginx/sites-enabled/atlas")))
        installed = json.load(open(self.sb.paths.INSTALLED))
        self.assertEqual((installed["version"], installed["seq"]), ("1.1.0", 2))
        self.assertEqual(self.sb.last_result()["result"], "success")
        rel = os.path.join(self.sb.paths.RELEASES, "1.1.0")
        self.assertTrue(os.path.exists(os.path.join(rel, ".complete")))
        self.assertTrue(os.path.islink(os.path.join(rel, "venv")))
        self.assertFalse(os.path.exists(os.path.join(rel, "wheels")))
        self.assertTrue(self.sb.runner.ran("systemctl", "restart", "atlas-control.service"))
        self.assertTrue(self.sb.runner.ran("nginx", "-t"))
        # hand-off consumed, transaction closed
        self.assertEqual(os.listdir(self.sb.paths.HANDOFF), [])
        self.assertEqual(json.load(open(self.sb.paths.JOURNAL))["state"], "IDLE")

    def test_second_update_keeps_one_previous_and_prunes(self):
        self.offer("1.1.0", 2)
        self.assertEqual(self.sb.engine().run(), "done")
        self.sb.healthy_versions.add("1.2.0")
        self.offer("1.2.0", 3)
        self.assertEqual(self.sb.engine().run(), "done")
        releases = sorted(os.listdir(self.sb.paths.RELEASES))
        # current 1.2.0, previous 1.1.0, factory 1.1.0 (first engine-installed) — 1.0.0 pruned
        self.assertEqual(releases, ["1.1.0", "1.2.0"])

    def test_nothing_to_do_without_request(self):
        self.assertEqual(self.sb.engine().run(), "idle")


class VerificationTests(EngineTestCase):
    def test_bad_signature_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.offer(sign_key=Ed25519PrivateKey.generate())
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assertEqual(self.sb.last_result()["message"], "Update could not be verified.")
        self.assert_unchanged()
        self.assertFalse(any(n.startswith(".staging") for n in os.listdir(self.sb.paths.RELEASES)))

    def test_tampered_manifest_is_refused(self):
        pub = self.offer()
        path = os.path.join(self.sb.paths.HANDOFF, "atlas-release.json")
        m = json.load(open(path))
        m["seq"] = 99
        json.dump(m, open(path, "w"))
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assert_unchanged()
        del pub

    def test_corrupted_bundle_is_refused(self):
        self.offer(tamper=lambda b: b[:-10] + b"0123456789")
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assertEqual(self.sb.last_result()["message"], "Update could not be verified.")
        self.assert_unchanged()

    def test_downgrade_is_refused(self):
        self.offer("0.9.0", seq=1)
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assertIn("not newer", self.sb.last_result()["error"])
        self.assert_unchanged()

    def test_wrong_architecture_is_refused(self):
        self.offer(manifest_overrides={"arch": "x86_64"})
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assert_unchanged()

    def test_insufficient_space_is_refused(self):
        self.offer(unpacked=4 * 1024 ** 4)
        result = self.sb.engine().run()
        self.assertEqual(result, "failed")
        self.assert_unchanged()

    def test_dev_key_only_trusted_in_dev_mode(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        import base64
        dev = Ed25519PrivateKey.generate()
        os.makedirs(self.sb.paths.DEV_KEYS)
        with open(os.path.join(self.sb.paths.DEV_KEYS, "dev.pub"), "w") as f:
            f.write(base64.b64encode(dev.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
        self.offer(sign_key=dev)
        self.assertEqual(self.sb.engine().run(), "failed")          # dev-mode flag absent
        os.makedirs(os.path.dirname(self.sb.paths.DEV_MODE_FLAG), exist_ok=True)
        open(self.sb.paths.DEV_MODE_FLAG, "w").close()
        self.offer(sign_key=dev)
        self.assertEqual(self.sb.engine().run(), "done")


class HostileBundleTests(EngineTestCase):
    def _member(self, name, type_=tarfile.REGTYPE, data=b"x", linkname=""):
        m = tarfile.TarInfo(name)
        m.type = type_
        m.size = len(data) if type_ == tarfile.REGTYPE else 0
        m.linkname = linkname
        return m, (data if type_ == tarfile.REGTYPE else None)

    def _refused(self, member):
        self.offer(members=[member])
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assert_unchanged()
        self.assertFalse(os.path.exists(os.path.join(self.sb.root, "pwned")))

    def test_path_traversal(self):
        self._refused(self._member("../../../pwned"))

    def test_absolute_path(self):
        self._refused(self._member("/pwned"))

    def test_symlink(self):
        self._refused(self._member("app/link", tarfile.SYMTYPE, linkname="/etc/shadow"))

    def test_hardlink(self):
        self._refused(self._member("app/hl", tarfile.LNKTYPE, linkname="app.py"))

    def test_device_node(self):
        self._refused(self._member("dev0", tarfile.CHRTYPE))

    def test_bomb_over_declared_unpacked_size(self):
        self.offer(unpacked=100)
        self.assertEqual(self.sb.engine().run(), "failed")
        self.assert_unchanged()


class RollbackTests(EngineTestCase):
    def test_failed_health_check_rolls_back_everything(self):
        self.sb.healthy_versions = {"1.0.0"}
        self.offer()
        self.assertEqual(self.sb.engine().run(), "rolled_back")
        self.assert_unchanged()
        self.assertEqual(self.sb.last_result()["message"],
                         "The update could not be completed. Atlas has restored the previous version.")
        self.assertFalse(os.path.exists(os.path.join(self.sb.paths.RELEASES, "1.1.0")))
        self.assertIn("1.0.0", self.sb.health_calls)       # previous release re-verified

    def test_invalid_nginx_config_rolls_back_before_switching(self):
        self.sb.runner.fail["nginx -t"] = 1
        self.offer()
        self.assertEqual(self.sb.engine().run(), "rolled_back")
        self.assert_unchanged()
        self.assertFalse(self.sb.runner.ran("systemctl", "reload-or-restart", "nginx")
                         and self.sb.current_version() == "1.1.0")

    def test_database_restored_when_new_release_raised_compat_floor(self):
        self.sb.healthy_versions = {"1.0.0"}
        data = os.path.join(self.sb.app_state, "data")
        db_path = os.path.join(data, "meshtastic.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE atlas_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO atlas_meta VALUES ('compat_floor', '0')")
        conn.execute("CREATE TABLE marker (v TEXT)")
        conn.execute("INSERT INTO marker VALUES ('before')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit(); conn.close()

        def new_release_migrates():
            if self.sb.current_version() != "1.1.0":
                return
            os.makedirs(os.path.join(data, "backups"), exist_ok=True)
            c = sqlite3.connect(db_path)
            c.execute(f"VACUUM INTO '{os.path.join(data, 'backups', 'meshtastic-v1-1.db')}'")
            c.execute("UPDATE atlas_meta SET value='5' WHERE key='compat_floor'")
            c.execute("UPDATE marker SET v='after'")
            c.execute("PRAGMA user_version = 5")
            c.commit(); c.close()

        self.sb.runner.on_restart = new_release_migrates
        self.offer()
        self.assertEqual(self.sb.engine().run(), "rolled_back")
        c = sqlite3.connect(db_path)
        self.assertEqual(c.execute("SELECT v FROM marker").fetchone()[0], "before")
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertTrue(any(f.startswith("rolled-back-") for f in os.listdir(os.path.join(data, "backups"))))

    def test_cancel_before_install(self):
        self.offer()
        os.makedirs(self.sb.paths.UPDATE_STATE, exist_ok=True)
        open(os.path.join(self.sb.paths.UPDATE_STATE, "cancel"), "w").close()
        self.assertEqual(self.sb.engine().run(), "cancelled")
        self.assert_unchanged()


class PowerLossTests(EngineTestCase):
    """Crash at every journal state, then boot: Atlas must end up consistent."""

    STATES = ["ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED",
              "APPLYING_SYSTEM", "SWITCHED", "HEALTH_CHECK", "COMMITTING", "ROLLING_BACK"]

    def _consistent(self):
        cur = os.path.realpath(self.sb.paths.CURRENT)
        self.assertTrue(os.path.exists(os.path.join(cur, ".complete")), cur)
        version = os.path.basename(cur)
        self.assertIn(f"v{version}", self.sb.read("/etc/systemd/system/atlas-control.service"))
        self.assertIn(f"v{version}", self.sb.read("/etc/nginx/sites-available/atlas"))
        self.assertEqual(json.load(open(self.sb.paths.JOURNAL))["state"], "IDLE")
        return version

    def _crash_then_boot(self, state, healthy=True):
        from atlas_engine import engine as engine_mod
        if not healthy:
            self.sb.healthy_versions = {"1.0.0"}
        self.offer()
        with self.assertRaises(engine_mod.SimulatedCrash):
            self.sb.engine(fault=f"{state}:crash").run()
        # power returns: recovery unit, then the engine unit (after atlas-control)
        outcome = self.sb.engine(boot_id="boot-2").recover()
        if outcome == "resume":
            self.sb.engine(boot_id="boot-2").run()
        return self._consistent()

    def test_crash_at_every_state(self):
        for state in self.STATES:
            healthy = state != "ROLLING_BACK"
            with self.subTest(state=state):
                self.tearDown_sandbox_and_reset()
                version = self._crash_then_boot(state, healthy=healthy)
                result = self.sb.last_result()["result"]
                if state in ("ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED",
                             "APPLYING_SYSTEM"):
                    self.assertEqual(version, "1.0.0")
                    self.assertEqual(result, "interrupted")
                elif state == "ROLLING_BACK":
                    self.assertEqual(version, "1.0.0")
                    self.assertEqual(result, "rolled_back")
                else:
                    self.assertEqual(version, "1.1.0")
                    self.assertEqual(result, "success")

    def tearDown_sandbox_and_reset(self):
        self.sb.cleanup()
        self.sb = Sandbox()
        self.sb.install_initial("1.0.0", seq=1)
        self.sb.healthy_versions = {"1.0.0", "1.1.0"}

    def test_release_that_never_becomes_healthy_is_rolled_back_after_two_boots(self):
        from atlas_engine import engine as engine_mod
        self.sb.healthy_versions = {"1.0.0"}
        self.offer()
        with self.assertRaises(engine_mod.SimulatedCrash):
            self.sb.engine(fault="HEALTH_CHECK:crash").run()
        # reboot loop: the new release hangs the box before health completes
        self.assertEqual(self.sb.engine(boot_id="boot-2").recover(), "resume")
        self.assertEqual(self.sb.engine(boot_id="boot-3").recover(), "resume")
        self.assertEqual(json.load(open(self.sb.paths.JOURNAL))["state"], "ROLLING_BACK")
        self.sb.engine(boot_id="boot-3").run()
        self.assertEqual(self._consistent(), "1.0.0")

    def test_broken_current_link_falls_back_to_previous(self):
        self.offer()
        self.assertEqual(self.sb.engine().run(), "done")
        os.unlink(os.path.join(os.path.realpath(self.sb.paths.CURRENT), ".complete"))
        self.sb.engine(boot_id="boot-9").recover()
        self.assertEqual(self.sb.current_version(), "1.0.0")


class PairingSurvivesUpdatesTests(EngineTestCase):
    """What a paired phone depends on — this unit's identity (device id, TLS
    key, setup code, proxy secret) and the paired-device list and settings in
    data/ — is never touched by an update, a rollback or a power cut."""

    def setUp(self):
        super().setUp()
        import hashlib
        from atlas_engine import identity
        identity.ensure()
        self.data = os.path.join(self.sb.app_state, "data")
        os.makedirs(self.data, exist_ok=True)
        self.db = os.path.join(self.data, "meshtastic.db")
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE paired_clients (id TEXT, token_hash TEXT, revoked INTEGER DEFAULT 0)")
        c.execute("INSERT INTO paired_clients VALUES ('phone', 'hash-of-token', 0)")
        c.execute("CREATE TABLE ai_settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO ai_settings VALUES ('model', 'qwen3.5:4b')")
        c.commit(); c.close()

        def snapshot():
            out = {}
            for dirpath, _, files in os.walk(self.sb.paths.IDENTITY):
                for name in files:
                    path = os.path.join(dirpath, name)
                    out[os.path.relpath(path, self.sb.paths.IDENTITY)] = hashlib.sha256(open(path, "rb").read()).hexdigest()
            return out
        self.snapshot = snapshot
        self.before = snapshot()
        self.assertIn("tls/key.pem", self.before)

    def assert_pairing_intact(self):
        self.assertEqual(self.snapshot(), self.before)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT id, token_hash, revoked FROM paired_clients").fetchall(),
                         [("phone", "hash-of-token", 0)])
        self.assertEqual(c.execute("SELECT value FROM ai_settings WHERE key='model'").fetchone()[0], "qwen3.5:4b")

    def test_update(self):
        self.offer()
        self.assertEqual(self.sb.engine().run(), "done")
        self.assert_pairing_intact()

    def test_rolled_back_update(self):
        self.sb.healthy_versions = {"1.0.0"}
        self.offer()
        self.assertEqual(self.sb.engine().run(), "rolled_back")
        self.assert_pairing_intact()

    def test_power_cut_while_switching(self):
        from atlas_engine import engine as engine_mod
        self.offer()
        with self.assertRaises(engine_mod.SimulatedCrash):
            self.sb.engine(fault="SWITCHED:crash").run()
        if self.sb.engine(boot_id="boot-2").recover() == "resume":
            self.sb.engine(boot_id="boot-2").run()
        self.assertEqual(self.sb.current_version(), "1.1.0")
        self.assert_pairing_intact()


if __name__ == "__main__":
    unittest.main()


class EngineSwitchTests(EngineTestCase):
    """A release that carries a newer engine installs it first, re-executes
    into it, and the new engine finishes the same transaction."""

    def test_newer_bundled_engine_installs_itself_and_finishes_the_update(self):
        from atlas_engine import ENGINE_VERSION
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = os.path.join(repo, "system", "atlas_engine")
        newer = ENGINE_VERSION + 1
        extra = {}
        for name in os.listdir(src):
            if name.endswith(".py"):
                text = open(os.path.join(src, name)).read()
                if name == "__init__.py":
                    text = text.replace(f"ENGINE_VERSION = {ENGINE_VERSION}\n", f"ENGINE_VERSION = {newer}\n")
                extra[f"system/atlas_engine/{name}"] = text
        self.offer(extra_files=extra, manifest_overrides={"engine_version": newer})

        class Exec(BaseException):          # the process is replaced; nothing in it runs on
            pass

        first = self.sb.engine()
        execs = []

        def fake_exec(exe, argv):
            execs.append(argv)
            raise Exec()                     # a real execv never returns
        first.execv = fake_exec
        with self.assertRaises(Exec):
            first.run()
        self.assertTrue(execs and execs[0][-1] == "run")
        engine_dir = self.sb.paths.ENGINE_DIR
        self.assertEqual(os.readlink(os.path.join(engine_dir, "current")), str(newer))
        installed = open(os.path.join(engine_dir, str(newer), "atlas_engine", "__init__.py")).read()
        self.assertIn(f"ENGINE_VERSION = {newer}", installed)
        self.assertEqual(json.load(open(self.sb.paths.JOURNAL))["state"], "VENV")

        # The re-executed engine picks the open transaction up where it left off.
        self.assertEqual(self.sb.engine().run(), "done")
        self.assertEqual(self.sb.current_version(), "1.1.0")
        self.assertEqual(self.sb.last_result()["result"], "success")

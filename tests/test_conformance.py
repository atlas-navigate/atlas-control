"""Engine 4: INI settings, kernel settings, the zram move, the power mode,
reapply, DIAG pruning and `atlas-engine check` — all in an ATLAS_ROOT sandbox."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest import mock

from engine_sandbox import Sandbox

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AVAHI = """[server]
#host-name=foo
use-ipv4=yes
use-ipv6=yes
allow-interfaces=wlP1p1s0,l4tbr0
#deny-interfaces=eth1

[wide-area]
enable-wide-area=yes

[publish]
#publish-hinfo=no
publish-workstation=yes
publish-workstation=yes
"""

NVPMODEL = """< PARAM TYPE=FILE NAME=CPU_ONLINE >
< POWER_MODEL ID=0 NAME=15W >
CPU_ONLINE CORE_0 1
< POWER_MODEL ID=1 NAME=25W >
CPU_ONLINE CORE_0 1
< POWER_MODEL ID=2 NAME=MAXN_SUPER >
CPU_ONLINE CORE_0 1
< PM_CONFIG DEFAULT=1 >
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _repo_release(sb):
    release = os.path.join(sb.root, "rel")
    shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
    return release


def _install(sb, release, backup="bk"):
    from atlas_engine import sysfiles
    with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
        return sysfiles.install(release, os.path.join(sb.root, backup), sb.runner)


class EditIniTests(unittest.TestCase):
    def setUp(self):
        from atlas_engine import sysfiles
        self.edit = sysfiles.edit_ini

    def test_keys_keep_their_place_and_duplicates_are_commented_out(self):
        out = self.edit(AVAHI, {"server": {"use-ipv6": "no", "allow-interfaces": None, "deny-interfaces": "docker0"},
                                "publish": {"publish-hinfo": "no", "publish-workstation": "no"}})
        lines = out.splitlines()
        self.assertEqual(lines[3], "use-ipv6=no")
        self.assertEqual(lines[4], "#allow-interfaces=")
        self.assertEqual(lines[5], "deny-interfaces=docker0")       # was commented out: now set in place
        self.assertIn("publish-hinfo=no", lines)
        self.assertEqual([ln for ln in lines if ln.startswith("publish-workstation")], ["publish-workstation=no"])
        self.assertIn("#publish-workstation=yes", lines)
        self.assertIn("enable-wide-area=yes", lines)                # other sections untouched
        self.assertTrue(out.endswith("\n"))

    def test_idempotent(self):
        values = {"server": {"use-ipv6": "no", "allow-interfaces": None}, "publish": {"publish-aaaa-on-ipv4": "no"}}
        once = self.edit(AVAHI, values)
        self.assertEqual(self.edit(once, values), once)

    def test_missing_key_goes_under_its_section_and_missing_section_at_the_end(self):
        out = self.edit("[server]\nuse-ipv4=yes\n", {"server": {"deny-interfaces": "docker0"},
                                                     "publish": {"publish-hinfo": "no"}})
        self.assertEqual(out, "[server]\ndeny-interfaces=docker0\nuse-ipv4=yes\n\n[publish]\npublish-hinfo=no\n")

    def test_a_key_in_another_section_is_not_touched(self):
        out = self.edit("[a]\nx=1\n[b]\nx=2\n", {"b": {"x": "3"}})
        self.assertEqual(out, "[a]\nx=1\n[b]\nx=3\n")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.conf = self.sb.paths.p("/etc/avahi/avahi-daemon.conf")
        _write(self.conf, AVAHI)

    def test_release_sets_avahi_settings_restarts_avahi_and_rollback_restores_the_file(self):
        from atlas_engine import sysfiles
        release = _repo_release(self.sb)
        changed = _install(self.sb, release)
        self.assertIn("avahi-daemon", changed)
        text = open(self.conf).read()
        for line in ("use-ipv6=no", "publish-aaaa-on-ipv4=no", "publish-hinfo=no", "publish-workstation=no",
                     "deny-interfaces=docker0", "#allow-interfaces="):
            self.assertIn(line, text.splitlines())
        sysfiles.reload_changed(changed, None, self.sb.runner)
        self.assertTrue(self.sb.runner.ran("systemctl", "try-restart", "avahi-daemon"))
        sysfiles.restore(os.path.join(self.sb.root, "bk"))
        self.assertEqual(open(self.conf).read(), AVAHI)

    def test_already_set_means_no_restart(self):
        release = _repo_release(self.sb)
        _install(self.sb, release)
        self.assertNotIn("avahi-daemon", _install(self.sb, release, "bk2"))

    def test_no_avahi_installed_is_skipped(self):
        os.unlink(self.conf)
        changed = _install(self.sb, _repo_release(self.sb))
        self.assertNotIn("avahi-daemon", changed)
        self.assertFalse(os.path.exists(self.conf))


class KernelAndSwapTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_memory_policy_and_zram_move_into_the_release(self):
        from atlas_engine import sysfiles
        old_conf = self.sb.paths.p("/etc/sysctl.d/99-zram.conf")
        old_bin = self.sb.paths.p("/usr/local/sbin/atlas-zram")
        _write(old_conf, "vm.swappiness=100\n")
        _write(old_bin, "#!/bin/sh\n")
        release = _repo_release(self.sb)
        changed = _install(self.sb, release)
        self.assertIn("sysctl", changed)
        conf = open(self.sb.paths.p("/etc/sysctl.d/99-atlas-memory.conf")).read()
        self.assertIn("vm.swappiness = 100", conf)
        self.assertIn("vm.vfs_cache_pressure = 200", conf)
        unit = open(self.sb.paths.p("/etc/systemd/system/zram-swap.service")).read()
        self.assertIn("ExecStart=/usr/local/lib/atlas/bin/atlas-zram start", unit)
        self.assertIn("Before=ollama.service", unit)
        self.assertTrue(os.access(self.sb.paths.p("/usr/local/lib/atlas/bin/atlas-zram"), os.X_OK))
        self.assertFalse(os.path.exists(old_conf))
        self.assertFalse(os.path.exists(old_bin))
        spec = sysfiles.load_spec(release)
        self.assertIn("zram-swap.service", spec["enable"])
        sysfiles.reload_changed(changed, spec, self.sb.runner)
        self.assertTrue(self.sb.runner.ran("sysctl", "-q", "--system"))
        self.assertTrue(self.sb.runner.ran("systemctl", "enable", "zram-swap.service"))
        sysfiles.restore(os.path.join(self.sb.root, "bk"))
        self.assertTrue(os.path.exists(old_conf))
        self.assertTrue(os.path.exists(old_bin))

    def test_a_malformed_kernel_setting_is_refused_and_nothing_is_left_changed(self):
        from atlas_engine import sysfiles
        release = _repo_release(self.sb)
        _write(os.path.join(release, "system", "sysctl", "99-atlas-memory.conf"), "vm.swappiness 100\n")
        with self.assertRaises(sysfiles.SystemFilesError):
            _install(self.sb, release)
        self.assertFalse(os.path.exists(self.sb.paths.p("/etc/sysctl.d/99-atlas-memory.conf")))
        self.assertFalse(os.path.exists(self.sb.paths.p("/etc/systemd/system/atlas-control.service")))

    def test_zram_helper_is_valid_shell(self):
        subprocess.run(["bash", "-n", os.path.join(REPO, "system", "bin", "atlas-zram")], check=True)


class HardeningFilesTests(unittest.TestCase):
    def test_sandboxes_ship_with_the_release(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        _write(sb.paths.p("/etc/systemd/system/ollama.service"), "[Service]\nUser=ollama\n")
        changed = _install(sb, _repo_release(sb))
        self.assertIn("nginx-unit", changed)
        self.assertTrue(sb.runner.ran("systemd-analyze", "verify", "--man=no", "nginx.service"))
        nginx = open(sb.paths.p("/etc/systemd/system/nginx.service.d/atlas.conf")).read()
        for d in ("NoNewPrivileges=yes", "ProtectSystem=full", "ProtectHome=yes", "PrivateTmp=yes"):
            self.assertIn(d, nginx)
        ollama = open(sb.paths.p("/etc/systemd/system/ollama.service.d/atlas.conf")).read()
        self.assertNotRegex(ollama, r"@[A-Z_]+@")
        for d in ("ProtectSystem=strict", "NoNewPrivileges=yes", "LLAMA_ARG_FIT_TARGET=256",
                  "OLLAMA_MAX_LOADED_MODELS=2", "OLLAMA_KV_CACHE_TYPE=q8_0", "OLLAMA_FLASH_ATTENTION=1"):
            self.assertIn(d, ollama)
        # The GPU device nodes and CUDA's run-time compiled kernels must stay usable.
        for d in ("PrivateDevices=", "MemoryDenyWriteExecute=", "DeviceAllow="):
            self.assertNotIn("\n" + d, ollama)
        app = open(sb.paths.p("/etc/systemd/system/atlas-control.service")).read()
        self.assertIn("CapabilityBoundingSet=\n", app)
        self.assertIn("AF_BLUETOOTH", app)
        rules = open(sb.paths.p("/etc/udev/rules.d/99-atlas-devices.rules")).read()
        for line in rules.splitlines():
            if line.startswith(("SUBSYSTEM", "KERNEL")) and "SYMLINK" in line:
                self.assertIn('ENV{ID_MM_DEVICE_IGNORE}="1"', line)

    def test_ollama_sandbox_lets_ollama_write_its_own_home(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import sysfiles
        _write(sb.paths.p("/etc/systemd/system/ollama.service"), "[Service]\nUser=nobody\n")
        import pwd
        self.assertEqual(sysfiles.template_vars()["OLLAMA_HOME"], pwd.getpwnam("nobody").pw_dir)


class PowerModeTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import perf
        self.perf = perf
        _write(self.sb.paths.NVPMODEL_CONF, NVPMODEL)

    def query(self, name, ident):
        self.sb.runner.outputs["nvpmodel -q"] = f"NV Power Mode: {name}\n{ident}\n"

    def test_modes_are_found_by_name(self):
        self.assertEqual(self.perf.power_modes(), {"15W": 0, "25W": 1, "MAXN_SUPER": 2})

    def test_switches_from_the_jetpack_default(self):
        self.query("25W", 1)
        r = self.perf.ensure_power_mode("MAXN_SUPER", self.sb.runner)
        self.assertTrue(r["changed"])
        self.assertTrue(self.sb.runner.ran("nvpmodel", "-m", "2"))

    def test_already_active_runs_nothing(self):
        self.query("MAXN_SUPER", 2)
        r = self.perf.ensure_power_mode("MAXN_SUPER", self.sb.runner)
        self.assertFalse(r["changed"])
        self.assertEqual(r["reason"], "already active")
        self.assertFalse(self.sb.runner.ran("nvpmodel", "-m"))

    def test_a_module_without_the_mode_is_left_alone(self):
        _write(self.sb.paths.NVPMODEL_CONF, NVPMODEL.replace("MAXN_SUPER", "MAXN"))
        self.query("25W", 1)
        r = self.perf.ensure_power_mode("MAXN_SUPER", self.sb.runner)
        self.assertFalse(r["changed"])
        self.assertIn("no such mode", r["reason"])
        self.assertFalse(self.sb.runner.ran("nvpmodel", "-m"))

    def test_not_a_jetson(self):
        os.unlink(self.sb.paths.NVPMODEL_CONF)
        r = self.perf.ensure_power_mode("MAXN_SUPER", self.sb.runner)
        self.assertFalse(r["changed"])
        self.assertFalse(self.sb.runner.ran("nvpmodel"))

    def test_nvpmodel_failure_never_fails_an_install(self):
        from atlas_engine import engine
        self.query("25W", 1)
        self.sb.runner.fail["nvpmodel -m"] = 1
        logs = []
        r = engine.apply_power_mode({"power_mode": "MAXN_SUPER"}, self.sb.runner, logs.append)
        self.assertFalse(r["changed"])
        self.assertTrue(any("nvpmodel failed" in m for m in logs), logs)
        with mock.patch.object(self.perf, "ensure_power_mode", side_effect=OSError("boom")):
            r = engine.apply_power_mode({"power_mode": "MAXN_SUPER"}, self.sb.runner, logs.append)
        self.assertIn("boom", r["reason"])

    def test_the_release_asks_for_maxn_super(self):
        spec = json.load(open(os.path.join(REPO, "system", "files.json")))
        self.assertEqual(spec["power_mode"], "MAXN_SUPER")


class ReapplyAndPruneTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.install_initial()

    def test_reapply_puts_a_drifted_file_back(self):
        from atlas_engine import engine
        unit = self.sb.paths.p("/etc/systemd/system/atlas-control.service")
        want = open(unit).read()
        _write(unit, "[Service]\n# edited by hand\n")
        r = engine.reapply(self.sb.runner, log=lambda m: None)
        self.assertEqual(open(unit).read(), want)
        self.assertIn("systemd", r["changed"])
        self.assertTrue(self.sb.runner.ran("systemctl", "daemon-reload"))
        r = engine.reapply(self.sb.runner, log=lambda m: None)
        self.assertEqual(r["changed"], [])

    def test_reapply_waits_for_a_running_update(self):
        from atlas_engine import engine, fsutil
        fsutil.atomic_write_json(self.sb.paths.JOURNAL, {"state": "APPLYING_SYSTEM"})
        with self.assertRaises(engine.EngineError):
            engine.reapply(self.sb.runner, log=lambda m: None)

    def test_prune_keeps_the_newest_diagnostic_logs(self):
        from atlas_engine import engine
        os.makedirs(self.sb.paths.DIAG)
        now = time.time()
        for i in range(engine.DIAG_KEPT + 5):
            path = os.path.join(self.sb.paths.DIAG, f"tx{i}.log")
            _write(path, "x")
            os.utime(path, (now - 1000 + i, now - 1000 + i))
        engine.prune()
        left = sorted(os.listdir(self.sb.paths.DIAG), key=lambda n: int(n[2:-4]))
        self.assertEqual(len(left), engine.DIAG_KEPT)
        self.assertEqual(left[0], "tx5.log")


class CheckTests(unittest.TestCase):
    """atlas-engine check on a sandbox that matches its release, then drifts."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import conform, sysfiles
        self.conform, self.sysfiles = conform, sysfiles
        self.current = self.sb.install_initial()
        shutil.rmtree(os.path.join(self.current, "system"))
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(self.current, "system"))
        _write(self.sb.paths.p("/etc/avahi/avahi-daemon.conf"), AVAHI)
        _install(self.sb, self.current)
        self.sb.runner.outputs["systemctl is-enabled"] = "enabled"

    def files_item(self, rep):
        return next(i for i in rep.items if i["area"] == "files")

    def test_a_matching_unit_passes(self):
        rep = self.conform.Report()
        self.conform.check_system_files(rep, self.current, self.sb.runner)
        self.assertEqual(self.files_item(rep)["status"], "ok", rep.text())
        self.assertTrue(rep.ok())

    def test_drift_is_named_with_the_fix(self):
        _write(self.sb.paths.p("/etc/systemd/system/ollama.service.d/atlas.conf"), "[Service]\n")
        avahi = self.sb.paths.p("/etc/avahi/avahi-daemon.conf")
        _write(avahi, open(avahi).read().replace("use-ipv6=no", "use-ipv6=yes"))
        _write(self.sb.paths.p("/etc/sysctl.d/99-zram.conf"), "vm.swappiness=60\n")
        self.sb.runner.outputs["systemctl is-enabled zram-swap.service"] = "disabled"
        rep = self.conform.Report()
        self.conform.check_system_files(rep, self.current, self.sb.runner)
        item = self.files_item(rep)
        self.assertEqual(item["status"], "fail")
        for part in ("ollama.service.d/atlas.conf differs", "avahi-daemon.conf settings",
                     "99-zram.conf should be gone", "atlas-engine reapply"):
            self.assertIn(part, item["detail"])
        self.assertTrue(any(i["status"] == "fail" and "zram-swap" in i["check"] for i in rep.items))
        self.assertFalse(rep.ok())
        # reapply fixes exactly what check found
        from atlas_engine import engine
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            engine.reapply(self.sb.runner, log=lambda m: None)
        rep = self.conform.Report()
        self.conform.check_system_files(rep, self.current, self.sb.runner)
        self.assertEqual(self.files_item(rep)["status"], "ok", rep.text())

    def test_power_mode_is_checked(self):
        _write(self.sb.paths.NVPMODEL_CONF, NVPMODEL)
        spec = self.sysfiles.load_spec(self.current)
        self.sb.runner.outputs["nvpmodel -q"] = "NV Power Mode: 25W\n1\n"
        rep = self.conform.Report()
        self.conform.check_power(rep, spec, self.sb.runner)
        self.assertEqual(rep.items[0]["status"], "fail")
        self.sb.runner.outputs["nvpmodel -q"] = "NV Power Mode: MAXN_SUPER\n2\n"
        rep = self.conform.Report()
        self.conform.check_power(rep, spec, self.sb.runner)
        self.assertEqual(rep.items[0]["status"], "ok")

    def test_listeners_outside_the_policy_are_reported(self):
        _write(self.sb.paths.p("/etc/atlas/firewall.json"), json.dumps({"tcp": [80, 443], "dev_tcp": [22]}))
        self.sb.runner.outputs["systemctl is-active"] = "active"
        self.sb.runner.outputs["ss -ltnH"] = (
            "LISTEN 0 511 0.0.0.0:443 0.0.0.0:*\n"
            "LISTEN 0 128 127.0.0.1:5000 0.0.0.0:*\n"
            "LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n")
        rep = self.conform.Report()
        self.conform.check_network(rep, self.sb.runner)
        by = {i["check"]: i for i in rep.items}
        self.assertEqual(by["only Atlas's ports listen on the network"]["status"], "warn")
        self.assertIn("8080", by["only Atlas's ports listen on the network"]["detail"])
        self.assertEqual(by["the app (port 5000) only on loopback"]["status"], "ok")
        self.sb.runner.outputs["ss -ltnH"] = "LISTEN 0 128 0.0.0.0:5000 0.0.0.0:*\n"
        rep = self.conform.Report()
        self.conform.check_network(rep, self.sb.runner)
        self.assertFalse(rep.ok())

    def test_json_output(self):
        out = subprocess.run([sys.executable, "-c",
                              "import sys; sys.path.insert(0, sys.argv[1]); from atlas_engine import cli; "
                              "sys.exit(cli.engine_main(['check', '--json']))", os.path.join(REPO, "system")],
                             capture_output=True, text=True, env=dict(os.environ), timeout=120)
        report = json.loads(out.stdout)
        self.assertIn("items", report)
        self.assertEqual(out.returncode, 0 if report["ok"] else 1)


class InferenceCheckTests(unittest.TestCase):
    """--infer asks the loaded model exactly the way the app does."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import conform
        self.conform = conform
        con = sqlite3.connect(os.path.join(self.sb.app_state, "data", "meshtastic.db"))
        con.execute("CREATE TABLE ai_settings (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO ai_settings VALUES ('model', 'qwen3.5:4b')")
        con.commit()
        con.close()
        self.posts = []

    def run_check(self, loaded, reply, caps=("completion", "thinking")):
        def post(url, body, timeout=60):
            self.posts.append((url, body))
            return {"capabilities": list(caps)} if url.endswith("/api/show") else reply
        rep = self.conform.Report()
        with mock.patch.object(self.conform, "_get_json", return_value={"models": [{"name": n} for n in loaded]}):
            self.conform.check_inference(rep, REPO, post=post)
        return rep.items[-1]

    def test_uses_the_apps_load_options_so_nothing_reloads(self):
        sys.path.insert(0, REPO)
        import model_profiles
        prof = model_profiles.resolve({"model": "qwen3.5:4b"})
        item = self.run_check(["qwen3.5:4b"], {"response": "It shows north.", "load_duration": 1.1e9,
                                               "eval_count": 32, "eval_duration": 2.4e9,
                                               "prompt_eval_count": 20, "prompt_eval_duration": 0.15e9})
        self.assertEqual(item["status"], "ok", item)
        self.assertIn("13.3 tokens/s", item["detail"])
        url, body = self.posts[-1]
        self.assertTrue(url.endswith("/api/generate"))
        self.assertEqual(body["model"], prof["tag"])
        self.assertIs(body["think"], False)
        self.assertNotIn("num_gpu", body["options"])          # as ai_manager._finalize_payload sends it
        for k in ("num_ctx", "num_batch", "num_thread"):
            self.assertEqual(body["options"][k], prof["options"][k])
        self.assertEqual(body["keep_alive"], prof["keep_alive"])

    def test_a_reload_is_reported(self):
        item = self.run_check(["qwen3.5:4b"], {"response": "x", "load_duration": 18e9,
                                               "eval_count": 1, "eval_duration": 1e8})
        self.assertEqual(item["status"], "warn")
        self.assertIn("reloaded", item["detail"])

    def test_a_model_that_is_not_loaded_is_not_loaded_by_the_check(self):
        item = self.run_check(["qwen3-embedding:0.6b"], {})
        self.assertEqual(item["status"], "skip")
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()

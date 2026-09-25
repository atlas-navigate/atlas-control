"""Root-side components: helper request validation, identity, firewall and
the real system/files.json installing cleanly into a sandbox."""
import json
import os
import shutil
import stat
import unittest
from unittest import mock

from engine_sandbox import Sandbox

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HelperValidationTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import helper, netctl
        self.helper, self.netctl = helper, netctl

    def test_unknown_or_malformed_requests_are_refused(self):
        for req in (None, [], "ping", {"verb": "shell", "args": {"cmd": "id"}},
                    {"verb": "net.wifi_connect", "args": "x"}, {"verb": 5}):
            with self.subTest(req=req):
                self.assertFalse(self.helper.handle(req)["ok"])

    def test_no_verb_runs_arbitrary_commands(self):
        for verb in self.helper.VERBS:
            self.assertNotIn("exec", verb)
            self.assertNotIn("run", verb)

    def test_network_arguments_are_validated(self):
        bad = [
            ("net.wifi_connect", {"ssid": "", "password": "12345678"}),
            ("net.wifi_connect", {"ssid": "x" * 33, "password": "12345678"}),
            ("net.wifi_connect", {"ssid": "home\nnet", "password": "12345678"}),
            ("net.wifi_connect", {"ssid": "home", "password": "short"}),
            ("net.wifi_connect", {"ssid": "home", "password": "x" * 64}),
            ("net.wifi_connect", {"ssid": "home", "password": "ok-password", "iface": "--help"}),
            ("net.wifi_connect", {"ssid": "home", "password": "ok-password", "iface": "wlan0; reboot"}),
            ("net.hotspot_up", {"iface": "", "ssid": "Atlas", "password": "12345678"}),
            ("net.disconnect", {"iface": "../../etc"}),
            ("net.set_autoconnect", {"name": "-a", "enabled": True}),
        ]
        with mock.patch.object(self.netctl, "_run") as run:
            for verb, args in bad:
                with self.subTest(verb=verb, args=args):
                    out = self.helper.handle({"verb": verb, "args": args})
                    self.assertFalse(out["ok"])
            run.assert_not_called()

    def test_wifi_password_goes_into_a_root_only_keyfile_not_argv(self):
        calls = []

        def fake_run(cmd, timeout=40):
            calls.append(cmd)
            return {"rc": 0, "out": ""}

        with mock.patch.object(self.netctl, "_run", side_effect=fake_run), \
                mock.patch.object(self.netctl, "check_iface", side_effect=lambda i, required=False: i):
            out = self.helper.handle({"verb": "net.wifi_connect",
                                      "args": {"ssid": "Home;Net", "password": "s3cret-pass!", "iface": "wlan0"}})
        self.assertTrue(out["ok"])
        self.assertFalse(any("s3cret-pass!" in " ".join(c) for c in calls))
        files = os.listdir(self.netctl.nm_connections_dir())
        self.assertEqual(len(files), 1)
        path = os.path.join(self.netctl.nm_connections_dir(), files[0])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        text = open(path).read()
        self.assertIn("psk=s3cret-pass!", text)
        self.assertIn("ssid=72;111;109;101;59;78;101;116;", text)   # bytes of "Home;Net"

    def test_update_apply_writes_a_request_without_paths_from_the_app(self):
        with mock.patch.object(self.helper, "_run", return_value={"rc": 0, "out": "", "err": ""}):
            out = self.helper.handle({"verb": "update.apply", "args": {"handoff_dir": "/etc"}})
        self.assertTrue(out["ok"])
        req = json.load(open(self.sb.paths.REQUEST))
        self.assertEqual(req["handoff_dir"], self.sb.paths.HANDOFF)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import identity
        self.identity = identity

    def test_create_generates_unique_secrets_with_safe_permissions(self):
        a = self.identity.ensure()
        shutil.rmtree(self.sb.paths.IDENTITY)
        b = self.identity.ensure()
        self.assertNotEqual(a["device_id"], b["device_id"])
        self.assertNotEqual(a["tls_spki_sha256"], b["tls_spki_sha256"])
        d = self.sb.paths.IDENTITY
        mode = lambda *p: stat.S_IMODE(os.stat(os.path.join(d, *p)).st_mode)
        self.assertEqual(mode("tls", "key.pem"), 0o600)
        self.assertEqual(mode("setup_code"), 0o600)
        self.assertEqual(mode("nginx-proxy-secret.conf"), 0o600)
        hotspot = json.load(open(os.path.join(d, "hotspot.json")))
        self.assertNotIn(hotspot["password"], self.identity.DEFAULT_HOTSPOT_PASSWORDS)
        self.assertGreaterEqual(len(hotspot["password"]), 12)

    def test_universal_default_password_is_never_adopted(self):
        dev = self.identity.ensure(adopt_hotspot_password="AtlasControl")
        pw = json.load(open(os.path.join(self.sb.paths.IDENTITY, "hotspot.json")))["password"]
        self.assertNotEqual(pw, "AtlasControl")
        del dev

    def test_certificate_is_valid_regardless_of_clock(self):
        from cryptography import x509
        self.identity.ensure()
        cert = x509.load_pem_x509_certificate(
            open(os.path.join(self.sb.paths.IDENTITY, "tls", "cert.pem"), "rb").read())
        self.assertLessEqual(cert.not_valid_before.year, 2024)
        self.assertGreaterEqual(cert.not_valid_after.year, 2099)

    def test_cloned_storage_gets_a_new_identity(self):
        with mock.patch.object(self.identity, "module_serial", return_value="SERIAL-A"):
            first = self.identity.ensure()
        with mock.patch.object(self.identity, "module_serial", return_value="SERIAL-B"):
            second = self.identity.ensure()
        self.assertNotEqual(first["device_id"], second["device_id"])
        self.assertEqual(second["created_reason"], "cloned-storage")

    def test_setup_code_hash_verifies(self):
        import base64
        import hashlib
        self.identity.ensure()
        code = open(os.path.join(self.sb.paths.IDENTITY, "setup_code")).read().strip()
        h = json.load(open(os.path.join(self.sb.paths.IDENTITY, "setup_code.hash")))
        digest = hashlib.pbkdf2_hmac("sha256", code.replace("-", "").encode(),
                                     base64.b64decode(h["salt"]), h["iterations"])
        self.assertEqual(base64.b64encode(digest).decode(), h["hash"])


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import firewall
        self.firewall = firewall
        self.policy = firewall.load_policy(os.path.join(REPO, "system", "firewall", "policy.json"))

    def test_uses_only_features_the_jetson_kernel_has(self):
        # No native nft ct/reject (CONFIG_NFT_CT / CONFIG_NFT_REJECT are off on
        # the Tegra kernel); conntrack and REJECT come from xtables.
        v4 = self.firewall.render_iptables(self.policy, dev=False)
        self.assertIn("-m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT", v4)
        self.assertIn("-j REJECT --reject-with tcp-reset", v4)
        local = self.firewall.render_nft_local(self.policy)
        self.assertNotIn(" ct ", local)
        self.assertNotIn("reject", local)
        self.assertIn("meta skuid", local)

    def test_ssh_and_docker_only_on_developer_units(self):
        retail = self.firewall.render_iptables(self.policy, dev=False)
        dev = self.firewall.render_iptables(self.policy, dev=True)
        self.assertNotIn(",22", retail)
        self.assertNotIn("docker0", retail)
        self.assertIn("--dports 80,443,53,22", dev)
        self.assertIn("-i docker0 -j ACCEPT", dev)

    def test_ipv6_keeps_neighbour_discovery(self):
        v6 = self.firewall.render_iptables(self.policy, ipv6=True, dev=False)
        self.assertIn("-p ipv6-icmp -j ACCEPT", v6)
        self.assertIn("546", v6)

    def test_chain_is_jumped_to_first_and_ends_closed(self):
        v4 = self.firewall.render_iptables(self.policy, dev=False).splitlines()
        self.assertIn("-I INPUT 1 -j ATLAS-INPUT", v4)
        rules = [l for l in v4 if l.startswith("-A ATLAS-INPUT")]
        self.assertEqual(rules[-1], "-A ATLAS-INPUT -j DROP")

    def test_invalid_rules_block_install_only_when_the_firewall_is_on(self):
        from atlas_engine import sysfiles
        runner = mock.Mock()
        runner.run.side_effect = lambda cmd, **kw: mock.Mock(
            returncode=1 if "restore" in cmd[0] else 0, stdout="disabled", stderr="bad rule")
        spec = {"files": [{"src": "firewall/policy.json", "dst": "/etc/atlas/firewall.json", "kind": "firewall"}],
                "enable": []}
        os.makedirs(self.sb.paths.p("/etc/atlas"), exist_ok=True)
        shutil.copy(os.path.join(REPO, "system", "firewall", "policy.json"), self.sb.paths.p("/etc/atlas/firewall.json"))
        sysfiles.validate(spec, {"firewall"}, runner)                     # off: warning only
        spec["enable"] = ["atlas-firewall.service"]
        with self.assertRaises(sysfiles.SystemFilesError):
            sysfiles.validate(spec, {"firewall"}, runner)


class RealSystemFilesTests(unittest.TestCase):
    def test_repo_system_files_install_and_render(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import sysfiles
        release = os.path.join(sb.root, "rel")
        os.makedirs(release)
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            changed = sysfiles.install(release, os.path.join(sb.root, "bk"), sb.runner)
        self.assertIn("systemd", changed)
        self.assertIn("nginx", changed)
        for entry in sysfiles.load_spec(release)["files"]:
            text = open(sb.paths.p(entry["dst"]), "rb").read().decode()
            if entry.get("template"):
                self.assertNotRegex(text, r"@[A-Z_]+@", entry["dst"])
        unit = open(sb.paths.p("/etc/systemd/system/atlas-control.service")).read()
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn(f"ATLAS_STATE_DIR={sb.app_state}", unit)
        self.assertIn("User=atlas", unit)
        site = open(sb.paths.p("/etc/nginx/sites-available/atlas")).read()
        self.assertIn("/etc/atlas/identity/tls/cert.pem", site)
        self.assertNotIn("0.0.0.0:5000", site)
        self.assertEqual(stat.S_IMODE(os.stat(sb.paths.p("/usr/local/lib/atlas/bin/atlas-launch")).st_mode), 0o755)

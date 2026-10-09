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

    def test_only_root_and_atlas_everywhere_and_the_atlas_group_only_on_dev_units(self):
        import collections
        Pw = collections.namedtuple("Pw", "pw_uid")
        Gr = collections.namedtuple("Gr", "gr_mem")
        uids = {"atlas": 978, "admin": 1000}
        with mock.patch.object(self.helper.pwd, "getpwnam", side_effect=lambda n: Pw(uids[n])), \
                mock.patch.object(self.helper.grp, "getgrnam", return_value=Gr(["admin"])):
            with mock.patch.object(self.helper.paths, "dev_mode", return_value=False):
                self.assertEqual(self.helper._allowed_uids(), {0, 978})       # retail: group alone is not enough
            with mock.patch.object(self.helper.paths, "dev_mode", return_value=True):
                self.assertEqual(self.helper._allowed_uids(), {0, 978, 1000})

    def test_update_apply_writes_a_request_without_paths_from_the_app(self):
        with mock.patch.object(self.helper, "_run", return_value={"rc": 0, "out": "", "err": ""}):
            out = self.helper.handle({"verb": "update.apply", "args": {"handoff_dir": "/etc"}})
        self.assertTrue(out["ok"])
        req = json.load(open(self.sb.paths.REQUEST))
        self.assertEqual(req["handoff_dir"], self.sb.paths.HANDOFF)

    def test_avahi_reclaim_restarts_rather_than_reloads(self):
        # A reload never probes the host name again, so it cannot undo atlas-2.local.
        with mock.patch.object(self.helper, "_run", return_value={"rc": 0, "out": "", "err": ""}) as run:
            out = self.helper.handle({"verb": "avahi.reclaim", "args": {}})
        self.assertTrue(out["ok"])
        run.assert_called_once_with(["systemctl", "restart", "avahi-daemon"], timeout=20)


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

    def test_every_unit_gets_the_standard_wifi_password_and_its_own_everything_else(self):
        d = self.sb.paths.IDENTITY
        read = lambda *p: open(os.path.join(d, *p)).read()
        with mock.patch.object(self.identity, "module_serial", return_value="1423425090328"):
            a = self.identity.ensure()
        a_hotspot, a_code = json.load(open(os.path.join(d, "hotspot.json"))), read("setup_code")
        shutil.rmtree(d)
        with mock.patch.object(self.identity, "module_serial", return_value="1423425090329"):
            b = self.identity.ensure()
        b_hotspot, b_code = json.load(open(os.path.join(d, "hotspot.json"))), read("setup_code")
        # Owner decision: one Wi-Fi password for every unit (printed on box and manual)...
        self.assertEqual(a_hotspot["password"], self.identity.FACTORY_HOTSPOT_PASSWORD)
        self.assertEqual(b_hotspot["password"], a_hotspot["password"])
        self.assertTrue(a_hotspot["shared"] and b_hotspot["shared"])
        # ...and nothing else shared.
        self.assertNotEqual(a_code, b_code)
        self.assertNotEqual(a_hotspot["ssid"], b_hotspot["ssid"])
        self.assertNotEqual(a["device_id"], b["device_id"])
        self.assertNotEqual(a["tls_spki_sha256"], b["tls_spki_sha256"])
        self.assertIn("(the standard password on every Atlas)", self.identity.label_text())

    def test_factory_hotspot_moves_an_existing_unit_to_the_standard_password(self):
        d = self.sb.paths.IDENTITY
        self.identity.ensure(adopt_hotspot_password="our-own-wifi-9", legacy_alias="atlas")
        before = json.load(open(os.path.join(d, "hotspot.json")))
        code = open(os.path.join(d, "setup_code")).read()
        self.assertEqual((before["password"], before["shared"]), ("our-own-wifi-9", False))
        self.assertEqual(self.identity.factory_hotspot(), before["ssid"])
        after = json.load(open(os.path.join(d, "hotspot.json")))
        self.assertEqual(after, {"ssid": before["ssid"], "password": self.identity.FACTORY_HOTSPOT_PASSWORD,
                                 "shared": True})
        self.assertEqual(open(os.path.join(d, "setup_code")).read(), code)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "hotspot.json")).st_mode), 0o640)

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

    def test_cloned_disk_image_gets_its_own_hotspot_on_the_next_boot(self):
        from atlas_engine import netctl
        conns = netctl.nm_connections_dir()
        os.makedirs(conns, exist_ok=True)
        with open(os.path.join(conns, "atlas-hotspot.nmconnection"), "w") as f:
            f.write("[connection]\nid=Hotspot\n[wifi]\nmode=ap\nssid=65;\n")
        with open(os.path.join(conns, "Home.nmconnection"), "w") as f:
            f.write("[connection]\nid=Home\n[wifi]\nmode=infrastructure\n")
        with mock.patch.object(self.identity, "module_serial", return_value="SERIAL-A"):
            golden = self.identity.ensure()
            code_a = open(os.path.join(self.sb.paths.IDENTITY, "setup_code")).read()
            self.assertEqual(self.identity.ensure(), golden)          # same unit: nothing changes
        self.assertEqual(sorted(os.listdir(conns)), ["Home.nmconnection", "atlas-hotspot.nmconnection"])
        with mock.patch.object(self.identity, "module_serial", return_value="SERIAL-B"):
            clone = self.identity.ensure()
        code_b = open(os.path.join(self.sb.paths.IDENTITY, "setup_code")).read()
        self.assertNotEqual(code_a, code_b)
        self.assertNotEqual(golden["tls_spki_sha256"], clone["tls_spki_sha256"])
        self.assertEqual(os.listdir(conns), ["Home.nmconnection"])  # client networks stay

    def test_serial_falls_back_to_the_drive_when_the_module_has_none(self):
        d = os.path.join(self.sb.root, "serials")
        os.makedirs(d)
        module, drive = os.path.join(d, "module"), os.path.join(d, "drive")
        with open(drive, "w") as f:
            f.write("S4EWNX0R123456\n")
        # The sources are read relative to ATLAS_ROOT (the sandbox).
        with mock.patch.object(self.identity, "SERIAL_SOURCES", ("/serials/module", "/serials/drive")):
            self.assertEqual(self.identity.module_serial(), "S4EWNX0R123456")
            with open(module, "wb") as f:
                f.write(b"1423123456789\x00")
            self.assertEqual(self.identity.module_serial(), "1423123456789")

    def test_names_derive_from_the_module_serial(self):
        derive = self.identity.derive_short
        self.assertEqual(derive("1423425090328"), derive("1423425090328"))
        self.assertNotEqual(derive("1423425090328"), derive("1423425090329"))
        self.assertNotEqual(derive("1423425090328"), derive("1423425090328", salt=1))
        code = derive("1423425090328")
        self.assertEqual(len(code), 6)
        self.assertTrue(set(code) <= set(self.identity.ALPHABET))
        # Uniform over the label alphabet (no modulo bias): every character turns up.
        seen = {}
        for i in range(3000):
            for ch in derive(f"serial-{i}"):
                seen[ch] = seen.get(ch, 0) + 1
        self.assertEqual(set(seen), set(self.identity.ALPHABET))
        expected = 3000 * 6 / len(self.identity.ALPHABET)
        self.assertTrue(all(abs(n - expected) < expected * 0.2 for n in seen.values()), seen)

    def test_a_reflashed_module_gets_its_name_back(self):
        with mock.patch.object(self.identity, "module_serial", return_value="1423425090328"):
            a = self.identity.ensure()
            shutil.rmtree(self.sb.paths.IDENTITY)
            b = self.identity.ensure()
        self.assertEqual((a["name"], a["hostname"]), (b["name"], b["hostname"]))
        self.assertEqual(a["hostname"], "atlas-" + a["short"].lower())
        self.assertEqual(a["name"], "Atlas-" + a["short"])
        self.assertEqual(len(a["short"]), 6)
        self.assertNotEqual(a["tls_spki_sha256"], b["tls_spki_sha256"])   # secrets are never derived

    def test_no_serial_falls_back_to_a_random_code(self):
        with mock.patch.object(self.identity, "module_serial", return_value=None):
            a = self.identity.ensure()
            shutil.rmtree(self.sb.paths.IDENTITY)
            b = self.identity.ensure()
        self.assertNotEqual(a["short"], b["short"])

    def test_legacy_atlas_unit_moves_to_its_own_name_and_keeps_its_key(self):
        from cryptography import x509
        d = self.sb.paths.IDENTITY
        with mock.patch.object(self.identity, "module_serial", return_value="1423425090328"):
            self.identity.ensure()
            # What migrate.py wrote for a unit adopted before per-unit names.
            legacy = json.load(open(os.path.join(d, "device.json")))
            legacy.update(hostname="atlas", short="PJNT", name="Atlas-PJNT", aliases=[])
            json.dump(legacy, open(os.path.join(d, "device.json"), "w"))
            moved = self.identity.ensure()
            again = self.identity.ensure()
        self.assertEqual(moved["hostname"], "atlas-pjnt")
        self.assertEqual(moved["aliases"], ["atlas"])
        self.assertEqual(moved["hostname_migrated_from"], "atlas")
        self.assertEqual(moved["tls_spki_sha256"], legacy["tls_spki_sha256"])
        self.assertEqual(moved["device_id"], legacy["device_id"])
        self.assertEqual(again, moved)                              # idempotent
        cert = x509.load_pem_x509_certificate(open(os.path.join(d, "tls", "cert.pem"), "rb").read())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName)
        self.assertIn("atlas-pjnt.local", names)
        self.assertIn("atlas.local", names)

    def test_adopting_a_legacy_unit_names_it_and_keeps_the_alias(self):
        with mock.patch.object(self.identity, "module_serial", return_value="1423425090328"), \
                mock.patch.object(self.identity, "apply_hostname") as applied:
            dev = self.identity.ensure(adopt_hotspot_password="our-own-wifi-9", legacy_alias="atlas")
        self.assertEqual(dev["aliases"], ["atlas"])
        self.assertTrue(dev["hostname"].startswith("atlas-"))
        applied.assert_called_once_with(dev["hostname"])

    def test_identity_is_checked_on_every_boot_before_anything_serves_it(self):
        unit = open(os.path.join(REPO, "system", "systemd", "atlas-firstboot.service")).read()
        self.assertNotIn("ConditionPathExists", unit)
        before = [l for l in unit.splitlines() if l.startswith("Before=")][0]
        for svc in ("NetworkManager.service", "nginx.service", "atlas-control.service"):
            self.assertIn(svc, before)

    def test_wifi_password_and_setup_code_cannot_be_mistaken_for_each_other(self):
        import re
        self.identity.ensure()
        d = self.sb.paths.IDENTITY
        code = open(os.path.join(d, "setup_code")).read().strip()
        pw = json.load(open(os.path.join(d, "hotspot.json")))["password"]
        self.assertRegex(code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertRegex(pw, r"^[a-z0-9]{4}(-[a-z0-9]{4}){3}$")
        text = self.identity.label_text()
        self.assertIn("Setup code — type this to pair", text)
        self.assertLess(text.index(code), text.index(pw))
        self.assertIsNone(re.search(r"setup code", text.split(code)[1], re.I))   # the code is the only "setup code"

    def test_setup_code_check_and_replacement(self):
        import device_identity
        self.identity.ensure()
        d = self.sb.paths.IDENTITY
        self.assertTrue(self.identity.setup_code_matches())
        old = open(os.path.join(d, "setup_code")).read().strip()
        with mock.patch.object(device_identity, "IDENTITY_DIR", d):
            self.assertTrue(device_identity.check_setup_code(old))           # what the app checks
            new = self.identity.new_setup_code()
            self.assertNotEqual(new, old)
            self.assertTrue(device_identity.check_setup_code(new.lower()))
            self.assertFalse(device_identity.check_setup_code(old))
        h = json.load(open(os.path.join(d, "setup_code.hash")))
        h["hash"] = h["hash"][::-1]
        json.dump(h, open(os.path.join(d, "setup_code.hash"), "w"))
        self.assertFalse(self.identity.setup_code_matches())

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

    def test_check_loads_rules_into_an_unhooked_probe_chain_and_removes_it(self):
        from engine_sandbox import FakeRunner
        runner = FakeRunner()
        inputs = []
        original = runner.run
        runner.run = lambda cmd, timeout=120, input=None, **kw: (inputs.append((cmd[0], input)),
                                                                   original(cmd, timeout, input, **kw))[1]
        path = os.path.join(REPO, "system", "firewall", "policy.json")
        self.assertEqual(self.firewall.check(path, runner).returncode, 0)
        restores = [text for tool, text in inputs if tool.endswith("-restore")]
        self.assertEqual(len(restores), 2)                              # IPv4 and IPv6, really committed
        for text in restores:
            self.assertIn(":ATLAS-PROBE - [0:0]", text)
            self.assertNotIn("-I INPUT", text)                          # nothing jumps to it
            self.assertNotIn("ATLAS-INPUT", text)
        self.assertFalse(any("--test" in c for c in runner.calls))
        for tool in ("iptables-nft", "ip6tables-nft"):
            self.assertEqual(runner.calls.count([tool, "-X", "ATLAS-PROBE"]), 2)   # cleared before and after
        self.assertTrue(runner.ran("nft", "-c", "-f"))

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


class NginxPolicyTests(unittest.TestCase):
    def test_throttling_is_429_and_only_proofs_get_the_pairing_rate(self):
        import re
        conf = open(os.path.join(REPO, "system", "nginx", "atlas.conf")).read()
        self.assertIn("limit_req_status  429;", conf)     # never a 503 → "Atlas is restarting"
        pair = re.search(r"location ~ (\S+) \{\s*limit_req zone=atlas_pair", conf).group(1)
        rx = re.compile(pair)
        for path in ("/api/pair/claim", "/api/pair/hotspot", "/api/pair/request"):
            self.assertTrue(rx.search(path), path)
        for path in ("/api/pair/request/1661c73f8eb33897", "/api/pair/status", "/api/pair/clients"):
            self.assertFalse(rx.search(path), path)          # polling / status: general API limit


class DiscoveryFilesTests(unittest.TestCase):
    def test_avahi_advert_carries_identity_and_old_advert_is_restorable(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import identity, sysfiles
        device = identity.ensure(legacy_alias="atlas")
        old = sb.paths.p("/etc/avahi/services/atlas-http.service")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        open(old, "w").write("<service-group>old _http._tcp 5000</service-group>")
        release = os.path.join(sb.root, "rel")
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
        backup = os.path.join(sb.root, "bk")
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            sysfiles.install(release, backup, sb.runner)
        self.assertFalse(os.path.exists(old))
        advert = open(sb.paths.p("/etc/avahi/services/atlas.service")).read()
        self.assertIn("<type>_atlas._tcp</type>", advert)
        self.assertIn(f"id={device['device_id']}", advert)
        self.assertIn(f"fp={device['tls_spki_sha256']}", advert)
        self.assertIn(f"host={device['hostname']}", advert)
        self.assertNotIn("5000", advert)
        sysfiles.restore(backup)
        self.assertTrue(os.path.exists(old))
        self.assertFalse(os.path.exists(sb.paths.p("/etc/avahi/services/atlas.service")))

    def test_boot_rerenders_the_advert_after_a_cloned_disk_gets_a_new_identity(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import identity, sysfiles
        release = os.path.join(sb.paths.RELEASES, "1.0.0")
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
        os.makedirs(os.path.dirname(sb.paths.CURRENT), exist_ok=True)
        os.symlink(release, sb.paths.CURRENT)
        with mock.patch.object(identity, "module_serial", return_value="SERIAL-A"):
            golden = identity.ensure()
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            sysfiles.install(release, os.path.join(sb.root, "bk"), sb.runner)
        advert = sb.paths.p("/etc/avahi/services/atlas.service")
        self.assertIn(golden["device_id"], open(advert).read())
        with mock.patch.object(identity, "module_serial", return_value="SERIAL-B"):
            clone = identity.ensure()
        self.assertEqual(sysfiles.rerender_installed(), [advert])
        text = open(advert).read()
        self.assertNotIn(golden["device_id"], text)
        self.assertIn(f"id={clone['device_id']}", text)
        self.assertIn(f"fp={clone['tls_spki_sha256']}", text)
        self.assertEqual(sysfiles.rerender_installed(), [])          # unchanged: nothing written


class OllamaDropInTests(unittest.TestCase):
    def test_runner_settings_ship_as_a_drop_in_and_restart_ollama_once(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import sysfiles
        unit = sb.paths.p("/etc/systemd/system/ollama.service")
        os.makedirs(os.path.dirname(unit), exist_ok=True)
        open(unit, "w").write("[Service]\nExecStart=/usr/local/bin/ollama serve\n")
        release = os.path.join(sb.root, "rel")
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            changed = sysfiles.install(release, os.path.join(sb.root, "bk"), sb.runner)
        dropin = open(sb.paths.p("/etc/systemd/system/ollama.service.d/atlas.conf")).read()
        self.assertIn('Environment="LLAMA_ARG_CACHE_RAM=', dropin)
        self.assertIn("ollama", changed)
        self.assertTrue(sb.runner.ran("systemd-analyze", "verify", "--man=no", "ollama.service"))
        sysfiles.reload_changed(changed, None, sb.runner)
        self.assertTrue(sb.runner.ran("systemctl", "try-restart", "ollama.service"))
        # Unchanged on the next update: no restart of Ollama (and Ray) for nothing.
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            again = sysfiles.install(release, os.path.join(sb.root, "bk2"), sb.runner)
        self.assertNotIn("ollama", again)


class NginxDefaultSiteTests(unittest.TestCase):
    def test_default_site_is_disabled_and_rollback_restores_the_link(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import sysfiles
        avail = sb.paths.p("/etc/nginx/sites-available/default")
        enabled = sb.paths.p("/etc/nginx/sites-enabled/default")
        os.makedirs(os.path.dirname(avail), exist_ok=True)
        os.makedirs(os.path.dirname(enabled), exist_ok=True)
        open(avail, "w").write("server { listen 80 default_server; }\n")
        os.symlink("/etc/nginx/sites-available/default", enabled)
        release = os.path.join(sb.root, "rel")
        shutil.copytree(os.path.join(REPO, "system"), os.path.join(release, "system"))
        backup = os.path.join(sb.root, "bk")
        with mock.patch("atlas_engine.firewall.check", return_value=mock.Mock(returncode=0)):
            changed = sysfiles.install(release, backup, sb.runner)
        self.assertFalse(os.path.lexists(enabled))            # would clash with Atlas's default_server
        self.assertIn("nginx", changed)
        sysfiles.restore(backup)
        self.assertTrue(os.path.islink(enabled))
        self.assertEqual(os.readlink(enabled), "/etc/nginx/sites-available/default")


class VerifyCommandTests(unittest.TestCase):
    def test_release_is_checked_against_this_units_keys(self):
        import base64
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from atlas_engine import cli
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        tree = sb.release_tree("3.0.0", 30)
        release = sb.make_bundle(tree, "3.0.0", 30)
        out, err = io.StringIO(), io.StringIO()
        with open(sb.paths.KEYRING, "w") as f:
            json.dump({"keys": []}, f)
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.verify_release_dir(release), 1)     # key not trusted
        raw = sb.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        with open(sb.paths.KEYRING, "w") as f:
            json.dump({"keys": [{"pub": base64.b64encode(raw).decode()}]}, f)
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.verify_release_dir(release), 0)
        self.assertIn('"version": "3.0.0"', out.getvalue())

"""The Android and iOS apps and the Atlas server must agree on pairing.

Neither app can be compiled on the Jetson, so this reads their sources and
checks them against the running Flask app: every endpoint they call exists,
both speak the same pairing version as the server, the label QR code the
server prints parses the way the apps parse it, the 4-digit match code the
apps compute equals the server's, and every answer the server can give an
approval poll is handled by both apps.
"""
import os
import re
import unittest

import engine_sandbox  # noqa: F401  (puts system/ on the path for atlas_engine)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(REPO, "mobile", "android", "app", "src")
IOS = os.path.join(REPO, "mobile", "ios", "AtlasMobile")


def _sources(root, ext):
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith(ext):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    out[os.path.relpath(path, REPO)] = f.read()
    return out


def _android_main():
    return {k: v for k, v in _sources(ANDROID, ".kt").items() if "/src/main/" in k}


def _ios():
    return _sources(IOS, ".swift")


def _endpoints(text):
    """API paths a client source refers to, with interpolations cut off."""
    return set(re.findall(r"/api/[a-z0-9_/.-]*[a-z0-9_]|/update-status\.json", text))


class MobileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.app = app
        cls.rules = [r.rule for r in app.app.url_map.iter_rules()]
        cls.android = _android_main()
        cls.ios = _ios()

    def _route_exists(self, path):
        for rule in self.rules:
            pattern = "^" + re.sub(r"<[^>]+>", "[^/]+", rule) + "$"
            if re.match(pattern, path) or re.match(pattern, path.rstrip("/") + "/x"):
                return True
        return False

    def test_every_endpoint_the_apps_call_exists(self):
        nginx = open(os.path.join(REPO, "system", "nginx", "atlas.conf")).read()
        for platform, files in (("android", self.android), ("ios", self.ios)):
            paths = set().union(*(_endpoints(t) for t in files.values()))
            self.assertIn("/api/pair/status", paths, platform)
            for path in sorted(paths):
                with self.subTest(platform=platform, path=path):
                    if path == "/update-status.json":
                        self.assertIn("location = /update-status.json", nginx)
                    else:
                        self.assertTrue(self._route_exists(path), f"{platform} calls {path}, which Atlas lacks")

    def test_both_apps_speak_the_servers_pairing_version(self):
        client = self.app.app.test_client()
        info = client.get("/api/device/info", base_url="https://127.0.0.1",
                          environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
        android = re.search(r"const val PAIRING_VERSION = (\d+)", "\n".join(self.android.values()))
        ios = re.search(r"static let pairingVersion = (\d+)", "\n".join(self.ios.values()))
        self.assertEqual(int(android.group(1)), info["pairing"])
        self.assertEqual(int(ios.group(1)), info["pairing"])

    def test_the_apps_send_kinds_and_fields_the_server_accepts(self):
        import pairing
        self.assertIn("android", pairing.KINDS)
        self.assertIn("ios", pairing.KINDS)
        for platform, files in (("android", self.android), ("ios", self.ios)):
            text = "\n".join(files.values())
            for field in ("setup_code", "client_nonce", "request_id", "server_nonce", "secret", "token",
                          "tls_spki_sha256", "device_id", "paired", "pairing"):
                with self.subTest(platform=platform, field=field):
                    self.assertIn(f'"{field}"', text)

    def test_every_approval_answer_is_handled(self):
        # pairing.poll_request can answer: pending, approved, denied, expired,
        # unknown, issued. Anything else (429, 5xx) is "retry" in both apps.
        for platform, files in (("android", self.android), ("ios", self.ios)):
            text = "\n".join(files.values())
            for status in ("approved", "denied", "expired", "unknown", "issued", "retry"):
                with self.subTest(platform=platform, status=status):
                    self.assertIn(f'"{status}"', text)

    def test_match_code_vectors_in_the_android_tests_are_the_servers(self):
        import device_identity
        test = open(os.path.join(ANDROID, "test", "java", "com", "atlascontrol", "mobile", "network",
                                 "PairingLogicTest.kt")).read()
        vectors = re.findall(r'assertEquals\("(\d{4})", AtlasClient\.matchCode\("([^"]+)",\s*"([^"]+)", "([^"]+)"\)\)',
                             test.replace("\n", " ").replace("            ", " "))
        self.assertGreaterEqual(len(vectors), 2)
        for code, pin, server_nonce, client_nonce in vectors:
            self.assertEqual(device_identity.match_code(server_nonce, client_nonce, spki=pin), code)
        # And both apps hash the same string the server does.
        for text in ("\n".join(self.android.values()), "\n".join(self.ios.values())):
            self.assertIn("atlas-pair|", text)

    def test_the_printed_label_qr_parses_the_way_the_apps_parse_it(self):
        from engine_sandbox import Sandbox
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        from atlas_engine import identity
        identity.ensure()
        label = identity.label_payload()
        qr = label["app_qr"]
        self.assertTrue(qr.startswith("ATLAS1:"))
        # Both apps: drop the prefix, split on ':' into at most 5 fields.
        fields = qr[len("ATLAS1:"):].split(":", 4)
        device = identity.load()
        self.assertEqual(fields[0], device["device_id"])
        self.assertEqual(fields[1], label["setup_code"].replace("-", ""))
        self.assertEqual(fields[2], device["tls_spki_sha256"])
        self.assertEqual(fields[3], label["hotspot_ssid"])
        self.assertEqual(fields[4], label["hotspot_password"])
        self.assertIn('split(":", limit = 5)', "\n".join(self.android.values()))
        self.assertIn('split(separator: ":", maxSplits: 4', "\n".join(self.ios.values()))

    def test_setup_code_formatting_matches_the_code_atlas_prints(self):
        from atlas_engine import identity
        code = identity._grouped()
        # The apps format as three groups of four from ALPHABET-only input.
        self.assertRegex(code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
        for text in ("\n".join(self.android.values()), "\n".join(self.ios.values())):
            self.assertRegex(text, r"LENGTH = 12|length = 12")

    def test_both_apps_detect_removal_and_have_the_same_screens(self):
        kt = "\n".join(self.android.values())
        sw = "\n".join(self.ios.values())
        for name_kt, name_sw in (("Onboarding", "onboarding"), ("Locating", "locating"),
                                 ("Connected", "connected"), ("Restarting", "restarting"),
                                 ("NotFound", "notFound"), ("SecurityWarning", "securityWarning"),
                                 ("Unpaired", "unpaired")):
            with self.subTest(screen=name_kt):
                self.assertRegex(kt, rf"(data object|data class) {name_kt}\b")
                self.assertRegex(sw, rf"case {name_sw}\b")
        for text in (kt, sw):
            self.assertIn("stillPaired", text)
            self.assertIn("reconnectRemoved", text)
            self.assertIn("isOlderAtlas", text)

    def test_both_apps_read_the_hostname_wherever_atlas_publishes_it(self):
        # /api/device/info "fqdn"/"hostname", DNS-SD TXT host=, beacon "host".
        info = self.app.app.test_client().get("/api/device/info", base_url="https://192.168.1.20",
                                              environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
        self.assertIn("hostname", info)
        self.assertTrue(info["fqdn"].endswith(".local"))
        advert = open(os.path.join(REPO, "system", "avahi", "atlas.service")).read()
        self.assertIn("<txt-record>host=@ATLAS_HOSTNAME@</txt-record>", advert)
        beacon = open(os.path.join(REPO, "lan_beacon.py")).read()
        self.assertIn('"host": device.get("fqdn")', beacon)
        kt = "\n".join(self.android.values())
        sw = "\n".join(self.ios.values())
        for text, txt_read in ((kt, 'attrs["host"]'), (sw, 'txt["host"]')):
            self.assertIn('"fqdn"', text)
            self.assertIn(txt_read, text)
            self.assertIn('"host"', text)                    # the beacon field
            self.assertIn("rememberHostname", text)

    def test_both_apps_show_the_address_after_pairing_and_when_atlas_is_lost(self):
        kt = "\n".join(self.android.values())
        sw = "\n".join(self.ios.values())
        self.assertRegex(kt, r"data class Paired\(")
        self.assertRegex(sw, r"case paired\(")
        for text in (kt, sw):
            self.assertIn("continueAfterPairing", text)
            self.assertIn("chooseTyped", text)
        self.assertIn("DeviceDetailsCard(", kt)
        self.assertIn("DeviceDetailsView(", sw)

    def test_the_web_ui_copy_button_reaches_both_apps(self):
        page = open(os.path.join(REPO, "templates", "index.html"), encoding="utf-8").read()
        self.assertIn("window.AtlasAndroid.copyText", page)
        self.assertIn("window.AtlasIOS.copyText", page)
        kt = "\n".join(self.android.values())
        sw = "\n".join(self.ios.values())
        self.assertRegex(kt, r"@android\.webkit\.JavascriptInterface\s+fun copyText\(")
        self.assertIn("copyText: function(text)", sw)
        self.assertIn('case "copyText"', sw)


if __name__ == "__main__":
    unittest.main()

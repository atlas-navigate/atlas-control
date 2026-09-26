"""The served app page: prebuilt bundle, nothing render-blocking, and a CSP
that lets no inline script or eval run."""
import json
import os
import re
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AppPageTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app

    def page(self, manifest=None):
        with mock.patch.object(self.app, "_MOBILE_HTML", None):
            if manifest is None:
                return self.app._build_mobile_html()
            real_open = open

            def fake_open(path, *a, **kw):
                if str(path).endswith(os.path.join("static", "build", "manifest.json")):
                    import io
                    return io.StringIO(json.dumps(manifest))
                return real_open(path, *a, **kw)
            with mock.patch("builtins.open", side_effect=fake_open):
                return self.app._build_mobile_html()

    def test_every_script_is_deferred_and_babel_is_gone(self):
        html = self.page()
        scripts = re.findall(r"<script[^>]*>", html)
        self.assertTrue(scripts)
        self.assertTrue(all(" defer " in s and "src=" in s for s in scripts), scripts)
        self.assertNotIn("babel", html)
        self.assertNotIn('type="text/babel"', html)
        self.assertNotIn("katex.min.js", html)                     # loaded on demand

    def test_release_page_uses_the_hashed_bundle(self):
        html = self.page({"app_js": "/static/build/app.0123456789abcdef.js"})
        self.assertIn('<script defer src="/static/build/app.0123456789abcdef.js">', html)

    def test_prebuilt_page_gets_the_strict_csp(self):
        r = self.app.app.test_client().get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        csp = r.headers["Content-Security-Policy"]
        script_src = re.search(r"script-src ([^;]*)", csp).group(1)
        self.assertEqual(script_src.strip(), "'self'")
        self.assertIn("object-src 'none'", csp)

    def test_release_uses_its_prebuilt_bundle_even_if_the_template_looks_newer(self):
        with mock.patch.object(self.app.atlas_paths, "code_is_writable", return_value=False), \
                mock.patch.object(self.app.os.path, "getmtime",
                                  side_effect=lambda p: 2.0 if p.endswith("index.html") else 1.0), \
                mock.patch.object(self.app, "_MOBILE_HTML", None), \
                mock.patch.object(self.app.logger, "warning") as warn:
            self.app._precompile_jsx()
            self.assertIsNotNone(self.app._MOBILE_HTML)
            self.assertIn("defer", self.app._MOBILE_HTML)
        warn.assert_not_called()

    def test_template_has_no_inline_event_handlers(self):
        html = open(os.path.join(REPO, "templates", "index.html"), encoding="utf-8").read()
        self.assertIsNone(re.search(r"<[^>]+\son[a-z]+\s*=\s*[\"']", html))


if __name__ == "__main__":
    unittest.main()

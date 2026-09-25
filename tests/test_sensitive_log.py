"""Private data must never reach the journal at normal log levels."""
import io
import logging
import unittest

import sensitive_log


class RedactTests(unittest.TestCase):
    def test_coordinate_pairs(self):
        self.assertNotIn("38.95890", sensitive_log.redact("fix at 38.95890, -77.41555 now"))
        self.assertNotIn("-77.4", sensitive_log.redact("lat=38.958901 lon=-77.415550"))
        out = sensitive_log.redact("{'lat': 38.9589, 'lon': -77.4155, 'type': 'coords'}")
        self.assertNotIn("38.9589", out)
        self.assertNotIn("77.4155", out)
        self.assertIn("'type': 'coords'", out)

    def test_secrets(self):
        for text in ('password=hunter2hunter2', '"password": "hunter2hunter2"',
                     "psk: s3cretpsk!", "wifi_psk=abcdefgh", "setup_code=ABCD-EFGH",
                     "Authorization: Bearer abcdefghijklmnop", "Cookie: atlas_client=deadbeefcafe"):
            with self.subTest(text=text):
                out = sensitive_log.redact(text)
                for secret in ("hunter2hunter2", "s3cretpsk!", "abcdefgh", "ABCD-EFGH",
                               "abcdefghijklmnop", "deadbeefcafe"):
                    self.assertNotIn(secret, out)

    def test_channel_url_and_pem(self):
        out = sensitive_log.redact("share https://meshtastic.org/e/#CgMSAQESBggBQAN4AQ and more")
        self.assertNotIn("CgMSAQ", out)
        self.assertIn("and more", out)
        pem = "-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBgkq\n-----END PRIVATE KEY-----"
        self.assertEqual(sensitive_log.redact(pem), "<pem>")

    def test_leaves_ordinary_text_alone(self):
        for text in ("Ray answered in 1.25 s", "SNR 6.5 dB, 3 hops", "version 1.0.0",
                     "rag=True score=0.512"):
            self.assertEqual(sensitive_log.redact(text), text)


class FilterTests(unittest.TestCase):
    def test_filter_redacts_formatted_records(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("atlas-test-sensitive")
        logger.propagate = False
        logger.addHandler(handler)
        sensitive_log.install(logger)
        logger.warning("sent to %s at %.5f, %.5f psk=%s", "!abcd", 38.9589, -77.41555, "topsecret")
        out = stream.getvalue()
        self.assertIn("!abcd", out)
        self.assertNotIn("38.9589", out)
        self.assertNotIn("topsecret", out)

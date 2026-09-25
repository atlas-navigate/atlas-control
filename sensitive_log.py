"""
Keep private data out of Atlas's logs.

Atlas logs to the journal, which outlives chats, survives "Delete Location
History", and is the first thing copied off a unit for diagnostics. Nothing
that identifies where the owner is, what they asked Ray, or how to get onto
their networks belongs there at normal log levels.

Two layers:

* call sites log only coarse facts ("GPS 3D fix, 9 sats"), with precise
  values at DEBUG; and
* this filter, installed on every root handler, rewrites whatever slips
  through: coordinate pairs, secrets written as key=value / "key": "value",
  Meshtastic channel URLs (they carry the channel key), bearer tokens,
  pairing cookies and PEM blocks.
"""
import logging
import re

_REDACTED = "<redacted>"

_PATTERNS = [
    # PEM blocks (private keys, certificates).
    (re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.S), "<pem>"),
    # Meshtastic channel share links embed the channel PSK after '#'.
    (re.compile(r"https?://(?:www\.)?meshtastic\.org/[a-z]/#\S+", re.I), "<channel-url>"),
    # Authorization headers / pairing cookies.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + _REDACTED),
    (re.compile(r"(?i)\b(atlas_client|atlas_csrf)=[^;\s]+"), r"\1=" + _REDACTED),
    # key=value, key: value and "key": "value" for secret-bearing keys.
    (re.compile(
        r"""(?ix)
        (?P<key>["']?\b(?:password|passwd|passphrase|psk|wifi_psk|secret|token|
            api[_-]?key|private[_-]?key|pairing[_-]?(?:code|secret|token)|
            setup[_-]?code|fixed_pin|pin)\b["']?)
        (?P<sep>\s*[:=]\s*)
        (?P<val>"[^"]*"|'[^']*'|[^\s,;}\]]+)
        """), lambda m: f"{m.group('key')}{m.group('sep')}{_REDACTED}"),
    # Latitude / longitude written as named fields.
    (re.compile(
        r"""(?ix)
        (?P<key>["']?\b(?:lat|lon|lng|latitude|longitude)\b["']?)
        (?P<sep>\s*[:=]\s*)
        (?P<val>-?\d{1,3}\.\d+)
        """), lambda m: f"{m.group('key')}{m.group('sep')}{_REDACTED}"),
    # Bare "38.95890, -77.41555" style coordinate pairs (>= 3 decimals each).
    (re.compile(r"(?<![\d.])-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}(?![\d.])"), "<lat,lon>"),
]


def redact(text):
    """Return text with coordinates, secrets and channel keys removed."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts the fully formatted message in place."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_FILTER = SensitiveDataFilter()


def install(logger=None):
    """Attach the redaction filter to every handler of `logger` (default root).

    Filters on a logger only see records logged directly on it, so the filter
    goes on the handlers, where records from every module end up.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
    if logging.lastResort is not None and _FILTER not in logging.lastResort.filters:
        logging.lastResort.addFilter(_FILTER)
    return _FILTER

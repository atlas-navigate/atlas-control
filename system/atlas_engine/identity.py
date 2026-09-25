"""
Per-device identity, created on the production line (or on first boot if a
unit skipped that step). Nothing here is shared between units.

/etc/atlas/identity/
  device.json            public facts: device_id, name, hostname, module
                         serial, TLS public-key pin (0644)
  tls/key.pem            ECDSA P-256 private key — never leaves the device (0600)
  tls/cert.pem           self-signed certificate, valid 2024-2099 so an
                         offline unit with its clock at 1970 still serves it
  hotspot.json           Atlas's own Wi-Fi network: SSID + random password,
                         printed on the label (0640 root:atlas)
  setup_code             the label's setup code (0600, root only)
  setup_code.hash        PBKDF2 of the setup code, what pairing checks (0640)
  proxy_secret           nginx→Flask shared secret (0640 root:atlas)
  nginx-proxy-secret.conf  the same secret as an nginx include (0600)

The identity survives factory reset — the printed label must keep working
for the life of the device. If the module serial number no longer matches
(the storage was cloned into another unit) everything is regenerated.
"""
import base64
import datetime
import grp
import hashlib
import json
import os
import pwd
import secrets

from . import fsutil, paths

# No 0/O, 1/I/L, 5/S: read aloud and typed from a label without mistakes.
ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
DEFAULT_HOTSPOT_PASSWORDS = {"AtlasControl", "password", "atlascontrol"}


def _d(*parts):
    return os.path.join(paths.IDENTITY, *parts)


def _code(n):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def _grouped(n_groups=3, size=4):
    return "-".join(_code(size) for _ in range(n_groups))


def module_serial():
    try:
        with open("/sys/firmware/devicetree/base/serial-number", "rb") as f:
            return f.read().replace(b"\x00", b"").decode(errors="replace").strip() or None
    except OSError:
        return None


def _owner(path, user="root", group="root", mode=0o600):
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
        if os.geteuid() == 0:
            os.chown(path, uid, gid)
    except KeyError:
        pass
    os.chmod(path, mode)


def hash_setup_code(code, salt=None):
    salt = salt or secrets.token_bytes(16)
    normalized = code.replace("-", "").replace(" ", "").upper().encode()
    digest = hashlib.pbkdf2_hmac("sha256", normalized, salt, 200_000)
    return {"alg": "pbkdf2-sha256", "iterations": 200_000,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode()}


def _make_tls(name, hostname):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    import ipaddress

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atlas")])
    san = x509.SubjectAlternativeName([
        x509.DNSName(f"{hostname}.local"), x509.DNSName("atlas.local"), x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("10.42.0.1")),
    ])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            # Fixed, generous validity: an offline Atlas may boot with its
            # clock anywhere from 1970 onward and must still present this.
            .not_valid_before(datetime.datetime(2024, 1, 1))
            .not_valid_after(datetime.datetime(2099, 12, 31))
            .add_extension(san, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    pin = base64.urlsafe_b64encode(hashlib.sha256(spki).digest()).decode().rstrip("=")
    return key_pem, cert_pem, pin


def load():
    return fsutil.read_json(_d("device.json"))


def create(hotspot_password=None, hostname=None, reason="new"):
    """Generate a complete identity. Returns device.json contents."""
    short = _code(4)
    name = f"Atlas-{short}"
    host = hostname or f"atlas-{short.lower()}"
    device_id = secrets.token_hex(16)
    key_pem, cert_pem, pin = _make_tls(name, host)
    os.makedirs(_d("tls"), mode=0o755, exist_ok=True)
    os.chmod(paths.IDENTITY, 0o755)

    fsutil.atomic_write_bytes(_d("tls", "key.pem"), key_pem, mode=0o600)
    fsutil.atomic_write_bytes(_d("tls", "cert.pem"), cert_pem, mode=0o644)

    password = hotspot_password or _grouped()
    fsutil.atomic_write_json(_d("hotspot.json"), {"ssid": name, "password": password}, mode=0o640)
    _owner(_d("hotspot.json"), "root", paths.SERVICE_USER, 0o640)

    setup = _grouped()
    fsutil.atomic_write_bytes(_d("setup_code"), (setup + "\n").encode(), mode=0o600)
    fsutil.atomic_write_json(_d("setup_code.hash"), hash_setup_code(setup), mode=0o640)
    _owner(_d("setup_code.hash"), "root", paths.SERVICE_USER, 0o640)

    secret = secrets.token_urlsafe(48)
    fsutil.atomic_write_bytes(_d("proxy_secret"), (secret + "\n").encode(), mode=0o640)
    _owner(_d("proxy_secret"), "root", paths.SERVICE_USER, 0o640)
    fsutil.atomic_write_bytes(_d("nginx-proxy-secret.conf"),
                              f'proxy_set_header X-Atlas-Proxy "{secret}";\n'.encode(), mode=0o600)

    device = {
        "version": 1,
        "device_id": device_id,
        "name": name,
        "short": short,
        "hostname": host,
        "serial": module_serial(),
        "tls_spki_sha256": pin,
        "created_reason": reason,
    }
    fsutil.atomic_write_json(_d("device.json"), device, mode=0o644)
    return device


def ensure(adopt_hotspot_password=None, hostname=None):
    """Create the identity if missing or cloned; otherwise leave it alone."""
    device = load()
    if device:
        serial = module_serial()
        if serial and device.get("serial") and serial != device["serial"]:
            return create(reason="cloned-storage")
        return device
    pw = adopt_hotspot_password
    if pw in DEFAULT_HOTSPOT_PASSWORDS:
        pw = None
    return create(hotspot_password=pw, hostname=hostname, reason="adopted" if hostname else "new")


def label_payload(device=None):
    """What the manufacturing label encodes (root only: reads the setup code)."""
    device = device or load()
    hotspot = fsutil.read_json(_d("hotspot.json"), {})
    with open(_d("setup_code"), encoding="utf-8") as f:
        setup = f.read().strip()
    app_qr = "ATLAS1:" + ":".join([device["device_id"], setup.replace("-", ""),
                                   device["tls_spki_sha256"], hotspot["ssid"], hotspot["password"]])
    def esc(v):
        return "".join("\\" + c if c in '\\;,":' else c for c in v)
    wifi_qr = f"WIFI:T:WPA;S:{esc(hotspot['ssid'])};P:{esc(hotspot['password'])};;"
    return {"name": device["name"], "hostname": device["hostname"], "setup_code": setup,
            "hotspot_ssid": hotspot["ssid"], "hotspot_password": hotspot["password"],
            "app_qr": app_qr, "wifi_qr": wifi_qr}

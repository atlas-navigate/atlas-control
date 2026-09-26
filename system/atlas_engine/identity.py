"""
Per-device identity, created on the production line (or on first boot if a
unit skipped that step). Nothing here is shared between units.

/etc/atlas/identity/
  device.json            public facts: device_id, name, hostname, module
                         serial, TLS public-key pin (0644)
  tls/key.pem            ECDSA P-256 private key — never leaves the device (0600)
  tls/cert.pem           self-signed certificate, valid 2024-2099 so an
                         offline unit with its clock at 1970 still serves it
  hotspot.json           Atlas's own Wi-Fi network: its own SSID and the
                         standard password shared by every unit, printed on
                         the label (0640 root:atlas)
  setup_code             the label's setup code (0600, root only)
  setup_code.hash        PBKDF2 of the setup code, what pairing checks (0640)
  proxy_secret           nginx→Flask shared secret (0640 root:atlas)
  nginx-proxy-secret.conf  the same secret as an nginx include (0600)

The identity survives factory reset and software updates — the printed
label must keep working for the life of the device. `ensure()` runs on every
boot (atlas-firstboot.service): if the module serial number no longer matches
(the storage, or a whole disk image, was cloned into another unit) everything
is regenerated, so no two units ever share a setup code, key or password.
"""
import base64
import datetime
import grp
import hashlib
import hmac
import os
import pwd
import secrets

from . import fsutil, paths

# No 0/O, 1/I/L, 5/S: read aloud and typed from a label without mistakes.
ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
# Every Atlas's own Wi-Fi starts with this password, the same on every unit
# (owner decision, 2026-09-26: it can be printed on the box and in the manual,
# which keeps production simple). Everything else in an identity — setup
# code, TLS key, device id, Wi-Fi name, proxy secret — stays unique per unit.
# It is public: anyone who knows it can join an Atlas's hotspot, and being on
# the hotspot lets a device pair. Owners can set their own in Settings → Wi-Fi,
# and Atlas reminds them to. Its shape (four lowercase groups) differs from a
# setup code's so nobody types one where the other belongs.
FACTORY_HOTSPOT_PASSWORD = "w2hh-m6n8-3nyc-2hhv"
if not (8 <= len(FACTORY_HOTSPOT_PASSWORD) <= 63 and FACTORY_HOTSPOT_PASSWORD.isascii()
        and FACTORY_HOTSPOT_PASSWORD.isprintable()):
    raise RuntimeError("FACTORY_HOTSPOT_PASSWORD must be a WPA2 passphrase: 8-63 printable ASCII characters")
# Old units' universal password: never adopted when a legacy unit is migrated.
DEFAULT_HOTSPOT_PASSWORDS = {"AtlasControl", "password", "atlascontrol"}


def _d(*parts):
    return os.path.join(paths.IDENTITY, *parts)


def _code(n):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def _grouped(n_groups=3, size=4):
    return "-".join(_code(size) for _ in range(n_groups))


SERIAL_SOURCES = ("/sys/firmware/devicetree/base/serial-number",   # the Jetson module
                  "/sys/class/nvme/nvme0/serial")                  # fallback: the NVMe drive


def module_serial():
    for source in SERIAL_SOURCES:
        try:
            with open(source, "rb") as f:
                serial = f.read().replace(b"\x00", b"").decode(errors="replace").strip()
        except OSError:
            continue
        if serial:
            return serial
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

    password = hotspot_password or FACTORY_HOTSPOT_PASSWORD
    _write_hotspot({"ssid": name, "password": password, "shared": password == FACTORY_HOTSPOT_PASSWORD})

    _write_setup_code(_grouped())

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
    if not hostname:
        apply_hostname(host)
    return device


def apply_hostname(host):
    """Give a newly provisioned unit its own mDNS name (atlas-xxxx.local)."""
    if paths.ROOT != "/" or os.geteuid() != 0:
        return False
    import subprocess
    subprocess.run(["hostnamectl", "set-hostname", host], capture_output=True)
    try:
        with open("/etc/hosts", encoding="utf-8") as f:
            lines = f.read().splitlines()
        lines = [l for l in lines if not l.startswith("127.0.1.1")]
        lines.insert(1, f"127.0.1.1\t{host}")
        fsutil.atomic_write_bytes("/etc/hosts", ("\n".join(lines) + "\n").encode(), mode=0o644)
    except OSError:
        pass
    subprocess.run(["systemctl", "try-reload-or-restart", "avahi-daemon"], capture_output=True)
    return True


def ensure(adopt_hotspot_password=None, hostname=None):
    """Create the identity if missing or cloned; otherwise leave it alone."""
    device = load()
    if device:
        serial = module_serial()
        if serial and device.get("serial") and serial != device["serial"]:
            device = create(reason="cloned-storage")
            forget_hotspot_profiles()
        return device
    pw = adopt_hotspot_password
    if pw in DEFAULT_HOTSPOT_PASSWORDS:
        pw = None
    return create(hotspot_password=pw, hostname=hostname, reason="adopted" if hostname else "new")


def _write_hotspot(hotspot):
    fsutil.atomic_write_json(_d("hotspot.json"), hotspot, mode=0o640)
    _owner(_d("hotspot.json"), "root", paths.SERVICE_USER, 0o640)


def factory_hotspot():
    """Give an existing unit the standard Wi-Fi password (units made before it
    existed, or support). The Wi-Fi name, setup code and paired devices stay.
    It takes effect the next time Atlas starts its hotspot; a password the
    owner chose in Settings still wins. Returns the Wi-Fi name."""
    hotspot = fsutil.read_json(_d("hotspot.json"), None) or {}
    if not hotspot.get("ssid"):
        raise RuntimeError("this unit has no identity yet (run: atlas-identity ensure)")
    hotspot.update(password=FACTORY_HOTSPOT_PASSWORD, shared=True)
    _write_hotspot(hotspot)
    return hotspot["ssid"]


def forget_hotspot_profiles():
    """Remove saved access-point (hotspot) profiles. A cloned disk carries the
    original unit's hotspot password; without the profile, Atlas rebuilds its
    hotspot from this unit's own label. Client Wi-Fi networks are kept."""
    from . import netctl
    directory = netctl.nm_connections_dir()
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if any(line.strip() == "mode=ap" for line in text.splitlines()):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    if removed and paths.ROOT == "/" and os.geteuid() == 0:
        import subprocess
        subprocess.run(["nmcli", "connection", "reload"], capture_output=True)
    return removed


def _write_setup_code(setup):
    fsutil.atomic_write_bytes(_d("setup_code"), (setup + "\n").encode(), mode=0o600)
    fsutil.atomic_write_json(_d("setup_code.hash"), hash_setup_code(setup), mode=0o640)
    _owner(_d("setup_code.hash"), "root", paths.SERVICE_USER, 0o640)


def setup_code_matches():
    """Does the stored setup code verify against the hash pairing checks?"""
    try:
        with open(_d("setup_code"), encoding="utf-8") as f:
            code = f.read().strip()
        stored = fsutil.read_json(_d("setup_code.hash"), None) or {}
        salt = base64.b64decode(stored["salt"])
        again = hash_setup_code(code, salt=salt)
        return hmac.compare_digest(again["hash"], stored["hash"])
    except (OSError, KeyError, ValueError, TypeError):
        return False


def new_setup_code():
    """Replace the setup code (support: a lost label). The TLS key, hotspot and
    paired devices stay; only new pairings need the new code. Returns it."""
    setup = _grouped()
    _write_setup_code(setup)
    if not setup_code_matches():
        raise RuntimeError("the new setup code did not verify")
    return setup


def label_text(payload=None):
    """The label's facts for a person to read, each clearly named."""
    p = payload or label_payload()
    return "\n".join([
        f"{p['name']}   https://{p['hostname']}.local",
        "",
        "  Setup code — type this to pair a phone or browser:",
        f"      {p['setup_code']}",
        "",
        "  Atlas's own Wi-Fi (not needed to pair on your network):",
        f"      Name:      {p['hotspot_ssid']}",
        f"      Password:  {p['hotspot_password']}" + ("   (the standard password on every Atlas)"
                                                       if p.get("hotspot_shared") else ""),
        "",
    ])


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
            "hotspot_shared": bool(hotspot.get("shared")),
            "app_qr": app_qr, "wifi_qr": wifi_qr}

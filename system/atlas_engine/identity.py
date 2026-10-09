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

Names: a unit's code (Atlas-7KQ3PM, atlas-7kq3pm.local, the hotspot's name)
is derived one-way from the module serial (derive_short), so a reflash of the
same module gets the same name back and a cloned disk gets a different one;
the serial itself cannot be read back from it. Units provisioned before that
keep their random 4-character code. Units from before per-unit names answered
to atlas.local: they move to atlas-<code>.local and keep "atlas" as an alias
(device.json "aliases"), which the app publishes only while no other device
on the network claims it.
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


NAME_LEN = 6
_NAME_KEY = b"atlas-device-name-v1"


def derive_short(serial, salt=0, n=NAME_LEN):
    """n characters of ALPHABET, derived one-way from the module serial.

    HMAC-SHA256 bytes are rejection-sampled (b < 232 = 8 * 29) so every
    character is uniform. `salt` lets the factory move a unit whose code
    collided with another unit's in its registry. The output (29^6 ≈ 5.9e8
    values) is far smaller than the serial space, so it does not reveal the
    serial."""
    out, counter = [], 0
    while len(out) < n:
        digest = hmac.new(_NAME_KEY, f"{int(salt)}:{counter}:{serial}".encode(), hashlib.sha256).digest()
        for b in digest:
            if b < 232:
                out.append(ALPHABET[b % len(ALPHABET)])
                if len(out) == n:
                    break
        counter += 1
    return "".join(out)


HOSTNAME_RE = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


SERIAL_SOURCES = ("/sys/firmware/devicetree/base/serial-number",   # the Jetson module
                  "/sys/class/nvme/nvme0/serial")                  # fallback: the NVMe drive


def module_serial():
    for source in SERIAL_SOURCES:
        try:
            with open(paths.p(source), "rb") as f:
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


def _make_tls(name, hostname, key=None, aliases=()):
    """A self-signed certificate for this unit; a new key unless one is given
    (re-issuing with the same key keeps every app's pin valid)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    import ipaddress

    key = key or ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atlas")])
    dns = []
    for n in [hostname, *aliases, "atlas"]:
        if f"{n}.local" not in dns:
            dns.append(f"{n}.local")
    san = x509.SubjectAlternativeName([
        *[x509.DNSName(d) for d in dns], x509.DNSName("localhost"),
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


def create(hotspot_password=None, legacy_alias=None, reason="new", salt=0):
    """Generate a complete identity. Returns device.json contents."""
    serial = module_serial()
    short = derive_short(serial, salt) if serial else _code(NAME_LEN)
    name = f"Atlas-{short}"
    host = f"atlas-{short.lower()}"
    aliases = [legacy_alias] if legacy_alias and legacy_alias != host else []
    device_id = secrets.token_hex(16)
    key_pem, cert_pem, pin = _make_tls(name, host, aliases=aliases)
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
        "name_salt": int(salt),
        "hostname": host,
        "aliases": aliases,
        "serial": serial,
        "tls_spki_sha256": pin,
        "created_reason": reason,
    }
    fsutil.atomic_write_json(_d("device.json"), device, mode=0o644)
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
    # Restart, not reload: avahi reads the host name only at startup.
    subprocess.run(["systemctl", "try-restart", "avahi-daemon"], capture_output=True)
    return True


def ensure(adopt_hotspot_password=None, legacy_alias=None):
    """Create the identity if missing or cloned; move a legacy atlas.local unit
    to its own name; make the OS hostname match. Otherwise leave it alone."""
    device = load()
    if device:
        serial = module_serial()
        if serial and device.get("serial") and serial != device["serial"]:
            device = create(reason="cloned-storage")
            forget_hotspot_profiles()
            return device
        device = migrate_legacy_hostname(device)
        reconcile_hostname(device)
        return device
    pw = adopt_hotspot_password
    if pw in DEFAULT_HOTSPOT_PASSWORDS:
        pw = None
    return create(hotspot_password=pw, legacy_alias=legacy_alias,
                  reason="adopted" if legacy_alias else "new")


def migrate_legacy_hostname(device):
    """A unit adopted from before per-unit names is called "atlas" (atlas.local,
    shared by every such unit). Give it its own name from its code — the one
    already in its Wi-Fi name — and keep "atlas" as an alias. The TLS key, and
    with it every paired app's pin, stays the same."""
    import re
    host = str(device.get("hostname") or "")
    short = str(device.get("short") or "")
    if re.fullmatch(r"atlas-" + HOSTNAME_RE, host) or not re.fullmatch(r"[A-Za-z0-9]{3,12}", short):
        return device
    new_host = f"atlas-{short.lower()}"
    aliases = sorted({*(device.get("aliases") or []), host} - {"", new_host})
    device = dict(device, hostname=new_host, aliases=aliases, hostname_migrated_from=host)
    try:
        reissue_cert(device)
    except Exception:
        pass   # the old certificate still serves; browsers warn either way (self-signed)
    fsutil.atomic_write_json(_d("device.json"), device, mode=0o644)
    return device


def reissue_cert(device):
    """New certificate for the current names, same key (same pin)."""
    from cryptography.hazmat.primitives import serialization
    with open(_d("tls", "key.pem"), "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    _key_pem, cert_pem, pin = _make_tls(device["name"], device["hostname"], key=key,
                                        aliases=device.get("aliases") or ())
    if device.get("tls_spki_sha256") and pin != device["tls_spki_sha256"]:
        raise RuntimeError("re-issued certificate does not match the pinned key")
    fsutil.atomic_write_bytes(_d("tls", "cert.pem"), cert_pem, mode=0o644)
    if paths.ROOT == "/" and os.geteuid() == 0:
        import subprocess
        subprocess.run(["systemctl", "try-reload-or-restart", "nginx"], capture_output=True)


def reconcile_hostname(device):
    """The OS hostname follows device.json (e.g. after a migration, or if
    something else renamed the system)."""
    import socket
    host = device.get("hostname")
    if host and paths.ROOT == "/" and socket.gethostname() != host:
        apply_hostname(host)


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

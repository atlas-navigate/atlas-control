"""
The only network changes the Atlas app may make, executed as root.

Replaces the old blanket `NOPASSWD: /usr/bin/nmcli`. Every argument is
validated; Wi-Fi and hotspot passwords are written into NetworkManager
keyfiles (root-only, 0600) instead of being passed on a command line, where
any local process could read them from /proc and sudo would have logged them.
"""
import os
import re
import subprocess
import uuid

from . import paths

_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
HOTSPOT_ID = "Hotspot"


class NetError(ValueError):
    pass


def _run(cmd, timeout=40):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout.strip() + ("\n" + r.stderr.strip() if r.stderr.strip() else "")).strip()
    return {"rc": r.returncode, "out": out}


def check_iface(iface, required=False):
    if not iface:
        if required:
            raise NetError("interface required")
        return None
    if not isinstance(iface, str) or not _IFACE.match(iface) or iface.startswith("-"):
        raise NetError("invalid interface name")
    if not os.path.exists(f"/sys/class/net/{iface}"):
        raise NetError("no such interface")
    return iface


def check_ssid(ssid):
    if not isinstance(ssid, str):
        raise NetError("invalid SSID")
    raw = ssid.encode("utf-8")
    if not 1 <= len(raw) <= 32 or any(b < 0x20 or b == 0x7f for b in raw):
        raise NetError("SSID must be 1-32 bytes without control characters")
    return ssid


def check_psk(psk, required=True):
    if psk in (None, "") and not required:
        return None
    if not isinstance(psk, str):
        raise NetError("invalid password")
    if _HEX64.match(psk):
        return psk
    if not 8 <= len(psk) <= 63 or any(not (0x20 <= ord(c) <= 0x7e) for c in psk):
        raise NetError("Wi-Fi passwords are 8-63 printable characters")
    return psk


def _keyfile_escape(value):
    """GKeyFile string escaping (values are validated free of control chars)."""
    out = value.replace("\\", "\\\\")
    if out.startswith(" "):
        out = "\\s" + out[1:]
    return out


def _ssid_bytes(ssid):
    # The integer-list form sidesteps every quoting rule for odd SSIDs.
    return ";".join(str(b) for b in ssid.encode("utf-8")) + ";"


def nm_connections_dir():
    return paths.p("etc/NetworkManager/system-connections")


def _write_keyfile(filename, text):
    directory = nm_connections_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, filename)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return path


def _connections():
    r = _run(["nmcli", "-t", "--escape", "yes", "-f", "NAME,UUID,TYPE", "con", "show"], timeout=20)
    conns = []
    for line in r["out"].splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 3:
            conns.append({"name": parts[0].replace("\\:", ":").replace("\\\\", "\\"),
                          "uuid": parts[1], "type": parts[2]})
    return conns


def _delete_named(name, conn_type="802-11-wireless"):
    for c in _connections():
        if c["name"] == name and c["type"] == conn_type:
            _run(["nmcli", "con", "delete", "uuid", c["uuid"]], timeout=20)


def _security_for(ssid, iface):
    """'sae' for WPA3-only networks, else 'wpa-psk' — what `nmcli dev wifi
    connect` would have picked from the scan."""
    cmd = ["nmcli", "-t", "--escape", "yes", "-f", "SSID,SECURITY", "dev", "wifi", "list"]
    if iface:
        cmd += ["ifname", iface]
    for line in _run(cmd, timeout=20)["out"].splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 2 and parts[0].replace("\\:", ":") == ssid:
            sec = parts[1]
            if "WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec:
                return "sae"
            return "wpa-psk"
    return "wpa-psk"


def wifi_list(iface=None, rescan=False):
    iface = check_iface(iface)
    cmd = ["nmcli", "--escape", "yes", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]
    if iface:
        cmd += ["ifname", iface]
    if rescan:
        cmd += ["--rescan", "yes"]
    return _run(cmd, timeout=30)


def wifi_connect(ssid, password=None, iface=None, timeout=35):
    ssid = check_ssid(ssid)
    password = check_psk(password, required=False)
    iface = check_iface(iface)
    con_uuid = str(uuid.uuid4())
    lines = ["[connection]", f"id={_keyfile_escape(ssid)}", f"uuid={con_uuid}", "type=wifi",
             "autoconnect=true"]
    if iface:
        lines.append(f"interface-name={iface}")
    lines += ["", "[wifi]", "mode=infrastructure", f"ssid={_ssid_bytes(ssid)}"]
    if password:
        lines += ["", "[wifi-security]", f"key-mgmt={_security_for(ssid, iface)}",
                  f"psk={_keyfile_escape(password)}"]
    lines += ["", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", "addr-gen-mode=default", ""]
    _delete_named(ssid)
    path = _write_keyfile(f"atlas-wifi-{con_uuid[:8]}.nmconnection", "\n".join(lines))
    load = _run(["nmcli", "con", "load", path], timeout=20)
    if load["rc"] != 0:
        os.unlink(path)
        return load
    cmd = ["nmcli", "--wait", str(int(timeout)), "con", "up", "uuid", con_uuid]
    if iface:
        cmd += ["ifname", iface]
    return _run(cmd, timeout=timeout + 10)


def hotspot_up(iface, ssid, password):
    iface = check_iface(iface, required=True)
    ssid = check_ssid(ssid)
    password = check_psk(password)
    _delete_named(HOTSPOT_ID)
    con_uuid = str(uuid.uuid4())
    text = "\n".join([
        "[connection]", f"id={HOTSPOT_ID}", f"uuid={con_uuid}", "type=wifi",
        f"interface-name={iface}", "autoconnect=false", "",
        "[wifi]", "mode=ap", f"ssid={_ssid_bytes(ssid)}", "",
        "[wifi-security]", "key-mgmt=wpa-psk", "proto=rsn;", "pairwise=ccmp;", "group=ccmp;",
        f"psk={_keyfile_escape(password)}", "",
        "[ipv4]", "method=shared", "", "[ipv6]", "method=ignore", "",
    ])
    path = _write_keyfile("atlas-hotspot.nmconnection", text)
    load = _run(["nmcli", "con", "load", path], timeout=20)
    if load["rc"] != 0:
        return load
    return _run(["nmcli", "--wait", "25", "con", "up", "uuid", con_uuid], timeout=35)


def hotspot_down():
    return _run(["nmcli", "con", "down", "id", HOTSPOT_ID], timeout=20)


def disconnect(iface):
    iface = check_iface(iface, required=True)
    return _run(["nmcli", "dev", "disconnect", iface], timeout=15)


def set_autoconnect(name, enabled):
    if not isinstance(name, str) or not name or len(name) > 64 or name.startswith("-") or \
            any(ord(c) < 0x20 for c in name):
        raise NetError("invalid connection name")
    if name not in {c["name"] for c in _connections()}:
        raise NetError("no such connection")
    return _run(["nmcli", "con", "modify", "id", name, "connection.autoconnect",
                 "yes" if enabled else "no"], timeout=15)


def forget_all_wifi():
    """Factory reset: remove every saved client Wi-Fi network (keeps Hotspot)."""
    removed = 0
    for c in _connections():
        if c["type"] == "802-11-wireless" and c["name"] != HOTSPOT_ID:
            _run(["nmcli", "con", "delete", "uuid", c["uuid"]], timeout=20)
            removed += 1
    return {"rc": 0, "out": f"removed {removed}"}

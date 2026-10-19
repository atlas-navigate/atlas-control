"""Install a release's system files (units, nginx, firewall, udev, avahi)
transactionally: back up what is there, write the new files, validate the
whole configuration, and put everything back at once if validation fails.

system/files.json in a release lists the files:

    {"files": [{"src": "systemd/atlas-control.service",
                "dst": "/etc/systemd/system/atlas-control.service",
                "mode": "0644", "kind": "systemd", "template": false}, …],
     "settings": [{"file": "/etc/avahi/avahi-daemon.conf", "kind": "avahi-daemon",
                   "values": {"server": {"use-ipv6": "no", "allow-interfaces": null}}}],
     "symlinks": [{"link": "/etc/nginx/sites-enabled/atlas",
                   "target": "/etc/nginx/sites-available/atlas"}],
     "enable": ["atlas-helper.socket", …],
     "power_mode": "MAXN_SUPER"}

Templates substitute @NAME@ placeholders from template_vars(). "settings"
edit single keys of a configuration file the distribution owns (a value of
null comments the key out); the rest of that file is left as it is. The
power mode is applied by the engine (perf.ensure_power_mode), not here.
"""
import json
import os
import shutil
import xml.etree.ElementTree as ET

from . import fsutil, paths

KINDS_RELOAD = {
    "systemd": [["systemctl", "daemon-reload"]],
    "nginx": [["systemctl", "reload-or-restart", "nginx"]],
    "udev": [["udevadm", "control", "--reload"], ["udevadm", "trigger", "--subsystem-match=tty"]],
    "firewall": [["systemctl", "try-restart", "atlas-firewall.service"]],
    "avahi": [["systemctl", "try-reload-or-restart", "avahi-daemon"]],
    # A drop-in for the Ollama service (installed by install.sh): reload
    # systemd and restart Ollama so its runners pick the settings up. Runs
    # before the app restarts, which then loads Ray's models again.
    "ollama": [["systemctl", "daemon-reload"], ["systemctl", "try-restart", "ollama.service"]],
    # journald only reads its config at start; a restart keeps services' log
    # streams (systemd holds them for it) and applies the size cap at once.
    "journald": [["systemctl", "restart", "systemd-journald"]],
    # Kernel settings: applied at once, and by systemd-sysctl at every boot.
    "sysctl": [["sysctl", "-q", "--system"]],
    # avahi-daemon.conf itself (not a service file): only a restart reads it.
    "avahi-daemon": [["systemctl", "try-restart", "avahi-daemon"]],
    # A drop-in for nginx's own unit (its sandbox): a restart applies it.
    "nginx-unit": [["systemctl", "daemon-reload"], ["systemctl", "try-restart", "nginx"]],
}


class SystemFilesError(Exception):
    pass


def template_vars():
    device = fsutil.read_json(os.path.join(paths.IDENTITY, "device.json"), {}) or {}
    return {
        "ATLAS_DEVICE_NAME": _xml_safe(device.get("name") or "Atlas"),
        "ATLAS_DEVICE_ID": _xml_safe(device.get("device_id") or "unprovisioned"),
        "ATLAS_TLS_PIN": _xml_safe(device.get("tls_spki_sha256") or ""),
        "ATLAS_HOSTNAME": _xml_safe(device.get("hostname") or "atlas"),
        "ATLAS_STATE_DIR": paths.APP_STATE,
        "ATLAS_SERVICE_USER": paths.SERVICE_USER,
        "ATLAS_TLS_CERT": "/etc/atlas/identity/tls/cert.pem",
        "ATLAS_TLS_KEY": "/etc/atlas/identity/tls/key.pem",
        "ATLAS_PROXY_SECRET_CONF": "/etc/atlas/identity/nginx-proxy-secret.conf",
        "ATLAS_CURRENT": "/opt/atlas/current",
        "OLLAMA_HOME": _ollama_home(),
    }


def _ollama_home():
    """Home directory of the account the Ollama base unit runs as."""
    import pwd
    import re
    user = "ollama"
    for unit in ("/etc/systemd/system/ollama.service", "/usr/lib/systemd/system/ollama.service"):
        try:
            m = re.search(r"^User=(\S+)", open(paths.p(unit), encoding="utf-8").read(), re.M)
        except OSError:
            continue
        if m:
            user = m.group(1)
            break
    try:
        return pwd.getpwnam(user).pw_dir
    except KeyError:
        return "/usr/share/ollama"


def _xml_safe(value):
    return "".join(c for c in str(value) if c.isalnum() or c in "-_.:= ")


def load_spec(release_dir):
    with open(os.path.join(release_dir, "system", "files.json"), encoding="utf-8") as f:
        spec = json.load(f)
    for entry in spec.get("files", []):
        src = os.path.normpath(entry["src"])
        if src.startswith("..") or os.path.isabs(src):
            raise SystemFilesError(f"bad source path {entry['src']}")
        if not os.path.isabs(entry["dst"]):
            raise SystemFilesError(f"destination must be absolute: {entry['dst']}")
    return spec


def render(release_dir, entry):
    with open(os.path.join(release_dir, "system", entry["src"]), "rb") as f:
        data = f.read()
    if entry.get("template"):
        text = data.decode("utf-8")
        for k, v in template_vars().items():
            text = text.replace(f"@{k}@", v)
        data = text.encode("utf-8")
    return data


def install(release_dir, backup_dir, runner):
    """Write the release's system files; return the list of kinds changed.

    On any validation failure the previous files are restored before the
    error propagates, so the running system never keeps a broken config.
    """
    spec = load_spec(release_dir)
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    record_path = os.path.join(backup_dir, "record.json")
    record = fsutil.read_json(record_path, {"files": [], "symlinks": []})
    done = {r["dst"] for r in record["files"]}
    changed = set()
    for i, entry in enumerate(spec.get("files", [])):
        dst = paths.p(entry["dst"])
        new = render(release_dir, entry)
        mode = int(entry.get("mode", "0644"), 8)
        if os.path.isfile(dst) and not os.path.islink(dst):
            with open(dst, "rb") as f:
                if f.read() == new and (os.stat(dst).st_mode & 0o7777) == mode:
                    continue
        if dst not in done:
            saved = None
            existed = os.path.lexists(dst)
            if existed and os.path.isfile(dst):
                saved = os.path.join(backup_dir, f"file-{i}")
                shutil.copy2(dst, saved, follow_symlinks=False)
            record["files"].append({"dst": dst, "existed": existed, "saved": saved,
                                    "mode": (os.lstat(dst).st_mode & 0o7777) if existed else None,
                                    "kind": entry.get("kind")})
            fsutil.atomic_write_json(record_path, record, mode=0o600)
        fsutil.atomic_write_bytes(dst, new, mode=mode)
        changed.add(entry.get("kind") or "plain")
    for i, dst_abs in enumerate(spec.get("remove", [])):
        dst = paths.p(dst_abs)
        if os.path.islink(dst) and dst not in done:
            # e.g. nginx's sites-enabled/default: restore() puts the link back.
            record["symlinks"].append({"link": dst, "old": os.readlink(dst)})
            fsutil.atomic_write_json(record_path, record, mode=0o600)
            os.unlink(dst)
            changed.add("nginx" if "/nginx/" in dst else "plain")
            continue
        if not os.path.isfile(dst) or dst in done:
            continue
        saved = os.path.join(backup_dir, f"removed-{i}")
        shutil.copy2(dst, saved, follow_symlinks=False)
        record["files"].append({"dst": dst, "existed": True, "saved": saved,
                                "mode": os.lstat(dst).st_mode & 0o7777, "kind": "removed"})
        fsutil.atomic_write_json(record_path, record, mode=0o600)
        os.unlink(dst)
        changed.add("avahi" if "/avahi/" in dst else "nginx" if "/nginx/" in dst else "plain")
    for i, setting in enumerate(spec.get("settings", [])):
        dst = paths.p(setting["file"])
        if not os.path.isfile(dst):
            continue                      # the package is not installed: nothing to configure
        with open(dst, encoding="utf-8") as f:
            old = f.read()
        new = edit_ini(old, setting["values"])
        if new == old:
            continue
        if dst not in done:
            saved = os.path.join(backup_dir, f"setting-{i}")
            shutil.copy2(dst, saved, follow_symlinks=False)
            record["files"].append({"dst": dst, "existed": True, "saved": saved,
                                    "mode": os.lstat(dst).st_mode & 0o7777, "kind": setting.get("kind")})
            fsutil.atomic_write_json(record_path, record, mode=0o600)
        fsutil.atomic_write_bytes(dst, new.encode("utf-8"), mode=os.lstat(dst).st_mode & 0o7777)
        changed.add(setting.get("kind") or "plain")
    for link in spec.get("symlinks", []):
        dst = paths.p(link["link"])
        target = link["target"]
        if os.path.islink(dst) and os.readlink(dst) == target:
            continue
        record["symlinks"].append({"link": dst, "old": os.readlink(dst) if os.path.islink(dst) else None})
        fsutil.atomic_write_json(record_path, record, mode=0o600)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        fsutil.atomic_symlink(target, dst)
        changed.add("nginx" if "/nginx/" in dst else "plain")
    try:
        validate(spec, changed, runner)
    except Exception:
        restore(backup_dir)
        raise
    return sorted(changed)


def edit_ini(text, values):
    """Set keys in an INI-style file: {"section": {"key": "value" | None}}.

    A key keeps its place: the first line for it in its section, active or
    commented out, becomes `key=value` (None: `#key=`), and any further
    active lines for it are commented out. A missing key goes right after its
    section header, a missing section at the end. Everything else (comments,
    other keys, order) is left untouched, so the edit is idempotent."""
    lines = text.splitlines()
    for section, keys in values.items():
        start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            start = len(lines) - 1
        for key, value in keys.items():
            want = f"#{key}=" if value is None else f"{key}={value}"
            end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
                       len(lines))
            hits = [i for i in range(start + 1, end)
                    if lines[i].lstrip("#; \t").split("=", 1)[0].strip() == key and "=" in lines[i]]
            active = [i for i in hits if not lines[i].lstrip().startswith(("#", ";"))]
            first = (active or hits or [None])[0]
            if first is None:
                lines.insert(start + 1, want)
                continue
            if value is None and not active:
                continue                  # already commented out, whatever it says
            lines[first] = want
            for i in active:
                if i != first:
                    lines[i] = "#" + lines[i].lstrip()
    out = "\n".join(lines)
    return out + "\n" if text.endswith("\n") or not text else out


def validate(spec, changed, runner):
    if "nginx" in changed:
        r = runner.run(["nginx", "-t"], timeout=60)
        if r.returncode != 0:
            raise SystemFilesError("nginx configuration invalid: " + (r.stderr or r.stdout).strip()[-400:])
    for entry in spec.get("files", []):
        dst = paths.p(entry["dst"])
        kind = entry.get("kind")
        if kind not in changed:
            continue
        if kind == "systemd":
            r = runner.run(["systemd-analyze", "verify", "--man=no", dst], timeout=60)
            if r.returncode != 0:
                raise SystemFilesError(f"unit {os.path.basename(dst)} invalid: {(r.stderr or r.stdout).strip()[-400:]}")
        elif kind == "firewall":
            from . import firewall
            r = firewall.check(dst, runner)
            if r.returncode != 0:
                message = "firewall rules invalid: " + (r.stderr or r.stdout).strip()[-400:]
                # Rules for a firewall that is switched off cannot lock anyone
                # out; report them, but only block the install once it is on.
                if firewall.enabled(runner) or "atlas-firewall.service" in spec.get("enable", []):
                    raise SystemFilesError(message)
                print(f"atlas-engine: warning: {message}", flush=True)
        elif kind == "udev":
            r = runner.run(["udevadm", "verify", dst], timeout=60)
            if r.returncode != 0:
                raise SystemFilesError("udev rules invalid: " + (r.stderr or r.stdout).strip()[-400:])
        elif kind == "nginx-unit":
            r = runner.run(["systemd-analyze", "verify", "--man=no", "nginx.service"], timeout=60)
            if r.returncode != 0:
                raise SystemFilesError("nginx sandbox invalid: " + (r.stderr or r.stdout).strip()[-400:])
        elif kind == "ollama":
            if os.path.exists(paths.p("/etc/systemd/system/ollama.service")) or \
                    os.path.exists(paths.p("/usr/lib/systemd/system/ollama.service")):
                r = runner.run(["systemd-analyze", "verify", "--man=no", "ollama.service"], timeout=60)
                if r.returncode != 0:
                    raise SystemFilesError("Ollama settings invalid: " + (r.stderr or r.stdout).strip()[-400:])
        elif kind == "avahi":
            try:
                ET.parse(dst)
            except ET.ParseError as exc:
                raise SystemFilesError(f"avahi service file invalid: {exc}")
        elif kind == "sysctl":
            with open(dst, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith(("#", ";")) and "=" not in line:
                        raise SystemFilesError(f"{os.path.basename(dst)}:{n}: not key = value")
    for setting in spec.get("settings", []):
        if (setting.get("kind") or "plain") not in changed:
            continue
        import configparser
        try:
            configparser.ConfigParser(strict=False, interpolation=None).read_string(
                open(paths.p(setting["file"]), encoding="utf-8").read())
        except configparser.Error as exc:
            raise SystemFilesError(f"{setting['file']} invalid after edit: {exc}")


def restore(backup_dir):
    """Undo install(): put every recorded file and symlink back."""
    record = fsutil.read_json(os.path.join(backup_dir, "record.json"))
    if not record:
        return
    for r in reversed(record.get("symlinks", [])):
        try:
            if r["old"] is None:
                if os.path.islink(r["link"]):
                    os.unlink(r["link"])
            else:
                fsutil.atomic_symlink(r["old"], r["link"])
        except OSError:
            pass
    for r in reversed(record.get("files", [])):
        dst = r["dst"]
        if r["existed"] and r["saved"]:
            with open(r["saved"], "rb") as f:
                fsutil.atomic_write_bytes(dst, f.read(), mode=r["mode"] or 0o644)
        elif not r["existed"]:
            try:
                os.unlink(dst)
            except FileNotFoundError:
                pass


def rerender_installed(kinds=("avahi",), runner=None):
    """Re-render the installed release's templated files of these kinds from
    the current identity (atlas-identity ensure, at boot). Skipped while an
    update is in progress — the engine renders them itself then. Returns the
    destinations rewritten."""
    journal = fsutil.read_json(paths.JOURNAL, {}) or {}
    if journal.get("state") not in (None, "IDLE"):
        return []
    current = os.path.realpath(paths.CURRENT)
    try:
        spec = load_spec(current)
    except (OSError, ValueError, SystemFilesError):
        return []
    written = []
    for entry in spec.get("files", []):
        if entry.get("kind") not in kinds or not entry.get("template"):
            continue
        dst = paths.p(entry["dst"])
        try:
            new = render(current, entry)
            with open(dst, "rb") as f:
                if f.read() == new:
                    continue
        except FileNotFoundError:
            pass
        except OSError:
            continue
        fsutil.atomic_write_bytes(dst, new, mode=int(entry.get("mode", "0644"), 8))
        written.append(dst)
    if written and paths.ROOT == "/":
        import subprocess
        for kind in kinds:
            for cmd in KINDS_RELOAD.get(kind, []):
                subprocess.run(cmd, capture_output=True, timeout=60)
    return written


def reload_changed(kinds, spec_or_none, runner):
    for kind in kinds:
        for cmd in KINDS_RELOAD.get(kind, []):
            runner.run(cmd, timeout=120)
    if spec_or_none:
        for unit in spec_or_none.get("enable", []):
            runner.run(["systemctl", "enable", unit], timeout=60)

"""Install a release's system files (units, nginx, firewall, udev, avahi)
transactionally: back up what is there, write the new files, validate the
whole configuration, and put everything back at once if validation fails.

system/files.json in a release lists the files:

    {"files": [{"src": "systemd/atlas-control.service",
                "dst": "/etc/systemd/system/atlas-control.service",
                "mode": "0644", "kind": "systemd", "template": false}, …],
     "symlinks": [{"link": "/etc/nginx/sites-enabled/atlas",
                   "target": "/etc/nginx/sites-available/atlas"}],
     "enable": ["atlas-helper.socket", …]}

Templates substitute @NAME@ placeholders from template_vars().
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
    }


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
        elif kind == "avahi":
            try:
                ET.parse(dst)
            except ET.ParseError as exc:
                raise SystemFilesError(f"avahi service file invalid: {exc}")


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

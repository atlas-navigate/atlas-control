"""
Which Atlas build is running.

A release directory carries release.json, written by tools/release/
build_release.py: {"version", "seq", "git_sha", "channel", "schema_target"}.
`seq` increases with every published release and is what the updater uses to
refuse downgrades — dates are never trusted, because an offline Atlas can
boot with its clock at 1970.

A developer checkout has no release.json; the version is VERSION plus the
git commit, and seq is 0 so any signed release counts as newer.
"""
import json
import threading

import atlas_blocking
import atlas_paths

_LOCK = threading.Lock()
_CACHE = {}


def _read_release_json():
    try:
        with open(atlas_paths.code("release.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("version"):
            return data
    except (OSError, ValueError):
        pass
    return None


def _dev_info():
    try:
        with open(atlas_paths.code("VERSION"), encoding="utf-8") as f:
            base = f.read().strip() or "0.0.0"
    except OSError:
        base = "0.0.0"
    sha = ""
    try:
        r = atlas_blocking.run(["git", "-C", atlas_paths.CODE_DIR, "rev-parse", "--short", "HEAD"],
                               capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            sha = r.stdout.strip()
    except Exception:
        pass
    return {
        "version": f"{base}-dev" + (f"+{sha}" if sha else ""),
        "seq": 0,
        "git_sha": sha,
        "channel": "dev",
        "dev": True,
    }


def info():
    """Return (and cache) the running build's version record."""
    with _LOCK:
        if not _CACHE:
            _CACHE.update(_read_release_json() or _dev_info())
            _CACHE.setdefault("dev", False)
        return dict(_CACHE)


def version():
    return info()["version"]


def seq():
    try:
        return int(info().get("seq") or 0)
    except (TypeError, ValueError):
        return 0

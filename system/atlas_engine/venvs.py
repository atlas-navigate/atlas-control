"""Per-dependency-set virtual environments, built in place from bundled wheels.

A venv cannot be moved after creation (absolute paths in its scripts), so it
is built directly at /opt/atlas/venvs/<key>/ and marked complete last.
Releases whose requirements.lock is identical share a venv, so most updates
reuse the running one; the previous release's venv is kept for rollback.
"""
import hashlib
import os
import platform
import shutil
import sys

from . import fsutil, paths

COMPLETE = ".atlas-complete"


def venv_key(release_dir):
    with open(os.path.join(release_dir, "requirements.lock"), "rb") as f:
        lock = f.read()
    ident = lock + f"|py{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}".encode()
    return hashlib.sha256(ident).hexdigest()[:24]


def is_complete(key):
    return os.path.exists(os.path.join(paths.VENVS, key, COMPLETE))


def ensure_venv(release_dir, runner, python="/usr/bin/python3"):
    """Return the key of a complete venv for this release, building if needed."""
    key = venv_key(release_dir)
    vdir = os.path.join(paths.VENVS, key)
    if is_complete(key):
        return key
    if os.path.lexists(vdir):
        shutil.rmtree(vdir)
    os.makedirs(paths.VENVS, mode=0o755, exist_ok=True)
    # --system-site-packages: BlueZ (dbus, gi) and numpy/Pillow come from the
    # distribution, exactly as on every Atlas since the first install.
    runner.run([python, "-m", "venv", "--system-site-packages", vdir], timeout=300, check=True)
    runner.run([os.path.join(vdir, "bin", "python3"), "-m", "pip", "install", "--no-index",
                "--no-deps", "--require-hashes", "--no-compile", "--disable-pip-version-check",
                "--find-links", os.path.join(release_dir, "wheels"),
                "-r", os.path.join(release_dir, "requirements.lock")],
               timeout=1800, check=True)
    runner.run([os.path.join(vdir, "bin", "python3"), "-m", "compileall", "-q", vdir], timeout=900)
    fsutil.atomic_write_bytes(os.path.join(vdir, COMPLETE), b"ok\n")
    return key


def link_release(release_dir, key):
    """releases/<v>/venv -> ../../venvs/<key> (relative, so the tree is relocatable)."""
    fsutil.atomic_symlink(os.path.join("..", "..", "venvs", key), os.path.join(release_dir, "venv"))

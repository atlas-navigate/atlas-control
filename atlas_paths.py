"""
Where Atlas keeps its code versus the device's own state.

A production Atlas runs its code from a read-only, root-owned release
directory (/opt/atlas/releases/<version>) and keeps everything the device
accumulates — databases, settings, downloaded maps, routing graphs, logs,
update downloads — in a separate state directory that survives updates and
rollbacks. ATLAS_STATE_DIR names that directory; the service unit sets it.

On a developer checkout ATLAS_STATE_DIR is usually unset, and the state
directory defaults to the code directory, which is exactly the layout every
Atlas used before releases existed. Nothing here creates directories at import
time: the release tree is read-only and tests point ATLAS_STATE_DIR at a temp
dir, so callers create what they write.
"""
import os

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.abspath(os.environ.get("ATLAS_STATE_DIR") or CODE_DIR)


def code(*parts):
    """Path inside the (possibly read-only) code/release directory."""
    return os.path.join(CODE_DIR, *parts)


def state(*parts):
    """Path inside the device state directory."""
    return os.path.join(STATE_DIR, *parts)


DATA_DIR = state("data")
LOG_DIR = state("logs")
CACHE_DIR = state("cache")
UPDATES_DIR = state("updates")
BACKUP_DIR = state("data", "backups")

# Large read-mostly datasets provisioned at the factory (maps, geodata,
# routing graphs). They live beside the state because they are per-device
# payload, not code, and are never shipped inside a release.
TILES_DIR = state("static", "tiles")
FONTS_DIR = state("static", "fonts")
STATIC_DATA_DIR = state("static", "data")
OSRM_STATES_DIR = state("osrm-data", "states")

DB_PATH = state("data", "meshtastic.db")


def data_file(name):
    """Path of a small state file that used to live at the state-dir root.

    hotspot_config.json and osrm_active.json were written next to app.py;
    they now belong in data/ so the service account never needs write access
    to the directory root. The first call copies a legacy file into data/
    (the original is left in place so an older release rolled back to still
    finds it) and every caller then reads and writes the new home.
    """
    new = os.path.join(DATA_DIR, name)
    legacy = state(name)
    if not os.path.exists(new) and os.path.isfile(legacy):
        try:
            import shutil
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.copy2(legacy, new)
        except OSError:
            return legacy
    return new


def code_is_writable():
    """True on a developer checkout; False inside a root-owned release."""
    return os.access(CODE_DIR, os.W_OK)

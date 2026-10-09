"""Every filesystem location the engine touches, rooted at ATLAS_ROOT.

ATLAS_ROOT is "/" on a device. Tests point it at a temporary directory so
the whole install/rollback machinery runs in a sandbox.
"""
import os

ROOT = os.path.abspath(os.environ.get("ATLAS_ROOT", "/"))


def p(*parts):
    return os.path.join(ROOT, *[x.lstrip("/") for x in parts])


# Releases and their virtual environments.
OPT = p("opt/atlas")
RELEASES = p("opt/atlas/releases")
VENVS = p("opt/atlas/venvs")
CURRENT = p("opt/atlas/current")
PREVIOUS = p("opt/atlas/previous")
FACTORY = p("opt/atlas/factory")

# The engine itself and the keys it trusts.
LIB = p("usr/local/lib/atlas")
BIN = p("usr/local/lib/atlas/bin")
ENGINE_DIR = p("usr/local/lib/atlas/engine")
KEYRING = p("usr/local/lib/atlas/keys/keyring.json")

# Device configuration (outlives factory reset).
ETC = p("etc/atlas")
DEV_MODE_FLAG = p("etc/atlas/dev-mode")
DEV_KEYS = p("etc/atlas/dev-keys")
IDENTITY = p("etc/atlas/identity")

# Update transaction state (root only) and its public projection.
UPDATE_STATE = p("var/lib/atlas-update")
JOURNAL = p("var/lib/atlas-update/journal.json")
REQUEST = p("var/lib/atlas-update/request.json")
INSTALLED = p("var/lib/atlas-update/installed.json")
TX = p("var/lib/atlas-update/tx")
SYSBACKUP = p("var/lib/atlas-update/sysbackup")
DIAG = p("var/lib/atlas-update/diag")
PUBLIC = p("var/lib/atlas-update/public")
LAST_RESULT = p("var/lib/atlas-update/public/last_result.json")
RUN = p("run/atlas-update")
STATUS = p("run/atlas-update/status.json")
LOCK = p("run/atlas-update/lock")

# The wall clock (clock.py): the RTCs, timesyncd's saved clock and sync flag,
# and what Atlas knows about the clock's basis.
RTC_CLASS = p("sys/class/rtc")
TIMESYNC_CLOCK = p("var/lib/systemd/timesync/clock")
TIMESYNC_SYNCED = p("run/systemd/timesync/synchronized")
CLOCK_RUN = p("run/atlas-clock")
CLOCK_STATE = p("run/atlas-clock/state.json")

# The app's state directory; downloads are handed over from here.
APP_STATE = os.path.abspath(os.environ.get("ATLAS_STATE_DIR") or p("atlas_data/atlas-control"))
HANDOFF = os.path.join(APP_STATE, "updates", "ready")

SERVICE_USER = os.environ.get("ATLAS_SERVICE_USER", "atlas")
APP_UNIT = "atlas-control.service"
APP_PORT = 5000


def dev_mode():
    return os.path.exists(DEV_MODE_FLAG)

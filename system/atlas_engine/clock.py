"""
Atlas's wall clock on a unit whose RTC may have lost power.

The PMIC RTC (rtc0) has no backup cell on most units, so after the unit is
unplugged it counts from 1970 again. Its driver registers about 15 s into
boot, and the kernel then sets the system clock from it (hctosys) — after
systemd and systemd-timesyncd have already moved the clock forward. Offline
the whole session would then run in 1970, and avahi (whose timers follow the
wall clock) can stall on the backward step.

atlas-clock.service runs `atlas-engine clock boot` before systemd-timesyncd,
avahi and the app:

  1. wait (bounded) until the RTC has registered and done its hctosys step;
  2. never move the clock backward, but floor it at the release's build time
     (timesyncd, which starts next, then restores its own saved clock — it
     saves every 60 s even offline — on top of this);
  3. record what the clock is based on in /run/atlas-clock/state.json, for
     the app: unset | restored | rtc | phone | gps (ntp is read live from
     timesyncd's /run/systemd/timesync/synchronized).

Later corrections come through `set_time` (helper verb clock.set): a GPS fix
may step the clock either way; a paired phone may only move it forward; both
are ignored while NTP is synchronized. Nothing here ever makes avahi or
discovery wait for NTP or a network.
"""
import fcntl
import json
import math
import os
import time

from . import fsutil, paths

FLOOR_MIN = 1735689600            # 2025-01-01: any clock before this is wrong
FLOOR_MAX = 4102444800            # 2100-01-01: any clock after this is wrong
BOOT_WAIT_UPTIME_S = 20.0         # stop waiting for the RTC this far into boot
HCTOSYS_SETTLE_S = 1.0            # the node appears just before hctosys runs
PHONE_MAX_AHEAD_OF_BUILD_S = 20 * 365 * 86400
STEP_EPSILON_S = 2.0

STATES = ("unset", "restored", "rtc", "phone", "gps", "ntp")


# ── seams for tests ──────────────────────────────────────────────────────────
def now():
    return time.time()


def set_clock(epoch):
    time.clock_settime(time.CLOCK_REALTIME, float(epoch))


def sleep(s):
    time.sleep(s)


def run(cmd, timeout=20):
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).returncode
    except (OSError, subprocess.SubprocessError):
        return None


# ── facts ────────────────────────────────────────────────────────────────────
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip().strip("\x00")
    except OSError:
        return None


def uptime():
    v = _read(paths.p("proc/uptime"))
    try:
        return float(v.split()[0])
    except (AttributeError, ValueError, IndexError):
        return 0.0


def rtc_expected():
    """Does the device tree say this board has a (late-registering) rtc0?"""
    return os.path.exists(paths.p("proc/device-tree/aliases/rtc0"))


def rtc_info(name="rtc0"):
    base = os.path.join(paths.RTC_CLASS, name)
    if not os.path.isdir(base):
        return None
    since = _read(os.path.join(base, "since_epoch"))
    return {"device": name, "name": _read(os.path.join(base, "name")),
            "hctosys": _read(os.path.join(base, "hctosys")) == "1",
            "since_epoch": int(since) if since and since.isdigit() else None}


def ntp_synchronized():
    return os.path.exists(paths.TIMESYNC_SYNCED)


def _valid(epoch):
    return epoch is not None and FLOOR_MIN <= epoch < FLOOR_MAX


def built_time():
    rel = fsutil.read_json(os.path.join(paths.CURRENT, "release.json"), {}) or {}
    try:
        built = int(rel.get("built"))
    except (TypeError, ValueError):
        return None
    return built if _valid(built) else None


def saved_time():
    """When timesyncd last saved the clock (it does so every 60 s, synced or not)."""
    try:
        t = os.stat(paths.TIMESYNC_CLOCK).st_mtime
    except OSError:
        return None
    return t if _valid(t) else None


def floor():
    """(epoch, source) the clock must not be below, or (None, None)."""
    best = (None, None)
    for value, source in ((built_time(), "build"), (saved_time(), "saved")):
        if value is not None and (best[0] is None or value > best[0]):
            best = (value, source)
    return best


def classify_rtc(info, floor_value):
    """kept-time: the RTC survived (backup cell or never unplugged);
    lost-power: it restarted from zero; stale: it runs but is behind the
    last saved clock; none: no RTC."""
    if not info or info.get("since_epoch") is None:
        return "none"
    t = info["since_epoch"]
    if t < FLOOR_MIN:
        return "lost-power"
    if floor_value is None or t >= floor_value - 120:
        return "kept-time"
    return "stale"


# ── state file ───────────────────────────────────────────────────────────────
def read_state():
    return fsutil.read_json(paths.CLOCK_STATE, {}) or {}


def effective_state(state=None):
    """The clock's basis right now: ntp wins whenever timesyncd is synchronized."""
    if ntp_synchronized():
        return "ntp"
    s = (state if state is not None else read_state()).get("state")
    return s if s in STATES else "unset"


def _write_state(update):
    os.makedirs(paths.CLOCK_RUN, mode=0o755, exist_ok=True)
    state = read_state()
    state.update(update)
    state["updated"] = now()
    fsutil.atomic_write_json(paths.CLOCK_STATE, state, mode=0o644)
    return state


class _Lock:
    def __enter__(self):
        os.makedirs(paths.CLOCK_RUN, mode=0o755, exist_ok=True)
        self.fd = os.open(os.path.join(paths.CLOCK_RUN, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ── boot ─────────────────────────────────────────────────────────────────────
def wait_for_rtc(deadline_uptime=BOOT_WAIT_UPTIME_S, poll=0.1):
    """Wait until rtc0 has registered (and the kernel has set the clock from it).

    Returns (outcome, uptime): registered | timeout | skipped.
    """
    if not rtc_expected():
        return "skipped", uptime()
    seen_at = None
    while True:
        up = uptime()
        info = rtc_info()
        if info is not None:
            if info["hctosys"]:
                return "registered", up
            seen_at = seen_at if seen_at is not None else up
            if up - seen_at >= HCTOSYS_SETTLE_S:
                return "registered", up      # RTC unreadable: the kernel won't step
        if up >= deadline_uptime:
            return "timeout", up
        sleep(poll)


def _apply_floor():
    """Move the clock up to the floor if it is below. Returns seconds stepped (0 if none)."""
    value, source = floor()
    t = now()
    if value is not None and t < value - 1:
        set_clock(value)
        return value - t, value, source
    return 0.0, value, source


def boot():
    outcome, at = wait_for_rtc()
    with _Lock():
        info = rtc_info()
        floor_value, _ = floor()
        rtc_status = classify_rtc(info, floor_value)
        stepped, value, source = _apply_floor()
        if ntp_synchronized():
            basis = "ntp"
        elif stepped:
            basis = "restored"
        elif rtc_status == "kept-time":
            basis = "rtc"
        elif value is not None:
            basis = "restored"   # already at/above the floor, but nothing vouches for it
        else:
            basis = "unset"
        state = _write_state({
            "state": basis, "since": now(), "source": "rtc" if basis == "rtc" else source,
            "stepped_s": round(stepped, 3), "floor": value, "floor_from": source,
            "rtc": dict(info or {}, status=rtc_status, wait=outcome, at_uptime=round(at, 2)),
            "boot_id": _read(paths.p("proc/sys/kernel/random/boot_id")),
        })
    return state


def rtc_added(settle_s=5.0, poll=0.1):
    """udev backstop: an RTC registered after boot() stopped waiting. Wait for
    its hctosys step, re-apply the floor, and restart avahi if the clock had
    gone backward under it."""
    t0 = uptime()
    while uptime() - t0 < settle_s:
        info = rtc_info()
        if info and info["hctosys"]:
            break
        sleep(poll)
    with _Lock():
        stepped, value, source = _apply_floor()
        update = {"rtc_backstop": {"at_uptime": round(uptime(), 2), "stepped_s": round(stepped, 3)}}
        if stepped > 60:
            info = rtc_info()
            update.update(state="restored", since=now(), source=source, stepped_s=round(stepped, 3),
                          rtc=dict(info or {}, status=classify_rtc(info, value), wait="late"))
            run(["systemctl", "try-restart", "avahi-daemon.service"])
        return _write_state(update)


# ── corrections from GPS and paired phones (helper verb clock.set) ───────────
def set_time(epoch, source):
    """Apply a time from a trusted source. Returns a dict describing the outcome."""
    if source not in ("gps", "phone"):
        raise ValueError("source must be gps or phone")
    try:
        epoch = float(epoch)
    except (TypeError, ValueError):
        raise ValueError("epoch must be a number")
    if not math.isfinite(epoch) or not _valid(epoch):
        raise ValueError("implausible time")
    built = built_time()
    if built is not None and epoch < built - 86400:
        return {"applied": False, "reason": "before this software was built"}
    if ntp_synchronized():
        return {"applied": False, "reason": "ntp"}
    with _Lock():
        current = effective_state()
        delta = epoch - now()
        if source == "phone":
            if current == "gps":
                return {"applied": False, "reason": "gps"}
            if built is not None and epoch > built + PHONE_MAX_AHEAD_OF_BUILD_S:
                return {"applied": False, "reason": "implausible"}
            if delta <= STEP_EPSILON_S:
                # Never backward on a phone's word; already as late or later.
                if current in ("unset", "restored", "rtc") and delta > -STEP_EPSILON_S:
                    _write_state({"state": "phone", "since": now(), "source": "phone", "stepped_s": 0.0})
                    return {"applied": True, "stepped_s": 0.0}
                return {"applied": False, "reason": "not ahead"}
        if abs(delta) > STEP_EPSILON_S:
            set_clock(epoch)
        else:
            delta = 0.0
        if delta < -STEP_EPSILON_S:
            # avahi's timers follow the wall clock: after a backward step its
            # scheduled announcements would wait out the difference.
            run(["systemctl", "try-restart", "avahi-daemon.service"])
        if source == "gps":
            run(["hwclock", "--systohc", "--utc"])  # keeps time on units with an RTC cell
        _write_state({"state": source, "since": now(), "source": source, "stepped_s": round(delta, 3)})
        return {"applied": True, "stepped_s": round(delta, 3)}


def status():
    state = read_state()
    return dict(state, effective=effective_state(state), now=now(), ntp=ntp_synchronized())


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "boot":
        result = boot()
    elif cmd == "rtc":
        result = rtc_added()
    elif cmd == "status":
        result = status()
    else:
        raise SystemExit("usage: atlas-engine clock boot|rtc|status")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

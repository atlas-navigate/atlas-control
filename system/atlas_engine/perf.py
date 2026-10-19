"""
CPU, GPU and memory-controller clocks while Ray answers.

All three idle low and ramp with load. Measured on an Orin Nano: Ollama
spends ~0.4 s (embedding) and ~0.5 s (answer) of CPU work on every request
before the model runs, and with the CPU at full clock that halves (391 -> 215
ms, 505 -> 267 ms); the GPU (nvhost_podgov) and memory controller (bwmgr)
ramp during the prompt's processing. `boost` raises the CPU frequency floors
(cpufreq policies) and both devfreq floors to their maximum for a short
lease; `release` puts the saved floors back.

The app (perf_boost.py) boosts only while Ray is generating and only on mains
power, renews the lease while it streams, and releases after 30 s idle. The
lease is enforced here as well: atlas-perf-release.timer (restarted by every
boost) releases the clocks 90 s after the last boost, so a crashed app can
never leave them high. Nothing is boosted at or above 85 °C.

The ceilings come from the Jetson power mode (nvpmodel). Every release names
the mode Atlas is tuned and measured in (system/files.json "power_mode");
ensure_power_mode() selects it on install and on every update, so a unit
does not run in whatever mode first-boot setup happened to leave.
"""
import json
import os
import re

from . import fsutil, paths

NODES = ("17000000.gpu", "bwmgr")
MAX_TEMP_C = 85.0
LEASE_TIMER = "atlas-perf-release.timer"


def _targets():
    """(key, min file, max-value file) for every clock floor we may raise."""
    out = [(f"devfreq:{n}", os.path.join(paths.DEVFREQ, n, "min_freq"), os.path.join(paths.DEVFREQ, n, "max_freq"))
           for n in NODES]
    try:
        policies = sorted(p for p in os.listdir(paths.CPUFREQ) if p.startswith("policy"))
    except OSError:
        policies = []
    for p in policies:
        d = os.path.join(paths.CPUFREQ, p)
        out.append((f"cpufreq:{p}", os.path.join(d, "scaling_min_freq"), os.path.join(d, "scaling_max_freq")))
    return out


def _read(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return None


def _write(path, value):
    with open(path, "w", encoding="ascii") as f:
        f.write(str(value))


def max_temp_c():
    best = None
    try:
        zones = os.listdir(paths.THERMAL)
    except OSError:
        return None
    for zone in zones:
        v = _read(os.path.join(paths.THERMAL, zone, "temp"))
        if v and v.lstrip("-").isdigit():
            c = int(v) / 1000.0
            best = c if best is None else max(best, c)
    return best


def power_modes():
    """{name: id} of the power modes this module's nvpmodel config offers."""
    try:
        with open(paths.NVPMODEL_CONF, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    return {m.group(2): int(m.group(1))
            for m in re.finditer(r"^<\s*POWER_MODEL\s+ID=(\d+)\s+NAME=(\S+)\s*>", text, re.M)}


def current_power_mode(runner):
    """(name, id) from `nvpmodel -q`, or (None, None) if it cannot tell."""
    try:
        r = runner.run(["nvpmodel", "-q"], timeout=30, input="")
    except Exception:
        return None, None
    if r.returncode != 0:
        return None, None
    name = re.search(r"Power Mode:\s*(\S+)", r.stdout or "")
    ident = re.search(r"^\s*(\d+)\s*$", r.stdout or "", re.M)
    return (name.group(1) if name else None), (int(ident.group(1)) if ident else None)


def ensure_power_mode(wanted, runner):
    """Select power mode `wanted` (a NAME from nvpmodel.conf) if this module
    offers it and it is not already active. Never reboots: nvpmodel's own
    reboot question is answered "no". Returns a dict for the log."""
    if not wanted:
        return {"power_mode": None, "changed": False, "reason": "release names no power mode"}
    modes = power_modes()
    if wanted not in modes:
        return {"power_mode": wanted, "changed": False,
                "reason": "this module has no such mode" if modes else "no nvpmodel configuration"}
    name, ident = current_power_mode(runner)
    if name == wanted or ident == modes[wanted]:
        return {"power_mode": wanted, "changed": False, "reason": "already active"}
    r = runner.run(["nvpmodel", "-m", str(modes[wanted])], timeout=60, input="NO\n")
    if r.returncode != 0:
        return {"power_mode": wanted, "changed": False,
                "reason": f"nvpmodel failed: {((r.stderr or r.stdout or '').strip())[-200:]}"}
    return {"power_mode": wanted, "changed": True, "from": name, "reason": "selected"}


def _saved_path():
    return os.path.join(paths.PERF_RUN, "saved.json")


def state():
    return fsutil.read_json(_saved_path(), None)


def boost(run=None):
    """Raise the floors to the maximum. Returns a dict (boosted, reason)."""
    temp = max_temp_c()
    if temp is not None and temp >= MAX_TEMP_C:
        release(run)
        return {"boosted": False, "reason": f"hot ({temp:.0f} °C)"}
    targets = {key: (low, high) for key, low, high in _targets()}
    saved = state()
    if saved is None:
        saved = {}
        for key, (low, _high) in targets.items():
            cur = _read(low)
            if cur is not None:
                saved[key] = cur
        os.makedirs(paths.PERF_RUN, mode=0o755, exist_ok=True)
        fsutil.atomic_write_json(_saved_path(), saved, mode=0o644)
    done = []
    for key in saved:
        if key not in targets:
            continue
        low, high = targets[key]
        top = _read(high)
        if top and top.isdigit():
            try:
                _write(low, top)
                done.append(key)
            except OSError:
                pass
    if run:
        run(["systemctl", "restart", LEASE_TIMER])
    return {"boosted": bool(done), "nodes": done}


def release(run=None):
    """Put the saved floors back (idempotent)."""
    saved = state()
    if saved:
        targets = {key: low for key, low, _high in _targets()}
        for key, value in saved.items():
            if key in targets:
                try:
                    _write(targets[key], value)
                except OSError:
                    pass
        try:
            os.unlink(_saved_path())
        except FileNotFoundError:
            pass
    if run:
        run(["systemctl", "stop", LEASE_TIMER])
    return {"released": bool(saved)}


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "release":
        result = release()
    elif cmd == "status":
        from .runner import DEFAULT
        name, _ident = current_power_mode(DEFAULT)
        result = {"boosted": state() is not None, "saved": state(), "max_temp_c": max_temp_c(),
                  "min_freq": {key: _read(low) for key, low, _high in _targets()},
                  "power_mode": name}
    else:
        raise SystemExit("usage: atlas-engine perf release|status")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

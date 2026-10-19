"""
atlas-engine check — is this unit configured the way its installed release
says it should be?

Read-only. Compares the live system with the release in /opt/atlas/current:
its system files and settings, the units it enables, the power mode, kernel
settings, swap, Ollama's effective settings and models, where Ray's model
runs, the firewall, open ports, retail lock-down and free storage. The same
report on a freshly installed unit and on a long-running one is the proof
that they match.

--infer adds one short answer from Ray's loaded model (its speed; never a
reload). Each line is ok, warn (works, but not as intended), fail (broken or not
configured), or skip (cannot be checked here, e.g. needs root). Exit status
1 if anything failed.
"""
import json
import os
import re
import sys
import urllib.request

from . import fsutil, paths, perf, sysfiles

ACTIVE = ("atlas-control.service", "nginx.service", "ollama.service", "avahi-daemon.service",
          "atlas-firewall.service", "atlas-helper.socket", "zram-swap.service")
# Off on sold units (tools/manufacturing/finalize-retail.sh).
RETAIL_DISABLED = ("docker.service", "docker.socket", "containerd.service", "snapd.service",
                   "snapd.socket", "ModemManager.service")
OLLAMA = "http://127.0.0.1:11434"
MIN_FREE_GB_WARN = 5
MIN_FREE_GB_FAIL = 1
GPU_SHARE_MIN = 95
RELOAD_S = 5


class Report:
    def __init__(self):
        self.items = []

    def add(self, status, area, what, detail=""):
        self.items.append({"status": status, "area": area, "check": what, "detail": str(detail)})

    def ok(self):
        return not any(i["status"] == "fail" for i in self.items)

    def text(self):
        mark = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]", "skip": "[skip]"}
        lines = [f"{mark[i['status']]} {i['area']:<9} {i['check']}" + (f" — {i['detail']}" if i["detail"] else "")
                 for i in self.items]
        counts = {s: sum(1 for i in self.items if i["status"] == s) for s in mark}
        lines.append(f"{counts['ok']} ok, {counts['warn']} warnings, {counts['fail']} failed, "
                     f"{counts['skip']} skipped")
        return "\n".join(lines)


def _systemctl(runner, *args):
    r = runner.run(["systemctl", *args], timeout=30)
    return (r.stdout or "").strip()


def _get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _version_tuple(v):
    parts = [int("".join(ch for ch in p if ch.isdigit()) or 0) for p in str(v or "0").lstrip("v").split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def release_profiles(current):
    """The installed release's model_profiles module (standard library only),
    so the check expects exactly what install.sh pre-installs and Ray runs."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("atlas_release_model_profiles",
                                                  os.path.join(current, "model_profiles.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm_tag(tag):
    return tag if ":" in tag else tag + ":latest"


# ── sections ─────────────────────────────────────────────────────────────
def check_release(rep):
    current = os.path.realpath(paths.CURRENT)
    if not os.path.isdir(current):
        rep.add("fail", "release", "an Atlas release is installed", "no /opt/atlas/current")
        return None
    if not (os.path.exists(os.path.join(current, ".complete")) or os.path.exists(os.path.join(current, ".dev-link"))):
        rep.add("fail", "release", "the current release is complete", current)
        return None
    info = fsutil.read_json(os.path.join(current, "release.json"), {}) or {}
    rep.add("ok", "release", f"Atlas {info.get('version', '?')} installed", current)
    return current


def check_system_files(rep, current, runner):
    try:
        spec = sysfiles.load_spec(current)
    except (OSError, ValueError, sysfiles.SystemFilesError) as exc:
        rep.add("fail", "files", "the release's system file list", exc)
        return None
    drift = []
    unreadable = 0
    for entry in spec.get("files", []):
        dst = paths.p(entry["dst"])
        try:
            want = sysfiles.render(current, entry)
            with open(dst, "rb") as f:
                have = f.read()
        except FileNotFoundError:
            drift.append(f"{entry['dst']} missing")
            continue
        except PermissionError:
            unreadable += 1
            continue
        if have != want:
            drift.append(f"{entry['dst']} differs")
        elif (os.stat(dst).st_mode & 0o7777) != int(entry.get("mode", "0644"), 8):
            drift.append(f"{entry['dst']} mode {oct(os.stat(dst).st_mode & 0o7777)}")
    for setting in spec.get("settings", []):
        dst = paths.p(setting["file"])
        try:
            text = open(dst, encoding="utf-8").read()
        except FileNotFoundError:
            continue
        except PermissionError:
            unreadable += 1
            continue
        if sysfiles.edit_ini(text, setting["values"]) != text:
            drift.append(f"{setting['file']} settings")
    for dst in spec.get("remove", []):
        if os.path.lexists(paths.p(dst)):
            drift.append(f"{dst} should be gone")
    for link in spec.get("symlinks", []):
        dst = paths.p(link["link"])
        if not os.path.islink(dst) or os.readlink(dst) != link["target"]:
            drift.append(f"{link['link']} link")
    if drift:
        rep.add("fail", "files", "system files match the release",
                "; ".join(drift[:6]) + (f" (+{len(drift) - 6} more)" if len(drift) > 6 else "") +
                " — fix: sudo /usr/local/lib/atlas/bin/atlas-engine reapply")
    elif unreadable:
        rep.add("skip", "files", "system files match the release", f"{unreadable} need root to read")
    else:
        rep.add("ok", "files", "system files and settings match the release",
                f"{len(spec.get('files', []))} files, {len(spec.get('settings', []))} settings")
    for unit in spec.get("enable", []):
        state = _systemctl(runner, "is-enabled", unit)
        rep.add("ok" if state in ("enabled", "static", "indirect", "alias") else "fail",
                "units", f"{unit} enabled", state or "unknown")
    return spec


def check_services(rep, runner):
    for unit in ACTIVE:
        state = _systemctl(runner, "is-active", unit)
        rep.add("ok" if state == "active" else "fail", "services", f"{unit} running", state or "unknown")


def check_power(rep, spec, runner):
    wanted = (spec or {}).get("power_mode")
    if not wanted:
        rep.add("skip", "power", "power mode", "the release names none")
        return
    modes = perf.power_modes()
    if wanted not in modes:
        rep.add("skip", "power", f"power mode {wanted}", "this module has no such mode" if modes else "not a Jetson")
        return
    name, ident = perf.current_power_mode(runner)
    if name == wanted or ident == modes[wanted]:
        rep.add("ok", "power", f"power mode {wanted}")
    else:
        rep.add("fail", "power", f"power mode {wanted}",
                f"running {name or ident or 'unknown'} — fix: sudo /usr/local/lib/atlas/bin/atlas-engine reapply")


def check_sysctl(rep, current, spec):
    for entry in (spec or {}).get("files", []):
        if entry.get("kind") != "sysctl":
            continue
        try:
            text = sysfiles.render(current, entry).decode()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            key, want = (x.strip() for x in line.split("=", 1))
            try:
                have = open(os.path.join("/proc/sys", key.replace(".", "/")), encoding="ascii").read().split()
            except OSError:
                rep.add("skip", "kernel", key, "not readable")
                continue
            rep.add("ok" if have == want.split() else "fail", "kernel", f"{key} = {want}",
                    "" if have == want.split() else f"is {' '.join(have)}")


def check_swap(rep):
    try:
        swaps = open("/proc/swaps", encoding="ascii").read()
    except OSError:
        rep.add("skip", "memory", "swap", "no /proc/swaps")
        return
    rep.add("ok" if "/dev/zram" in swaps else "fail", "memory", "compressed swap (zram) active")
    disk = [ln.split()[0] for ln in swaps.splitlines()[1:] if ln and not ln.startswith("/dev/zram")]
    rep.add("ok" if disk else "warn", "memory", "disk swap for heavy routing builds",
            ", ".join(disk) if disk else "none active")


def check_ollama(rep, current, runner):
    dropin = os.path.join(current, "system", "ollama", "atlas.conf")
    try:
        wanted = re.findall(r'^Environment="([^"=]+)=([^"]*)"', open(dropin, encoding="utf-8").read(), re.M)
    except OSError:
        wanted = []
    env = _systemctl(runner, "show", "ollama.service", "-p", "Environment", "--value")
    if not env and wanted:
        rep.add("skip", "ollama", "Ollama settings", "systemctl show gave nothing")
    else:
        missing = [f"{k}={v}" for k, v in wanted if f"{k}={v}" not in env.split()]
        rep.add("fail" if missing else "ok", "ollama", "Atlas's Ollama settings in effect",
                ("missing " + ", ".join(missing)) if missing else f"{len(wanted)} settings")
    try:
        profiles = release_profiles(current)
        need, tested = profiles.min_ollama(), profiles.tested_ollama()
        expected, embedder = profiles.preinstall_tags(), profiles.embedder()["id"]
    except Exception as exc:
        rep.add("fail", "ollama", "the release's model catalog", exc)
        return
    try:
        version = _get_json(f"{OLLAMA}/api/version").get("version", "")
    except Exception as exc:
        rep.add("fail", "ollama", "Ollama answering", exc)
        return
    if _version_tuple(version) < _version_tuple(need):
        rep.add("fail", "ollama", f"Ollama {need} or newer", f"is {version}")
    elif _version_tuple(version) != _version_tuple(tested):
        rep.add("warn", "ollama", f"Ollama {tested} (the version Ray was tested with)", f"is {version}")
    else:
        rep.add("ok", "ollama", f"Ollama {version}")
    try:
        have = {_norm_tag(m["name"]) for m in _get_json(f"{OLLAMA}/api/tags").get("models", [])}
    except Exception as exc:
        rep.add("fail", "ollama", "model list", exc)
        return
    missing = [t for t in expected if _norm_tag(t) not in have]
    rep.add("fail" if missing else "ok", "ollama", "every model Ray offers is installed",
            ("missing " + ", ".join(missing)) if missing else ", ".join(expected))
    try:
        loaded = _get_json(f"{OLLAMA}/api/ps").get("models", [])
    except Exception:
        loaded = []
    chat = [m for m in loaded if _norm_tag(m.get("name", "")) != _norm_tag(embedder)]
    if not chat:
        rep.add("skip", "ray", "Ray's model on the GPU", "no chat model loaded yet")
    for m in chat:
        share = round(100 * (m.get("size_vram") or 0) / m["size"]) if m.get("size") else 0
        rep.add("ok" if share >= GPU_SHARE_MIN else "warn", "ray", f"{m['name']} on the GPU",
                f"{share}%" + ("" if share >= GPU_SHARE_MIN else " — part of it runs on the CPU (slower answers)"))


def check_inference(rep, current, post=None):
    """One short answer from the chat model Ray already has loaded, asked
    with exactly the load options the app uses (the release's model_profiles
    on Ray's saved settings), so nothing is reloaded or moved. Reports its
    speed. A reload (15-25 s; a resident Qwen3.5 still reports ~1 s of load
    time on every request) means the check and the app disagree about how
    the model runs."""
    import sqlite3
    post = post or _post_json
    try:
        loaded = _get_json(f"{OLLAMA}/api/ps").get("models", [])
        profiles = release_profiles(current)
        db = os.path.join(paths.APP_STATE, "data", "meshtastic.db")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            settings = dict(con.execute("SELECT key, value FROM ai_settings").fetchall())
        finally:
            con.close()
        prof = profiles.resolve(settings)
    except Exception as exc:
        rep.add("skip", "ray", "a test answer", exc)
        return
    if not any(_norm_tag(m.get("name", "")) == _norm_tag(prof["tag"]) for m in loaded):
        rep.add("skip", "ray", "a test answer", f"{prof['tag']} is not loaded (Ray loads it on first use)")
        return
    # As ai_manager._finalize_payload sends it: no "thinking" first, and a
    # negative num_gpu left out so Ollama places the layers itself.
    options = dict(prof["options"], num_predict=32, temperature=0)
    if int(options.get("num_gpu", -1)) < 0:
        options.pop("num_gpu", None)
    body = {"model": prof["tag"], "prompt": "In one sentence, what is a compass for?", "stream": False,
            "options": options, "keep_alive": prof.get("keep_alive", "-1m")}
    try:
        if "thinking" in (post(f"{OLLAMA}/api/show", {"model": prof["tag"]}, timeout=10).get("capabilities") or []):
            body["think"] = False
        r = post(f"{OLLAMA}/api/generate", body, timeout=120)
    except Exception as exc:
        rep.add("fail", "ray", f"a test answer from {prof['tag']}", exc)
        return
    load_s = (r.get("load_duration") or 0) / 1e9
    gen = (r.get("eval_count") or 0) / ((r.get("eval_duration") or 1) / 1e9)
    prompt = (r.get("prompt_eval_count") or 0) / ((r.get("prompt_eval_duration") or 1) / 1e9)
    if not (r.get("response") or "").strip():
        rep.add("fail", "ray", f"a test answer from {prof['tag']}", "empty answer")
    elif load_s > RELOAD_S:
        rep.add("warn", "ray", f"a test answer from {prof['tag']}",
                f"the model was reloaded ({load_s:.0f} s) — the check and Ray disagree about its settings")
    else:
        rep.add("ok", "ray", f"a test answer from {prof['tag']}",
                f"{gen:.1f} tokens/s generated, {prompt:.0f} tokens/s prompt")


def _post_json(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_network(rep, runner):
    state = _systemctl(runner, "is-active", "atlas-firewall.service")
    rep.add("ok" if state == "active" else "fail", "network", "firewall on", state)
    policy = fsutil.read_json(paths.p("/etc/atlas/firewall.json"), {}) or {}
    allowed = set(policy.get("tcp", []))
    if paths.dev_mode():
        allowed |= set(policy.get("dev_tcp", []))
    r = runner.run(["ss", "-ltnH"], timeout=30)
    exposed = set()
    for line in (r.stdout or "").splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        addr, _, port = fields[3].rpartition(":")
        if addr.startswith(("127.", "[::1]", "[::ffff:127.")) or "%lo" in addr:
            continue
        if port.isdigit() and int(port) not in allowed:
            exposed.add(int(port))
    rep.add("ok" if not exposed else "warn", "network", "only Atlas's ports listen on the network",
            ("also listening (the firewall refuses them): " + ", ".join(map(str, sorted(exposed)))) if exposed
            else ", ".join(map(str, sorted(allowed))))
    app_port = policy.get("app_port", paths.APP_PORT)
    public_app = any(re.search(rf"(^|\s)(0\.0\.0\.0|\*|\[::\]):{app_port}\s", ln) for ln in (r.stdout or "").splitlines())
    rep.add("fail" if public_app else "ok", "network", f"the app (port {app_port}) only on loopback")


def check_lockdown(rep, runner):
    retail = os.path.exists(paths.p("/etc/atlas/retail"))
    if paths.dev_mode():
        rep.add("skip", "retail", "retail lock-down", "developer unit")
        return
    ssh = [u for u in ("ssh.service", "ssh.socket", "sshd.service")
           if _systemctl(runner, "is-enabled", u) in ("enabled", "enabled-runtime")]
    rep.add("fail" if ssh else "ok", "retail", "SSH off", ", ".join(ssh) + " enabled" if ssh else "")
    if not retail:
        rep.add("warn", "retail", "retail lock-down done", "run tools/manufacturing/finalize-retail.sh")
        return
    target = _systemctl(runner, "get-default")
    rep.add("ok" if target == "multi-user.target" else "fail", "retail", "boots without the desktop", target)
    on = [u for u in RETAIL_DISABLED if _systemctl(runner, "is-enabled", u) in ("enabled", "enabled-runtime")]
    rep.add("fail" if on else "ok", "retail", "unused services off", ", ".join(on) + " enabled" if on else "")


def check_identity(rep):
    try:
        device = fsutil.read_json(os.path.join(paths.IDENTITY, "device.json"))
    except PermissionError:
        rep.add("skip", "identity", "this unit's identity", "needs root")
        return
    if not device:
        rep.add("fail", "identity", "this unit's identity", "none — run: sudo atlas-identity ensure")
        return
    try:
        hostname = open(paths.p("/etc/hostname"), encoding="utf-8").read().strip()
    except OSError:
        hostname = ""
    rep.add("ok" if hostname == device.get("hostname") else "fail", "identity",
            f"{device.get('name')} answers as {device.get('hostname')}.local",
            "" if hostname == device.get("hostname") else f"hostname is {hostname or 'unset'}")


def check_storage(rep):
    target = paths.APP_STATE if os.path.isdir(paths.APP_STATE) else paths.p("/")
    st = os.statvfs(target)
    free_gb = st.f_bavail * st.f_frsize / 1024 ** 3
    status = "fail" if free_gb < MIN_FREE_GB_FAIL else "warn" if free_gb < MIN_FREE_GB_WARN else "ok"
    rep.add(status, "storage", f"free storage at least {MIN_FREE_GB_WARN} GB", f"{free_gb:.0f} GB free")


def run(runner=None, infer=False):
    from .runner import DEFAULT
    runner = runner or DEFAULT
    rep = Report()
    current = check_release(rep)
    spec = check_system_files(rep, current, runner) if current else None
    check_services(rep, runner)
    check_power(rep, spec, runner)
    if current:
        check_sysctl(rep, current, spec)
    check_swap(rep)
    if current:
        check_ollama(rep, current, runner)
        if infer:
            check_inference(rep, current)
    check_network(rep, runner)
    check_lockdown(rep, runner)
    check_identity(rep)
    check_storage(rep)
    return rep


def main(argv):
    rep = run(infer="--infer" in argv)
    if "--json" in argv:
        print(json.dumps({"ok": rep.ok(), "items": rep.items}, indent=2))
    else:
        print(rep.text())
    return 0 if rep.ok() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

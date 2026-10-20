#!/usr/bin/python3 -I
"""
On-device acceptance test for a developer Atlas (the spec's "production
acceptance test" and final verification, the parts a script can drive).

    sudo tools/dev/acceptance [run] [--only NAMES]   ~30–45 min, restores everything
    sudo tools/dev/acceptance reboot-test            simulated power cut mid-update (reboots!)
    sudo tools/dev/acceptance after-reboot           checks recovery after reboot-test

Sections of `run` (names for --only):
  security   root-side checks: no sudo rules, firewall live, code not writable
             by the service account, identity secrets unreadable to it
  function   Ray answers from the knowledge base, an offline route, stats,
             GPS/radio status, no outbound connections while doing so
  models     every offered AI model is on the unit; switch to each, it
             answers, and the choice survives an app restart
  download   a real app-driven update from a local release server: progress,
             "Internet" cut mid-download → paused → resumes with Range, app
             restarted mid-download → resumes, verified, installed, reconnect
  forged     a manifest whose signature does not match is never offered
  rollback   a deliberately unhealthy release is rolled back automatically
  faults     the engine crashes at every transaction state; recovery must
             leave a complete, healthy release every time
After every update scenario the test's phone pairing must still work, with
the same device id, TLS key and chosen AI model (pairing survives updates).

Only on developer units (/etc/atlas/dev-mode). Everything temporary lives
in /run (drop-ins, the test's pairing is revoked at the end), so even an
aborted run is undone by a reboot. The report — no secrets — goes to
/var/log/atlas-acceptance/<time>.{json,txt}.
"""
import argparse
import http.server
import json
import os
import pwd
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "https://127.0.0.1"
PORT = 8971
IDENTITY = "/etc/atlas/identity"
STATE = os.environ.get("ATLAS_STATE_DIR", "/atlas_data/atlas-control")
HANDOFF = os.path.join(STATE, "updates", "ready")
JOURNAL = "/var/lib/atlas-update/journal.json"
INSTALLED = "/var/lib/atlas-update/installed.json"
LAST_RESULT = "/var/lib/atlas-update/public/last_result.json"
CURRENT = "/opt/atlas/current"
HELPER = "/run/atlas/helper.sock"
REPORT_DIR = "/var/log/atlas-acceptance"
RUN_DROPINS = "/run/systemd/system"
REBOOT_STATE = "/var/lib/atlas-acceptance-reboot.json"
BUILD_PY = os.environ.get("ATLAS_BUILD_PYTHON", "/atlas_data/atlas-control/venv/bin/python3")
MSG_PAUSED = "Download paused — waiting for Internet connection."
MSG_NOT_VERIFIED = "Update could not be verified."
FAULT_STATES = ["ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED",
                "APPLYING_SYSTEM", "SWITCHED", "HEALTH_CHECK", "COMMITTING"]
CTX = ssl._create_unverified_context()      # loopback to this unit's own nginx


def msg_updated(version):
    """update_manager's message once `version` is installed."""
    return f"Updated to Atlas {version}."


def msg_rolled_back(version, running):
    """The start of update_manager's message after a rollback (its advice follows)."""
    return f"The update to Atlas {version} could not be completed, so Atlas went back to {running}."


# ── report ───────────────────────────────────────────────────────────────
class Report:
    def __init__(self):
        self.checks = []
        self.started = time.time()

    def check(self, section, name, ok, detail=""):
        self.checks.append({"section": section, "check": name, "ok": bool(ok), "detail": str(detail)[:400]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {section}: {name}" + (f" — {detail}" if detail else ""), flush=True)
        return ok

    def note(self, section, name, detail):
        self.checks.append({"section": section, "check": name, "ok": None, "detail": str(detail)[:400]})
        print(f"  [INFO] {section}: {name} — {detail}", flush=True)

    def write(self, extra=None):
        os.makedirs(REPORT_DIR, mode=0o755, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        data = {"started": self.started, "finished": time.time(), "checks": self.checks, **(extra or {})}
        with open(os.path.join(REPORT_DIR, f"{stamp}.json"), "w") as f:
            json.dump(data, f, indent=2)
        fails = [c for c in self.checks if c["ok"] is False]
        lines = [f"Atlas acceptance {stamp}: {sum(1 for c in self.checks if c['ok'])} passed, {len(fails)} failed", ""]
        lines += [f"[{'PASS' if c['ok'] else 'FAIL' if c['ok'] is False else 'INFO'}] {c['section']}: {c['check']}"
                  + (f" — {c['detail']}" if c["detail"] else "") for c in self.checks]
        with open(os.path.join(REPORT_DIR, f"{stamp}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        for name in (f"{stamp}.json", f"{stamp}.txt"):
            os.chmod(os.path.join(REPORT_DIR, name), 0o644)
        print(f"\nreport: {REPORT_DIR}/{stamp}.txt  ({len(fails)} failed)")
        return not fails


# ── helpers ──────────────────────────────────────────────────────────────
def sh(cmd, check=False, timeout=600, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def api(method, path, body=None, token=None, timeout=30):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, {"error": str(e)}


def health():
    code, body = api("GET", "/api/health", timeout=5)
    return body if code == 200 else None


def wait(pred, timeout, interval=1.0, what=""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {what}")


def unit_active(unit):
    return sh(["systemctl", "is-active", unit]).stdout.strip() in ("active", "activating", "reloading")


def dropin(unit, name, text):
    d = os.path.join(RUN_DROPINS, f"{unit}.d")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{name}.conf"), "w") as f:
        f.write(text)
    sh(["systemctl", "daemon-reload"], check=True)


def remove_dropin(unit, name):
    try:
        os.unlink(os.path.join(RUN_DROPINS, f"{unit}.d", f"{name}.conf"))
    except FileNotFoundError:
        return
    sh(["systemctl", "daemon-reload"])


def helper(verb):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(30)
    s.connect(HELPER)
    s.sendall(json.dumps({"verb": verb, "args": {}}).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode() or "{}")


def engine_idle():
    return (read_json(JOURNAL, {}) or {}).get("state", "IDLE") == "IDLE" and \
        not unit_active("atlas-update-engine.service") and not unit_active("atlas-update-recover.service")


def last_result():
    return read_json(LAST_RESULT, {}) or {}


def wait_new_result(before, timeout, what):
    """Wait for the engine to close a transaction that started after `before`
    (the last_result seen before asking for it) and go idle."""
    def done():
        r = last_result()
        return r if r.get("finished", 0) > before.get("finished", 0) and engine_idle() else None
    return wait(done, timeout, 2, what)


def installed_version():
    return (read_json(INSTALLED, {}) or {}).get("version")


def invariant():
    """After anything: current is a complete release, the app serves it."""
    rel = os.path.realpath(CURRENT)
    complete = os.path.exists(os.path.join(rel, ".complete"))
    h = health()
    ok = complete and h is not None and h.get("version") == installed_version() and \
        (read_json(JOURNAL, {}) or {}).get("state", "IDLE") == "IDLE"
    return ok, {"current": os.path.basename(rel), "complete": complete, "serving": (h or {}).get("version"),
                "installed": installed_version()}


def owner():
    user = os.environ.get("SUDO_USER") or "admin"
    return user, pwd.getpwnam(user)


def as_owner(cmd, **kw):
    user, _ = owner()
    return sh(["runuser", "-u", user, "--"] + cmd, **kw)


# ── releases ─────────────────────────────────────────────────────────────
class Builder:
    """Builds dev-signed releases as the developer (git and the key are theirs)."""

    def __init__(self):
        _, pw = owner()
        self.key = os.environ.get("ATLAS_DEV_KEY", os.path.join(pw.pw_dir, ".config/atlas-dev/dev-release.key"))
        self.out = as_owner(["mktemp", "-d"], check=True).stdout.strip()
        base = open(os.path.join(REPO, "VERSION")).read().strip()
        self.base = base

    def build(self, tag, repo=REPO):
        time.sleep(1.1)                                    # seq is epoch seconds: strictly increasing
        # After the base version, a version allows 40 letters, digits, "." and
        # "-": "dev.<time>." leaves 21 for the tag ("faults-health-check").
        tag = tag.replace("_", "-")[:21]
        version = f"{self.base}-dev.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}.{tag}"
        print(f"  building {version} …", flush=True)
        as_owner([BUILD_PY, os.path.join(repo, "tools/release/build_release.py"), "--version", version,
                  "--ref", "WORKTREE", "--channel", "dev", "--out", self.out, "--python", BUILD_PY,
                  # No bundle URL (manifests may only name https ones): the app
                  # fetches the bundle from beside the manifest, on ReleaseServer.
                  "--url-base", ""], check=True, timeout=1800)
        as_owner([BUILD_PY, os.path.join(repo, "tools/release/sign_release.py"),
                  os.path.join(self.out, version, "atlas-release.json"), "--key", self.key, "--yes"],
                 check=True)
        return os.path.join(self.out, version)

    def build_unhealthy(self):
        """A release whose app exits at start: from a throwaway git worktree."""
        wt = as_owner(["mktemp", "-d"], check=True).stdout.strip()
        os.rmdir(wt)
        as_owner(["git", "-C", REPO, "worktree", "add", "--detach", wt, "HEAD"], check=True)
        try:
            app = os.path.join(wt, "app.py")
            text = open(app).read()
            as_owner(["python3", "-c", "import sys; p=sys.argv[1]; t=open(p).read(); "
                      "open(p,'w').write('raise SystemExit(\"acceptance: deliberately unhealthy\")\\n'+t)", app],
                     check=True)
            del text
            return self.build("unhealthy", repo=wt)
        finally:
            as_owner(["git", "-C", REPO, "worktree", "remove", "--force", wt])

    def cleanup(self):
        shutil.rmtree(self.out, ignore_errors=True)


def manifest(release_dir):
    return read_json(os.path.join(release_dir, "atlas-release.json"), {})


# ── a local stand-in for GitHub Releases, with an on/off "Internet" ─────
class ReleaseServer:
    def __init__(self, bytes_per_second=1_000_000):
        self.root = None
        self.online = True
        self.rate = bytes_per_second
        self.requests = []
        srv = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append((time.time(), self.path, self.headers.get("Range"),
                                     self.headers.get("User-Agent")))
                if not srv.online or not srv.root:
                    self.close_connection = True
                    self.connection.close()
                    return
                name = os.path.basename(self.path.split("?")[0])
                path = os.path.join(srv.root, name)
                if not os.path.isfile(path):
                    self.send_error(404)
                    return
                size = os.path.getsize(path)
                start = 0
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0] or 0)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(size - start))
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    while True:
                        if not srv.online:
                            self.connection.close()        # the "Internet" went away mid-transfer
                            return
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            return
                        try:
                            self.wfile.write(chunk)
                        except OSError:
                            return
                        time.sleep(len(chunk) / srv.rate)

        native = _native_socket_class()

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def __init__(self, addr, handler):
                socketserver.TCPServer.__init__(self, addr, handler, bind_and_activate=False)
                self.socket = native(self.address_family, self.socket_type)
                self.server_bind()
                self.server_activate()

            def get_request(self):
                fd, peer = self.socket._accept()
                return native(self.address_family, self.socket_type, fileno=fd), peer

        self.httpd = Server(("127.0.0.1", PORT), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def serve(self, release_dir):
        """Serve one release at the root and under v<version>/ (the bundle URL)."""
        self.root = release_dir

    def close(self):
        self.httpd.shutdown()


def _native_socket_class():
    """Plain threads need plain sockets. The harness runs under the system
    Python, but the test suite imports it into a gevent-patched process."""
    try:
        from gevent import monkey
        if monkey.is_module_patched("socket"):
            return monkey.get_original("socket", "socket")
    except ImportError:
        pass
    return socket.socket


# ── pairing for the test ─────────────────────────────────────────────────
def pair():
    with open(os.path.join(IDENTITY, "setup_code")) as f:
        code = f.read().strip()
    status, body = api("POST", "/api/pair/claim", {"setup_code": code, "name": "Acceptance test", "kind": "android"})
    if status != 200 or not body.get("token"):
        raise RuntimeError(f"could not pair the test client: {status} {body.get('error')}")
    return body["token"], body.get("client_id")


def unpair(token, client_id):
    if token and client_id:
        api("DELETE", f"/api/pair/clients/{client_id}", token=token)


def connection_facts(token=None):
    """What a paired phone relies on: its token still accepted, the same
    device id and TLS key, and the owner's chosen AI model."""
    _, info = api("GET", "/api/device/info", timeout=10)
    facts = {"device_id": info.get("device_id"), "pin": info.get("tls_spki_sha256")}
    if token:
        _, st = api("GET", "/api/pair/status", token=token, timeout=10)
        _, ai = api("GET", "/api/ai/settings", token=token, timeout=10)
        facts.update(paired=st.get("paired"), model=ai.get("model"))
    return facts


def check_persistence(rep, section, ctx, when):
    """Spec: updates never make a paired device pair again or lose settings."""
    before = ctx.get("facts")
    if not before:
        return
    now = connection_facts(ctx.get("token"))
    changed = sorted(k for k in before if now.get(k) != before[k])
    rep.check(section, f"still paired, same key and AI model {when}", not changed,
              ", ".join(f"{k} changed" for k in changed))


# ── sections ─────────────────────────────────────────────────────────────
def section_security(rep, ctx):
    S = "security"
    sudoers = [f for f in os.listdir("/etc/sudoers.d") if f.startswith("atlas")]
    rep.check(S, "no Atlas sudo rules", not sudoers, ", ".join(sudoers))
    r = sh(["sudo", "-n", "-l", "-U", "atlas"])
    rep.check(S, "atlas account has no sudo rights", "not allowed" in (r.stdout + r.stderr), (r.stdout + r.stderr).strip()[-120:])
    chain = sh(["iptables-nft", "-S", "ATLAS-INPUT"]).stdout
    jump = sh(["iptables-nft", "-S", "INPUT"]).stdout
    rep.check(S, "firewall chain live and jumped to", "-j DROP" in chain and "ATLAS-INPUT" in jump)
    rep.check(S, "loopback app-port table live", sh(["nft", "list", "table", "inet", "atlas_local"]).returncode == 0)
    for path in ("/opt/atlas/current/app.py", "/usr/local/lib/atlas/engine/current/atlas_engine/engine.py",
                 "/usr/local/lib/atlas/keys/keyring.json", "/etc/systemd/system/atlas-control.service"):
        rep.check(S, f"atlas cannot modify {path}", sh(["runuser", "-u", "atlas", "--", "test", "-w", path]).returncode != 0)
    for path in ("setup_code", "tls/key.pem"):
        rep.check(S, f"atlas cannot read identity {path}",
                  sh(["runuser", "-u", "atlas", "--", "test", "-r", os.path.join(IDENTITY, path)]).returncode != 0)
    user = sh(["systemctl", "show", "-p", "User", "--value", "atlas-control"]).stdout.strip()
    rep.check(S, "atlas-control runs as atlas", user == "atlas", user)
    score = sh(["systemd-analyze", "security", "atlas-control.service", "--no-pager"]).stdout.strip().splitlines()
    rep.note(S, "systemd exposure", score[-1] if score else "n/a")


def outbound(pid):
    out = sh(["ss", "-tnpH", "state", "established"]).stdout
    return [l for l in out.splitlines() if f"pid={pid}," in l and "127.0.0.1" not in l.split()[3]
            and "[::1]" not in l.split()[3]]


def section_function(rep, ctx):
    S = "function"
    token = ctx["token"]
    pid = sh(["systemctl", "show", "-p", "MainPID", "--value", "atlas-control"]).stdout.strip()
    before = outbound(pid)
    code, stats = api("GET", "/api/system/stats", token=token)
    rep.check(S, "stats (tegrastats as atlas)", code == 200 and "gpu_pct" in stats and "cpu_pct" in stats,
              {k: stats.get(k) for k in ("cpu_pct", "gpu_pct", "ram_used_mb")})
    code, chat = api("POST", "/api/ai/chats", {}, token=token)
    chat_id = chat.get("id")
    t = time.time()
    code, ans = api("POST", f"/api/ai/chats/{chat_id}/message", {"text": "How do I purify water in the field?"},
                    token=token, timeout=600)
    rep.check(S, "Ray answers", code == 200 and len(ans.get("content", "")) > 80,
              f"{code} in {time.time() - t:.0f}s, {len(ans.get('content', ''))} chars")
    rag = []
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STATE}/data/meshtastic.db?mode=ro", uri=True)
        row = db.execute("SELECT explain FROM ai_messages WHERE chat_id=? AND role='assistant' "
                         "ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        rag = (json.loads(row[0]).get("rag") or {}).get("docs", []) if row and row[0] else []
    except Exception as e:
        rep.note(S, "explain trace", e)
    rep.check(S, "Ray used the knowledge base (RAG)", len(rag) > 0, ", ".join(d.get("title", "") for d in rag[:3]))
    api("DELETE", f"/api/ai/chats/{chat_id}", token=token)
    code, route = api("GET", "/api/nav/route?from_lat=37.5407&from_lon=-77.4360&to_lat=38.0293&to_lon=-78.4767&mode=road",
                      token=token, timeout=600)
    r0 = (route.get("routes") or [{}])[0]
    rep.check(S, "offline route (Richmond → Charlottesville)",
              code == 200 and not route.get("degraded") and r0.get("_source", "").startswith(("local", "stitched")),
              f"{r0.get('_source')} {round((r0.get('distance') or 0) / 1000)} km")
    code, gps = api("GET", "/api/gps", token=token)
    rep.note(S, "GPS", f"connected={gps.get('connected')} fix={'yes' if gps.get('fix') else 'no'}")
    code, dev = api("GET", "/api/device", token=token)
    rep.note(S, "Meshtastic radio", f"connected={dev.get('connected')}")
    after = outbound(pid)
    new = [c for c in after if c not in before]
    rep.check(S, "no outbound connections while using Ray, maps and routing", not new, "; ".join(new)[:300])


def switch_ray_model(token, model, timeout=1800):
    """Ask Atlas to switch Ray's model and wait for the job to finish."""
    code, r = api("POST", "/api/ai/model", {"model": model}, token=token)
    if code not in (200, 202):
        return {"result": "failed", "error": r.get("error") or f"HTTP {code}"}
    job_id = r["switch"]["id"]

    def finished():
        _, m = api("GET", "/api/ai/models", token=token)
        job = m.get("switch") or {}
        return job if job.get("id") == job_id and job.get("finished") else None
    return wait(finished, timeout, 3, f"the switch to {model}")


def ask_ray(token, question):
    code, chat = api("POST", "/api/ai/chats", {}, token=token)
    t = time.time()
    code, ans = api("POST", f"/api/ai/chats/{chat.get('id')}/message", {"text": question}, token=token, timeout=600)
    api("DELETE", f"/api/ai/chats/{chat.get('id')}", token=token)
    return code, ans.get("content", ""), time.time() - t


def section_models(rep, ctx):
    """Spec: every offered model runs tuned for this unit, switching is
    seamless, and the choice survives a restart."""
    S = "models"
    token = ctx["token"]
    _, m = api("GET", "/api/ai/models", token=token)
    start = m.get("active")
    for x in m.get("models", []):
        rep.note(S, x["label"], f"{x['status']} · {x['tag']} {x['quant']} · {x['options']['num_ctx']}-token context")
        rep.check(S, f"{x['label']} is on this unit", x["status"] == "installed", x["status"])
    order = [x["id"] for x in m.get("models", []) if x["id"] != start] + [start]
    for model in order:
        job = switch_ray_model(token, model)
        rep.check(S, f"switch to {model}", job.get("result") == "success",
                  job.get("error") or f"{job.get('gpu_pct')}% on the GPU")
        wait(lambda: api("GET", "/api/ai/status", token=token)[1].get("ai_ready"), 300, 3, "Ray ready")
        code, text, secs = ask_ray(token, "How do I purify water in the field?")
        rep.check(S, f"{model} answers", code == 200 and len(text) > 80, f"{code} in {secs:.0f}s")
        if model != start:
            sh(["systemctl", "restart", "atlas-control.service"])
            wait(health, 180, 2, "the app after a restart")
            wait(lambda: api("GET", "/api/ai/status", token=token)[1].get("ai_ready"), 600, 3, "Ray ready")
            _, after = api("GET", "/api/ai/models", token=token)
            gpu = next((x.get("speed_tok_s") for x in after.get("models", []) if x["id"] == model), None)
            rep.check(S, f"{model} still chosen after a restart", after.get("active") == model, f"{gpu} tokens/s")


def wait_update(token, pred, timeout, what):
    def probe():
        code, st = api("GET", "/api/update/state", token=token, timeout=10)
        return st if code == 200 and pred(st) else None
    return wait(probe, timeout, 1.0, what)


def section_download(rep, ctx):
    S = "download"
    token, server, rel = ctx["token"], ctx["server"], ctx["r1"]
    m = manifest(rel)
    server.serve(rel)
    n0 = len(server.requests)
    code, st = api("POST", "/api/update/check", {}, token=token)
    rep.check(S, "update found", st.get("state") == "available" and m["version"] in st.get("message", ""),
              st.get("message"))
    reqs = server.requests[n0:]
    rep.check(S, "the check sends no identifiers", reqs and all("?" not in r[1] and r[3] == "Atlas-Updater/1"
                                                               for r in reqs),
              sorted({(r[1].split("/")[-1], r[3]) for r in reqs}))
    code, st = api("POST", "/api/update/start", {"install": False}, token=token)
    samples = []

    def progress(want):
        def f(s):
            if s.get("progress_percent") is not None:
                samples.append(s["progress_percent"])
            return s.get("state") == "downloading" and (s.get("progress_percent") or 0) >= want
        return f
    wait_update(token, progress(30), 300, "30% downloaded")
    server.online = False
    t_cut = time.time()
    st = wait_update(token, lambda s: s.get("state") == "paused", 120, "paused")
    rep.check(S, "Internet cut mid-download → paused", st.get("message") == MSG_PAUSED, st.get("message"))
    time.sleep(8)
    server.online = True
    st = wait_update(token, progress(45), 300, "resumed")
    ranges = [r for r in server.requests if r[0] > t_cut and r[2]]
    rep.check(S, "download resumes with an HTTP Range request", bool(ranges), ranges[-1][2] if ranges else "none")
    wait_update(token, progress(60), 300, "60% downloaded")
    sh(["systemctl", "restart", "atlas-control.service"], check=True)
    wait(health, 120, 1, "app back after restart")
    st = wait_update(token, lambda s: s.get("state") in ("downloading", "paused", "verifying", "ready"), 60, "state kept")
    rep.check(S, "app restarted mid-download: download kept", st.get("state") in ("downloading", "paused", "verifying", "ready"),
              f"{st.get('state')} {st.get('progress_percent')}%")
    st = wait_update(token, lambda s: s.get("state") == "ready", 600, "verified and ready")
    rep.check(S, "download verified (signature + hashes)", True, st.get("message"))
    rep.check(S, "live progress was reported and never went backwards",
              len(samples) > 5 and all(b >= a for a, b in zip(samples, samples[1:]) if b is not None),
              f"{len(samples)} samples")
    before = last_result()
    code, st = api("POST", "/api/update/install", {}, token=token)
    dup = None
    for _ in range(20):
        time.sleep(0.5)
        dup = helper("update.apply")
        if dup.get("already_running"):
            break
    rep.check(S, "a second install request while installing is refused", bool(dup and dup.get("already_running")), dup)
    t = time.time()
    result = wait_new_result(before, 900, "the install to finish")
    rep.check(S, "engine verified and installed it", result.get("result") == "success", result.get("result"))
    wait(lambda: (health() or {}).get("version") == m["version"], 300, 2, "new version serving")
    st = wait_update(token, lambda s: s.get("state") in ("up_to_date", "failed", "available", "idle"), 120, "result")
    rep.check(S, "installed, restarted, reconnected",
              st.get("message") == msg_updated(m["version"]) and (st.get("updated") or {}).get("version") == m["version"],
              f"{st.get('message')} ({time.time() - t:.0f}s)")
    ok, inv = invariant()
    rep.check(S, "consistent afterwards", ok, inv)


def section_forged(rep, ctx):
    S = "forged"
    token, server = ctx["token"], ctx["server"]
    forged = tempfile.mkdtemp()
    src = ctx["r_next"]
    for name in os.listdir(src):
        shutil.copy(os.path.join(src, name), forged)
    m = manifest(forged)
    m["notes"] = ["tampered after signing"]
    with open(os.path.join(forged, "atlas-release.json"), "w") as f:
        json.dump(m, f)
    server.serve(forged)
    code, st = api("POST", "/api/update/check", {}, token=token)
    rep.check(S, "forged manifest is not offered", st.get("state") != "available" and
              st.get("message") == MSG_NOT_VERIFIED, st.get("message"))
    shutil.rmtree(forged, ignore_errors=True)


def section_rollback(rep, ctx):
    S = "rollback"
    token, server, bad = ctx["token"], ctx["server"], ctx["r_bad"]
    before = installed_version()
    prev = last_result()
    server.serve(bad)
    code, st = api("POST", "/api/update/check", {}, token=token)
    code, st = api("POST", "/api/update/start", {"install": True}, token=token)
    t = time.time()
    result = wait_new_result(prev, 1500, "the engine to finish the unhealthy release")
    wait(health, 300, 2, "app back")
    rep.check(S, "unhealthy release rolled back automatically", result.get("result") == "rolled_back",
              f"{result.get('result')} after {time.time() - t:.0f}s")
    rep.check(S, "previous version serving again", (health() or {}).get("version") == before, before)
    st = wait_update(token, lambda s: s.get("state") != "installing", 120, "app result")
    rep.check(S, "owner sees the rollback message",
              (st.get("message") or "").startswith(msg_rolled_back(manifest(bad)["version"], before)), st.get("message"))
    ok, inv = invariant()
    rep.check(S, "consistent afterwards", ok, inv)


def hand_off(release_dir):
    os.makedirs(HANDOFF, exist_ok=True)
    for name in os.listdir(HANDOFF):
        os.unlink(os.path.join(HANDOFF, name))
    for name in os.listdir(release_dir):
        dst = os.path.join(HANDOFF, name)
        shutil.copy(os.path.join(release_dir, name), dst)
        shutil.chown(dst, "atlas", "atlas")
        os.chmod(dst, 0o640)


def section_faults(rep, ctx):
    S = "faults"
    builder = ctx["builder"]
    release = builder.build("faults-a")
    for state in FAULT_STATES:
        before = installed_version()
        if before == manifest(release)["version"]:
            release = builder.build(f"faults-{state.lower()}")
        dropin("atlas-update-engine.service", "acceptance-fault",
               f"[Service]\nEnvironment=ATLAS_ENGINE_FAULT={state}:crash\n")
        try:
            prev = last_result()
            hand_off(release)
            reply = helper("update.apply")
            if not reply.get("ok"):
                rep.check(S, f"crash at {state}", False, reply)
                continue
            # the crash fails the unit; OnFailure= runs atlas-update-recover
            result = wait_new_result(prev, 1200, f"recovery after a crash at {state}")
        except TimeoutError as e:
            rep.check(S, f"crash at {state} → recovered", False, e)
            continue
        finally:
            remove_dropin("atlas-update-engine.service", "acceptance-fault")
        wait(health, 300, 2, "app healthy")
        ok, inv = invariant()
        outcome = "installed" if installed_version() == manifest(release)["version"] else "not installed"
        rep.check(S, f"crash at {state} → recovered ({outcome}, {result.get('result')})", ok, inv)
        check_persistence(rep, S, ctx, f"after a crash at {state}")


def ensure_latest(rep, ctx):
    """Leave the unit on a fresh release of this checkout."""
    release = ctx["builder"].build("final")
    prev = last_result()
    hand_off(release)
    helper("update.apply")
    wait_new_result(prev, 1200, "the final release")
    wait(lambda: (health() or {}).get("version") == manifest(release)["version"], 300, 3, "final release serving")
    rep.note("final", "left on", manifest(release)["version"])


# ── commands ─────────────────────────────────────────────────────────────
def preflight():
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    if not os.path.exists("/etc/atlas/dev-mode"):
        sys.exit("developer units only (/etc/atlas/dev-mode)")
    if not health():
        sys.exit("Atlas is not answering on https://127.0.0.1 — fix that first")
    if not engine_idle():
        sys.exit("an update is in progress — wait for it to finish")


def cmd_run(args):
    preflight()
    rep = Report()
    only = set(filter(None, (args.only or "").split(","))) or \
        {"security", "function", "models", "download", "forged", "rollback", "faults"}
    ctx = {"builder": Builder()}
    server = None
    token = client_id = None
    try:
        token, client_id = pair()
        ctx["token"] = token
        ctx["facts"] = connection_facts(token)
        if "security" in only:
            section_security(rep, ctx)
        if "function" in only:
            section_function(rep, ctx)
        if "models" in only:
            try:
                section_models(rep, ctx)
            except Exception as e:
                rep.check("models", "section finished", False, f"{type(e).__name__}: {e}")
                traceback.print_exc()
        releases = only & {"download", "forged", "rollback"}
        if releases:
            try:
                ctx["r1"] = ctx["builder"].build("download")
                ctx["r_bad"] = ctx["builder"].build_unhealthy() if "rollback" in only else None
                ctx["r_next"] = ctx["builder"].build("forged") if "forged" in only else None
            except Exception as e:
                for name in sorted(releases):
                    rep.check(name, "test releases built", False, f"{type(e).__name__}: {e}")
                traceback.print_exc()
                releases = set()
        if releases:
            server = ReleaseServer()
            ctx["server"] = server
            dropin("atlas-control.service", "acceptance-releases",
                   f"[Service]\nEnvironment=ATLAS_RELEASES_URL=http://127.0.0.1:{PORT}\n")
            sh(["systemctl", "restart", "atlas-control.service"], check=True)
            wait(health, 120, 1, "app with the test release server")
            for name, fn in (("download", section_download), ("forged", section_forged), ("rollback", section_rollback)):
                if name in releases:
                    try:
                        fn(rep, ctx)
                    except Exception as e:
                        rep.check(name, "section finished", False, f"{type(e).__name__}: {e}")
                        traceback.print_exc()
                    check_persistence(rep, name, ctx, f"after the {name} scenario")
        if "faults" in only:
            try:
                section_faults(rep, ctx)
            except Exception as e:
                rep.check("faults", "section finished", False, f"{type(e).__name__}: {e}")
                traceback.print_exc()
    finally:
        remove_dropin("atlas-update-engine.service", "acceptance-fault")
        remove_dropin("atlas-control.service", "acceptance-releases")
        if server:
            server.close()
        sh(["systemctl", "restart", "atlas-control.service"])
        try:
            wait(health, 180, 2, "app after cleanup")
        except TimeoutError as e:
            rep.check("cleanup", "app healthy after the test", False, e)
        try:
            if not args.no_final:
                ensure_latest(rep, ctx)
                check_persistence(rep, "final", ctx, "on the final release")
        except Exception as e:
            rep.check("final", "left on a fresh release", False, e)
        unpair(token, client_id)
        ctx["builder"].cleanup()
    return 0 if rep.write({"only": sorted(only)}) else 1


def cmd_reboot_test(args):
    preflight()
    builder = Builder()
    release = builder.build("reboot")
    m = manifest(release)
    with open(REBOOT_STATE, "w") as f:
        json.dump({"version": m["version"], "from": installed_version(), "at": time.time(),
                   "facts": connection_facts(),
                   "result_before": last_result().get("finished", 0),
                   "boot_id": open("/proc/sys/kernel/random/boot_id").read().strip()}, f)
    dropin("atlas-update-engine.service", "acceptance-fault", "[Service]\nEnvironment=ATLAS_ENGINE_FAULT=SWITCHED:panic\n")
    hand_off(release)
    sh(["sync"])
    print(f"installing {m['version']}; the engine will cut power right after switching releases.")
    print("After Atlas comes back, run:  sudo tools/dev/acceptance after-reboot")
    helper("update.apply")
    time.sleep(600)                      # the reboot interrupts this
    return 1


def cmd_after_reboot(args):
    rep = Report()
    S = "power-loss"
    state = read_json(REBOOT_STATE)
    if not state:
        sys.exit("no reboot-test was started")
    boot = open("/proc/sys/kernel/random/boot_id").read().strip()
    rep.check(S, "the unit actually rebooted", boot != state["boot_id"])
    try:
        wait_new_result({"finished": state.get("result_before", 0)}, 900, "boot recovery")
        wait(health, 300, 3, "the app")
    except TimeoutError as e:
        rep.check(S, "recovered on boot", False, e)
    result = last_result()
    ok, inv = invariant()
    rep.check(S, "recovered on boot to a consistent state", ok, inv)
    rep.check(S, "the interrupted update was completed (health check resumed after boot)",
              installed_version() == state["version"] and result.get("result") == "success", result.get("result"))
    if state.get("facts"):
        now = connection_facts()
        rep.check(S, "same device id and TLS key after the power cut (paired phones reconnect)",
                  now == state["facts"], "")
    log = sh(["journalctl", "-b", "-u", "atlas-update-recover", "--no-pager", "-o", "cat"]).stdout.strip().splitlines()
    rep.note(S, "boot recovery log", " | ".join(log[-4:]))
    os.unlink(REBOOT_STATE)
    return 0 if rep.write({"test": "reboot"}) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--only", help="comma-separated sections")
    r.add_argument("--no-final", action="store_true", help="do not finish on a fresh release")
    sub.add_parser("reboot-test")
    sub.add_parser("after-reboot")
    args = ap.parse_args()
    if args.cmd in (None, "run"):
        if args.cmd is None:
            args.only, args.no_final = None, False
        return cmd_run(args)
    return {"reboot-test": cmd_reboot_test, "after-reboot": cmd_after_reboot}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

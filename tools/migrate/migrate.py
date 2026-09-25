"""
Move an existing (pre-release) Atlas onto the hardened layout — or back.

    sudo tools/migrate/atlas-migrate apply  --bundle DIR [--dev-key PUB] [--rotate-hotspot]
    sudo tools/migrate/atlas-migrate rollback

apply, in order (each step is recorded and skipped if already done, so a
failed or interrupted run can simply be re-run):

  preflight   platform, free space, nginx config, database integrity, and the
              release bundle's signature
  backup      every system file this touches, owners/modes of state paths and
              a consistent copy of the database, under /var/lib/atlas-migrate
  user        the unprivileged `atlas` service account (admin joins its group
              so the developer checkout keeps working)
  engine      /usr/local/lib/atlas: engine, trusted keys (+ developer key)
  identity    /etc/atlas/identity in "adopt" mode: keeps hostname and a
              non-default hotspot password, new per-device TLS key
  state       data/ logs/ updates/ cache/ owned by atlas; datasets stay
              read-only; WAL datasets switched to a read-only-friendly mode
  release     the bundle installed as /opt/atlas/releases/<version>
  cutover     new units/nginx/udev installed and validated, sudo rules and
              the old update launcher removed, services restarted
  verify      health check of the running release; any failure rolls back

rollback restores every backed-up file, ownership and the old service,
leaving /opt/atlas and /etc/atlas in place (harmless) unless --purge.
"""
import argparse
import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "system"))

from atlas_engine import (ENGINE_VERSION, engine, extract, fsutil, health, identity,  # noqa: E402
                          paths, sysfiles, venvs, verify)
from atlas_engine.runner import DEFAULT as RUNNER  # noqa: E402

P = paths.p   # every absolute path goes through ATLAS_ROOT (sandbox tests)
STATE_DIR = P("/var/lib/atlas-migrate")
STATE = os.path.join(STATE_DIR, "state.json")
BACKUP = os.path.join(STATE_DIR, "backup")
SYSBACKUP = os.path.join(STATE_DIR, "sysfiles")
APP = paths.APP_STATE
LEGACY_OWNER = None

BACKED_UP_FILES = [P(f) for f in (
    "/etc/systemd/system/atlas-control.service",
    "/etc/nginx/sites-available/atlas",
    "/etc/sudoers.d/atlas-nmcli",
    "/etc/sudoers.d/atlas-smartctl",
    "/etc/sudoers.d/atlas-avahi",
    "/etc/sudoers.d/atlas-update",
    "/usr/local/sbin/atlas-update",
    "/etc/udev/rules.d/99-atlas-devices.rules",
    "/etc/avahi/services/atlas-http.service",
    "/etc/hostname",
)]
REMOVED_AT_CUTOVER = [P(f) for f in (
    "/etc/sudoers.d/atlas-nmcli",
    "/etc/sudoers.d/atlas-smartctl",
    "/etc/sudoers.d/atlas-avahi",
    "/etc/sudoers.d/atlas-update",
    "/usr/local/sbin/atlas-update",
)]
WRITABLE_STATE = ["data", "logs", "updates", "cache"]


def say(msg):
    print(f"[atlas-migrate] {msg}", flush=True)


def sh(cmd, check=True, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()[-800:]}")
    return r


def load_state():
    return fsutil.read_json(STATE, {"done": [], "started": time.time()})


def mark(state, step, **extra):
    if step not in state["done"]:
        state["done"].append(step)
    state.update(extra)
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    fsutil.atomic_write_json(STATE, state, mode=0o600)


def legacy_owner():
    return pwd.getpwuid(os.stat(APP).st_uid).pw_name


def as_user(user, cmd):
    return ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups"] + cmd


# ── steps ────────────────────────────────────────────────────────────────
def step_preflight(state, args):
    import platform
    import tarfile
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    if platform.machine() != "aarch64":
        raise SystemExit("not an aarch64 Atlas")
    if sys.version_info[:2] != (3, 12) or not hasattr(tarfile, "data_filter"):
        raise SystemExit("needs the system Python 3.12")
    st = os.statvfs(P("/opt") if os.path.exists(P("/opt")) else P("/"))
    if st.f_bavail * st.f_frsize < 3 * 1024 ** 3:
        raise SystemExit("need at least 3 GB free")
    if sh(["nginx", "-t"], check=False).returncode != 0:
        raise SystemExit("the current nginx configuration does not validate; fix that first")
    db = os.path.join(APP, "data", "meshtastic.db")
    if os.path.exists(db):
        r = sh(as_user(legacy_owner(), ["/usr/bin/python3", "-I", "-c",
                "import sqlite3,sys;print(sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)"
                ".execute('PRAGMA quick_check').fetchone()[0])", db]))
        if r.stdout.strip() != "ok":
            raise SystemExit(f"database integrity check failed: {r.stdout.strip()}")
    keys = verify.load_keyring(os.path.join(REPO, "system", "keys", "keyring.json"))
    if args.dev_key:
        import base64
        raw = base64.b64decode(open(args.dev_key).read().strip())
        keys[verify.key_id(raw)] = raw
    mbytes = open(os.path.join(args.bundle, engine.MANIFEST_NAME), "rb").read()
    sig = open(os.path.join(args.bundle, engine.SIG_NAME), "rb").read()
    manifest, kid = verify.verify_manifest(mbytes, sig, keys, arch="aarch64")
    bundle_path = os.path.join(args.bundle, manifest["bundle"]["name"])
    if verify.verify_bundle_file(bundle_path, manifest["bundle"]) is not None:
        raise SystemExit("release bundle does not match its signed manifest")
    say(f"release {manifest['version']} (seq {manifest['seq']}) signed by {kid}: OK")
    mark(state, "preflight", manifest=manifest, bundle=bundle_path, legacy_owner=legacy_owner())


def step_backup(state, args):
    os.makedirs(BACKUP, mode=0o700, exist_ok=True)
    record = []
    for path in BACKED_UP_FILES:
        entry = {"path": path, "existed": os.path.lexists(path)}
        if entry["existed"]:
            dest = os.path.join(BACKUP, path.strip("/").replace("/", "__"))
            shutil.copy2(path, dest, follow_symlinks=False)
            st = os.lstat(path)
            entry.update(saved=dest, mode=st.st_mode & 0o7777, uid=st.st_uid, gid=st.st_gid)
        record.append(entry)
    enabled = P("/etc/nginx/sites-enabled/atlas")
    owners = []
    for rel in WRITABLE_STATE + ["hotspot_config.json", "osrm_active.json"]:
        top = os.path.join(APP, rel)
        if not os.path.exists(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top) if os.path.isdir(top) else [(os.path.dirname(top), [], [os.path.basename(top)])]:
            for name in ([dirpath] if os.path.isdir(top) else []) + [os.path.join(dirpath, f) for f in filenames]:
                st = os.lstat(name)
                owners.append([name, st.st_uid, st.st_gid, st.st_mode & 0o7777])
    fsutil.atomic_write_json(os.path.join(BACKUP, "files.json"), record, mode=0o600)
    fsutil.atomic_write_json(os.path.join(BACKUP, "owners.json"), owners, mode=0o600)
    fsutil.atomic_write_json(os.path.join(BACKUP, "nginx-enabled.json"),
                             {"link": os.readlink(enabled) if os.path.islink(enabled) else None}, mode=0o600)
    db = os.path.join(APP, "data", "meshtastic.db")
    if os.path.exists(db):
        copy = os.path.join(BACKUP, "meshtastic.db")
        if os.path.exists(copy):
            os.unlink(copy)
        # Run as the database's owner so no root-owned -wal/-shm files appear.
        tmp = os.path.join(APP, "data", "migrate-backup.db")
        sh(as_user(state["legacy_owner"], ["/usr/bin/python3", "-I", "-c",
                   "import sqlite3,sys;sqlite3.connect(sys.argv[1]).execute('VACUUM INTO ?',(sys.argv[2],))",
                   db, tmp]))
        shutil.move(tmp, copy)
        os.chmod(copy, 0o600)
    say(f"backed up system files and state ownership to {BACKUP}")
    mark(state, "backup")


def step_user(state, args):
    user = paths.SERVICE_USER
    try:
        pwd.getpwnam(user)
    except KeyError:
        sh(["useradd", "--system", "--user-group", "--home-dir", "/var/lib/atlas",
            "--shell", "/usr/sbin/nologin", user])
    groups = [g for g in ("dialout", "i2c", "gpio", "bluetooth") if _group_exists(g)]
    if groups:
        sh(["usermod", "-aG", ",".join(groups), user])
    if state["legacy_owner"] not in ("root", user):
        sh(["usermod", "-aG", user, state["legacy_owner"]])
    say(f"service account '{user}' ready (groups: {', '.join(groups)})")
    mark(state, "user")


def _group_exists(name):
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def step_engine(state, args):
    staging = os.path.join(paths.RELEASES, ".staging-migrate")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(paths.RELEASES, mode=0o755, exist_ok=True)
    extract.extract_bundle(state["bundle"], staging, state["manifest"]["bundle"]["unpacked_size"])
    engine.install_engine(os.path.join(staging, "system"), ENGINE_VERSION, RUNNER)
    fsutil.atomic_write_bytes(paths.KEYRING, open(os.path.join(staging, "system", "keys",
                                                              "keyring.json"), "rb").read(), mode=0o644)
    if args.dev_key:
        os.makedirs(paths.DEV_KEYS, mode=0o755, exist_ok=True)
        fsutil.atomic_write_bytes(os.path.join(paths.DEV_KEYS, "dev.pub"),
                                  open(args.dev_key, "rb").read(), mode=0o644)
        fsutil.atomic_write_bytes(paths.DEV_MODE_FLAG, b"developer unit\n", mode=0o644)
    say(f"engine {ENGINE_VERSION} installed in {paths.ENGINE_DIR}" +
        (" (developer mode, dev key trusted)" if args.dev_key else ""))
    mark(state, "engine", staging=staging)


def step_identity(state, args):
    adopt = None
    if not args.rotate_hotspot:
        cfg = fsutil.read_json(os.path.join(APP, "hotspot_config.json"), {}) or \
            fsutil.read_json(os.path.join(APP, "data", "hotspot_config.json"), {}) or {}
        adopt = cfg.get("password")
    device = identity.ensure(adopt_hotspot_password=adopt, hostname="atlas")
    hotspot = fsutil.read_json(os.path.join(paths.IDENTITY, "hotspot.json"), {})
    rotated = hotspot.get("password") != adopt
    say(f"identity {device['name']} (TLS pin {device['tls_spki_sha256'][:12]}…)")
    mark(state, "identity", hotspot_rotated=rotated)


def step_state(state, args):
    user = paths.SERVICE_USER
    uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(user).gr_gid
    for rel in WRITABLE_STATE:
        os.makedirs(os.path.join(APP, rel), exist_ok=True)
    os.makedirs(os.path.join(APP, "data", "backups"), exist_ok=True)
    os.makedirs(os.path.join(APP, "updates", "ready"), exist_ok=True)
    for rel in ("hotspot_config.json", "osrm_active.json"):
        legacy, new = os.path.join(APP, rel), os.path.join(APP, "data", rel)
        if os.path.exists(legacy) and not os.path.exists(new):
            shutil.copy2(legacy, new)
    if state.get("hotspot_rotated"):
        hs = fsutil.read_json(os.path.join(paths.IDENTITY, "hotspot.json"))
        fsutil.atomic_write_json(os.path.join(APP, "data", "hotspot_config.json"),
                                 {"ssid": hs["ssid"], "password": hs["password"]}, mode=0o600)
    for rel in WRITABLE_STATE:
        for dirpath, dirnames, filenames in os.walk(os.path.join(APP, rel)):
            os.chown(dirpath, uid, gid)
            os.chmod(dirpath, 0o2770)
            for f in filenames:
                p = os.path.join(dirpath, f)
                if not os.path.islink(p):
                    os.chown(p, uid, gid)
                    os.chmod(p, 0o660)
    for secret in ("hotspot_config.json", ".secret_key"):
        p = os.path.join(APP, "data", secret)
        if os.path.exists(p):
            os.chmod(p, 0o600)
    say("state directories handed to the service account")
    mark(state, "state")


def convert_wal_datasets(state):
    """Read-only datasets in WAL mode need a writable -shm to open; switch
    them to a rollback journal. Runs with Atlas stopped (nothing holds them)."""
    static_data = os.path.join(APP, "static", "data")
    if not os.path.isdir(static_data):
        return
    for f in sorted(os.listdir(static_data)):
        if f.endswith(".db") and os.path.exists(os.path.join(static_data, f + "-wal")):
            r = sh(as_user(state["legacy_owner"], ["/usr/bin/python3", "-I", "-c",
                   "import sqlite3,sys;c=sqlite3.connect(sys.argv[1],timeout=30);"
                   "c.execute('PRAGMA wal_checkpoint(TRUNCATE)');"
                   "print(c.execute('PRAGMA journal_mode=DELETE').fetchone()[0])",
                   os.path.join(static_data, f)]), check=False)
            if r.returncode != 0:
                say(f"warning: could not convert {f} ({(r.stderr or '').strip()[-120:]})")


def step_release(state, args):
    m = state["manifest"]
    target = os.path.join(paths.RELEASES, m["version"])
    staging = state["staging"]
    if not os.path.exists(os.path.join(target, ".complete")):
        key = venvs.ensure_venv(staging, RUNNER)
        venvs.link_release(staging, key)
        engine.strip_write_bits(os.path.join(paths.VENVS, key))
        RUNNER.run(["/usr/bin/python3", "-m", "compileall", "-q", staging], timeout=900)
        shutil.rmtree(os.path.join(staging, "wheels"), ignore_errors=True)
        engine.strip_write_bits(staging)
        fsutil.atomic_write_bytes(os.path.join(staging, ".complete"), (m["version"] + "\n").encode())
        os.sync()
        shutil.rmtree(target, ignore_errors=True)
        os.rename(staging, target)
    say(f"release {m['version']} staged at {target}")
    mark(state, "release", target=target)


def step_cutover(state, args):
    target = state["target"]
    RUNNER.run(["systemctl", "stop", "atlas-control.service"], timeout=120)
    convert_wal_datasets(state)
    changed = sysfiles.install(target, SYSBACKUP, RUNNER)
    for path in REMOVED_AT_CUTOVER:
        if os.path.lexists(path):
            os.unlink(path)
    fsutil.atomic_symlink(os.path.relpath(target, paths.OPT), paths.CURRENT)
    if not os.path.lexists(paths.FACTORY):
        fsutil.atomic_symlink(os.path.relpath(target, paths.OPT), paths.FACTORY)
    m = state["manifest"]
    fsutil.atomic_write_json(paths.INSTALLED, {"version": m["version"], "seq": m["seq"], "release": target,
                                               "schema_target": m["db"]["schema_target"],
                                               "installed": time.time(), "txid": "migration"})
    os.makedirs(paths.UPDATE_STATE, mode=0o700, exist_ok=True)
    fsutil.atomic_write_json(paths.JOURNAL, {"state": "IDLE", "last_txid": "migration"}, mode=0o600)
    RUNNER.run(["systemctl", "daemon-reload"])
    spec = sysfiles.load_spec(target)
    sysfiles.reload_changed(changed, spec, RUNNER)
    RUNNER.run(["systemctl", "enable", "--now", "atlas-helper.socket"])
    RUNNER.run(["systemctl", "start", "atlas-control.service"])
    RUNNER.run(["systemctl", "reload-or-restart", "nginx"])
    say(f"cut over to {m['version']} (changed: {', '.join(changed)})")
    mark(state, "cutover")


def step_verify(state, args):
    m = state["manifest"]
    result = health.check(RUNNER, m["version"], state["target"], budget_s=240, stable_s=20)
    if not result.ok:
        raise SystemExit(f"health check failed: {result.detail}")
    r = sh(["runuser", "-u", paths.SERVICE_USER, "--", "test", "-w",
            os.path.join(APP, "data", "meshtastic.db")], check=False)
    if r.returncode != 0:
        raise SystemExit("the service account cannot write the database")
    helper = sh(["runuser", "-u", paths.SERVICE_USER, "--", "/usr/bin/python3", "-I", "-c",
                 f"import socket,json;s=socket.socket(socket.AF_UNIX);s.connect({P('/run/atlas/helper.sock')!r});"
                 "s.sendall(b'{\"verb\":\"ping\"}\\n');print(s.recv(200).decode())"], check=False)
    if '"pong": true' not in helper.stdout:
        raise SystemExit(f"atlas-helper did not answer: {helper.stdout or helper.stderr}")
    say("health check passed; helper answering; service account can write its data")
    mark(state, "verify", finished=time.time())


STEPS = [("preflight", step_preflight), ("backup", step_backup), ("user", step_user),
         ("engine", step_engine), ("identity", step_identity), ("state", step_state),
         ("release", step_release), ("cutover", step_cutover), ("verify", step_verify)]


def apply(args):
    state = load_state()
    if "verify" in state["done"]:
        say("already migrated")
        return 0
    for name, fn in STEPS:
        if name in state["done"]:
            continue
        say(f"step: {name}")
        try:
            fn(state, args)
        except BaseException as exc:
            say(f"step {name} failed: {exc}")
            if "cutover" in state["done"] or name == "cutover":
                say("rolling back to the previous setup …")
                rollback(argparse.Namespace(purge=False))
            return 1
    hs = fsutil.read_json(os.path.join(paths.IDENTITY, "hotspot.json"), {})
    say("done. Atlas now runs as the unprivileged 'atlas' account from "
        f"{os.path.realpath(paths.CURRENT)}.")
    if state.get("hotspot_rotated"):
        say(f"NEW hotspot network: {hs.get('ssid')}  password: {hs.get('password')}")
    return 0


def rollback(args):
    state = load_state()
    say("stopping Atlas")
    RUNNER.run(["systemctl", "stop", "atlas-control.service"], timeout=120)
    RUNNER.run(["systemctl", "disable", "--now", "atlas-helper.socket"], timeout=60)
    sysfiles.restore(SYSBACKUP)
    for entry in fsutil.read_json(os.path.join(BACKUP, "files.json"), []) or []:
        if entry["existed"] and entry.get("saved"):
            shutil.copy2(entry["saved"], entry["path"], follow_symlinks=False)
            os.chown(entry["path"], entry["uid"], entry["gid"], follow_symlinks=False)
            os.chmod(entry["path"], entry["mode"])
    for name, uid, gid, mode in fsutil.read_json(os.path.join(BACKUP, "owners.json"), []) or []:
        if os.path.lexists(name):
            os.chown(name, uid, gid, follow_symlinks=False)
            if not os.path.islink(name):
                os.chmod(name, mode)
    owner = state.get("legacy_owner")
    if owner:
        for rel in WRITABLE_STATE:
            top = os.path.join(APP, rel)
            if os.path.isdir(top):
                sh(["chown", "-R", f"{owner}:{owner}", top], check=False)
    for unit in ("atlas-update-recover.service", "atlas-firstboot.service", "atlas-firewall.service"):
        RUNNER.run(["systemctl", "disable", unit], timeout=60)
    RUNNER.run(["systemctl", "daemon-reload"])
    if sh(["nginx", "-t"], check=False).returncode == 0:
        RUNNER.run(["systemctl", "reload-or-restart", "nginx"])
    RUNNER.run(["udevadm", "control", "--reload"])
    RUNNER.run(["udevadm", "trigger", "--subsystem-match=tty"])
    RUNNER.run(["systemctl", "start", "atlas-control.service"])
    if getattr(args, "purge", False):
        for p in ("/opt/atlas", "/usr/local/lib/atlas", "/var/lib/atlas-update", "/etc/atlas"):
            shutil.rmtree(P(p), ignore_errors=True)
    fsutil.atomic_write_json(STATE, {"done": [], "rolled_back": time.time()}, mode=0o600)
    say("rolled back: Atlas runs the previous way again")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--bundle", required=True, help="directory with atlas-release.json(.sig) and the bundle")
    a.add_argument("--dev-key", help="developer public key to trust (makes this a developer unit)")
    a.add_argument("--rotate-hotspot", action="store_true", help="give the hotspot a new random password")
    r = sub.add_parser("rollback")
    r.add_argument("--purge", action="store_true")
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    return apply(args) if args.cmd == "apply" else rollback(args)


if __name__ == "__main__":
    sys.exit(main())

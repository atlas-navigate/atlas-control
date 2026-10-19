#!/usr/bin/python3 -I
"""Hand a signed release to this Atlas's update engine and wait for the result.

    handoff.py RELEASE_DIR [--state DIR] [--timeout SECONDS]

RELEASE_DIR holds atlas-release.json, its .sig and the bundle. They are copied
into <state>/updates/ready (as root: owned by the atlas account; as a member
of the atlas group: group-readable), atlas-helper is asked to apply them, and
the engine's progress is printed until it finishes. Exit status 0 only if the
release was installed; a refusal or a rollback is reported at once.

Used by install.sh (root) and tools/dev/deploy (developer units). Standard
library only.
"""
import argparse
import glob
import json
import os
import pwd
import shutil
import socket
import sys
import time

STATUS = os.environ.get("ATLAS_UPDATE_STATUS", "/run/atlas-update/status.json")
HELPER = os.environ.get("ATLAS_HELPER_SOCKET", "/run/atlas/helper.sock")
DONE = ("success", "rolled_back", "failed", "cancelled", "interrupted")


def status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def stage_files(release_dir, ready):
    files = [os.path.join(release_dir, "atlas-release.json"), os.path.join(release_dir, "atlas-release.json.sig")]
    files += sorted(glob.glob(os.path.join(release_dir, "*.tar.xz")))
    missing = [f for f in files[:2] if not os.path.isfile(f)]
    if missing or len(files) < 3:
        raise SystemExit(f"not a release directory (need atlas-release.json, .sig and a bundle): {release_dir}")
    root = os.geteuid() == 0
    if root:
        atlas = pwd.getpwnam("atlas")
        os.makedirs(ready, exist_ok=True)
        os.chown(ready, atlas.pw_uid, atlas.pw_gid)
        os.chmod(ready, 0o2770)
    for old in os.listdir(ready):
        try:
            os.unlink(os.path.join(ready, old))
        except OSError:
            pass
    for src in files:
        dst = os.path.join(ready, os.path.basename(src))
        shutil.copyfile(src, dst)
        if root:
            os.chown(dst, atlas.pw_uid, atlas.pw_gid)
            os.chmod(dst, 0o640)
        else:
            os.chmod(dst, 0o640)


def ask_apply():
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(30)
        s.connect(HELPER)
        s.sendall(b'{"verb": "update.apply", "args": {}}\n')
        reply = s.recv(4096).decode(errors="replace").strip()
    except OSError as exc:
        raise SystemExit(f"atlas-helper is not answering at {HELPER}: {exc}")
    finally:
        s.close()
    try:
        ok = json.loads(reply).get("ok")
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit("atlas-helper refused the update (on a retail unit only Atlas itself may "
                         f"install updates, and only signed releases): {reply or 'no reply'}")


def wait(before, timeout, out=sys.stdout):
    last = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = status()
        line = f"{st.get('stage')}: {st.get('message') or ''}"
        if line != last:
            print("   ", line, file=out, flush=True)
            last = line
        if st.get("txid") != before and st.get("engine_state") == "IDLE" and st.get("stage") in DONE:
            return st
        time.sleep(1)
    raise SystemExit(f"timed out after {timeout} s waiting for the update engine")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release_dir")
    ap.add_argument("--state", default=os.environ.get("ATLAS_STATE_DIR", "/atlas_data/atlas-control"))
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)
    # The status file still shows the previous update's result until the
    # engine picks this one up: only a result with a new transaction id counts.
    before = status().get("txid")
    stage_files(os.path.abspath(args.release_dir), os.path.join(args.state, "updates", "ready"))
    ask_apply()
    st = wait(before, args.timeout)
    if st["stage"] != "success":
        print(f"the release was not installed ({st['stage']}): {st.get('message') or ''}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

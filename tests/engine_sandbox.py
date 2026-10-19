"""A throwaway root filesystem for exercising the update engine.

Builds signed release bundles with a test Ed25519 key, fakes every external
command (systemctl, nginx, venv/pip, ...) and lets tests decide whether the
"new release" passes its health check.
"""
import base64
import hashlib
import importlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

_SYSTEM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "system")
if _SYSTEM not in sys.path:
    sys.path.insert(0, _SYSTEM)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records commands; simulates the few whose side effects matter."""

    def __init__(self):
        self.calls = []
        self.fail = {}          # first word or "nginx -t" -> returncode
        self.outputs = {}       # first word, first two, or the whole command -> stdout
        self.on_restart = None  # callback when atlas-control restarts

    def run(self, cmd, timeout=120, input=None, check=False, env=None):
        self.calls.append(list(cmd))
        key = " ".join(cmd[:2])
        rc = self.fail.get(key, self.fail.get(cmd[0], 0))
        if "-m" in cmd and "venv" in cmd:
            vdir = cmd[-1]
            os.makedirs(os.path.join(vdir, "bin"), exist_ok=True)
            with open(os.path.join(vdir, "bin", "python3"), "w") as f:
                f.write("#!fake\n")
        if cmd[:2] == ["systemctl", "restart"] and cmd[2:] == ["atlas-control.service"] and self.on_restart:
            self.on_restart()
        if cmd[0] == "/usr/bin/python3" and "-c" in cmd and "sqlite3" in cmd[cmd.index("-c") + 1]:
            # probe_db: run it for real against the sandbox database
            import subprocess
            r = subprocess.run(cmd, capture_output=True, text=True)
            return FakeResult(r.returncode, r.stdout, r.stderr)
        if check and rc:
            raise RuntimeError(f"{cmd[0]} failed")
        out = self.outputs.get(" ".join(cmd), self.outputs.get(key, self.outputs.get(cmd[0], "")))
        return FakeResult(rc, out(cmd) if callable(out) else out)

    def ran(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


class Sandbox:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="atlas-root-")
        self.app_state = os.path.join(self.root, "atlas_data", "atlas-control")
        os.makedirs(os.path.join(self.app_state, "data"), exist_ok=True)
        os.environ["ATLAS_ROOT"] = self.root
        os.environ["ATLAS_STATE_DIR"] = self.app_state
        from atlas_engine import paths
        importlib.reload(paths)
        self.paths = paths
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        os.makedirs(os.path.dirname(paths.KEYRING), exist_ok=True)
        with open(paths.KEYRING, "w") as f:
            json.dump({"keyring_version": 1,
                       "keys": [{"pub": base64.b64encode(raw).decode(), "status": "active"}]}, f)
        self.runner = FakeRunner()
        self.healthy_versions = set()
        self.health_calls = []

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # ── releases ─────────────────────────────────────────────────────────
    def release_tree(self, version, seq, system_marker="v", schema_target=1, extra_files=None):
        tree = tempfile.mkdtemp(prefix="atlas-rel-", dir=self.root)
        files = {
            "release.json": json.dumps({"version": version, "seq": seq, "schema_target": schema_target,
                                        "engine_version": 1}),
            "requirements.lock": "flask==3.0.0 --hash=sha256:" + "0" * 64 + "\n",
            "app.py": f"VERSION = {version!r}\n",
            "wheels/placeholder.txt": "wheel",
            "system/files.json": json.dumps({
                "files": [
                    {"src": "systemd/atlas-control.service",
                     "dst": "/etc/systemd/system/atlas-control.service", "mode": "0644", "kind": "systemd"},
                    {"src": "nginx/atlas.conf", "dst": "/etc/nginx/sites-available/atlas",
                     "mode": "0644", "kind": "nginx", "template": True},
                ],
                "symlinks": [{"link": "/etc/nginx/sites-enabled/atlas",
                              "target": "/etc/nginx/sites-available/atlas"}],
                "enable": ["atlas-helper.socket"],
            }),
            "system/systemd/atlas-control.service": f"[Service]\n# {system_marker}{version}\n",
            "system/nginx/atlas.conf": f"# {system_marker}{version} state=@ATLAS_STATE_DIR@\n",
        }
        files.update(extra_files or {})
        for rel, content in files.items():
            path = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
        return tree

    def make_bundle(self, tree, version, seq, tamper=None, members=None, unpacked=None, sign_key=None,
                    manifest_overrides=None):
        out = tempfile.mkdtemp(prefix="atlas-pub-", dir=self.root)
        name = f"atlas-{version}.tar.xz"
        bundle = os.path.join(out, name)
        with tarfile.open(bundle, "w:xz") as tar:
            for dirpath, _, filenames in os.walk(tree):
                for fn in sorted(filenames):
                    full = os.path.join(dirpath, fn)
                    tar.add(full, arcname=os.path.relpath(full, tree))
            for m, data in (members or []):
                tar.addfile(m, io.BytesIO(data) if data is not None else None)
        with open(bundle, "rb") as f:
            source = f.read()          # the manifest always describes this
        if tamper:
            with open(bundle, "wb") as f:
                f.write(tamper(source))
        chunk = 1024 * 1024
        size = len(source)
        manifest = {
            "format": 1, "product": "atlas-control", "arch": "aarch64", "python": None,
            "version": version, "seq": seq, "min_engine": 1, "engine_version": 1,
            "notes": ["Test release"],
            "bundle": {"name": name, "url": "", "size": size,
                       "sha256": hashlib.sha256(source).hexdigest(),
                       "chunk_size": chunk,
                       "chunks": [hashlib.sha256(source[i:i + chunk]).hexdigest()
                                  for i in range(0, max(size, 1), chunk)],
                       "unpacked_size": unpacked or 64 * 1024 * 1024},
            "db": {"schema_target": 1, "compat_floor": 0},
        }
        manifest.pop("python")
        manifest.update(manifest_overrides or {})
        mbytes = json.dumps(manifest, indent=2).encode()
        from atlas_engine import verify
        sig = verify.sign_manifest(mbytes, sign_key or self.key)
        with open(os.path.join(out, "atlas-release.json"), "wb") as f:
            f.write(mbytes)
        with open(os.path.join(out, "atlas-release.json.sig"), "wb") as f:
            f.write(sig)
        return out

    def hand_off(self, pub_dir):
        from atlas_engine import fsutil
        handoff = self.paths.HANDOFF
        shutil.rmtree(handoff, ignore_errors=True)
        shutil.copytree(pub_dir, handoff)
        fsutil.atomic_write_json(self.paths.REQUEST, {"requested": time.time()})

    def install_initial(self, version="1.0.0", seq=1):
        """Put a complete release in place as `current` (as the migration would)."""
        from atlas_engine import fsutil
        tree = self.release_tree(version, seq)
        dest = os.path.join(self.paths.RELEASES, version)
        os.makedirs(self.paths.RELEASES, exist_ok=True)
        shutil.copytree(tree, dest)
        with open(os.path.join(dest, ".complete"), "w") as f:
            f.write(version)
        fsutil.atomic_symlink(os.path.join("releases", version), self.paths.CURRENT)
        fsutil.atomic_write_json(self.paths.INSTALLED, {"version": version, "seq": seq})
        for rel, dst in (("system/systemd/atlas-control.service", "/etc/systemd/system/atlas-control.service"),
                         ("system/nginx/atlas.conf", "/etc/nginx/sites-available/atlas")):
            target = self.paths.p(dst)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(os.path.join(dest, rel), target)
        return dest

    # ── engine ───────────────────────────────────────────────────────────
    def health(self, version, release):
        self.health_calls.append(version)
        from atlas_engine import health
        return health.HealthResult(version in self.healthy_versions, "fake")

    def engine(self, fault="", boot_id="boot-1"):
        from atlas_engine import engine
        return engine.Engine(runner=self.runner, health_check=self.health, boot_id=boot_id,
                             arch="aarch64", fault=fault, log=lambda m: None)

    def current_version(self):
        return os.path.basename(os.path.realpath(self.paths.CURRENT))

    def read(self, dst):
        with open(self.paths.p(dst)) as f:
            return f.read()

    def last_result(self):
        with open(self.paths.LAST_RESULT) as f:
            return json.load(f)

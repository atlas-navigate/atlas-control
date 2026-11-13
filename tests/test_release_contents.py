"""Everything a release needs is tracked by git.

tools/dev/deploy builds from `git stash create`, which leaves untracked files
out: a new module that was never `git add`ed is missing from the release and
the app fails on import only on the device."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracked():
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    return set(out.stdout.split()) if out.returncode == 0 else None


class ReleaseContentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tracked = _tracked()
        if cls.tracked is None:
            raise unittest.SkipTest("not a git checkout")

    def test_every_system_file_is_tracked(self):
        spec = json.load(open(os.path.join(REPO, "system", "files.json")))
        for entry in spec["files"]:
            self.assertIn(f"system/{entry['src']}", self.tracked, entry["src"])

    def test_every_system_file_kind_is_applied(self):
        # A kind the engine has no reload for would be written but never take
        # effect until the next boot.
        import sys
        system = os.path.join(REPO, "system")
        if system not in sys.path:
            sys.path.insert(0, system)
        from atlas_engine import sysfiles
        spec = json.load(open(os.path.join(REPO, "system", "files.json")))
        for entry in spec["files"]:
            kind = entry.get("kind", "plain")
            self.assertTrue(kind == "plain" or kind in sysfiles.KINDS_RELOAD, f"{entry['src']}: {kind}")

    def test_every_engine_module_is_tracked(self):
        engine = os.path.join(REPO, "system", "atlas_engine")
        for name in os.listdir(engine):
            if name.endswith(".py"):
                self.assertIn(f"system/atlas_engine/{name}", self.tracked, name)

    def test_every_local_module_the_app_imports_is_tracked(self):
        local = {n[:-3] for n in os.listdir(REPO) if n.endswith(".py")}
        for src in ("app.py", "ai_manager.py", "local_device.py", "perf_boost.py", "ray_prompt.py"):
            text = open(os.path.join(REPO, src), encoding="utf-8").read()
            for mod in set(re.findall(r"^\s*(?:import|from)\s+([a-z_]+)", text, re.M)) & local:
                self.assertIn(f"{mod}.py", self.tracked, f"{src} imports {mod}")


if __name__ == "__main__":
    unittest.main()


class WorktreeExportTests(unittest.TestCase):
    """build_release.py --ref WORKTREE, as tools/dev/deploy and the acceptance
    harness use it, right after something touched every file in the checkout
    (install.sh's chown -R on a developer unit changes every ctime)."""

    FILES = ("a.py", "b.txt")

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="atlas-export-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        for name in self.FILES:
            self.write(name, name)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.head = self.git("rev-parse", "HEAD")
        tools = os.path.join(REPO, "tools", "release")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import build_release
        self.build_release = build_release
        pt = mock.patch.object(build_release._common, "REPO", self.repo)
        pt.start()
        self.addCleanup(pt.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def touch_every_file(self):
        time.sleep(1.05 - time.time() % 1)     # git compares whole seconds of ctime
        for name in self.FILES:
            path = os.path.join(self.repo, name)
            os.chmod(path, os.stat(path).st_mode)          # same mode, new ctime

    def export(self):
        dest = tempfile.mkdtemp(prefix="atlas-export-out-")
        self.addCleanup(shutil.rmtree, dest, True)
        sha, _ = self.build_release.export_tree("WORKTREE", dest)
        return sha, dest

    def test_an_annotated_release_tag_exports_its_commit(self):
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", "v0.1.0", "-m", "Atlas 0.1.0")
        dest = tempfile.mkdtemp(prefix="atlas-export-out-")
        self.addCleanup(shutil.rmtree, dest, True)
        sha, commit_time = self.build_release.export_tree("v0.1.0", dest)
        self.assertEqual(sha, self.head)
        self.assertEqual(commit_time, int(self.git("show", "-s", "--format=%ct", "HEAD")))
        self.assertEqual(sorted(os.listdir(dest)), sorted(self.FILES))

    def test_a_clean_tree_exports_head(self):
        self.touch_every_file()
        sha, dest = self.export()
        self.assertEqual(sha, self.head)
        self.assertEqual(sorted(os.listdir(dest)), sorted(self.FILES))

    def test_uncommitted_changes_are_exported(self):
        self.write("a.py", "changed")
        self.touch_every_file()
        sha, dest = self.export()
        self.assertNotEqual(sha, self.head)
        self.assertEqual(open(os.path.join(dest, "a.py")).read(), "changed")
        self.assertEqual(self.git("status", "--porcelain"), "M a.py")     # the checkout is untouched


class SignReleaseTests(unittest.TestCase):
    """A release can carry the developer and the production signature."""

    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.tmp = tempfile.mkdtemp(prefix="atlas-sign-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        sys.path.insert(0, os.path.join(REPO, "system"))
        from atlas_engine import verify
        self.verify = verify
        self.keys = {}
        for name in ("dev", "prod"):
            key = Ed25519PrivateKey.generate()
            path = os.path.join(self.tmp, f"{name}.key")
            with open(path, "wb") as f:
                f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
            raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self.keys[name] = (path, verify.key_id(raw), raw)
        manifest = {"format": 1, "product": "atlas-control", "channel": "stable", "arch": "aarch64",
                    "python": "3.12", "version": "0.1.1", "seq": 1800000000, "git_sha": "0" * 40,
                    "min_engine": 1, "engine_version": 6, "notes": [],
                    "bundle": {"name": "atlas-0.1.1-aarch64.tar.xz", "url": "", "size": 1, "sha256": "0" * 64,
                               "chunk_size": 8388608, "chunks": ["0" * 64], "unpacked_size": 2},
                    "db": {"schema_target": 1, "compat_floor": 0}}
        self.manifest = os.path.join(self.tmp, "atlas-release.json")
        with open(self.manifest, "w") as f:
            json.dump(manifest, f, indent=2)

    def sign(self, name, *extra):
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "release", "sign_release.py"),
                            self.manifest, "--key", self.keys[name][0], "--yes", *extra],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.manifest + ".sig") as f:
            return [e["key_id"] for e in json.load(f)["signatures"]]

    def trusted_by(self, name):
        mbytes = open(self.manifest, "rb").read()
        sig = open(self.manifest + ".sig", "rb").read()
        return self.verify.check_signature(mbytes, sig, {self.keys[name][1]: self.keys[name][2]})

    def test_a_second_key_adds_its_signature(self):
        self.assertEqual(self.sign("dev"), [self.keys["dev"][1]])
        self.assertEqual(self.sign("prod"), [self.keys["dev"][1], self.keys["prod"][1]])
        for name in ("dev", "prod"):              # a dev unit and a retail unit both accept it
            self.assertEqual(self.trusted_by(name), self.keys[name][1])
        self.assertEqual(self.sign("prod"), [self.keys["dev"][1], self.keys["prod"][1]])   # re-signing replaces
        self.assertEqual(self.sign("prod", "--fresh"), [self.keys["prod"][1]])

"""Everything a release needs is tracked by git.

tools/dev/deploy builds from `git stash create`, which leaves untracked files
out: a new module that was never `git add`ed is missing from the release and
the app fails on import only on the device."""
import json
import os
import re
import subprocess
import unittest

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

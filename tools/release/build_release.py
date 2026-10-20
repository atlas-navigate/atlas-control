#!/usr/bin/env python3
"""Build an Atlas release bundle and its (unsigned) manifest.

    tools/release/build_release.py --version 1.2.0 [--ref v1.2.0] [--out dist]
        [--channel stable] [--notes notes.txt] [--url-base https://… | ""]

Run on an aarch64 machine with Python 3.12 and node (a Jetson works). The
bundle is the git tree at --ref (minus export-ignore paths) plus:

  static/app.compiled.js, static/build/app.<hash>.js   prebuilt frontend
  wheels/                                               every dependency, per requirements.lock
  release.json                                          version, seq, schema, engine

Output: <out>/<version>/atlas-<version>-aarch64.tar.xz and atlas-release.json.
Then sign with sign_release.py, check with verify_release.py, publish with
publish.sh. `seq` is the build's UTC epoch seconds: strictly increasing
across every build, which is what devices use to refuse downgrades.
"""
import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import _common

CHUNK = 8 * 1024 * 1024


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def module_constant(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def schema_facts(tree_dir):
    """SCHEMA_VERSION = MIGRATIONS[-1][0] — read the migration numbers statically."""
    src = open(os.path.join(tree_dir, "database.py"), encoding="utf-8").read()
    numbers = [int(n) for n in re.findall(r"^\s*\((\d+),\s*\"[a-z0-9_]+\",", src, re.M)]
    floor = module_constant(os.path.join(tree_dir, "database.py"), "COMPAT_FLOOR") or 0
    return (max(numbers) if numbers else 0), int(floor)


def export_tree(ref, dest):
    """git archive of `ref`; a dirty working tree can be captured with ref=WORKTREE."""
    if ref == "WORKTREE":
        # Refresh git's stat cache first: once every file's ctime has changed
        # (install.sh's chown -R of a developer checkout does that), the next
        # `git stash create` exits 1 without a message while it rewrites the
        # index.
        subprocess.run(["git", "-C", _common.REPO, "update-index", "-q", "--refresh"], capture_output=True)
        stash = subprocess.run(["git", "-C", _common.REPO, "stash", "create"], capture_output=True, text=True)
        if stash.returncode:
            sys.exit(f"git stash create failed: {stash.stderr.strip() or f'exit status {stash.returncode}'}")
        ref = stash.stdout.strip() or "HEAD"
    sha = subprocess.run(["git", "-C", _common.REPO, "rev-parse", ref],
                         capture_output=True, text=True, check=True).stdout.strip()
    archive = subprocess.run(["git", "-C", _common.REPO, "archive", "--format=tar", sha],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    commit_time = int(subprocess.run(["git", "-C", _common.REPO, "show", "-s", "--format=%ct", sha],
                                     capture_output=True, text=True, check=True).stdout.strip())
    return sha, commit_time


def build_frontend(tree):
    html = open(os.path.join(tree, "templates", "index.html"), encoding="utf-8").read()
    m = re.search(r'<script type="text/babel">(.*?)</script>', html, re.DOTALL)
    if not m:
        sys.exit("no JSX block in templates/index.html")
    with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False, encoding="utf-8") as t:
        t.write(m.group(1))
        jsx = t.name
    out = os.path.join(tree, "static", "app.compiled.js")
    try:
        run(["node", os.path.join(tree, "compile_jsx.js"), jsx, out], capture_output=True)
    finally:
        os.unlink(jsx)
    data = open(out, "rb").read()
    digest = hashlib.sha256(data).hexdigest()[:16]
    build = os.path.join(tree, "static", "build")
    os.makedirs(build, exist_ok=True)
    name = f"app.{digest}.js"
    shutil.copyfile(out, os.path.join(build, name))
    with open(os.path.join(build, "manifest.json"), "w") as f:
        json.dump({"app_js": f"/static/build/{name}"}, f)
    # Same mtime as the template so the app never considers it stale.
    st = os.stat(os.path.join(tree, "templates", "index.html"))
    os.utime(out, (st.st_atime, st.st_mtime))
    # Releases never compile JSX in the browser: babel (2.8 MB) stays out.
    os.unlink(os.path.join(tree, "static", "vendor", "babel.min.js"))


def fetch_wheels(tree, python):
    wheels = os.path.join(tree, "wheels")
    run([python, "-m", "pip", "download", "--disable-pip-version-check", "--no-deps",
         "--only-binary=:all:", "--require-hashes", "-r", os.path.join(tree, "requirements.lock"),
         "-d", wheels])


def make_tar(tree, bundle_path, mtime):
    """Deterministic tar: sorted entries, root-owned, normalized modes, fixed mtime."""
    def normalized(ti):
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        ti.mtime = mtime
        if ti.isdir():
            ti.mode = 0o755
        elif ti.isreg():
            ti.mode = 0o755 if ti.mode & 0o111 else 0o644
        else:
            raise SystemExit(f"refusing to package non-regular file {ti.name}")
        return ti

    total = 0
    entries = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, tree)
        if rel_dir != ".":
            entries.append(rel_dir)
        for fn in sorted(filenames):
            entries.append(os.path.normpath(os.path.join(rel_dir, fn)))
    with tarfile.open(bundle_path, "w:xz", format=tarfile.PAX_FORMAT, preset=6) as tar:
        for rel in entries:
            full = os.path.join(tree, rel)
            if os.path.islink(full):
                raise SystemExit(f"refusing to package symlink {rel}")
            ti = normalized(tar.gettarinfo(full, arcname=rel))
            if ti.isreg():
                total += ti.size
                with open(full, "rb") as f:
                    tar.addfile(ti, f)
            else:
                tar.addfile(ti)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--ref", default="HEAD", help="git ref, or WORKTREE for uncommitted changes (dev)")
    ap.add_argument("--out", default=os.path.join(_common.REPO, "dist"))
    ap.add_argument("--channel", default="stable")
    ap.add_argument("--notes", help="text file, one bullet per line")
    ap.add_argument("--url-base", default=_common.DEFAULT_URL_BASE,
                    help='https only; "" leaves the bundle URL out, so it is fetched from beside the manifest')
    ap.add_argument("--python", default=sys.executable, help="pip used to fetch wheels")
    ap.add_argument("--seq", type=int, help="override (default: now, UTC epoch seconds)")
    args = ap.parse_args()

    seq = args.seq or int(time.time())
    out_dir = os.path.join(args.out, args.version)
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-build-") as tmp:
        tree = os.path.join(tmp, "tree")
        os.makedirs(tree)
        sha, commit_time = export_tree(args.ref, tree)
        build_frontend(tree)
        fetch_wheels(tree, args.python)
        schema_target, compat_floor = schema_facts(tree)
        engine_version = module_constant(os.path.join(tree, "system", "atlas_engine", "__init__.py"),
                                         "ENGINE_VERSION")
        with open(os.path.join(tree, "release.json"), "w") as f:
            json.dump({"version": args.version, "seq": seq, "git_sha": sha, "channel": args.channel,
                       "schema_target": schema_target, "compat_floor": compat_floor,
                       "engine_version": engine_version, "built": int(time.time())}, f, indent=2)
        bundle_name = f"atlas-{args.version}-aarch64.tar.xz"
        bundle_path = os.path.join(out_dir, bundle_name)
        unpacked = make_tar(tree, bundle_path, commit_time)

    data_hash = hashlib.sha256()
    chunks = []
    with open(bundle_path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            data_hash.update(block)
            chunks.append(hashlib.sha256(block).hexdigest())
    notes = []
    if args.notes:
        notes = [l.strip().lstrip("-*• ").strip() for l in open(args.notes, encoding="utf-8") if l.strip()]
    manifest = {
        "format": 1,
        "product": "atlas-control",
        "channel": args.channel,
        "arch": "aarch64",
        "python": "3.12",
        "version": args.version,
        "seq": seq,
        "git_sha": sha,
        "min_engine": 1,
        "engine_version": engine_version,
        "notes": notes,
        "bundle": {
            "name": bundle_name,
            "url": f"{args.url_base.rstrip('/')}/v{args.version}/{bundle_name}" if args.url_base else "",
            "size": os.path.getsize(bundle_path),
            "sha256": data_hash.hexdigest(),
            "chunk_size": CHUNK,
            "chunks": chunks,
            # Headroom over the payload for filesystem overhead.
            "unpacked_size": int(unpacked * 1.1) + 1024 * 1024,
        },
        "db": {"schema_target": schema_target, "compat_floor": compat_floor},
    }
    sys.path.insert(0, os.path.join(_common.REPO, "system"))
    from atlas_engine import verify
    verify.validate_manifest(manifest)
    with open(os.path.join(out_dir, "atlas-release.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"built Atlas {args.version} (seq {seq}) from {sha[:10]}: {bundle_path} "
          f"({manifest['bundle']['size'] / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

"""Unpack a verified release bundle without trusting its contents.

The bundle's SHA-256 was checked against the signed manifest before this
runs, but extraction is still defensive: only regular files and
directories, no absolute paths or '..', no links or device nodes, no
setuid/setgid bits, bounded entry count and unpacked size, and nothing
written outside the staging directory.
"""
import os
import stat
import tarfile

MAX_ENTRIES = 50000


class ExtractError(Exception):
    pass


def _safe_name(name):
    if not name or name.startswith("/") or "\x00" in name or "\\" in name:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def extract_bundle(bundle_path, dest, unpacked_limit):
    """Extract into `dest` (must not exist). Returns (files, total_bytes)."""
    if os.path.lexists(dest):
        raise ExtractError(f"staging directory already exists: {dest}")
    os.makedirs(dest, mode=0o755)
    real_dest = os.path.realpath(dest)
    total = 0
    count = 0
    with tarfile.open(bundle_path, mode="r:xz") as tar:
        for member in tar:
            count += 1
            if count > MAX_ENTRIES:
                raise ExtractError("bundle has too many entries")
            name = _safe_name(member.name)
            if name is None:
                raise ExtractError(f"unsafe path in bundle: {member.name!r}")
            if not (member.isreg() or member.isdir()):
                raise ExtractError(f"bundle entry is not a plain file or directory: {name}")
            target = os.path.realpath(os.path.join(real_dest, name))
            if target != real_dest and not target.startswith(real_dest + os.sep):
                raise ExtractError(f"bundle entry escapes staging: {name}")
            if member.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            total += member.size
            if total > unpacked_limit:
                raise ExtractError("bundle unpacks larger than declared")
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                raise ExtractError(f"cannot read {name}")
            mode = 0o755 if member.mode & 0o111 else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            written = 0
            try:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    written += len(block)
                    if written > member.size:
                        raise ExtractError(f"{name} is larger than its header says")
                    os.write(fd, block)
            finally:
                os.close(fd)
            os.chmod(target, mode)
    # Nothing in a release is writable by anyone but root.
    for dirpath, dirnames, filenames in os.walk(dest):
        os.chmod(dirpath, 0o755)
        for f in filenames:
            fp = os.path.join(dirpath, f)
            st = os.lstat(fp)
            if not stat.S_ISREG(st.st_mode):
                raise ExtractError(f"unexpected file type after extraction: {fp}")
    return count, total

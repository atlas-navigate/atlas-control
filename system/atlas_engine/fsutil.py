"""Crash-safe file primitives. Every state change the engine relies on after
a power cut is written with write-temp → fsync → rename → fsync(dir)."""
import hashlib
import json
import os
import stat


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data, mode=0o644):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    fsync_dir(directory)


def atomic_write_json(path, obj, mode=0o644):
    atomic_write_bytes(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode(), mode)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def atomic_symlink(target, link):
    """Point `link` at `target` atomically (symlink to temp name, rename)."""
    directory = os.path.dirname(link)
    tmp = f"{link}.tmp-{os.getpid()}"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, link)
    fsync_dir(directory)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def copy_regular_file(src, dst, max_size, mode=0o600):
    """Copy a file the app handed over into root-owned space.

    Refuses symlinks, devices, FIFOs and anything over max_size, so the app
    cannot trick root into reading something else. Returns bytes copied.
    """
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"{os.path.basename(src)} is not a regular file")
        if st.st_size > max_size:
            raise ValueError(f"{os.path.basename(src)} is larger than expected")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        out = os.open(dst + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        copied = 0
        try:
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                copied += len(block)
                if copied > max_size:
                    raise ValueError(f"{os.path.basename(src)} grew while being copied")
                os.write(out, block)
            os.fsync(out)
        finally:
            os.close(out)
        os.replace(dst + ".part", dst)
        return copied
    finally:
        os.close(fd)

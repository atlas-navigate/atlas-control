"""Install some of the release kit's system files ahead of the release.

    kit_sysfile.py KIT SRC...      (SRC as in system/files.json, e.g. bin/atlas-zram)

For the install steps that need them before the release itself is installed
(swap during the dataset builds, Ollama's settings during the model pulls).
Each file is rendered exactly as the update engine renders it and written to
the same place, so the engine later finds it already correct; prints the
destination of each file it changed. Standard library only.
"""
import os
import sys


def main(argv):
    kit, srcs = argv[0], argv[1:]
    sys.path.insert(0, os.path.join(kit, "system"))
    from atlas_engine import fsutil, paths, sysfiles
    entries = {e["src"]: e for e in sysfiles.load_spec(kit).get("files", [])}
    for src in srcs:
        entry = entries.get(src)
        if entry is None:
            raise SystemExit(f"{src} is not one of the release's system files")
        dst = paths.p(entry["dst"])
        new = sysfiles.render(kit, entry)
        mode = int(entry.get("mode", "0644"), 8)
        try:
            with open(dst, "rb") as f:
                if f.read() == new and (os.stat(dst).st_mode & 0o7777) == mode:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        fsutil.atomic_write_bytes(dst, new, mode=mode)
        print(entry["dst"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

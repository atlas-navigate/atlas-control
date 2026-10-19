"""Command-line entry points behind the /usr/local/lib/atlas/bin shims."""
import json
import sys

from . import ENGINE_VERSION


def engine_main(argv):
    from . import engine
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        result = engine.Engine().run()
    elif cmd == "recover":
        result = engine.Engine().recover()
    elif cmd == "prune":
        engine.prune()
        result = "pruned"
    elif cmd == "firewall-up":
        from . import firewall
        return firewall.up()
    elif cmd == "firewall-down":
        from . import firewall
        return firewall.down()
    elif cmd == "version":
        result = str(ENGINE_VERSION)
    elif cmd == "clock":
        from . import clock
        return clock.main(argv[1:])
    elif cmd == "perf":
        from . import perf
        return perf.main(argv[1:])
    elif cmd == "verify" and len(argv) == 2:
        return verify_release_dir(argv[1])
    elif cmd == "check":
        from . import conform
        return conform.main(argv[1:])
    elif cmd == "reapply":
        try:
            engine.reapply(log=lambda msg: print(f"[atlas-engine] {msg}", flush=True))
        except engine.EngineError as exc:
            print(f"atlas-engine: {exc}", file=sys.stderr)
            return 1
        return 0
    else:
        print("usage: atlas-engine run|recover|prune|version|verify RELEASE_DIR|check [--json] [--infer]|reapply|"
              "clock boot|rtc|status|perf release|status", file=sys.stderr)
        return 2
    print(result)
    return 0 if result not in ("failed", "recovery_failed") else 1


def verify_release_dir(release_dir):
    """Check a release directory (manifest, signature, bundle) against the keys
    this unit trusts — e.g. before a golden unit copies it onto the
    manufacturing drive. Prints the version; exit status 1 if untrusted."""
    import os
    from . import engine, paths, verify
    try:
        with open(os.path.join(release_dir, engine.MANIFEST_NAME), "rb") as f:
            manifest_bytes = f.read()
        with open(os.path.join(release_dir, engine.SIG_NAME), "rb") as f:
            sig = f.read()
        keys = verify.load_keyring(paths.KEYRING, paths.DEV_KEYS, paths.dev_mode())
        manifest, kid = verify.verify_manifest(manifest_bytes, sig, keys, arch="aarch64")
        bad = verify.verify_bundle_file(os.path.join(release_dir, manifest["bundle"]["name"]), manifest["bundle"])
        if bad is not None:
            raise verify.VerifyError("bundle does not match the signed manifest")
    except (OSError, ValueError, verify.VerifyError) as exc:
        print(f"not trusted: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"version": manifest["version"], "seq": manifest["seq"], "key": kid,
                      "bundle": manifest["bundle"]["name"]}))
    return 0


def helper_main(argv):
    from . import helper
    return helper.serve_stdin()


def identity_main(argv):
    from . import identity
    cmd = argv[0] if argv else "show"
    if cmd == "ensure":
        device = identity.ensure()
        # The advert carries the id, key pin and host name: after a cloned
        # disk got a new identity, or a legacy unit its own name, it must not
        # keep announcing the old ones until the next update.
        from . import sysfiles
        sysfiles.rerender_installed(kinds=("avahi",))
    elif cmd == "show":
        device = identity.load() or {}
    elif cmd == "label-json":
        device = identity.label_payload()
    elif cmd == "label":
        print(identity.label_text())
        return 0
    elif cmd == "check":
        ok = identity.setup_code_matches()
        print("setup code verifies" if ok else "setup code does NOT verify — run: atlas-identity new-setup-code")
        return 0 if ok else 1
    elif cmd == "new-setup-code":
        print(f"New setup code: {identity.new_setup_code()}  (print a new label; paired devices are unaffected)")
        return 0
    elif cmd == "factory-hotspot":
        ssid = identity.factory_hotspot()
        print(f"{ssid} now uses the standard Wi-Fi password. It takes effect the next time Atlas starts its "
              "hotspot (Settings → Wi-Fi, or a reboot); a password set in Settings still takes precedence. "
              "Print a new label.")
        return 0
    else:
        print("usage: atlas-identity show|ensure|label|label-json|check|new-setup-code|factory-hotspot",
              file=sys.stderr)
        return 2
    print(json.dumps(device, indent=2))
    return 0

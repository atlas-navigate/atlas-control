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
    else:
        print("usage: atlas-engine run|recover|prune|version", file=sys.stderr)
        return 2
    print(result)
    return 0 if result not in ("failed", "recovery_failed") else 1


def helper_main(argv):
    from . import helper
    return helper.serve_stdin()


def identity_main(argv):
    from . import identity
    cmd = argv[0] if argv else "show"
    if cmd == "ensure":
        device = identity.ensure()
    elif cmd == "show":
        device = identity.load() or {}
    elif cmd == "label-json":
        device = identity.label_payload()
    else:
        print("usage: atlas-identity ensure|show|label-json", file=sys.stderr)
        return 2
    print(json.dumps(device, indent=2))
    return 0

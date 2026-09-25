"""Atlas's firewall (atlas-firewall.service).

Inbound: an ATLAS-INPUT chain, jumped to first from INPUT, built with
iptables-nft. (The Jetson kernel has connection tracking and REJECT only as
xtables matches/targets — native nftables `ct` and `reject` are not built —
and iptables-nft reaches them through nft_compat.) Established replies,
loopback, ICMP, the Atlas web ports, discovery, hotspot DHCP/DNS and the USB
device-mode link are accepted; SSH only on developer units; TCP is refused
with a reset and everything else dropped.

Loopback: a small native nftables table drops connections to the app's
loopback port from any account other than root, nginx and atlas.

The policy itself is data (system/firewall/policy.json → /etc/atlas/firewall.json).
"""
import json
import os
import pwd
import subprocess

from . import fsutil, paths

CHAIN = "ATLAS-INPUT"
LOCAL_TABLE = "atlas_local"


def _policy_path():
    return paths.p("etc/atlas/firewall.json")


def _run_dir():
    return paths.p("run/atlas")


def load_policy(path=None):
    with open(path or _policy_path(), encoding="utf-8") as f:
        return json.load(f)


def _ports(ports):
    return ",".join(str(int(p)) for p in ports)


def render_iptables(policy, ipv6=False, dev=None):
    dev = paths.dev_mode() if dev is None else dev
    icmp = "ipv6-icmp" if ipv6 else "icmp"
    udp = policy.get("udp6" if ipv6 else "udp", policy.get("udp", []))
    lines = ["*filter", f":{CHAIN} - [0:0]",
             f"-A {CHAIN} -i lo -j ACCEPT",
             f"-A {CHAIN} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
             f"-A {CHAIN} -m conntrack --ctstate INVALID -j DROP",
             f"-A {CHAIN} -p {icmp} -j ACCEPT"]
    ifaces = list(policy.get("trusted_interfaces", []))
    if dev:
        ifaces += policy.get("dev_trusted_interfaces", [])
    for iface in ifaces:
        lines.append(f"-A {CHAIN} -i {iface} -j ACCEPT")
    tcp = list(policy.get("tcp", []))
    if dev:
        tcp += policy.get("dev_tcp", [])
    if tcp:
        lines.append(f"-A {CHAIN} -p tcp -m multiport --dports {_ports(tcp)} -j ACCEPT")
    if udp:
        lines.append(f"-A {CHAIN} -p udp -m multiport --dports {_ports(udp)} -j ACCEPT")
    lines += [f"-A {CHAIN} -p tcp -j REJECT --reject-with tcp-reset",
              f"-A {CHAIN} -j DROP",
              f"-I INPUT 1 -j {CHAIN}",
              "COMMIT", ""]
    return "\n".join(lines)


def render_nft_local(policy):
    uids = []
    for name in policy.get("app_port_users", []):
        try:
            uids.append(str(pwd.getpwnam(name).pw_uid))
        except KeyError:
            continue
    uids = sorted(set(uids or ["0"]))
    return "\n".join([
        f"table inet {LOCAL_TABLE}",
        f"delete table inet {LOCAL_TABLE}",
        f"table inet {LOCAL_TABLE} {{",
        "    chain output {",
        "        type filter hook output priority filter; policy accept;",
        f"        oif \"lo\" tcp dport {int(policy['app_port'])} meta skuid != {{ {', '.join(uids)} }} drop",
        "    }",
        "}",
        ""])


def _write(name, text):
    os.makedirs(_run_dir(), mode=0o755, exist_ok=True)
    path = os.path.join(_run_dir(), name)
    fsutil.atomic_write_bytes(path, text.encode(), mode=0o600)
    return path


class CheckResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def check(policy_path, runner):
    """Dry-run every generated ruleset against the running kernel."""
    policy = load_policy(policy_path)
    tests = [(["iptables-nft-restore", "--test", "--noflush"], render_iptables(policy)),
             (["ip6tables-nft-restore", "--test", "--noflush"], render_iptables(policy, ipv6=True)),
             (["nft", "-c", "-f"], render_nft_local(policy))]
    for cmd, text in tests:
        if cmd[0] == "nft":
            r = runner.run(cmd + [_write("firewall-local.check", text)], timeout=60)
        else:
            r = runner.run(cmd, timeout=60, input=text)
        if r.returncode != 0:
            return CheckResult(r.returncode, r.stdout, f"{cmd[0]}: {(r.stderr or r.stdout).strip()}")
    return CheckResult(0)


def _remove_input_chain(tool):
    for _ in range(8):
        if subprocess.run([tool, "-D", "INPUT", "-j", CHAIN], capture_output=True).returncode != 0:
            break
    subprocess.run([tool, "-F", CHAIN], capture_output=True)
    subprocess.run([tool, "-X", CHAIN], capture_output=True)


def up():
    policy = load_policy()
    failures = 0
    for tool, restore, v6 in (("iptables-nft", "iptables-nft-restore", False),
                              ("ip6tables-nft", "ip6tables-nft-restore", True)):
        _remove_input_chain(tool)
        r = subprocess.run([restore, "--noflush"], input=render_iptables(policy, ipv6=v6),
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"atlas-firewall: {restore}: {r.stderr.strip()}", flush=True)
            failures += 1
    local = _write("firewall-local.nft", render_nft_local(policy))
    r = subprocess.run(["nft", "-f", local], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"atlas-firewall: nft: {r.stderr.strip()}", flush=True)
        failures += 1
    return 1 if failures else 0


def down():
    for tool in ("iptables-nft", "ip6tables-nft"):
        _remove_input_chain(tool)
    subprocess.run(["nft", "delete", "table", "inet", LOCAL_TABLE], capture_output=True)
    return 0


def enabled(runner):
    r = runner.run(["systemctl", "is-enabled", "atlas-firewall.service"], timeout=15)
    return (r.stdout or "").strip() == "enabled"

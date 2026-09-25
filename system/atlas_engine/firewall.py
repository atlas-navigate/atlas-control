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
PROBE_CHAIN = "ATLAS-PROBE"
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


def render_iptables(policy, ipv6=False, dev=None, chain=CHAIN, hook=True):
    """iptables-restore input for the inbound chain. hook=False leaves out
    the jump from INPUT, so the rules exist without affecting traffic."""
    dev = paths.dev_mode() if dev is None else dev
    icmp = "ipv6-icmp" if ipv6 else "icmp"
    udp = policy.get("udp6" if ipv6 else "udp", policy.get("udp", []))
    lines = ["*filter", f":{chain} - [0:0]",
             f"-A {chain} -i lo -j ACCEPT",
             f"-A {chain} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
             f"-A {chain} -m conntrack --ctstate INVALID -j DROP",
             f"-A {chain} -p {icmp} -j ACCEPT"]
    ifaces = list(policy.get("trusted_interfaces", []))
    if dev:
        ifaces += policy.get("dev_trusted_interfaces", [])
    for iface in ifaces:
        lines.append(f"-A {chain} -i {iface} -j ACCEPT")
    tcp = list(policy.get("tcp", []))
    if dev:
        tcp += policy.get("dev_tcp", [])
    if tcp:
        lines.append(f"-A {chain} -p tcp -m multiport --dports {_ports(tcp)} -j ACCEPT")
    if udp:
        lines.append(f"-A {chain} -p udp -m multiport --dports {_ports(udp)} -j ACCEPT")
    lines += [f"-A {chain} -p tcp -j REJECT --reject-with tcp-reset",
              f"-A {chain} -j DROP"]
    if hook:
        lines.append(f"-I INPUT 1 -j {chain}")
    lines += ["COMMIT", ""]
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


def _drop_chain(runner, tool, chain):
    runner.run([tool, "-F", chain], timeout=30)
    runner.run([tool, "-X", chain], timeout=30)


def check(policy_path, runner):
    """Prove every generated ruleset loads in the *running kernel* without
    it taking effect.

    `iptables-nft-restore --test` only parses in userspace, so it cannot
    tell whether the kernel can run a match or target. Instead the inbound
    rules are committed into a probe chain that nothing jumps to (so no
    packet ever reaches it) and removed again. The loopback table goes
    through `nft -c`, which the kernel itself validates — the check that
    caught the unsupported `ct`/`reject` on the Tegra kernel."""
    policy = load_policy(policy_path)
    for tool, v6 in (("iptables-nft", False), ("ip6tables-nft", True)):
        _drop_chain(runner, tool, PROBE_CHAIN)
        r = runner.run([f"{tool}-restore", "--noflush"], timeout=60,
                       input=render_iptables(policy, ipv6=v6, chain=PROBE_CHAIN, hook=False))
        _drop_chain(runner, tool, PROBE_CHAIN)
        if r.returncode != 0:
            return CheckResult(r.returncode, r.stdout, f"{tool}-restore: {(r.stderr or r.stdout).strip()}")
    r = runner.run(["nft", "-c", "-f", _write("firewall-local.check", render_nft_local(policy))], timeout=60)
    if r.returncode != 0:
        return CheckResult(r.returncode, r.stdout, f"nft: {(r.stderr or r.stdout).strip()}")
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

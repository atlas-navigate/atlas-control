"""Render and load the Atlas nftables table (atlas-firewall.service)."""
import os
import pwd
import subprocess

from . import fsutil, paths

def _template():
    return paths.p("etc/atlas/nftables.nft")


def _rendered():
    return paths.p("run/atlas/firewall.nft")


def render(template_path=None):
    template_path = template_path or _template()
    with open(template_path, encoding="utf-8") as f:
        text = f.read()

    def uid(name, fallback):
        try:
            return str(pwd.getpwnam(name).pw_uid)
        except KeyError:
            return str(fallback)

    ssh = "tcp dport 22 accept  # developer mode only" if paths.dev_mode() else "# SSH closed (retail)"
    return (text.replace("@SSH_RULE@", ssh)
                .replace("@WWW_UID@", uid("www-data", 33))
                .replace("@ATLAS_UID@", uid(paths.SERVICE_USER, 0)))


def check(template_path, runner):
    """nft -c on the rendered rules (used when a release installs new ones)."""
    rendered = render(template_path)
    os.makedirs(os.path.dirname(_rendered()), exist_ok=True)
    tmp = _rendered() + ".check"
    fsutil.atomic_write_bytes(tmp, rendered.encode(), mode=0o600)
    try:
        return runner.run(["nft", "-c", "-f", tmp], timeout=60)
    finally:
        os.unlink(tmp)


def up():
    fsutil.atomic_write_bytes(_rendered(), render().encode(), mode=0o600)
    return subprocess.run(["nft", "-f", _rendered()], capture_output=True, text=True).returncode


def down():
    subprocess.run(["nft", "delete", "table", "inet", "atlas_filter"], capture_output=True)
    return 0

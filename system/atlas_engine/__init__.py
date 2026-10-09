"""
Atlas's root-owned system engine: release verification, installation,
health checking, rollback and boot-time recovery, plus the privileged helper
the app talks to over a Unix socket.

Installed to /usr/local/lib/atlas/engine/<ENGINE_VERSION>/ and run with
/usr/bin/python3 -I (stdlib + the distribution's python3-cryptography only).
Nothing here may import code from a directory the Atlas service account can
write to.
"""
ENGINE_VERSION = 3

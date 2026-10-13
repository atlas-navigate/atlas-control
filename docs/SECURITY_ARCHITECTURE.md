# Atlas security architecture

Atlas is a consumer appliance: no accounts, no logins, no PINs. Whoever has
the device (and its label) owns it. Everything below exists so that owning
the device is the *only* way in, and so that one unit's secrets never unlock
another.

## Threat model

| Who | What they can reach | What must hold |
|---|---|---|
| Someone on the same Wi-Fi / LAN | ports 80/443, mDNS, the discovery beacon | nothing works until a device is paired; no secrets are disclosed before pairing |
| Someone who knows the standard Wi-Fi password, within range of an Atlas with its hotspot on | Atlas's hotspot, and through it pairing | **accepted risk** (owner decision 2026-09-26): they can join and pair with one tap. Owners are reminded to set their own Wi-Fi password, which closes this |
| A web page the owner visits | the owner's browser, which *can* reach Atlas | cross-site requests, DNS rebinding and forged forwarding headers are refused |
| Another radio on the mesh | text, node names and positions arriving over LoRa | they are data, never markup or instructions (UI and Ray) |
| A compromised app process | the `atlas` account | no sudo, no root path, no write access to code or other units' keys |
| A malicious or broken update | the download | nothing installs without a valid Ed25519 signature; a bad install rolls back |
| A second-hand buyer | a factory-reset unit | none of the previous owner's data, pairings or networks remain |

Out of scope: an attacker with a shell as root, or physical access to the
NVMe (the drive is not encrypted; see "Known limits").

## Network entry

- **nginx is the only listener** (80 → 443, HTTP/2, TLS with the unit's own
  certificate). The Flask app listens on `127.0.0.1:5000` only
  (`ATLAS_BIND`), and a native nftables rule drops loopback connections to
  :5000 from any account other than root, `www-data` and `atlas`.
- **Firewall** (`atlas-firewall.service`, `system/firewall/policy.json`):
  an iptables-nft `ATLAS-INPUT` chain accepts established traffic, loopback,
  ICMP, 80/443, mDNS, the beacon (UDP 5050), DHCP/DNS for the hotspot and
  the USB device-mode link; SSH (22) only on developer units; everything
  else is refused. The Tegra kernel has no native nftables `ct`/`reject`,
  so conntrack and REJECT go through `nft_compat`; `atlas-migrate` and the
  update engine load the rules into an unhooked probe chain first, so a
  kernel that cannot run them stops the install before anything changes.
- **udev**: the radio and GPS nodes are `GROUP=dialout MODE=0660`
  (previously world-writable).

## The edge guard (`request_guard.py`)

A WSGI middleware that wraps *everything*, including Flask-SocketIO's own
middleware (so `/socket.io/` is covered too):

- **Client address**: forwarding headers are honoured only when the peer is
  loopback **and** the request carries nginx's per-device `X-Atlas-Proxy`
  secret; otherwise every `X-Forwarded-*`/`X-Real-IP`/`X-Atlas-*` header is
  stripped. A forged `X-Forwarded-For` cannot make a LAN client look local.
- **Host allowlist**: IP literals, the unit's own `atlas-xxxxxx.local` (and avahi's conflict rename, and the legacy `atlas.local` alias),
  `atlas.local`, `localhost`, and names the owner adds. DNS rebinding
  (`evil.example` resolving to the unit) is refused.
- **Cross-site**: state-changing requests must come from an allowed origin
  (`Sec-Fetch-Site`, `Origin` or `Referer`), bodies must be JSON (multipart
  only on the upload routes), and cookie-authenticated clients must send an
  `X-Atlas-CSRF` value derived from their pairing token. A custom header
  forces a CORS preflight that Atlas never approves, so a form or
  `text/plain` POST from another site cannot act.

## Pairing (`pairing.py`, `device_identity.py`)

- A client proves ownership once: the label's **setup code** (PBKDF2-hashed
  on the unit, rate-limited per address and globally), a connection over
  **Atlas's own hotspot** (its password is on the label), or **approval on
  an already paired device**. For approvals both screens show a 4-digit code
  derived from the TLS key the phone actually saw, so a LAN man-in-the-middle
  shows a mismatch.
- The result is a random token stored only as its SHA-256. Browsers get it
  as an `HttpOnly; Secure; SameSite=Strict` cookie with `Max-Age` (clock-
  independent); the apps keep it in the Android Keystore / iOS Keychain and
  send it as a bearer token.
- Unpaired clients see only the "Connect to this Atlas" screen and the
  public identity (`/api/device/info`: name, device id, TLS key pin).
  Socket.IO connections are refused the same way; removing a device closes
  its live connection immediately.

## Identity and TLS (`/etc/atlas/identity`)

Created per unit on first install (never copied from a golden image):
device id, `Atlas-XXXXXX` name and `atlas-xxxxxx` hostname (derived from the module serial), ECDSA P-256 key and
self-signed certificate (valid 2024–2099 so a wrong clock never breaks it),
hotspot SSID (the password is the standard one shared by every unit, see
[PAIRING.md](PAIRING.md)), setup code, the nginx proxy secret.
The apps pin the certificate's SPKI SHA-256 (from the label QR code, the
mDNS/beacon advert and the pairing handshake) and warn loudly if the same
device id ever presents a different key. The module serial is recorded and
checked on every boot (`atlas-firstboot.service`), so storage or a disk image
cloned into another unit regenerates the identity before anything serves it;
pairings made with the old identity end, and the cloned hotspot profile is
removed.

## No sudo

The app runs as the unprivileged `atlas` account with `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp` and write access to only
`data/ logs/ updates/ cache/`. Root actions go through `atlas-helper`, a
socket-activated root service (`/run/atlas/helper.sock`, `root:atlas 0660`,
peer credentials checked: root and the `atlas` account only — on developer
units also members of the `atlas` group, so `tools/dev/deploy` needs no sudo)
with a fixed verb table and validated arguments:
Wi-Fi scan/connect/forget/disconnect, hotspot up/down, avahi reload, SMART
read, reboot/shutdown, update apply/cancel/status. Wi-Fi passwords are
written by root into NetworkManager keyfiles — never on a command line,
never in `sudo` logs. There are no `/etc/sudoers.d/atlas-*` files.

## Code integrity and updates

Code lives in root-owned, read-only release directories under
`/opt/atlas/releases/<version>`; the running release is `/opt/atlas/current`.
Only the root update engine installs releases, and only ones whose manifest
carries a valid Ed25519 signature from a key in the engine's keyring
(`ATLAS-MANIFEST-V1\0` domain separation, monotonic `seq` against
downgrade, per-chunk and whole-bundle SHA-256). Developer keys are honoured
only on units with `/etc/atlas/dev-mode`. See [UPDATES.md](UPDATES.md) and
[RELEASES.md](RELEASES.md).

## Untrusted content

- **Ray** (`untrusted.py`): mesh messages, alerts, node and channel names,
  knowledge-base passages and the owner's notes are neutralised (control
  characters, fake role/section markers) and fenced in blocks with a
  per-request random boundary; the system prompt states that fenced text is
  data, never instructions. The calculator (`safe_math.py`) is a strict AST
  allowlist with size, depth, exponent and factorial limits — no `eval`.
- **Ray, the chat message itself** (`untrusted.strip_claims`):
  - **Template tokens.** Chat-template tokens typed into a message (`<|im_start|>system …`) were parsed by Ollama as real role switches (41 vs 53 prompt tokens with the token broken). They are removed before the model sees the message, and from history.
  - **Instruction claims.** A message that claims to change Ray's instructions (a fake SYSTEM/ADMIN line, "ignore previous instructions", "you are now …", "new system prompt") reaches the model without the claim, the output demands and the role assignments around it, plus a note that part was removed. The real question is kept.
  - **Why removal, not a warning.** No warning prompt changed qwen2.5:3b's behaviour (1 of 12 dev probes held at best; `tools/bench/injection_variants.py`).
  - **What is not touched.** The stored message, which is what the user sees, is unchanged. Messages without a claim, including plain format requests ("reply with only the number"), pass through untouched.
  - **Limit.** This is pattern-based: a novel phrasing of an override can still get through to a 3B model.
- **Web UI**: model output and mesh text render through React (no raw HTML);
  Markdown links go through `atlasSafeHref` (http/https/mailto/relative only).
  Map popups escape every field and carry navigation targets in data
  attributes (a node name used to be able to break out of an inline
  `onclick`). The prebuilt app page is served with
  `script-src 'self'` — no inline script, no `eval` — so injected markup
  could not run script even if it got onto the page.

## Privacy

- Retention (Settings → Privacy): Ray conversations kept until deleted,
  answer sources 30 days, location history 7 days, shared phones 30 days;
  "Delete AI History" and "Delete Location History" erase with SQLite
  `secure_delete` and a truncated WAL.
- Ray sees Atlas's position rounded to about a kilometre or a place name.
- Logs never contain passwords, tokens, setup codes or precise coordinates
  (`sensitive_log.py` redacts, and coordinate logging was demoted).
- The security log (Settings → Privacy) records which paired device changed
  settings, networks, pairing or updates — never the values.
- Factory reset (typed `RESET`) removes the owner's data, documents,
  calendar, settings, paired devices, saved Wi-Fi networks, downloads and
  database copies, and returns the hotspot to the label's credentials.

## Manufacturing

Each unit gets its own identity at provisioning; the payload drive never
carries a database, hotspot password, Wi-Fi list, secret key or identity.
Retail units have setup-account passwords locked and SSH masked. See
[MANUFACTURING.md](MANUFACTURING.md).

## Known limits

- The NVMe is not encrypted: someone who removes it can read the owner's
  data. Full-disk encryption needs a TPM-backed unlock the Orin Nano devkit
  does not provide out of the box.
- Browsers show a one-time warning for the self-signed certificate (there is
  no offline way to get a publicly trusted certificate for a `.local` name);
  the apps pin the key instead.
- Anyone who can open the unit's case, or who has the label, can pair — by
  design, that is the owner.
- The hotspot password is the same on every unit, and joining the hotspot
  pairs with one tap. So until the owner sets their own Wi-Fi password, anyone
  who knows the standard one can take control of an Atlas whose hotspot is on,
  from within Wi-Fi range. Universal default passwords are restricted in some
  markets (UK PSTI, EU EN 18031, California SB-327).
- Typing the setup code (rather than scanning the label's QR code) trusts
  the first TLS key the phone sees; an attacker actively intercepting the
  LAN at that moment could capture the code. Scanning the QR code (which
  carries the key pin) or approving from a paired device (match code) closes
  that gap.

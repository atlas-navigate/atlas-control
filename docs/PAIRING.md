# Connecting phones and browsers to Atlas

There are no accounts or passwords to remember. A phone or browser is paired
once; after that it simply works — on Atlas's own Wi-Fi, on a home network,
after Atlas changes address, and after updates.

## The first device: the Atlas button

A new (or factory-reset) Atlas has no primary user: it is **unclaimed**, and
its button glows dim white. The first device to pair becomes the **primary
user**:

1. Join Atlas's Wi-Fi. Its name is Atlas's own address, e.g.
   `atlas-k7m2qx9wp.local`, and the password is the code in that name:
   `k7m2qx9wp`. No label ships with Atlas; nothing else is needed. (Or use a
   network Atlas is already on.)
2. Open the Atlas app, or `https://atlas-k7m2qx9wp.local` in a browser, and
   tap **Connect with the Atlas button**.
3. **Press the button on Atlas once.** Its light turns one of six colours
   (green, blue, purple, yellow, cyan, white — never red or amber, which mean
   reset).
4. **Pick that colour** on the screen. One pick per press: a wrong pick ends
   the attempt, and the owner presses the button again.

Only someone next to Atlas can see the light, so the colour is the proof of
physical access. Details: the colour is chosen with `secrets.choice` when the
button is pressed and shown only on the LED; the screen's six swatches are
shuffled. A press binds to the one device waiting; if two devices wait at
once, both attempts end ("Two devices tried to connect") and the light blinks
amber. A press with nobody waiting opens a 60 s window for the next device to
ask. An attempt lasts two minutes. Atlas keeps attempts in memory only.

**Backup**, accepted only while Atlas is unclaimed (a broken button, or
colours that are hard to tell apart): a **setup code**. Every unit has its
own; no label ships with Atlas, so it comes from the factory's record of the
unit (Atlas support). The Atlas app can also scan that record's QR code,
which carries the unit's id, setup code, TLS key pin and Wi-Fi details.
The code is three groups of capitals and digits (`H7QD-M3XR-9KTW`), unlike
the Wi-Fi password (nine lowercase characters). Dashes and case don't
matter. Five wrong codes from one address, or twenty in total, pause
attempts for ten minutes; the count survives restarts.

Being on Atlas's Wi-Fi is **not** a proof any more: anyone who sees its name
knows its starting password (below). The old one-tap hotspot pairing answers
`410 update_app`.

**Right after the first device pairs**, Atlas asks it to set its own Wi-Fi
password ("Set your own Wi-Fi password", with Skip for now). It asks once;
the reminder on Settings → Wi-Fi stays until the password changes. A factory
reset asks the next owner again.

## Later devices: the primary user allows them

Once Atlas has a primary user, the setup code and the button no longer pair
anything. A new device taps **Ask to connect**; the primary user's phone or
browser shows "Allow new device?" with a 4-digit code, and the button blinks
blue while a request waits. Allow only if the code matches the one on the new
device's screen. Only the primary user sees these requests and can answer
them (others get `403 owner_required`).

Settings → Connected devices lists every paired device and marks the primary
user. The primary user can remove any device (its connection is closed at
once) and can hand the role to another device (**Make primary**); it can't
remove itself while other devices remain. Other devices can remove only
themselves.

Lost the primary user's phone? Hold the Atlas button for 15 seconds (below).
That erases Atlas's data; the next device pairs with the button again.

## The button: lights and the 15-second reset

The button is a DFRobot DFR0991 (I2C `0x2a`) on header pins 27/28 (bus 1,
100 kHz); see docs/MANUFACTURING.md for the wiring. Atlas runs it from the app
process (`status_button.py`) and polls it 20 times a second.

| Light | Meaning |
|---|---|
| dim white | not set up yet: press, then pick the colour on the screen |
| a colour (steady) | pick this colour on the device that is connecting |
| white blinking | pressed with nobody waiting: open Atlas on your device now |
| green blinks | paired |
| blue blinking | a device is waiting for the primary user to allow it |
| amber blinking | the button is being held: release before 15 s to cancel |
| red (steady, 2 s) | held for 15 s: the factory reset begins |
| green (steady) | the factory reset has started and is running |
| red blinks | a reset was refused (an update is installing) |
| off | set up and idle |

Holding the button 15 seconds runs the same **factory reset** as Settings →
Factory reset (below), recorded in the security log as by "Atlas button".
Releasing it earlier cancels. A reset is refused while an update is
installing. Afterwards the hotspot comes back with its factory name and
password (the code in that name) and the light returns to dim white.

Settings → Connected devices shows whether the button works. "Plugged into the
wrong pins" means pins 3/5 (bus 7, 400 kHz), where this module stops
responding; "not responding" needs it unplugged and plugged back in (a
software reset can't recover it). The hold resets every Atlas for real,
developer units included.

## Every Atlas has its own setup code

There is no shared or default code. Each unit makes its own at provisioning
(`atlas-identity`, from the operating system's secure random source). It is
12 characters from a 29-letter alphabet, about 58 bits, so it can't be guessed
within the rate limit. It is stored only on that unit: root-only, with a
PBKDF2 hash that pairing checks, and in the factory's record of the unit
(nothing is printed for the owner).

The same run gives the unit its own:
- device id and TLS key (the apps pin it);
- `Atlas-XXXXXXXXX` / `atlas-xxxxxxxxx` name. Units made since 2026-11 get 9
  characters derived from the Jetson module's serial by a keyed permutation
  (a bijection over 29^9 values), so two units can never get the same name.
  The name is not secret; anyone with the code can recover the serial from
  it, and the serial is printed on the module anyway. Older units keep their
  6-character name;
- Wi-Fi (hotspot) name: the `.local` address, e.g. `atlas-k7m2qx9wp.local`;
- proxy secret.

## The Wi-Fi password is the code in the Wi-Fi name

Atlas's own Wi-Fi (hotspot) is named after its address and starts with the
code in that name as its password (owner decision, 2026-11-12; no label ships
with Atlas): Wi-Fi `atlas-k7m2qx9wp.local`, password `k7m2qx9wp`. Each unit's
is different, but it is **public**: anyone who sees the Wi-Fi name knows it.
Joining grants nothing: a device still needs the button (while unclaimed) or
the primary user's Allow.

Atlas asks the first owner to set their own right after pairing, and Settings
→ Wi-Fi → Hotspot reminds them until they do. A factory reset returns the
hotspot to the code password (and the prompt to the next owner).

- **Older units.** Units made before 2026-11-12 with a 9-character code move
  from the earlier shared password to their code on their next update.
  Developer units with 4- or 6-character codes (Wi-Fi needs at least 8) keep
  the shared password (`FACTORY_HOTSPOT_PASSWORD` in
  `system/atlas_engine/identity.py`). A password the owner set is never
  changed.
- **Older names.** Software updates move an older unit's factory Wi-Fi name
  (`Atlas-XXXXXX`) to its `.local` address; a name the owner chose is kept.
- On the hotspot, Atlas's DNS also answers that name (`10.42.0.1`), for phones
  that don't use mDNS.
- `sudo atlas-identity factory-hotspot` gives a unit its factory password.

Regulations to check before selling:
- UK PSTI (2024);
- EU radio-equipment cybersecurity (EN 18031, 2025);
- California SB-327.
They restrict default passwords on connected consumer products, including
ones derived from information the product makes public, such as its network
name.

The setup code is persistent. Software updates, reboots and factory resets
keep it, so the factory's record works for the life of the unit.
`atlas-identity new-setup-code` replaces it; paired devices are unaffected.

Mass production never duplicates it:
- The payload drive carries no identity.
- Every boot checks that the identity belongs to this module (its serial
  number, or the drive's if the module has none). A unit made from a cloned
  disk image gets a fresh identity — code, key, name — before anything serves
  it.
- Pairings made with the old identity end (they were made with a different
  unit).
- The old unit's hotspot profile is removed and rebuilt from the new identity.

## What keeps a pairing, and what ends it

Pairing is done once. The same token, device id and TLS key carry on through:

- Atlas software updates, including an update that was rolled back and a
  power cut in the middle of one (`/etc/atlas/identity` and `data/` are
  outside the release; the update engine never touches them);
- reboots, a new IP address, network or Wi-Fi changes, and updates of the
  apps themselves.

A paired phone or browser has to pair again only when:

| Cause | What happens |
|---|---|
| Removed in Settings → Connected devices | its connection closes; the app shows "Connect this phone again" |
| Factory reset | all pairings are cleared; the button pairs the next owner |
| A new identity (cloned storage or a replaced module) | the app shows a security warning: the key changed |

Browsers keep an HttpOnly cookie for ten years (`Max-Age`, so a wrong clock
can't expire it early). The apps keep the token in the Android Keystore or the
iOS Keychain, and detect removal within seconds of connecting.

## Finding Atlas without an address

The apps remember Atlas by its device id and TLS key, never by address. They
find it through:

1. mDNS / Bonjour: `_atlas._tcp` with the device id and key pin in TXT records;
2. the LAN beacon (UDP 5050), which answers with the same identity;
3. Atlas's hotspot gateway, which is always `10.42.0.1`;
4. on request, a scan of the local network.

Whatever address answers must present the pinned key; if the same device id
ever shows a different key, the app stops and warns instead of connecting.

Browsers use `https://atlas-xxxxxxxxx.local` (Atlas's Wi-Fi name). Each browser
warns once about Atlas's self-signed certificate; the pairing cookie then
lasts until the device is removed or Atlas is reset.

## Every Atlas has its own .local name

Each unit answers at its own address on the local network, for example
`atlas-k7m2qx9wp.local`. The code is the one in the unit's name
(Atlas-K7M2QX9WP), Wi-Fi name and starting Wi-Fi password, so two Atlases on
one network never share an address.

- **Where it comes from.**
  - Units made since 2026-11 get 9 characters derived from the module serial
    by a keyed permutation (see "Every Atlas has its own setup code" above),
    from an alphabet without 0/O, 1/I/L or 5/S. A reflash of the same module
    gets the same name back; a disk cloned into another unit gets a
    different one.
  - Units made before that keep their six- or four-character code (derived
    one-way from the serial, or older).
- **Older units** that answered to `atlas.local` move to `atlas-<their
  code>.local` on their first update. They keep their key, so paired apps
  are unaffected; the certificate is re-issued with the new name. They also
  keep answering `atlas.local` as an alias, but only while no other device
  on the network claims it.
- **Where it is published.** In the DNS-SD advert (TXT `host=`), the LAN
  beacon, `/api/device/info` (`hostname`, and `fqdn` for the name that
  resolves right now), `/api/device/local`, and Settings → Local Device
  Address. The apps show it after pairing and when Atlas can't be found.
- **Name conflicts.** If another device takes the name, avahi answers at
  `atlas-7kq3pm-2.local` meanwhile. Atlas keeps accepting that name and
  retries its own name after 30 s, 2 min and 10 min, then hourly.
- **Names don't grant trust.** The apps confirm every address by device id
  and pinned key.

## Factory reset and resale

Settings → Factory reset prepares Atlas for a new owner.

| Erased | Kept (the unit's hardware identity) |
|---|---|
| every pairing, including the primary user (all phones and browsers must pair again) | name, `.local` hostname and device id |
| messages, Ray conversations, owner documents, location history, trackers, routes, waypoints, schedules, calendar, alerts | TLS key (so the apps' pinned key still matches) |
| saved Wi-Fi networks, Bluetooth pairings, network hints, an owner-set hotspot name/password (back to the factory ones) | setup code; factory Wi-Fi name and password (the code in the name) |
| the security log, downloads, pre-migration database copies | installed software, maps, routing data, built-in reference |
| Atlas's logs and caches, the system journal, Ray's in-memory conversation state | |
| every setting the owner changed, Ray's included (back to the defaults every new Atlas starts with) | |

The previous owner keeps nothing that lets them back in: their phones' and
browsers' tokens are revoked. Holding the button 15 s does the same reset.
The next owner pairs with the button (or a setup code from support); either
is the intended physical-possession proof. Support can issue a new setup code
(`atlas-identity new-setup-code`) if a unit's code must change hands without
the unit.

## Shared phone location (tracker)

`/tracker` pairs through the same approval flow (kind "Shared phone") and
sends its position at most every 10 s and 15 m, with a 30 s heartbeat.
Shared-phone positions are kept 30 days by default (Settings → Privacy).

## Support

On the unit (root): `atlas-identity label` prints the unit's facts, each
clearly named (never the Wi-Fi password); `atlas-identity check` confirms the
stored setup code verifies; `atlas-identity new-setup-code` issues a new one
(paired devices keep working).

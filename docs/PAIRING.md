# Connecting phones and browsers to Atlas

There are no accounts or passwords to remember. A phone or browser is paired
once; after that it simply works — on Atlas's own Wi-Fi, on a home network,
after Atlas changes address, and after updates.

## Pairing a device (the owner)

Any one of these proves the device belongs to the owner:

- **Scan the label** with the Atlas app. The QR code carries the unit's id,
  setup code, TLS key pin and hotspot details, so the app joins Atlas's
  Wi-Fi if needed and pairs without any typing.
- **Type the setup code** printed on the label — three groups of capitals
  and digits (`H7QD-M3XR-9KTW`); the Wi-Fi password looks different (four
  lowercase groups) so the two can't be confused. Dashes and case don't
  matter. Five wrong codes from one address, or twenty in total, pause
  attempts for ten minutes.
- **Join Atlas's own Wi-Fi** (its name and password are on the label) and
  tap Connect — being on that network already proves physical access.
- **Allow it from a paired device**: the new device asks, and every paired
  device shows "Allow new device?" with a 4-digit code. Allow only if the
  code matches the one on the new device's screen.

Settings → Connected devices lists every paired device; remove any you don't
recognise (its connection is closed at once). Factory reset removes them all.

## Every Atlas has its own setup code

There is no shared or default code. Each unit makes its own at provisioning
(`atlas-identity`, from the operating system's secure random source). It is
12 characters from a 29-letter alphabet, about 58 bits, so it can't be guessed
within the rate limit. It is stored only on that unit: root-only, with a
PBKDF2 hash that pairing checks. It is printed on that unit's label.

The same run gives the unit its own:
- device id and TLS key (the apps pin it);
- `atlas-xxxx` name;
- hotspot password;
- proxy secret.

It is persistent. Software updates, reboots and factory resets keep it, so the
label works for the life of the unit. `atlas-identity new-setup-code` replaces
it if a label is lost; paired devices are unaffected.

Mass production never duplicates it:
- The payload drive carries no identity.
- Every boot checks that the identity belongs to this module (its serial
  number, or the drive's if the module has none). A unit made from a cloned
  disk image gets a fresh identity — code, key, hotspot — before anything
  serves it.
- Pairings made with the old identity end (they were made with a different
  unit).
- The old unit's hotspot profile is removed and rebuilt from the new label.

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
| Factory reset | all pairings are cleared; the label still works |
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

Browsers use `https://atlas-xxxx.local` (printed on the label). Each browser
warns once about Atlas's self-signed certificate; the pairing cookie then
lasts until the device is removed or Atlas is reset.

## Shared phone location (tracker)

`/tracker` pairs through the same approval flow (kind "Shared phone") and
sends its position at most every 10 s and 15 m, with a 30 s heartbeat.
Shared-phone positions are kept 30 days by default (Settings → Privacy).

## Support

On the unit (root): `atlas-identity label` prints the label's facts, each
clearly named; `atlas-identity check` confirms the stored setup code verifies;
`atlas-identity new-setup-code` issues a new one (for a lost or damaged label —
paired devices keep working). Units made before the Wi-Fi password format
changed keep their original password.

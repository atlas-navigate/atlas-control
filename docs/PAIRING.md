# Connecting phones and browsers to Atlas

There are no accounts or passwords to remember. A phone or browser is paired
once; after that it simply works — on Atlas's own Wi-Fi, on a home network,
after Atlas changes address, and after updates.

## Pairing a device (the owner)

Any one of these proves the device belongs to the owner:

- **Scan the label** with the Atlas app. The QR code carries the unit's id,
  setup code, TLS key pin and hotspot details, so the app joins Atlas's
  Wi-Fi if needed and pairs without any typing.
- **Type the setup code** printed on the label (dashes and case don't
  matter). Five wrong codes from one address, or twenty in total, pause
  attempts for ten minutes.
- **Join Atlas's own Wi-Fi** (its name and password are on the label) and
  tap Connect — being on that network already proves physical access.
- **Allow it from a paired device**: the new device asks, and every paired
  device shows "Allow new device?" with a 4-digit code. Allow only if the
  code matches the one on the new device's screen.

Settings → Connected devices lists every paired device; remove any you don't
recognise (its connection is closed at once). Factory reset removes them all.

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

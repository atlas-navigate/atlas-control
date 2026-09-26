# Atlas mobile apps

Native Android (`android/`, Kotlin + Compose) and iOS (`ios/`, SwiftUI) clients.
Both show the Atlas web UI in a web view and add what a browser can't do:
finding Atlas, pairing with it once, pinning its security key, reconnecting by
itself after network changes, reboots and updates, and notifications.

## How a phone connects

1. **Find** — the app lists Atlas devices it can see, by name:
   - DNS-SD / Bonjour `_atlas._tcp` (TXT records `id`, `fp`, `api`)
   - the Atlas LAN beacon, UDP 5050 (broadcast; beacon v2 carries `id` and `fp`)
   - Atlas's own hotspot (gateway `10.42.0.1`)
   - on request only: an HTTPS probe of the phone's own /24 for `/api/device/info`
   Typing an address is an *advanced* fallback.
2. **Pair once** (no account, username, password or PIN):
   - on Atlas's own Wi-Fi: one tap;
   - scan the QR code on the Atlas label — it contains the device id, setup
     code, TLS key pin and hotspot credentials, so the very first connection
     is already trusted; the app joins Atlas's Wi-Fi first if needed;
   - type the label's setup code; or
   - ask someone already using this Atlas to tap **Allow** (both screens show
     the same 4-digit code, computed from the TLS key each side saw).
3. **Stay connected** — the app stores the device id, the SHA-256 pin of the
   device's public key and a pairing token (Android Keystore / iOS Keychain,
   never backed up). Connections — native and web view — succeed only with
   that key; the address doesn't matter. After an IP change, a network switch,
   a reboot or an update the app finds the same device again by id. If the
   device answers with a different key the app shows a security warning and
   never connects silently.
4. **Updates** — while Atlas installs an update the app shows "Atlas is
   installing an update and restarting." (from nginx's `/update-status.json`)
   and "Update complete." when Atlas is back.

## When a phone has to pair again

Pairing is done once. It keeps working — with no action from the owner —
after Atlas software updates (including one that was rolled back), reboots,
power cuts, a new IP address or network, and updates of the app itself: the
token, the device id and Atlas's TLS key never change for any of these.

A phone pairs again only when Atlas itself ended the pairing:

| What happened | What the app shows |
|---|---|
| The phone was removed in Settings → Connected devices | "Connect this phone again" — same Atlas, same key; one tap to start |
| Atlas was factory reset (paired devices are cleared; the label stays valid) | the same |
| Atlas answers with a different key (storage cloned into another unit, or someone impersonating it) | "Security warning" — never connects silently |

The app notices removal within seconds of connecting (`/api/pair/status`
with its token), rather than leaving the web view on a pairing page.

## Compatibility

- `/api/device/info` carries `"pairing": 1`. An app that finds a higher
  number asks to be updated; an Atlas still running software from before
  pairing (it answers `/api/device` but has no `/api/device/info`) is
  explained ("Update Atlas first"), never connected to insecurely.
- The setup-code field formats as `XXXX-XXXX-XXXX` while typing and says
  it is not the Wi-Fi password (which is four lowercase groups); pasting the
  Wi-Fi password shows that hint.
- Approval requests wait out a busy or restarting Atlas for the request's
  whole 3-minute life instead of failing early.

## Security properties

- HTTPS only; no cleartext; no user-installed CAs (Android); no ATS arbitrary
  loads (iOS; only `NSAllowsLocalNetworking`).
- TLS trust = public-key pin captured at pairing (label QR, hotspot, or an
  owner-approved request). No trust-all managers or hostname bypasses remain.
- Web view: pinned certificate, no file/content access or mixed content,
  permissions only for Atlas's own origin, other links open outside the app.
- Phone location sharing (iOS) is opt-in, foreground-only and throttled
  (≥30 s and ≥50 m between reports).

## Building and testing

Neither app can be built on the Jetson (no JDK/Gradle/Xcode there).
- `tests/test_mobile_contract.py` (run with the server's test suite) checks
  both apps' sources against the server: endpoints, pairing version, label
  QR format, match-code formula, approval answers and screens.
- Android: `./gradlew test` runs `PairingLogicTest` (match code vectors from
  the server, QR parsing, setup-code formatting, approval answers).
- Before a release, on a real phone of each kind: pair by label QR, by typed
  code and by Allow; install an Atlas update and watch the app reconnect by
  itself; remove the phone in Settings → Connected devices and check it shows
  "Connect this phone again".
- Android: open `android/` in Android Studio (AGP 9, Kotlin 2.3, minSdk 29).
- iOS: `cd ios && xcodegen` then open `AtlasMobile.xcodeproj` (iOS 17+).
  The HotspotConfiguration, Wi-Fi-info and multicast entitlements must be
  enabled for the App ID.

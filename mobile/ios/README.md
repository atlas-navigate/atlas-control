# Atlas Mobile — iOS

SwiftUI / WKWebView companion app that mirrors the Android client at
`mobile/android`. Uses the same JS bridge contract (`window.AtlasAndroid`)
so the React UI served by the Atlas Jetson works identically on both
platforms.

## Architecture

```
AtlasMobile/
├── AtlasMobileApp.swift         # @main App entry — owns AppViewModel + SetupViewModel
├── MainView.swift               # Root router (.idle/.checking/.connected/.failed)
├── Theme.swift                  # AtlasTheme.* color palette + atlasCard modifier
│
├── Network/
│   ├── AtlasModels.swift        # Codable mirrors of backend JSON
│   ├── AtlasApiClient.swift     # URLSession (probe + full) + AtlasApi enum
│   ├── MdnsResolver.swift       # Direct UDP mDNS — bypasses iOS .local cache
│   ├── AtlasDiscovery.swift     # NWBrowser + multi-subnet HTTP scan
│   └── NetworkMonitor.swift     # NWPathMonitor + NEHotspotNetwork SSID
│
├── Setup/
│   ├── AtlasBleScanner.swift    # CoreBluetooth pair (service 7d2ea28a-...001)
│   ├── SetupViewModel.swift     # Welcome → hotspot → pairing → LAN provision
│   └── SetupWizardScreen.swift  # SwiftUI port of the Compose wizard
│
├── UI/
│   ├── AppViewModel.swift       # State machine + LAN-switch loop
│   ├── ConnectingView.swift     # Pulsing "Searching for Atlas…" screen
│   ├── ErrorView.swift          # Failed-connection screen w/ manual IP
│   └── AtlasWebScreen.swift     # WKWebView wrapper + AtlasAndroid JS bridge
│
├── Notifications/
│   └── AtlasNotificationManager.swift   # UNUserNotificationCenter wrapper
│
└── Services/
    └── AtlasPhoneTracker.swift  # Optional: posts phone GPS to /api/tracker/checkin
```

## Generate the Xcode project

```bash
brew install xcodegen           # one-time
cd mobile/ios
xcodegen generate
open AtlasMobile.xcodeproj
```

Pick a development team (Signing & Capabilities) and run on a real iPhone
or the iOS Simulator (iOS 17+).  Bluetooth pairing requires a real device;
LAN/hotspot reachability and the embedded web app work in the Simulator if
you point Atlas at a discoverable subnet.

## Required entitlements

The hand-written `AtlasMobile/AtlasMobile.entitlements` declares two
NetworkExtension entitlements:

* `com.apple.developer.networking.wifi-info` — read the live SSID via
  `NEHotspotNetwork.fetchCurrent` so the app can prefer hotspot URLs when
  the phone is on `atlas_navigate`.
* `com.apple.developer.networking.HotspotConfiguration` — programmatically
  join the Atlas hotspot from the setup wizard via
  `NEHotspotConfigurationManager.apply(...)`.

Both require capabilities issued to a paid Apple Developer account.  If
you're building with a free Apple ID (Simulator or sideload), comment
out both keys in `AtlasMobile.entitlements` — the app still builds and
the only loss is the in-app "Join Atlas Hotspot" shortcut and live SSID
detection (the user can still join Wi-Fi from Settings).

## Required usage strings (Info.plist)

The app prompts the user for:

* Bluetooth (`NSBluetoothAlwaysUsageDescription`) — pair with the Jetson.
* Local Network (`NSLocalNetworkUsageDescription`) — reach Atlas at
  `atlas.local` / hotspot IPs / LAN IPs.
* Bonjour services (`NSBonjourServices`) — `_https._tcp` + `_http._tcp`
  let `NWBrowser` resolve Atlas without a separate prompt.
* Location (`NSLocationWhenInUseUsageDescription`) — required for
  `NEHotspotNetwork.fetchCurrent` (iOS 14+) and the optional phone
  tracker.

`NSAppTransportSecurity` allows arbitrary loads (Atlas presents a
self-signed cert on `atlas.local` / hotspot IPs and serves cleartext on
port 5000 inside the LAN).

## How it talks to Atlas

Identical contract to the Android port:

1. Setup wizard probes `gateway → 10.42.0.1 → atlas.local → mDNS → subnet
   scan` to find Atlas; pulls `/api/mobile/bootstrap` to enumerate every
   reachable URL.
2. URLs are persisted under `hotspot_urls` and `lan_urls` in
   `UserDefaults`.
3. WKWebView loads `<baseUrl>/mobile`.  The app injects
   `window.AtlasAndroid` (the React app's existing bridge) so the same
   web codebase that targets Android works here unchanged.  iOS
   notifications, manual IP fallback, and the LAN-switch loop are wired
   into the JS bridge methods `lanSwitching`, `lanSwitchedTo`,
   `showSetupWizard`, and `reconnect`.

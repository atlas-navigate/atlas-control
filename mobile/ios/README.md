# Atlas for iOS

SwiftUI app; see `../README.md` for how discovery, pairing, key pinning and
reconnection work (identical on Android).

- `Network/AtlasTrust.swift` — SPKI pin of the server key (EC P-256) and the
  URLSession delegate that enforces it.
- `Network/DeviceStore.swift` — paired device; token in the Keychain
  (`AfterFirstUnlockThisDeviceOnly`).
- `Network/AtlasDiscovery.swift`, `AtlasBeacon.swift` — Bonjour `_atlas._tcp`,
  UDP beacon, hotspot gateway.
- `UI/ConnectionModel.swift` — connection state machine (find by device id,
  update-aware reconnect, security warning on key change).
- `UI/OnboardingView.swift`, `QrScannerView.swift` — pairing.
- `UI/AtlasWebScreen.swift` — WKWebView with the same pin, pairing cookie and
  the `window.AtlasIOS` bridge.

Generate the project with `xcodegen` and build with Xcode 15+ for iOS 17+.

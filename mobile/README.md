# Atlas Mobile

Native mobile clients for Atlas Control.

## Transport model

1. Mobile app discovers the Jetson over Bluetooth Low Energy.
2. The Jetson exposes a compact bootstrap manifest over BLE.
3. The app uses the manifest to join the Atlas hotspot and connect to the local Atlas HTTP API.
4. Meshtastic, GPS, navigation, and AI remain on the Jetson.

## First-run walkthrough

Both native apps now include a guided onboarding flow:

1. Welcome
2. Permissions
3. Bluetooth scan for the Jetson
4. Hotspot handoff
5. Local API verification
6. Dashboard handoff

## BLE contract

- Service UUID: `7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0001`
- Manifest characteristic UUID: `7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0002`

The characteristic returns UTF-8 JSON with:

- `device`
- `api.preferredBaseUrl`
- `api.baseUrls`
- `hotspot`
- `capabilities`
- `bluetooth`

## Projects

- `android/`: Kotlin + Jetpack Compose
- `ios/`: SwiftUI app generated from `project.yml` with XcodeGen

## Current backend endpoint

For local debugging without BLE, the same payload is also exposed at:

- `GET /api/mobile/bootstrap`

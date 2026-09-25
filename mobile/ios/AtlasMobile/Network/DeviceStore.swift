import Foundation
import Security

/// The one Atlas this phone is paired with.
struct PairedDevice: Equatable {
    var deviceId: String
    var name: String
    var pin: String
    var token: String
    var lastUrl: String?
}

/// Device id, name, key pin and last address in UserDefaults; the pairing
/// token in the Keychain, this-device-only (never synced or backed up).
enum DeviceStore {
    private static let defaults = UserDefaults.standard
    private static let service = "com.atlascontrol.mobile.pairing"

    static func load() -> PairedDevice? {
        guard let id = defaults.string(forKey: "atlas.device_id"),
              let pin = defaults.string(forKey: "atlas.pin"),
              let token = readToken() else { return nil }
        return PairedDevice(deviceId: id, name: defaults.string(forKey: "atlas.name") ?? "Atlas",
                            pin: pin, token: token, lastUrl: defaults.string(forKey: "atlas.last_url"))
    }

    static func save(_ d: PairedDevice) {
        defaults.set(d.deviceId, forKey: "atlas.device_id")
        defaults.set(d.name, forKey: "atlas.name")
        defaults.set(d.pin, forKey: "atlas.pin")
        defaults.set(d.lastUrl, forKey: "atlas.last_url")
        writeToken(d.token)
    }

    static func rememberUrl(_ url: String) { defaults.set(url, forKey: "atlas.last_url") }

    static func forget() {
        for k in ["atlas.device_id", "atlas.name", "atlas.pin", "atlas.last_url", "atlas.share_location"] {
            defaults.removeObject(forKey: k)
        }
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrService: service] as CFDictionary)
    }

    private static func readToken() -> String? {
        var out: CFTypeRef?
        let q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                  kSecAttrAccount: "token", kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func writeToken(_ token: String) {
        let base: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                     kSecAttrAccount: "token"]
        SecItemDelete(base as CFDictionary)
        var add = base
        add[kSecValueData] = Data(token.utf8)
        add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}

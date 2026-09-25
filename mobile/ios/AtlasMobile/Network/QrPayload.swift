import Foundation

/// What the QR code on an Atlas label contains:
///
///   ATLAS1:<device id>:<setup code>:<TLS key pin>:<hotspot SSID>:<hotspot password>
///
/// The pin makes the first connection trusted, the setup code pairs, and the
/// hotspot credentials let the app join Atlas's own Wi-Fi when needed. A
/// standard Wi-Fi QR ("WIFI:T:WPA;S:…;P:…;;") is understood too.
struct AtlasLabel: Equatable {
    var deviceId: String?
    var setupCode: String?
    var pin: String?
    var ssid: String?
    var password: String?
}

enum QrPayload {
    static func parse(_ text: String) -> AtlasLabel? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("ATLAS1:") {
            // The password is last and may itself contain ':'.
            let parts = t.dropFirst("ATLAS1:".count).split(separator: ":", maxSplits: 4, omittingEmptySubsequences: false)
                .map(String.init)
            guard parts.count >= 3 else { return nil }
            func field(_ i: Int) -> String? { i < parts.count && !parts[i].isEmpty ? parts[i] : nil }
            return AtlasLabel(deviceId: field(0), setupCode: field(1), pin: field(2), ssid: field(3), password: field(4))
        }
        if t.hasPrefix("WIFI:") {
            var fields: [String: String] = [:]
            var chars = Array(t.dropFirst(5))
            var i = 0
            while i < chars.count {
                guard let colon = chars[i...].firstIndex(of: ":") else { break }
                let key = String(chars[i..<colon])
                var value = ""
                var j = colon + 1
                while j < chars.count && chars[j] != ";" {
                    if chars[j] == "\\" && j + 1 < chars.count { value.append(chars[j + 1]); j += 2 } else { value.append(chars[j]); j += 1 }
                }
                fields[key] = value
                i = j + 1
            }
            chars.removeAll()
            guard let ssid = fields["S"] else { return nil }
            return AtlasLabel(deviceId: nil, setupCode: nil, pin: nil, ssid: ssid, password: fields["P"])
        }
        return nil
    }
}

import Combine
import Foundation

/// Atlas addresses as people type them and as Atlas advertises them — the
/// Swift counterpart of the Android AtlasAddress.
///
/// Every Atlas has its own .local name (atlas-7kq3pm.local), advertised in
/// Bonjour (TXT host=), its LAN beacon and /api/device/info. iOS resolves
/// .local names itself, so the name is also a way to reach a paired Atlas
/// after its IP changes; the connection is still trusted by the pinned key,
/// never by the name.
enum AtlasAddress {
    private static func isLabel(_ s: String) -> Bool {
        s.range(of: "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", options: .regularExpression) != nil
    }

    /// "atlas-7kq3pm" or "Atlas-7KQ3PM.local" → "atlas-7kq3pm.local"; nil if not a name.
    static func fqdn(_ raw: String?) -> String? {
        guard var h = raw?.trimmingCharacters(in: .whitespaces).lowercased(), !h.isEmpty else { return nil }
        while h.hasSuffix(".") { h.removeLast() }
        if h.hasSuffix(".local") { h = String(h.dropLast(6)) }
        return isLabel(h) ? "\(h).local" : nil
    }

    struct Typed: Equatable {
        var url: String
        var host: String
        var isLocalName: Bool
    }

    /// Accepts "atlas-7kq3pm", "atlas-7kq3pm.local", "192.168.1.20",
    /// "192.168.1.20:8443", "https://atlas-7kq3pm.local/mobile", "[fd00::5]".
    static func parse(_ input: String) -> Typed? {
        var s = input.trimmingCharacters(in: .whitespaces)
        for prefix in ["https://", "http://"] where s.lowercased().hasPrefix(prefix) { s = String(s.dropFirst(prefix.count)) }
        s = String(s.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false).first ?? "")
        guard !s.isEmpty, !s.contains(where: { $0.isWhitespace }) else { return nil }
        let ipv6 = s.hasPrefix("[")
        let host: String
        let port: String
        if ipv6 {
            guard let close = s.firstIndex(of: "]") else { return nil }
            host = String(s[s.index(after: s.startIndex)..<close])
            let rest = s[s.index(after: close)...]
            port = rest.hasPrefix(":") ? String(rest.dropFirst()) : String(rest)
        } else {
            let parts = s.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            host = String(parts[0])
            port = parts.count > 1 ? String(parts[1]) : ""
        }
        if !port.isEmpty, (Int(port) ?? 0) < 1 || (Int(port) ?? 0) > 65535 { return nil }
        let suffix = port.isEmpty || port == "443" ? "" : ":\(port)"
        if ipv6 {
            guard !host.isEmpty, host.allSatisfy({ $0.isHexDigit || $0 == ":" }) else { return nil }
            return Typed(url: "https://[\(host)]\(suffix)", host: host, isLocalName: false)
        }
        let octets = host.split(separator: ".", omittingEmptySubsequences: false)
        if octets.count == 4, octets.allSatisfy({ !$0.isEmpty && $0.count <= 3 && $0.allSatisfy(\.isNumber) }) {
            guard octets.allSatisfy({ Int($0)! <= 255 }) else { return nil }
            return Typed(url: "https://\(host)\(suffix)", host: host, isLocalName: false)
        }
        var name = host.lowercased()
        while name.hasSuffix(".") { name.removeLast() }
        if !name.contains(".") {
            guard let f = fqdn(name) else { return nil }
            name = f
        } else if !name.split(separator: ".").allSatisfy({ isLabel(String($0)) }) {
            return nil
        }
        return Typed(url: "https://\(name)\(suffix)", host: name, isLocalName: name.hasSuffix(".local"))
    }

    /// "https://192.168.1.20/mobile" → "192.168.1.20".
    static func host(of url: String?) -> String? {
        guard let url, let u = URL(string: url), let h = u.host, !h.isEmpty else { return nil }
        return h
    }
}

/// Whether iOS lets this app reach the local network (the Local Network
/// privacy prompt). Denial shows up as Bonjour's PolicyDenied error.
/// Updated on the main queue only.
final class LocalNetworkAccess: ObservableObject {
    static let shared = LocalNetworkAccess()
    @Published var denied = false

    /// The first-run explanation was shown, so iOS's own prompt makes sense.
    var primed: Bool {
        get { UserDefaults.standard.bool(forKey: "atlas.local_network_primed") }
        set { UserDefaults.standard.set(newValue, forKey: "atlas.local_network_primed") }
    }
}

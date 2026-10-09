import Foundation
import Network

/// Finding Atlas without anyone typing an address:
///
///  1. Bonjour: Atlas advertises `_atlas._tcp` with its device id and TLS key
///     pin in TXT records.
///  2. The Atlas LAN beacon (UDP 5050).
///  3. Atlas's own hotspot (its gateway is always 10.42.0.1).
///
/// A paired Atlas is recognised by device id and confirmed with its pinned
/// key, so an IP change is just a new address for the same trusted device.
enum AtlasDiscovery {
    static let hotspotUrl = "https://10.42.0.1"

    static func discover(timeout: TimeInterval = 5) async -> [Sighting] {
        async let bonjour = browse(timeout: timeout)
        async let beacon = AtlasBeacon.listen(timeout: timeout)
        var seen: [String: Sighting] = [:]
        for s in await bonjour + beacon where seen[s.deviceId] == nil { seen[s.deviceId] = s }
        return Array(seen.values)
    }

    /// Where is the paired Atlas right now? Returns a confirmed base URL.
    static func locate(_ device: PairedDevice) async -> String? {
        if let last = device.lastUrl, await AtlasClient.confirm(last, device: device) { return last }
        // Its .local name: iOS resolves it over Bonjour, so this survives an IP change.
        if let name = device.hostname {
            let url = "https://\(name)"
            if url != device.lastUrl, await AtlasClient.confirm(url, device: device) { return url }
        }
        for s in await discover(timeout: 5) where s.deviceId == device.deviceId {
            if await AtlasClient.confirm(s.baseUrl, device: device) { return s.baseUrl }
        }
        if await AtlasClient.confirm(hotspotUrl, device: device) { return hotspotUrl }
        return nil
    }

    // ── Bonjour ─────────────────────────────────────────────────────────
    private static func browse(timeout: TimeInterval) async -> [Sighting] {
        let results: [NWBrowser.Result] = await withCheckedContinuation { cont in
            let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_atlas._tcp", domain: nil), using: .tcp)
            var latest: Set<NWBrowser.Result> = []
            browser.browseResultsChangedHandler = { r, _ in latest = r }
            // Local Network privacy: a refusal is Bonjour's PolicyDenied (-65570).
            browser.stateUpdateHandler = { state in
                switch state {
                case .waiting(let error), .failed(let error):
                    if case .dns(let code) = error, Int(code) == -65570 {
                        DispatchQueue.main.async { LocalNetworkAccess.shared.denied = true }
                    }
                case .ready:
                    DispatchQueue.main.async { LocalNetworkAccess.shared.denied = false }
                default: break
                }
            }
            browser.start(queue: .global(qos: .utility))
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout * 0.6) {
                browser.cancel()
                cont.resume(returning: Array(latest))
            }
        }
        var out: [Sighting] = []
        for r in results {
            guard case let .bonjour(txt) = r.metadata, let id = txt["id"], !id.isEmpty,
                  case let .service(name, _, _, _) = r.endpoint,
                  let host = await resolveIPv4(r.endpoint) else { continue }
            out.append(Sighting(deviceId: id, name: name, pin: txt["fp"], baseUrl: "https://\(host)", via: "bonjour",
                                hostname: AtlasAddress.fqdn(txt["host"])))
        }
        return out
    }

    /// Connect briefly to a Bonjour endpoint to learn its IPv4 address.
    private static func resolveIPv4(_ endpoint: NWEndpoint) async -> String? {
        await withCheckedContinuation { cont in
            let params = NWParameters.tcp
            if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options { ip.version = .v4 }
            let conn = NWConnection(to: endpoint, using: params)
            var done = false
            let finish: (String?) -> Void = { value in
                guard !done else { return }
                done = true
                conn.cancel()
                cont.resume(returning: value)
            }
            conn.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if case let .hostPort(host, _) = conn.currentPath?.remoteEndpoint {
                        finish("\(host)".components(separatedBy: "%").first)
                    } else { finish(nil) }
                case .failed, .cancelled: finish(nil)
                default: break
                }
            }
            let q = DispatchQueue(label: "atlas.resolve")
            conn.start(queue: q)
            q.asyncAfter(deadline: .now() + 3) { finish(nil) }
        }
    }
}

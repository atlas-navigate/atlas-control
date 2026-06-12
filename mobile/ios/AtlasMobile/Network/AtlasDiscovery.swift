import Foundation
import Network

/// LAN discovery for Atlas — iOS port of the Android `NsdHelper` object.
///
/// The reliable strategy is **a fingerprint-validated port-5000 HTTP sweep**.
/// Single-radio Atlas (Jetson Orin Nano) tends to drop or delay its avahi
/// announcement when joining a brand-new LAN, so Bonjour / NWBrowser is
/// too fragile to be the primary path. Instead, [findAtlasByPortSweep]
/// enumerates every host across the device's own /24 plus a curated list
/// of common home / lab / Docker / VPN prefixes and probes each one's
/// `/api/device` endpoint. A candidate only counts as "Atlas" when the
/// response carries the `app == "atlas-control"` fingerprint (or, for
/// older firmware, looks structurally like Atlas's `DeviceInfo`).
///
/// **Why port 5000 only?** Atlas's Flask listens on `0.0.0.0:5000`. The
/// previous sweep also probed `https://prefix.host` (port 443), which
/// rarely succeeds on a freshly bound interface (nginx isn't always
/// reachable) and lets random TLS-on-443 services false-positive when
/// paired with a weak fingerprint check.
///
/// Bonjour / DNS-SD remains as an opportunistic supplementary path
/// ([findAtlasOnLan]) but is deliberately given a short window and never
/// gates the loop.
///
/// **Simulator note:** the Xcode iOS Simulator uses the host machine's
/// networking, so it can reach Atlas anywhere the Mac can. We always
/// scan [extendedPrefixes] to cover any host LAN regardless of what
/// `getifaddrs` reports.
enum AtlasDiscovery {

    // Matches Android's `NsdHelper.SWEEP_CONCURRENCY = 256`. The per-iteration
    // sweep is capped at 20 s by the transition loops, so a 256-wide window is
    // what lets the loop drain the full ~7 300-candidate space (29 prefixes ×
    // 254 hosts) to Atlas's host in time when Atlas lands on a /24 that isn't
    // the phone's own subnet. (If the Simulator host's file-descriptor limit
    // ever bites under 256-wide, step this down to ~192 — behaviour, not
    // correctness, is affected.)
    private static let sweepConcurrency = 256
    // Hard per-probe cap, mirroring Android's `withTimeoutOrNull(700ms)` around
    // each `/api/device` probe (on top of the 0.9 s session timeout).
    private static let probeTimeoutSeconds: TimeInterval = 0.7

    /// Atlas's Flask app port. nginx / TLS on 443 is intentionally NOT
    /// swept — see file-level docs.
    private static let atlasPort = 5000

    private static let extendedPrefixes: [String] = [
        "192.168.1",
        "192.168.0",
        "192.168.2",
        "192.168.3",
        "192.168.4",
        "192.168.5",
        "192.168.10",
        "192.168.11",
        "192.168.20",
        "192.168.50",
        "192.168.68",
        "192.168.86",
        "192.168.88",
        "192.168.100",
        "192.168.123",
        "192.168.168",
        "10.0.0",
        "10.0.1",
        "10.1.0",
        "10.1.1",
        "10.10.0",
        "10.10.1",
        "10.20.0",
        "10.42.0",
        "172.16.0",
        "172.16.1",
        "172.17.0",
        "172.20.0",
    ]

    /// Subnets that Atlas's hotspot can occupy. When the caller is
    /// mid-LAN switch (`excludeHotspot=true`) the sweep skips these.
    private static let hotspotPrefixes: Set<String> = ["10.42.0", "192.168.4", "192.168.43"]

    private static func isHotspotPrefix(_ prefix: String) -> Bool {
        hotspotPrefixes.contains(prefix)
    }

    static func isHotspotIp(_ ip: String) -> Bool {
        let parts = ip.split(separator: ".").map(String.init)
        guard parts.count >= 3 else { return false }
        return isHotspotPrefix(parts[0..<3].joined(separator: "."))
    }

    // MARK: - Public discovery entry points

    /// **Primary** LAN discovery path: an HTTP-only port-5000 sweep
    /// across every candidate /24, fingerprint-validated against Atlas's
    /// `/api/device` response.
    ///
    /// Designed to fit comfortably inside one ~10-second iteration of
    /// the LAN-transition loop so the loop can retry many times within
    /// its 120 s deadline — important when Atlas hasn't finished joining
    /// the new LAN on the first iteration.
    ///
    /// - Parameter excludeHotspot: when true, skips Atlas's hotspot
    ///   prefixes. Required during a pending hotspot→LAN switch so the
    ///   dying hotspot can't masquerade as the winner.
    static func findAtlasByPortSweep(excludeHotspot: Bool = false) async -> String? {
        let candidates = buildPortSweepCandidates(excludeHotspot: excludeHotspot)
        guard !candidates.isEmpty else { return nil }
        return await sweepAtlasFingerprint(candidates)
    }

    /// Backwards-compatible alias kept for callers that haven't migrated.
    /// Identical to [findAtlasByPortSweep].
    static func findAtlasBySubnetScan(excludeHotspot: Bool = false) async -> String? {
        await findAtlasByPortSweep(excludeHotspot: excludeHotspot)
    }

    /// Targeted /24 port-5000 sweep starting at [hintIp] (probes the
    /// hint host first). Used when the backend handed us a hint address.
    ///
    /// - Parameter excludeHotspot: when true, returns nil if [hintIp]
    ///   falls in a hotspot prefix.
    static func findAtlasByTargetSubnet(hintIp: String, excludeHotspot: Bool = false) async -> String? {
        let parts = hintIp.split(separator: ".").map(String.init)
        guard parts.count == 4, let hintHost = Int(parts[3]) else { return nil }
        let prefix = parts[0..<3].joined(separator: ".")
        if excludeHotspot && isHotspotPrefix(prefix) { return nil }
        let hosts: [Int] = [hintHost] + (1...254).filter { $0 != hintHost }
        let candidates = hosts.map { "http://\(prefix).\($0):\(atlasPort)" }
        return await sweepAtlasFingerprint(candidates)
    }

    /// Bonjour / DNS-SD discovery — **supplementary** path only.
    ///
    /// Atlas's avahi announcements on a single-radio Jetson are
    /// unreliable enough on a brand-new LAN that the loop can't depend
    /// on this. Callers should pass a tight timeout (~5 s) and run it
    /// in parallel with [findAtlasByPortSweep].
    static func findAtlasOnLan(timeout: TimeInterval = 5) async -> String? {
        await withTaskGroup(of: String?.self, returning: String?.self) { group in
            group.addTask {
                await browseBonjour(serviceType: "_https._tcp", scheme: "https", timeout: timeout)
            }
            group.addTask {
                await browseBonjour(serviceType: "_http._tcp",  scheme: "http",  timeout: timeout)
            }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
            }
            return nil
        }
    }

    // MARK: - Fingerprint-validated sweep core

    /// True if [info] looks like a response from Atlas Control.
    ///
    /// Strong signal: the explicit `app == "atlas-control"` field
    /// stamped by the backend in `app.py` `/api/device`.
    ///
    /// Fallback for older Atlas firmware: the response decoded into a
    /// `DeviceInfo` without exception AND at least one Atlas-shaped
    /// field is populated (mesh node id, owner name, or hardware
    /// string). Random IoT / printer / router endpoints that 200 on
    /// `/api/device` almost never satisfy this.
    private static func looksLikeAtlas(_ info: DeviceInfo) -> Bool {
        if info.app == "atlas-control" { return true }
        if let id = info.myNodeId, !id.isEmpty { return true }
        if !info.name.isEmpty     { return true }
        if !info.hardware.isEmpty { return true }
        return false
    }

    /// Probe one URL with the fingerprint check. Returns the URL on a
    /// confirmed Atlas hit, nil otherwise.
    static func probeOne(_ url: String) async -> String? {
        do {
            // Hard 0.7 s cap per probe (Android parity) — a stalled host can't
            // hold a sweep slot for the full 0.9 s session timeout.
            return try await withTimeout(seconds: probeTimeoutSeconds) {
                let info = try await AtlasApi.getDevice(base: url, probe: true)
                return looksLikeAtlas(info) ? url : nil
            }
        } catch {
            return nil
        }
    }

    /// Probe [candidates] in parallel using `AtlasApiClient.probeSession`.
    /// Returns the first URL whose `/api/device` response looks like
    /// Atlas. All other in-flight requests are cancelled when a winner
    /// is selected.
    static func scanCandidates(_ candidates: [String]) async -> String? {
        await sweepAtlasFingerprint(candidates)
    }

    private static func sweepAtlasFingerprint(_ candidates: [String]) async -> String? {
        guard !candidates.isEmpty else { return nil }
        return await withTaskGroup(of: String?.self, returning: String?.self) { group in
            var inFlight = 0
            var iter = candidates.makeIterator()
            while inFlight < sweepConcurrency, let url = iter.next() {
                group.addTask { await probeOne(url) }
                inFlight += 1
            }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
                if let next = iter.next() {
                    group.addTask { await probeOne(next) }
                }
            }
            return nil
        }
    }

    // MARK: - Bonjour browsing (supplementary)

    private static func browseBonjour(serviceType: String, scheme: String, timeout: TimeInterval) async -> String? {
        await withCheckedContinuation { continuation in
            var resumed = false
            let lock = NSLock()
            let resume: (String?) -> Void = { value in
                lock.lock(); defer { lock.unlock() }
                if resumed { return }
                resumed = true
                continuation.resume(returning: value)
            }

            let parameters = NWParameters()
            parameters.includePeerToPeer = true
            let browser = NWBrowser(
                for: .bonjour(type: serviceType + ".", domain: "local."),
                using: parameters
            )

            browser.stateUpdateHandler = { state in
                if case .failed = state {
                    browser.cancel()
                    resume(nil)
                }
            }

            browser.browseResultsChangedHandler = { results, _ in
                for result in results {
                    if case let .service(name, _, _, _) = result.endpoint,
                       name.lowercased().contains("atlas") {
                        let connection = NWConnection(to: result.endpoint, using: .tcp)
                        connection.stateUpdateHandler = { connState in
                            switch connState {
                            case .ready:
                                if let endpoint = connection.currentPath?.remoteEndpoint,
                                   case let .hostPort(host, port) = endpoint {
                                    let host = formatHost(host)
                                    let portValue = port.rawValue
                                    let portSuffix: String
                                    if (scheme == "https" && portValue == 443) ||
                                       (scheme == "http"  && portValue == 80) {
                                        portSuffix = ""
                                    } else {
                                        portSuffix = ":\(portValue)"
                                    }
                                    connection.cancel()
                                    browser.cancel()
                                    resume("\(scheme)://\(host)\(portSuffix)")
                                } else {
                                    connection.cancel()
                                }
                            case .failed, .cancelled:
                                connection.cancel()
                            default:
                                break
                            }
                        }
                        connection.start(queue: .global())
                    }
                }
            }

            browser.start(queue: .global())

            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                browser.cancel()
                resume(nil)
            }
        }
    }

    private static func formatHost(_ host: NWEndpoint.Host) -> String {
        switch host {
        case .ipv4(let address):
            return address.debugDescription.split(separator: "%").first.map(String.init) ?? address.debugDescription
        case .ipv6(let address):
            let raw = address.debugDescription
            let withoutZone = raw.split(separator: "%").first.map(String.init) ?? raw
            return "[\(withoutZone)]"
        case .name(let name, _):
            return name
        @unknown default:
            return ""
        }
    }

    // MARK: - Sweep candidate-list builder

    private static func buildPortSweepCandidates(excludeHotspot: Bool) -> [String] {
        var selfPrefix: String? = nil
        var selfHost: Int? = nil
        var gatewayHost: Int? = nil

        if let info = LocalNetworkInfo.current() {
            let parts = info.ip.split(separator: ".").map(String.init)
            if parts.count == 4, let host = Int(parts[3]) {
                selfPrefix = parts[0..<3].joined(separator: ".")
                selfHost   = host
            }
            if let gw = info.gateway,
               let gwParts = Optional(gw.split(separator: ".").map(String.init)),
               gwParts.count == 4,
               let host = Int(gwParts[3]),
               gwParts[0..<3].joined(separator: ".") == selfPrefix {
                gatewayHost = host
            }
        }

        let effectiveSelfPrefix: String? = {
            guard let selfPrefix else { return nil }
            if excludeHotspot && Self.isHotspotPrefix(selfPrefix) { return nil }
            return selfPrefix
        }()
        let effectiveGatewayHost: Int? = (effectiveSelfPrefix == nil) ? nil : gatewayHost

        var prefixes: [String] = []
        if let effectiveSelfPrefix { prefixes.append(effectiveSelfPrefix) }
        for p in extendedPrefixes {
            if excludeHotspot && Self.isHotspotPrefix(p) { continue }
            prefixes.append(p)
        }
        let uniquePrefixes = Array(NSOrderedSet(array: prefixes)) as? [String] ?? prefixes

        var urls: [String] = []
        if let effectiveGatewayHost,
           effectiveGatewayHost >= 1,
           effectiveGatewayHost <= 254,
           effectiveGatewayHost != selfHost {
            for prefix in uniquePrefixes {
                urls.append("http://\(prefix).\(effectiveGatewayHost):\(atlasPort)")
            }
        }
        for host in 1...254 where host != selfHost && host != effectiveGatewayHost {
            for prefix in uniquePrefixes {
                urls.append("http://\(prefix).\(host):\(atlasPort)")
            }
        }
        return urls
    }
}

// MARK: - Helpers

/// Light replacement for Android's `ConnectivityManager.getLinkProperties`
/// — gives us the device's primary IPv4 address and the active default
/// gateway so the subnet sweep can prioritise their /24.
enum LocalNetworkInfo {

    struct Info { let ip: String; let gateway: String? }

    static func current() -> Info? {
        guard let ip = primaryIPv4() else { return nil }
        let parts = ip.split(separator: ".").map(String.init)
        guard parts.count == 4 else { return Info(ip: ip, gateway: nil) }
        // Most consumer routers (and Atlas's own hotspot) put the
        // default gateway at <prefix>.1. We probe it as part of the
        // sweep so a wrong guess is harmless.
        let gateway = "\(parts[0]).\(parts[1]).\(parts[2]).1"
        return Info(ip: ip, gateway: gateway)
    }

    private static func primaryIPv4() -> String? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>? = nil
        if getifaddrs(&ifaddr) != 0 { return nil }
        defer { freeifaddrs(ifaddr) }

        var best: String? = nil
        var ptr = ifaddr
        while ptr != nil {
            defer { ptr = ptr?.pointee.ifa_next }
            guard let interface = ptr?.pointee else { continue }
            let flags = Int32(interface.ifa_flags)
            guard (flags & IFF_UP)       != 0 else { continue }
            guard (flags & IFF_LOOPBACK) == 0 else { continue }
            guard let saAddr = interface.ifa_addr else { continue }
            guard saAddr.pointee.sa_family == sa_family_t(AF_INET) else { continue }
            let name = String(cString: interface.ifa_name)
            var hostBuf = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(
                saAddr, socklen_t(saAddr.pointee.sa_len),
                &hostBuf, socklen_t(hostBuf.count),
                nil, 0, NI_NUMERICHOST
            )
            if result == 0 {
                let address = String(cString: hostBuf)
                if name.hasPrefix("en") { return address }
                if best == nil { best = address }
            }
        }
        return best
    }
}

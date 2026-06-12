import Foundation
import Darwin

/// UDP "shout-and-receive" discovery client.
///
/// Pairs with `lan_beacon.py` on the Atlas backend. On every call, the
/// client:
///
///   1. Opens a single UDP socket bound to `0.0.0.0:0` with broadcast
///      enabled.
///   2. **Shouts** `ATLAS-DISCOVER\n{"nonce":"<hex>"}` to:
///        - the global broadcast (`255.255.255.255:5050`)
///        - every per-prefix gateway IP (`<prefix>.1:5050`)
///        - every host on the curated /24 prefixes (`<prefix>.<host>:5050`)
///      The unicast fan-out keeps this working when the network blocks
///      broadcast packets (some enterprise / mesh routers do this) — and
///      under iOS Simulator, where the host's networking is shared so
///      everything Just Works.
///   3. Listens for replies on the same socket. The first packet whose
///      JSON body satisfies the Atlas fingerprint (`app == "atlas-control"`)
///      wins; its sender IP becomes the candidate access URL.
///
/// The returned URL is a *candidate* — callers re-verify it via
/// `AtlasApi.getDevice` (TCP `/api/device` fingerprint) before applying it
/// to the wizard's discovery state.
///
/// This implementation uses BSD sockets directly rather than `NWConnection`.
/// Reasons:
///   - `NWConnection` does not expose the source IP of received UDP datagrams
///     in an ergonomic way — we need it because Atlas's published
///     `accessUrls` may be stale on a freshly bound interface.
///   - We need to enable `SO_BROADCAST` and send to `255.255.255.255`, which
///     `NWConnection` won't do without jumping through `NWMulticastGroup`
///     hoops that trigger `NSLocalNetworkUsageDescription` prompts the user
///     has already approved at the Bonjour level.
enum AtlasBeacon {

    private static let beaconPort: UInt16 = 5050
    private static let probeToken = "ATLAS-DISCOVER"
    /// Resend probe bursts every [burstInterval] until the deadline. Without
    /// this, a single first-burst miss (Atlas not yet up on the new LAN, or
    /// transient packet loss while iOS is still associating with the new
    /// SSID) means the entire ``discover()`` call returns nil even if Atlas
    /// comes up mid-window.
    private static let burstInterval: TimeInterval = 3

    /// Mirrors `AtlasDiscovery.extendedPrefixes` so the beacon and the
    /// port-5000 sweep cover the same address space.
    private static let extendedPrefixes: [String] = [
        "192.168.1",  "192.168.0",  "192.168.2",  "192.168.3",  "192.168.5",
        "192.168.4",  "192.168.10", "192.168.11", "192.168.20", "192.168.50",
        "192.168.68", "192.168.86", "192.168.88", "192.168.100","192.168.123",
        "192.168.168","10.0.0",     "10.0.1",     "10.1.0",     "10.1.1",
        "10.10.0",    "10.10.1",    "10.20.0",    "10.42.0",    "172.16.0",
        "172.16.1",   "172.17.0",   "172.20.0",
    ]

    private static let hotspotPrefixes: Set<String> = ["10.42.0", "192.168.4", "192.168.43"]

    /// Run a discovery cycle. Returns the access URL of the first beacon
    /// reply that looks like Atlas, or nil on timeout.
    ///
    /// - Parameters:
    ///   - timeout: how long to listen for replies, in seconds. Typical
    ///     values: 3 s inside the LAN-transition loop (which re-runs us),
    ///     8 s for a one-shot first-launch search.
    ///   - excludeHotspot: when true, skips probes targeting Atlas's
    ///     hotspot prefixes — required during a pending hotspot→LAN
    ///     switch so the dying hotspot doesn't beat the real LAN address.
    static func discover(timeout: TimeInterval = 4, excludeHotspot: Bool = false) async -> String? {
        await withCheckedContinuation { (continuation: CheckedContinuation<String?, Never>) in
            DispatchQueue.global(qos: .userInitiated).async {
                let result = runDiscovery(timeout: timeout, excludeHotspot: excludeHotspot)
                continuation.resume(returning: result)
            }
        }
    }

    /// Run [discover] in parallel with the existing port-5000 sweep and
    /// return whichever wins first. Convenience wrapper that the setup
    /// wizard can use as a single "find Atlas now" call.
    static func discoverFastest(timeout: TimeInterval = 8, excludeHotspot: Bool = false) async -> String? {
        await withTaskGroup(of: String?.self, returning: String?.self) { group in
            group.addTask { await discover(timeout: timeout, excludeHotspot: excludeHotspot) }
            group.addTask {
                (try? await withTimeout(seconds: timeout) {
                    await AtlasDiscovery.findAtlasByPortSweep(excludeHotspot: excludeHotspot)
                }) ?? nil
            }
            for await value in group {
                if let value {
                    group.cancelAll()
                    return value
                }
            }
            return nil
        }
    }

    // MARK: - Core BSD-socket implementation

    private static func runDiscovery(timeout: TimeInterval, excludeHotspot: Bool) -> String? {
        let fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        // Enable broadcast so 255.255.255.255 sends don't return EACCES.
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &yes, socklen_t(MemoryLayout<Int32>.size))

        // Pin egress to the Wi-Fi interface so broadcast/unicast probes leave
        // via Wi-Fi (where Atlas lives), not cellular, on a dual-path device —
        // the iOS counterpart of Android holding the Wi-Fi MulticastLock.
        // Best-effort: if en0 is absent the option is simply ignored. Only on
        // device — the Simulator shares the host stack and already routes
        // correctly, so we leave its behaviour untouched.
        #if !targetEnvironment(simulator)
        var wifiIfIndex = if_nametoindex("en0")
        if wifiIfIndex != 0 {
            setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &wifiIfIndex, socklen_t(MemoryLayout<UInt32>.size))
        }
        #endif

        // Bind ephemeral port on 0.0.0.0 so reply packets reach us. Skipping
        // the bind means recvfrom blocks forever (the kernel won't deliver
        // unsolicited UDP to an unbound socket).
        var bindAddr = sockaddr_in()
        bindAddr.sin_family = sa_family_t(AF_INET)
        bindAddr.sin_addr.s_addr = INADDR_ANY.bigEndian
        bindAddr.sin_port = 0
        bindAddr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        let bindOk = withUnsafePointer(to: &bindAddr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                Darwin.bind(fd, saPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindOk == 0 else { return nil }

        // Short recv timeout so we re-check the deadline frequently and can
        // bail out early when a winner is found.
        var tv = timeval(tv_sec: 0, tv_usec: 200_000)  // 200 ms
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        let nonce = makeNonce()
        let probe = "\(probeToken)\n{\"nonce\":\"\(nonce)\"}".data(using: .utf8)!
        let targets = buildProbeTargets(excludeHotspot: excludeHotspot)
        let deadline = Date().addingTimeInterval(timeout)
        let cancelled = AtomicFlag()

        // Fire repeated probe bursts every burstInterval until the deadline.
        // Each burst sweeps every target IP; the unicast fan-out is what
        // makes this work on AVD-style emulators (and any router that drops
        // broadcast UDP). Real devices and the iOS Simulator would hear
        // Atlas's own heartbeat shouts directly, but the bursts also serve
        // as resilience against packet loss during a Wi-Fi association.
        let sendQueue = DispatchQueue.global(qos: .utility)
        sendQueue.async {
            var burstNum = 0
            while !cancelled.value && Date() < deadline {
                burstNum += 1
                for target in targets where !cancelled.value {
                    guard let addr = makeSockaddr(ip: target, port: beaconPort) else { continue }
                    _ = probe.withUnsafeBytes { buf -> Int in
                        var sa = addr
                        return withUnsafePointer(to: &sa) { ptr in
                            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                                sendto(
                                    fd,
                                    buf.baseAddress, buf.count, 0,
                                    saPtr, socklen_t(MemoryLayout<sockaddr_in>.size)
                                )
                            }
                        }
                    }
                }
                NSLog("[AtlasBeacon] burst \(burstNum): \(targets.count) probes sent")
                // Sleep burstInterval seconds in small slices so we react to
                // cancellation promptly when the receiver finds a winner.
                let nextBurstAt = Date().addingTimeInterval(burstInterval)
                while !cancelled.value && Date() < nextBurstAt && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.15)
                }
            }
        }

        var buffer = [UInt8](repeating: 0, count: 2048)

        while Date() < deadline {
            var fromAddr = sockaddr_in()
            var fromLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let n: ssize_t = buffer.withUnsafeMutableBufferPointer { buf in
                withUnsafeMutablePointer(to: &fromAddr) { ptr in
                    ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                        recvfrom(fd, buf.baseAddress, buf.count, 0, saPtr, &fromLen)
                    }
                }
            }
            if n <= 0 { continue }  // timeout — re-check deadline
            let bytes = Data(bytes: buffer, count: Int(n))
            if let url = parseBeaconReply(data: bytes, fromAddr: fromAddr, expectedNonce: nonce) {
                cancelled.set()  // tell the sender to stop probing
                NSLog("[AtlasBeacon] beacon reply from \(url)")
                return url
            }
        }
        cancelled.set()  // ensure sender exits even on timeout
        return nil
    }

    /// Tiny thread-safe boolean. The probe-sender runs on a background
    /// dispatch queue and the receiver runs synchronously — they need to
    /// share a stop flag without using the Swift concurrency model.
    private final class AtomicFlag: @unchecked Sendable {
        private let lock = NSLock()
        private var raw = false
        var value: Bool {
            lock.lock(); defer { lock.unlock() }
            return raw
        }
        func set() {
            lock.lock(); defer { lock.unlock() }
            raw = true
        }
    }

    // MARK: - Reply parsing

    private static func parseBeaconReply(
        data: Data, fromAddr: sockaddr_in, expectedNonce: String
    ) -> String? {
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return nil
        }
        guard let app = obj["app"] as? String, app == "atlas-control" else { return nil }
        // If the reply echoes a nonce, only trust matching ones — protects
        // against stale replies arriving in the next probe cycle.
        if let echoed = obj["nonce"] as? String, !echoed.isEmpty, echoed != expectedNonce {
            return nil
        }
        // Synthesize the URL from the source IP — that's guaranteed to be
        // the address Atlas is actually answering on, even when the
        // advertised accessUrls list is stale.
        if let ip = ipv4String(from: fromAddr),
           !ip.isEmpty, ip != "0.0.0.0", ip != "255.255.255.255" {
            return "http://\(ip):5000"
        }
        // Fallback: first non-hotspot accessUrl from the manifest.
        if let urls = obj["accessUrls"] as? [String] {
            for u in urls where !u.isEmpty && !looksLikeHotspot(u) {
                return u
            }
        }
        return nil
    }

    private static func looksLikeHotspot(_ url: String) -> Bool {
        for prefix in hotspotPrefixes where url.contains("\(prefix).") { return true }
        return false
    }

    // MARK: - Probe target list

    private static func buildProbeTargets(excludeHotspot: Bool) -> [String] {
        var targets: [String] = []
        var seen = Set<String>()
        func push(_ ip: String) {
            if seen.insert(ip).inserted { targets.append(ip) }
        }
        push("255.255.255.255")
        for prefix in extendedPrefixes {
            if excludeHotspot && hotspotPrefixes.contains(prefix) { continue }
            push("\(prefix).1")
        }
        for host in 2...254 {
            for prefix in extendedPrefixes {
                if excludeHotspot && hotspotPrefixes.contains(prefix) { continue }
                push("\(prefix).\(host)")
            }
        }
        return targets
    }

    // MARK: - sockaddr helpers

    private static func makeSockaddr(ip: String, port: UInt16) -> sockaddr_in? {
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        if inet_pton(AF_INET, ip, &addr.sin_addr) != 1 { return nil }
        return addr
    }

    private static func ipv4String(from addr: sockaddr_in) -> String? {
        var copy = addr
        var buf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        let ok = withUnsafePointer(to: &copy.sin_addr) { ptr -> Bool in
            inet_ntop(AF_INET, ptr, &buf, socklen_t(INET_ADDRSTRLEN)) != nil
        }
        return ok ? String(cString: buf) : nil
    }

    private static func makeNonce() -> String {
        let raw = UInt64.random(in: 0...UInt64.max)
        return String(format: "%016llx", raw)
    }
}

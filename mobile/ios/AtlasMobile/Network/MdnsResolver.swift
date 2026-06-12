import Foundation
import Network
import Darwin

/// Direct-UDP mDNS resolver for `*.local` hostnames.
///
/// iOS will normally resolve `.local` via `mdnsd` automatically once the user
/// grants the Local Network permission, but during the hotspot → LAN handoff
/// the cached answer for `atlas.local` may briefly point at the dying hotspot
/// IP.  This helper:
///
///   1. Sends a multicast DNS A-record query (with the QU bit set) to
///      `224.0.0.251:5353` so any responder replies directly to our socket.
///   2. Caches the answer for [cacheTtl] seconds.
///   3. Exposes [clearCache] so the LAN-switch loop can force a re-query.
///
/// Direct mirror of the Android `MdnsDns` object so behaviour is identical
/// across both platforms.
enum MdnsResolver {

    private static let mdnsAddr = "224.0.0.251"
    private static let mdnsPort: UInt16 = 5353
    private static let timeout: TimeInterval = 1.5
    private static let cacheTtl: TimeInterval = 60

    private static let queue = DispatchQueue(label: "atlas.mdns.cache")
    private static var cachedHost = ""
    private static var cachedIp = ""
    private static var cacheExpiry: Date = .distantPast

    /// Force the next [resolve] call to send a fresh mDNS query.
    static func clearCache() {
        queue.sync {
            cachedHost = ""
            cachedIp = ""
            cacheExpiry = .distantPast
        }
    }

    /// Resolve [hostname] (with or without trailing dot).  Returns the first
    /// IPv4 address found, or `nil` after [timeout] seconds without a reply.
    /// Hostnames not ending in `.local` return `nil` immediately — the system
    /// resolver handles those.
    static func resolve(_ hostname: String) -> String? {
        let host = hostname.lowercased()
        guard host.hasSuffix(".local") || host.hasSuffix(".local.") else { return nil }

        if let cached: String = queue.sync(execute: { () -> String? in
            if host == cachedHost && Date() < cacheExpiry && !cachedIp.isEmpty {
                return cachedIp
            }
            return nil
        }) {
            return cached
        }

        guard let ip = sendQuery(host) else { return nil }
        queue.sync {
            cachedHost  = host
            cachedIp    = ip
            cacheExpiry = Date().addingTimeInterval(cacheTtl)
        }
        return ip
    }

    // MARK: - UDP transport

    private static func sendQuery(_ host: String) -> String? {
        let sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        if sock < 0 { return nil }
        defer { close(sock) }

        // Allow our local socket to bind/send even if mdnsd is also using the
        // mDNS port; we never bind to 5353 ourselves so this is mostly a
        // belt-and-braces option for older iOS versions.
        var reuse: Int32 = 1
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        // Pin the multicast query to the Wi-Fi interface on device so it leaves
        // via Wi-Fi rather than cellular (Android parity — it browses mDNS with
        // a MulticastLock held on the Wi-Fi radio). Best-effort; ignored if en0
        // is absent. Left untouched on the Simulator, which shares the host stack.
        #if !targetEnvironment(simulator)
        var wifiIfIndex = if_nametoindex("en0")
        if wifiIfIndex != 0 {
            setsockopt(sock, IPPROTO_IP, IP_BOUND_IF, &wifiIfIndex, socklen_t(MemoryLayout<UInt32>.size))
        }
        #endif

        // Receive timeout
        var tv = timeval(tv_sec: 1, tv_usec: 500_000)   // 1.5 s
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        let query = buildQuery(host)
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port   = mdnsPort.bigEndian
        addr.sin_addr.s_addr = inet_addr(mdnsAddr)

        let sent = query.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> Int in
            withUnsafePointer(to: &addr) { (ap: UnsafePointer<sockaddr_in>) -> Int in
                ap.withMemoryRebound(to: sockaddr.self, capacity: 1) { sap in
                    sendto(sock, raw.baseAddress, raw.count, 0, sap, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
        if sent < 0 { return nil }

        var buf = [UInt8](repeating: 0, count: 4096)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let n = buf.withUnsafeMutableBytes { recv(sock, $0.baseAddress, $0.count, 0) }
            if n <= 0 { break }
            if let ip = parseARecord(Array(buf.prefix(n))) { return ip }
        }
        return nil
    }

    // MARK: - DNS packet helpers

    private static func buildQuery(_ host: String) -> Data {
        let trimmed = host.hasSuffix(".") ? String(host.dropLast()) : host
        var bytes: [UInt8] = []
        // Header: ID=0, flags=0, QDCOUNT=1, AN/NS/AR=0
        bytes.append(contentsOf: [0x00, 0x00,
                                  0x00, 0x00,
                                  0x00, 0x01,
                                  0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        for label in trimmed.split(separator: ".") {
            let chars = Array(label.utf8)
            bytes.append(UInt8(chars.count))
            bytes.append(contentsOf: chars)
        }
        bytes.append(0x00)                   // root
        bytes.append(contentsOf: [0x00, 0x01])           // QTYPE = A
        bytes.append(contentsOf: [0x80, 0x01])           // QCLASS = IN | QU
        return Data(bytes)
    }

    private static func parseARecord(_ data: [UInt8]) -> String? {
        guard data.count >= 12 else { return nil }
        let anCount = (Int(data[6]) << 8) | Int(data[7])
        guard anCount > 0 else { return nil }

        var pos = 12
        pos = skipName(data, start: pos)
        pos += 4                               // QTYPE + QCLASS

        for _ in 0..<anCount {
            if pos >= data.count { return nil }
            pos = skipName(data, start: pos)
            if pos + 10 > data.count { return nil }
            let type  = (Int(data[pos    ]) << 8) | Int(data[pos + 1])
            let rdLen = (Int(data[pos + 8]) << 8) | Int(data[pos + 9])
            pos += 10
            if type == 1 && rdLen == 4 && pos + 4 <= data.count {
                return "\(data[pos]).\(data[pos + 1]).\(data[pos + 2]).\(data[pos + 3])"
            }
            pos += rdLen
        }
        return nil
    }

    private static func skipName(_ data: [UInt8], start: Int) -> Int {
        var pos = start
        while pos < data.count {
            let b = Int(data[pos])
            if b == 0           { return pos + 1 }
            if (b & 0xC0) == 0xC0 { return pos + 2 }
            pos += b + 1
        }
        return pos
    }
}

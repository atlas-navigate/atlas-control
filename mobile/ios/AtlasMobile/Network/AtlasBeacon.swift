import Darwin
import Foundation

/// An Atlas seen on the network, not yet verified.
struct Sighting: Identifiable, Equatable {
    var id: String { deviceId }
    var deviceId: String
    var name: String
    var pin: String?
    var baseUrl: String
    var via: String
}

/// UDP discovery against Atlas's LAN beacon (port 5050).
///
/// Asks with a broadcast and also hears Atlas's unsolicited announcements
/// (frequent right after Atlas changes networks). Beacon v2 replies carry the
/// device id and TLS key pin; the reply's source address is where Atlas is.
/// Nothing is trusted from a sighting — the caller confirms with the pinned key.
/// Sending broadcasts needs the multicast networking entitlement on a device.
enum AtlasBeacon {
    private static let port: UInt16 = 5050

    static func listen(timeout: TimeInterval = 4) async -> [Sighting] {
        await Task.detached(priority: .utility) { run(timeout: timeout) }.value
    }

    private static func run(timeout: TimeInterval) -> [Sighting] {
        let fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return [] }
        defer { close(fd) }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &yes, socklen_t(MemoryLayout<Int32>.size))
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &yes, socklen_t(MemoryLayout<Int32>.size))
        var tv = timeval(tv_sec: 0, tv_usec: 250_000)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        // Bind to 5050 when possible so Atlas's own announcements reach us too.
        var local = sockaddr_in()
        local.sin_family = sa_family_t(AF_INET)
        local.sin_port = port.bigEndian
        local.sin_addr.s_addr = INADDR_ANY
        let bound = withUnsafePointer(to: &local) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        if bound != 0 {
            local.sin_port = 0
            _ = withUnsafePointer(to: &local) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
        }

        let probe = Array("ATLAS-DISCOVER\n{\"nonce\":\"\(UUID().uuidString)\"}".utf8)
        var target = sockaddr_in()
        target.sin_family = sa_family_t(AF_INET)
        target.sin_port = port.bigEndian
        target.sin_addr.s_addr = INADDR_BROADCAST

        var found: [String: Sighting] = [:]
        let deadline = Date().addingTimeInterval(timeout)
        var nextProbe = Date.distantPast
        var buf = [UInt8](repeating: 0, count: 4096)
        while Date() < deadline {
            if Date() >= nextProbe {
                _ = withUnsafePointer(to: &target) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        sendto(fd, probe, probe.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                    }
                }
                nextProbe = Date().addingTimeInterval(1.5)
            }
            var from = sockaddr_in()
            var len = socklen_t(MemoryLayout<sockaddr_in>.size)
            let n = withUnsafeMutablePointer(to: &from) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { recvfrom(fd, &buf, buf.count, 0, $0, &len) }
            }
            guard n > 0 else { continue }
            let data = Data(buf[0..<n])
            guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  o["app"] as? String == "atlas-control", (o["v"] as? Int ?? 0) >= 2,
                  let id = o["id"] as? String, !id.isEmpty else { continue }
            var addr = from.sin_addr
            var ipBuf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            guard inet_ntop(AF_INET, &addr, &ipBuf, socklen_t(INET_ADDRSTRLEN)) != nil else { continue }
            let ip = String(cString: ipBuf)
            found[id] = Sighting(deviceId: id, name: o["name"] as? String ?? "Atlas",
                                 pin: (o["fp"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                                 baseUrl: "https://\(ip)", via: "beacon")
        }
        return Array(found.values)
    }
}

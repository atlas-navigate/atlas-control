import CryptoKit
import Foundation

/// Public identity an Atlas shows before pairing (/api/device/info).
struct AtlasInfo: Equatable {
    var baseUrl: String
    var deviceId: String
    var name: String
    var advertisedPin: String?
    /// The key this connection actually presented.
    var observedPin: String
    var version: String?
    /// Pairing protocol Atlas speaks (/api/device/info "pairing"); see AtlasClient.pairingVersion.
    var pairingVersion: Int = 1
}

struct PairingError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct ApprovalRequest {
    let requestId: String
    let secret: String
    let serverNonce: String
    let clientNonce: String
}

enum HealthResult { case up, restarting, unreachable, wrongKey }

/// HTTPS calls to Atlas, always through a pinned (or, before pairing,
/// observing) session. Nothing secret is sent over an observing session.
enum AtlasClient {
    /// The pairing protocol this app speaks. A higher number on Atlas means: update the app.
    static let pairingVersion = 1

    static func normalize(_ url: String) -> String {
        var u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        while u.hasSuffix("/") { u.removeLast() }
        return u
    }

    private static func session(pin: String?, timeout: TimeInterval) -> (URLSession, PinnedSessionDelegate) {
        let delegate = PinnedSessionDelegate(expectedPin: pin)
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = timeout
        cfg.timeoutIntervalForResource = timeout * 2
        cfg.httpCookieStorage = nil
        cfg.urlCache = nil
        return (URLSession(configuration: cfg, delegate: delegate, delegateQueue: nil), delegate)
    }

    private static func request(_ url: String, method: String = "GET", token: String? = nil,
                                json: [String: Any]? = nil) -> URLRequest? {
        guard let u = URL(string: url) else { return nil }
        var r = URLRequest(url: u)
        r.httpMethod = method
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try? JSONSerialization.data(withJSONObject: json)
        }
        return r
    }

    private static func object(_ data: Data) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    }

    /// Read the public identity of an Atlas we have not paired with yet.
    static func probe(_ baseUrl: String, timeout: TimeInterval = 2) async -> AtlasInfo? {
        let base = normalize(baseUrl)
        let (s, d) = session(pin: nil, timeout: timeout)
        defer { s.invalidateAndCancel() }
        guard let req = request("\(base)/api/device/info"),
              let (data, resp) = try? await s.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        let o = object(data)
        guard o["app"] as? String == "atlas-control", let pin = d.observedPin,
              let id = o["device_id"] as? String else { return nil }
        return AtlasInfo(baseUrl: base, deviceId: id, name: o["name"] as? String ?? "Atlas",
                         advertisedPin: o["tls_spki_sha256"] as? String, observedPin: pin,
                         version: o["version"] as? String, pairingVersion: o["pairing"] as? Int ?? 1)
    }

    /// Is this an Atlas running software from before pairing existed (it answers
    /// /api/device but has no /api/device/info)? Only used to explain why the
    /// app can't connect — never to connect without pairing.
    static func isOlderAtlas(_ baseUrl: String) async -> Bool {
        let (s, _) = session(pin: nil, timeout: 2.5)
        defer { s.invalidateAndCancel() }
        guard let req = request("\(normalize(baseUrl))/api/device"),
              let (data, resp) = try? await s.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return false }
        return object(data)["app"] as? String == "atlas-control"
    }

    /// Does Atlas still accept this iPhone's pairing? false = removed in
    /// Settings → Connected devices, or Atlas was factory reset; nil = couldn't
    /// tell right now (restarting, unreachable).
    static func stillPaired(_ baseUrl: String, device: PairedDevice) async -> Bool? {
        let (s, _) = session(pin: device.pin, timeout: 4)
        defer { s.invalidateAndCancel() }
        guard let req = request("\(normalize(baseUrl))/api/pair/status", token: device.token),
              let (data, resp) = try? await s.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        let o = object(data)
        guard let paired = o["paired"] as? Bool else { return nil }
        if let id = (o["device"] as? [String: Any])?["id"] as? String, id != device.deviceId { return nil }
        if o["required"] as? Bool == false { return true }
        return paired
    }

    /// Is the paired Atlas answering at this address with its pinned key?
    static func confirm(_ baseUrl: String, device: PairedDevice, timeout: TimeInterval = 3) async -> Bool {
        let (s, _) = session(pin: device.pin, timeout: timeout)
        defer { s.invalidateAndCancel() }
        guard let req = request("\(normalize(baseUrl))/api/device/info", token: device.token),
              let (data, resp) = try? await s.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return false }
        return object(data)["device_id"] as? String == device.deviceId
    }

    private static func post(_ url: String, pin: String, body: [String: Any]) async throws -> [String: Any] {
        let (s, _) = session(pin: pin, timeout: 20)
        defer { s.invalidateAndCancel() }
        guard let req = request(url, method: "POST", json: body) else { throw PairingError(message: "Bad address") }
        let (data, resp) = try await s.data(for: req)
        let o = object(data)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw PairingError(message: o["error"] as? String ?? "Atlas said no.")
        }
        return o
    }

    static func claim(_ baseUrl: String, pin: String, setupCode: String, name: String) async throws -> String {
        let o = try await post("\(normalize(baseUrl))/api/pair/claim", pin: pin,
                               body: ["setup_code": setupCode, "name": name, "kind": "ios"])
        guard let t = o["token"] as? String, !t.isEmpty else { throw PairingError(message: "Atlas did not return a token") }
        return t
    }

    static func claimViaHotspot(_ baseUrl: String, pin: String, name: String) async throws -> String {
        let o = try await post("\(normalize(baseUrl))/api/pair/hotspot", pin: pin, body: ["name": name, "kind": "ios"])
        guard let t = o["token"] as? String, !t.isEmpty else { throw PairingError(message: "Atlas did not return a token") }
        return t
    }

    static func requestApproval(_ baseUrl: String, pin: String, name: String) async throws -> ApprovalRequest {
        let nonce = UUID().uuidString
        let o = try await post("\(normalize(baseUrl))/api/pair/request", pin: pin,
                               body: ["name": name, "kind": "ios", "client_nonce": nonce])
        guard let id = o["request_id"] as? String, let secret = o["secret"] as? String,
              let sn = o["server_nonce"] as? String else { throw PairingError(message: "Unexpected reply from Atlas") }
        return ApprovalRequest(requestId: id, secret: secret, serverNonce: sn, clientNonce: nonce)
    }

    /// "pending" | "retry" | "approved:<token>" | "denied" | "expired" | "unknown".
    /// "retry": Atlas was busy, restarting or briefly unreachable — keep waiting.
    static func pollApproval(_ baseUrl: String, pin: String, request req: ApprovalRequest) async -> String {
        let (s, _) = session(pin: pin, timeout: 10)
        defer { s.invalidateAndCancel() }
        let secret = req.secret.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
        guard let r = request("\(normalize(baseUrl))/api/pair/request/\(req.requestId)?secret=\(secret)&kind=ios"),
              let (data, resp) = try? await s.data(for: r),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return "retry" }
        return approvalStatus(object(data))
    }

    static func approvalStatus(_ o: [String: Any]) -> String {
        let status = o["status"] as? String ?? "unknown"
        return status == "approved" ? "approved:\(o["token"] as? String ?? "")" : status
    }

    /// The 4-digit code this phone shows — computed from the key it actually saw.
    static func matchCode(observedPin: String, serverNonce: String, clientNonce: String) -> String {
        let digest = Array(SHA256.hash(data: Data("atlas-pair|\(observedPin)|\(serverNonce)|\(clientNonce)".utf8)))
        let v = (UInt32(digest[0]) << 24) | (UInt32(digest[1]) << 16) | (UInt32(digest[2]) << 8) | UInt32(digest[3])
        return String(format: "%04d", v % 10000)
    }

    /// Update/restart stage from nginx (answers even while the Atlas app is down).
    static func updateStage(_ baseUrl: String, device: PairedDevice) async -> String? {
        let (s, _) = session(pin: device.pin, timeout: 4)
        defer { s.invalidateAndCancel() }
        guard let r = request("\(normalize(baseUrl))/update-status.json", token: device.token),
              let (data, resp) = try? await s.data(for: r),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return object(data)["stage"] as? String
    }

    static func health(_ baseUrl: String, device: PairedDevice) async -> HealthResult {
        let (s, d) = session(pin: device.pin, timeout: 4)
        defer { s.invalidateAndCancel() }
        guard let r = request("\(normalize(baseUrl))/api/health", token: device.token) else { return .unreachable }
        do {
            let (_, resp) = try await s.data(for: r)
            return (resp as? HTTPURLResponse)?.statusCode == 200 ? .up : .restarting
        } catch {
            return d.pinMismatch ? .wrongKey : .unreachable
        }
    }

    /// GET a JSON value from the paired Atlas (notifications polling).
    static func getJSON(_ baseUrl: String, path: String, device: PairedDevice) async -> Any? {
        let (s, _) = session(pin: device.pin, timeout: 8)
        defer { s.invalidateAndCancel() }
        guard let r = request("\(normalize(baseUrl))\(path)", token: device.token),
              let (data, resp) = try? await s.data(for: r),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    /// POST JSON to the paired Atlas (phone location sharing).
    static func postJSON(_ baseUrl: String, path: String, device: PairedDevice, body: [String: Any]) async {
        let (s, _) = session(pin: device.pin, timeout: 8)
        defer { s.invalidateAndCancel() }
        guard let r = request("\(normalize(baseUrl))\(path)", method: "POST", token: device.token, json: body) else { return }
        _ = try? await s.data(for: r)
    }
}

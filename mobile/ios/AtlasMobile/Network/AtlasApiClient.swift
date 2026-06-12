import Foundation

/// Trust-all delegate so that Atlas's self-signed cert (atlas.local, hotspot IPs)
/// is accepted exactly as the Android OkHttp client does.  Atlas is reachable
/// only over the local LAN/hotspot, never the open internet.
final class AtlasURLSessionDelegate: NSObject, URLSessionDelegate {
    static let shared = AtlasURLSessionDelegate()

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        if let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}

enum AtlasApiError: Error {
    case invalidUrl
    case http(Int)
    case decoding(Error)
    case transport(Error)
    case noData
    case timeout
}

/// Mirror of Android's `ApiClient` object.  Keeps a `probe` URLSession with
/// short timeouts (used for fan-out subnet probing) and a `full` URLSession
/// with relaxed timeouts (used for chat / streaming / long-poll).
enum AtlasApiClient {

    static let probeSession: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        // Per-callsite `withTimeout` wrappers own the real probe budget
        // (0.7 s sweep, 1.5/2 s fast probe, 2 s status poll, 8 s manual IP).
        // The session limits are only a backstop and must sit ABOVE the
        // largest wrapped budget that needs to succeed in one shot: at 0.9 s
        // they silently overrode every wrapper, so a `.local` resolve or a
        // self-signed TLS handshake could never finish even when the caller
        // had budgeted 2–8 s for it (Android's probe client allows
        // 0.9 s connect + 0.9 s read on top of its own mDNS resolver).
        cfg.timeoutIntervalForRequest  = 2.5
        cfg.timeoutIntervalForResource = 8.5
        // Keep at/above the sweep task-group width (AtlasDiscovery.sweepConcurrency
        // = 256) so URLSession never queues probes below the intended fan-out.
        // Sweep targets are unique hosts, so this per-host cap is effectively a
        // safety floor rather than a real limit.
        cfg.httpMaximumConnectionsPerHost = 256
        cfg.waitsForConnectivity = false
        cfg.urlCache = nil
        cfg.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        return URLSession(configuration: cfg, delegate: AtlasURLSessionDelegate.shared, delegateQueue: nil)
    }()

    static let fullSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest  = 15
        cfg.timeoutIntervalForResource = 180
        cfg.httpMaximumConnectionsPerHost = 16
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg, delegate: AtlasURLSessionDelegate.shared, delegateQueue: nil)
    }()

    /// Trim trailing slashes; everything else stays intact.  Mirrors the
    /// `normalize` step inside Android's `ApiClient.create*` paths.
    static func normalize(_ baseUrl: String) -> String {
        var s = baseUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "https://" + s }
        if !s.hasSuffix("/") { s += "/" }
        return s
    }

    static func makeUrl(base: String, path: String) -> URL? {
        let trimmedBase = base.hasSuffix("/") ? String(base.dropLast()) : base
        let p = path.hasPrefix("/") ? path : "/" + path
        return URL(string: trimmedBase + p)
    }
}

/// Repository-style helpers that perform a single GET/POST and decode the
/// result.  All calls take an explicit base URL so the caller can probe many
/// candidate URLs in parallel without rebuilding a session per URL.
enum AtlasApi {

    // MARK: - GET helpers

    private static func get<T: Decodable>(_ type: T.Type, base: String, path: String, session: URLSession) async throws -> T {
        guard let url = AtlasApiClient.makeUrl(base: base, path: path) else { throw AtlasApiError.invalidUrl }
        do {
            let (data, response) = try await session.data(from: url)
            guard let http = response as? HTTPURLResponse else { throw AtlasApiError.noData }
            guard (200..<300).contains(http.statusCode) else { throw AtlasApiError.http(http.statusCode) }
            do { return try JSONDecoder().decode(T.self, from: data) }
            catch { throw AtlasApiError.decoding(error) }
        } catch let err as AtlasApiError { throw err }
        catch let urlErr as URLError where urlErr.code == .timedOut { throw AtlasApiError.timeout }
        catch { throw AtlasApiError.transport(error) }
    }

    private static func post<T: Decodable>(_ type: T.Type, base: String, path: String, body: Encodable, session: URLSession) async throws -> T {
        guard let url = AtlasApiClient.makeUrl(base: base, path: path) else { throw AtlasApiError.invalidUrl }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do { req.httpBody = try JSONEncoder().encode(AnyEncodable(body)) }
        catch { throw AtlasApiError.transport(error) }
        do {
            let (data, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw AtlasApiError.noData }
            guard (200..<300).contains(http.statusCode) else { throw AtlasApiError.http(http.statusCode) }
            do { return try JSONDecoder().decode(T.self, from: data) }
            catch { throw AtlasApiError.decoding(error) }
        } catch let err as AtlasApiError { throw err }
        catch let urlErr as URLError where urlErr.code == .timedOut { throw AtlasApiError.timeout }
        catch { throw AtlasApiError.transport(error) }
    }

    // MARK: - Public API

    static func getDevice(base: String, probe: Bool = false) async throws -> DeviceInfo {
        try await get(DeviceInfo.self, base: base, path: "/api/device",
                      session: probe ? AtlasApiClient.probeSession : AtlasApiClient.fullSession)
    }

    static func getMessages(base: String) async throws -> [AtlasMessage] {
        try await get([AtlasMessage].self, base: base, path: "/api/messages", session: AtlasApiClient.fullSession)
    }

    static func getBootstrap(base: String, probe: Bool = false) async throws -> BootstrapManifest {
        try await get(BootstrapManifest.self, base: base, path: "/api/mobile/bootstrap",
                      session: probe ? AtlasApiClient.probeSession : AtlasApiClient.fullSession)
    }

    static func getWifiNetworks(base: String) async throws -> WifiNetworksResponse {
        try await get(WifiNetworksResponse.self, base: base, path: "/api/wifi/networks", session: AtlasApiClient.fullSession)
    }

    static func connectWifi(base: String, request: WifiConnectRequest) async throws -> WifiConnectResponse {
        try await post(WifiConnectResponse.self, base: base, path: "/api/wifi/connect", body: request, session: AtlasApiClient.fullSession)
    }

    static func getWifiStatus(base: String, probe: Bool = false) async throws -> WifiStatusResponse {
        try await get(WifiStatusResponse.self, base: base, path: "/api/wifi/status",
                      session: probe ? AtlasApiClient.probeSession : AtlasApiClient.fullSession)
    }

    static func getMyIps(base: String, probe: Bool = false) async throws -> MyIpsResponse {
        try await get(MyIpsResponse.self, base: base, path: "/api/wifi/my_ips",
                      session: probe ? AtlasApiClient.probeSession : AtlasApiClient.fullSession)
    }

    /// Triggers Atlas's hotspot to come up using its saved SSID/password.
    /// On single-radio Atlas this drops any active LAN connection — the response
    /// often never reaches the caller because the LAN dies mid-flight, so callers
    /// should ignore transport errors and proceed to wizard reset.
    static func startHotspot(base: String) async throws {
        guard let url = AtlasApiClient.makeUrl(base: base, path: "/api/hotspot/start") else { throw AtlasApiError.invalidUrl }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = "{}".data(using: .utf8)
        _ = try await AtlasApiClient.probeSession.data(for: req)
    }
}

/// Lets `JSONEncoder` handle a heterogeneous `Encodable` body without exposing
/// its concrete type to the caller.
private struct AnyEncodable: Encodable {
    private let _encode: (Encoder) throws -> Void
    init(_ wrapped: Encodable) { _encode = wrapped.encode }
    func encode(to encoder: Encoder) throws { try _encode(encoder) }
}

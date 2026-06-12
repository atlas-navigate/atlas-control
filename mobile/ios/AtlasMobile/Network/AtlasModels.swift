import Foundation

// MARK: - /api/device

struct DeviceInfo: Decodable {
    let connected: Bool
    let name: String
    let hardware: String
    let firmware: String
    let myNodeId: String?
    let batteryPct: Int?
    let batteryStatus: String?
    let batteryPhase: String?
    let batteryVoltage: Double?
    let batteryCurrentMa: Double?
    /// Fingerprint stamped by Atlas's `/api/device` — used by the LAN
    /// port-5000 sweep to confirm a probed host is actually Atlas (not a
    /// stray Flask / IoT server that happens to 200 on `/api/device`).
    let app: String?

    enum CodingKeys: String, CodingKey {
        case connected, name, hardware, firmware, app
        case myNodeId         = "my_node_id"
        case batteryPct       = "battery_pct"
        case batteryStatus    = "battery_status"
        case batteryPhase     = "battery_phase"
        case batteryVoltage   = "battery_voltage"
        case batteryCurrentMa = "battery_current_ma"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        connected         = (try? c.decode(Bool.self,    forKey: .connected))    ?? false
        name              = (try? c.decode(String.self,  forKey: .name))         ?? ""
        hardware          = (try? c.decode(String.self,  forKey: .hardware))     ?? ""
        firmware          = (try? c.decode(String.self,  forKey: .firmware))     ?? ""
        myNodeId          = try? c.decodeIfPresent(String.self, forKey: .myNodeId)
        batteryPct        = try? c.decodeIfPresent(Int.self,    forKey: .batteryPct)
        batteryStatus     = try? c.decodeIfPresent(String.self, forKey: .batteryStatus)
        batteryPhase      = try? c.decodeIfPresent(String.self, forKey: .batteryPhase)
        batteryVoltage    = try? c.decodeIfPresent(Double.self, forKey: .batteryVoltage)
        batteryCurrentMa  = try? c.decodeIfPresent(Double.self, forKey: .batteryCurrentMa)
        app               = try? c.decodeIfPresent(String.self, forKey: .app)
    }
}

// MARK: - /api/messages

struct AtlasMessage: Decodable {
    let fromId: String?
    let toId: String?
    let channel: Int?
    let text: String?
    let rxTime: Int64
    let packetId: Int64?
    let isDirect: Int

    enum CodingKeys: String, CodingKey {
        case fromId    = "from_id"
        case toId      = "to_id"
        case channel
        case text
        case rxTime    = "rx_time"
        case packetId  = "packet_id"
        case isDirect  = "is_direct"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fromId   = try? c.decodeIfPresent(String.self, forKey: .fromId)
        toId     = try? c.decodeIfPresent(String.self, forKey: .toId)
        channel  = try? c.decodeIfPresent(Int.self,    forKey: .channel)
        text     = try? c.decodeIfPresent(String.self, forKey: .text)
        rxTime   = (try? c.decode(Int64.self, forKey: .rxTime))   ?? 0
        packetId = try? c.decodeIfPresent(Int64.self, forKey: .packetId)
        isDirect = (try? c.decode(Int.self,   forKey: .isDirect)) ?? 0
    }
}

// MARK: - /api/mobile/bootstrap

struct BootstrapManifest: Decodable {
    let device: BootstrapDevice?
    let api: BootstrapApi?
    let hotspot: HotspotInfo?
    let capabilities: [String: Bool]?
    let generatedAt: Int64

    enum CodingKeys: String, CodingKey {
        case device, api, hotspot, capabilities
        case generatedAt = "generatedAt"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        device       = try? c.decodeIfPresent(BootstrapDevice.self,    forKey: .device)
        api          = try? c.decodeIfPresent(BootstrapApi.self,       forKey: .api)
        hotspot      = try? c.decodeIfPresent(HotspotInfo.self,        forKey: .hotspot)
        capabilities = try? c.decodeIfPresent([String: Bool].self,     forKey: .capabilities)
        generatedAt  = (try? c.decode(Int64.self, forKey: .generatedAt)) ?? 0
    }

    init() {
        device = nil; api = nil; hotspot = nil; capabilities = nil; generatedAt = 0
    }
}

struct BootstrapDevice: Decodable {
    let name: String
    let shortName: String

    enum CodingKeys: String, CodingKey { case name, shortName }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name      = (try? c.decode(String.self, forKey: .name))      ?? "Atlas Control"
        shortName = (try? c.decode(String.self, forKey: .shortName)) ?? "ATLS"
    }
}

struct BootstrapApi: Decodable {
    let preferredBaseUrl: String
    let baseUrls: [String]

    enum CodingKeys: String, CodingKey { case preferredBaseUrl, baseUrls }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        preferredBaseUrl = (try? c.decode(String.self,   forKey: .preferredBaseUrl)) ?? ""
        baseUrls         = (try? c.decode([String].self, forKey: .baseUrls))         ?? []
    }
}

struct HotspotInfo: Decodable {
    let active: Bool
    let ssid: String
    let password: String

    enum CodingKeys: String, CodingKey { case active, ssid, password }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        active   = (try? c.decode(Bool.self,   forKey: .active))   ?? false
        ssid     = (try? c.decode(String.self, forKey: .ssid))     ?? ""
        password = (try? c.decode(String.self, forKey: .password)) ?? ""
    }
}

// MARK: - /api/wifi/networks

struct WifiNetwork: Decodable {
    let ssid: String
    let signal: Int
    let security: String
    let inUse: Bool

    enum CodingKeys: String, CodingKey {
        case ssid, signal, security
        case inUse = "in_use"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ssid     = (try? c.decode(String.self, forKey: .ssid))     ?? ""
        signal   = (try? c.decode(Int.self,    forKey: .signal))   ?? 0
        security = (try? c.decode(String.self, forKey: .security)) ?? ""
        inUse    = (try? c.decode(Bool.self,   forKey: .inUse))    ?? false
    }
}

struct WifiNetworksResponse: Decodable {
    let networks: [WifiNetwork]
    let error: String?

    enum CodingKeys: String, CodingKey { case networks, error }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        networks = (try? c.decode([WifiNetwork].self, forKey: .networks)) ?? []
        error    = try? c.decodeIfPresent(String.self, forKey: .error)
    }
}

// MARK: - /api/wifi/connect

struct WifiConnectRequest: Encodable {
    let ssid: String
    let password: String
    let stopHotspot: Bool
    let background: Bool
}

struct WifiConnectResponse: Decodable {
    let ok: Bool
    let pending: Bool
    let error: String?
    let message: String?
    let accessUrls: [String]?
    let hintIp: String?

    enum CodingKeys: String, CodingKey { case ok, pending, error, message, accessUrls, hintIp }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok         = (try? c.decode(Bool.self, forKey: .ok))      ?? false
        pending    = (try? c.decode(Bool.self, forKey: .pending)) ?? false
        error      = try? c.decodeIfPresent(String.self,   forKey: .error)
        message    = try? c.decodeIfPresent(String.self,   forKey: .message)
        accessUrls = try? c.decodeIfPresent([String].self, forKey: .accessUrls)
        hintIp     = try? c.decodeIfPresent(String.self,   forKey: .hintIp)
    }
}

// MARK: - /api/wifi/status

struct WifiStatusResponse: Decodable {
    let accessUrls: [String]?
    let wifiSwitch: WifiSwitchState?

    enum CodingKeys: String, CodingKey { case accessUrls, wifiSwitch }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        accessUrls = try? c.decodeIfPresent([String].self,      forKey: .accessUrls)
        wifiSwitch = try? c.decodeIfPresent(WifiSwitchState.self, forKey: .wifiSwitch)
    }
}

struct WifiSwitchState: Decodable {
    let pending: Bool
    let ok: Bool?
    let result: WifiSwitchResult?

    enum CodingKeys: String, CodingKey { case pending, ok, result }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pending = (try? c.decode(Bool.self, forKey: .pending)) ?? true
        ok      = try? c.decodeIfPresent(Bool.self, forKey: .ok)
        result  = try? c.decodeIfPresent(WifiSwitchResult.self, forKey: .result)
    }
}

struct WifiSwitchResult: Decodable {
    let ip: String?
    let hintIp: String?
    let accessUrls: [String]?
}

// MARK: - /api/wifi/my_ips

struct MyIpsResponse: Decodable {
    let ips: [String]
    let urls: [String]

    enum CodingKeys: String, CodingKey { case ips, urls }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ips  = (try? c.decode([String].self, forKey: .ips))  ?? []
        urls = (try? c.decode([String].self, forKey: .urls)) ?? []
    }
}

// MARK: - BLE pairing manifest (read from the Atlas BLE characteristic)

struct AtlasBlePairing {
    let deviceName: String
    let preferredUrl: String
    let candidateUrls: [String]
    let hotspotSsid: String?
    let hotspotPassword: String?
}

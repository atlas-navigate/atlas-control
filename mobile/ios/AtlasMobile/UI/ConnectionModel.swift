import Foundation
import NetworkExtension
import SwiftUI
import UIKit

enum Screen: Equatable {
    case onboarding
    case locating(String)
    case connected(String)
    case restarting(String)
    case notFound
    case securityWarning
}

struct PairingTarget: Equatable {
    var info: AtlasInfo
    var onAtlasHotspot: Bool
}

/// Owns the connection to the one Atlas this phone is paired with — the Swift
/// counterpart of the Android ConnectionViewModel.
///
/// Atlas is remembered by device id + TLS key pin + pairing token (Keychain),
/// never by address. When the connection drops (IP change, network switch,
/// reboot, update) it is found again by id and confirmed by its key. A
/// different key for the same device id is a security warning.
@MainActor
final class ConnectionModel: ObservableObject {
    static let restartingForUpdate = "Atlas is installing an update and restarting."
    private static let updateStages: Set<String> = ["preparing", "installing", "restarting", "validating",
                                                    "rolling_back", "verifying"]

    @Published private(set) var screen: Screen = .locating("Connecting to Atlas…")
    @Published private(set) var notice: String?

    // Onboarding
    @Published private(set) var nearby: [Sighting] = []
    @Published private(set) var searching = false
    @Published private(set) var target: PairingTarget?
    @Published private(set) var pairingError: String?
    @Published private(set) var matchCode: String?
    @Published private(set) var joiningWifi: AtlasLabel?
    @Published var askLocationSharing = false

    private(set) var device: PairedDevice? = DeviceStore.load()
    private var locateTask: Task<Void, Never>?
    private var monitorTask: Task<Void, Never>?
    private var updateInProgress = false
    private var pendingUrls: [String] = []
    let tracker = AtlasPhoneTracker()

    var deviceName: String { device?.name ?? "Atlas" }

    init() {
        if device == nil {
            screen = .onboarding
            searchNearby()
        } else {
            relocate()
        }
    }

    // ── connecting ───────────────────────────────────────────────────────
    func relocate(_ message: String? = nil) {
        guard let dev = device else { screen = .onboarding; return }
        locateTask?.cancel(); monitorTask?.cancel()
        tracker.stop()
        screen = updateInProgress ? .restarting(Self.restartingForUpdate)
                                  : .locating(message ?? "Connecting to \(deviceName)…")
        locateTask = Task { [weak self] in
            var attempt = 0
            while !Task.isCancelled {
                guard let self else { return }
                for u in self.pendingUrls {
                    if await AtlasClient.confirm(u, device: dev) { self.connected(u); return }
                }
                if let url = await AtlasDiscovery.locate(dev) { self.connected(url); return }
                if await self.identityChanged(dev) { self.screen = .securityWarning; return }
                attempt += 1
                if attempt == 4 && !self.updateInProgress { self.screen = .notFound }
                try? await Task.sleep(nanoseconds: UInt64(min(2 * attempt, 15)) * 1_000_000_000)
            }
        }
    }

    private func identityChanged(_ dev: PairedDevice) async -> Bool {
        for s in await AtlasDiscovery.discover(timeout: 3) where s.deviceId == dev.deviceId {
            if let info = await AtlasClient.probe(s.baseUrl), info.deviceId == dev.deviceId, info.observedPin != dev.pin {
                return true
            }
        }
        return false
    }

    private func connected(_ url: String) {
        DeviceStore.rememberUrl(url)
        device?.lastUrl = url
        pendingUrls = []
        if updateInProgress {
            updateInProgress = false
            flash("Update complete.")
        }
        screen = .connected(url)
        startMonitor(url)
        if let dev = device { tracker.configure(baseUrl: url, device: dev) }
        if UserDefaults.standard.object(forKey: "atlas.share_location") == nil {
            askLocationSharing = true
        }
    }

    func setLocationSharing(_ on: Bool) {
        UserDefaults.standard.set(on, forKey: "atlas.share_location")
        askLocationSharing = false
        if case .connected(let url) = screen, let dev = device { tracker.configure(baseUrl: url, device: dev) }
    }

    private func flash(_ text: String) {
        notice = text
        Task { try? await Task.sleep(nanoseconds: 4_000_000_000); if notice == text { notice = nil } }
    }

    private func startMonitor(_ url: String) {
        monitorTask?.cancel()
        guard let dev = device else { return }
        monitorTask = Task { [weak self] in
            var misses = 0
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                guard let self, !Task.isCancelled else { return }
                if let stage = await AtlasClient.updateStage(url, device: dev), Self.updateStages.contains(stage) {
                    self.updateInProgress = true
                }
                switch await AtlasClient.health(url, device: dev) {
                case .up:
                    misses = 0
                    await AtlasNotifier.poll(url: url, device: dev)
                case .restarting:
                    self.screen = .restarting(self.updateInProgress ? Self.restartingForUpdate : "Atlas is restarting…")
                    await self.waitForReturn(url, dev)
                    return
                case .wrongKey:
                    self.screen = .securityWarning
                    return
                case .unreachable:
                    misses += 1
                    if misses >= 2 {
                        self.relocate(self.updateInProgress ? Self.restartingForUpdate : "Reconnecting to \(self.deviceName)…")
                        return
                    }
                }
            }
        }
    }

    private func waitForReturn(_ url: String, _ dev: PairedDevice) async {
        for _ in 0..<120 {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if Task.isCancelled { return }
            switch await AtlasClient.health(url, device: dev) {
            case .up: connected(url); return
            case .unreachable: relocate(); return
            case .wrongKey: screen = .securityWarning; return
            case .restarting: continue
            }
        }
        relocate()
    }

    // Web UI bridge (window.AtlasIOS / AtlasAndroid)
    func atlasSwitchingNetworks() {
        screen = .locating("Atlas is switching networks. If your iPhone was on Atlas's Wi-Fi, it will rejoin your usual network — this can take a minute.")
    }

    func atlasSwitchedNetworks(_ urls: [String]) {
        pendingUrls = urls.filter { $0.hasPrefix("https://") }
        relocate("Finding \(deviceName) on its new network…")
    }

    // ── onboarding ───────────────────────────────────────────────────────
    func searchNearby() {
        guard !searching else { return }
        searching = true
        Task {
            var found = await AtlasDiscovery.discover(timeout: 5)
            if !found.contains(where: { $0.baseUrl == AtlasDiscovery.hotspotUrl }),
               let info = await AtlasClient.probe(AtlasDiscovery.hotspotUrl) {
                found.append(Sighting(deviceId: info.deviceId, name: info.name, pin: info.advertisedPin,
                                      baseUrl: info.baseUrl, via: "hotspot"))
            }
            var seen = Set<String>()
            nearby = found.filter { seen.insert($0.deviceId).inserted }
            searching = false
        }
    }

    func choose(_ baseUrl: String, expectedPin: String? = nil) {
        pairingError = nil
        Task {
            guard let info = await AtlasClient.probe(baseUrl, timeout: 3) else {
                pairingError = "Couldn't reach Atlas at that address."; return
            }
            if let expectedPin, expectedPin != info.observedPin {
                pairingError = "This Atlas presented an unexpected security key. Don't connect to it."; return
            }
            let onHotspot = AtlasClient.normalize(info.baseUrl) == AtlasDiscovery.hotspotUrl
            target = PairingTarget(info: info, onAtlasHotspot: onHotspot)
            if onHotspot { pairViaHotspot() }
        }
    }

    func cancelPairing() { target = nil; matchCode = nil; pairingError = nil; joiningWifi = nil }

    func pairViaHotspot() {
        pair { t in try await AtlasClient.claimViaHotspot(t.info.baseUrl, pin: t.info.observedPin, name: Self.phoneName) }
    }

    func pairWithCode(_ code: String) {
        pair { t in try await AtlasClient.claim(t.info.baseUrl, pin: t.info.observedPin, setupCode: code, name: Self.phoneName) }
    }

    func askForApproval() {
        guard let t = target else { return }
        pairingError = nil
        Task {
            do {
                let req = try await AtlasClient.requestApproval(t.info.baseUrl, pin: t.info.observedPin, name: Self.phoneName)
                matchCode = AtlasClient.matchCode(observedPin: t.info.observedPin, serverNonce: req.serverNonce,
                                                  clientNonce: req.clientNonce)
                for _ in 0..<90 {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    guard matchCode != nil else { return }
                    let status = await AtlasClient.pollApproval(t.info.baseUrl, pin: t.info.observedPin, request: req)
                    if status.hasPrefix("approved:") { finishPairing(t, token: String(status.dropFirst(9))); return }
                    if status == "denied" { pairingError = "The request was declined."; matchCode = nil; return }
                    if status == "expired" || status == "unknown" {
                        pairingError = "The request timed out. Try again."; matchCode = nil; return
                    }
                }
            } catch {
                pairingError = error.localizedDescription
                matchCode = nil
            }
        }
    }

    /// A scanned label QR code: its pin makes the very first connection trusted.
    func pairWithLabel(_ label: AtlasLabel) {
        pairingError = nil
        Task {
            let sightings = await AtlasDiscovery.discover(timeout: 4)
            let candidates = sightings.filter { label.deviceId == nil || $0.deviceId == label.deviceId }.map(\.baseUrl)
                + [AtlasDiscovery.hotspotUrl]
            for url in Array(Set(candidates)) {
                guard let info = await AtlasClient.probe(url, timeout: 3) else { continue }
                if let pin = label.pin, pin != info.observedPin { continue }
                if let id = label.deviceId, id != info.deviceId { continue }
                target = PairingTarget(info: info, onAtlasHotspot: url == AtlasDiscovery.hotspotUrl)
                if let code = label.setupCode { pairWithCode(code) } else { pairViaHotspot() }
                return
            }
            if let ssid = label.ssid {
                joiningWifi = label
                await joinAtlasWifi(ssid: ssid, password: label.password)
                await waitForAtlasWifi(label)
            } else {
                pairingError = "Couldn't find that Atlas. Make sure it is switched on and nearby."
            }
        }
    }

    private func joinAtlasWifi(ssid: String, password: String?) async {
        let config = password.map { NEHotspotConfiguration(ssid: ssid, passphrase: $0, isWEP: false) }
            ?? NEHotspotConfiguration(ssid: ssid)
        config.joinOnce = false
        try? await NEHotspotConfigurationManager.shared.apply(config)
    }

    private func waitForAtlasWifi(_ label: AtlasLabel) async {
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard joiningWifi != nil else { return }
            guard let info = await AtlasClient.probe(AtlasDiscovery.hotspotUrl, timeout: 2) else { continue }
            if let pin = label.pin, pin != info.observedPin { continue }
            joiningWifi = nil
            target = PairingTarget(info: info, onAtlasHotspot: true)
            if let code = label.setupCode { pairWithCode(code) } else { pairViaHotspot() }
            return
        }
        joiningWifi = nil
        pairingError = "Couldn't join Atlas's Wi-Fi. Join it in Settings, then try again."
    }

    private func pair(_ block: @escaping (PairingTarget) async throws -> String) {
        guard let t = target else { return }
        pairingError = nil
        Task {
            do { finishPairing(t, token: try await block(t)) }
            catch { pairingError = error.localizedDescription }
        }
    }

    private func finishPairing(_ t: PairingTarget, token: String) {
        let dev = PairedDevice(deviceId: t.info.deviceId, name: t.info.name, pin: t.info.observedPin,
                               token: token, lastUrl: t.info.baseUrl)
        DeviceStore.save(dev)
        device = dev
        cancelPairing()
        connected(t.info.baseUrl)
    }

    func forgetDevice() {
        locateTask?.cancel(); monitorTask?.cancel()
        tracker.stop()
        DeviceStore.forget()
        device = nil
        screen = .onboarding
        searchNearby()
    }

    func appBecameActive() {
        if case .connected(let url) = screen, let dev = device { tracker.configure(baseUrl: url, device: dev) }
    }

    func appWentToBackground() { tracker.stop() }

    private static var phoneName: String { String(UIDevice.current.name.prefix(60)) }
}

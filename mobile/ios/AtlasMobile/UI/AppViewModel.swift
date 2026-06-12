import Foundation
import Combine

enum ConnectionState { case idle, checking, connected, failed }

/// SwiftUI port of the Android `AppViewModel`.
///
/// Manages Atlas connectivity across two independent networks:
///  - The Atlas Soft AP / hotspot (atlas_navigate, gateway 10.42.0.1)
///  - Any LAN that both the phone and Atlas are connected to
///
/// URLs from both sources are persisted in `UserDefaults`.  On startup and
/// after every Wi-Fi change, [probeAll] tries them in network-aware
/// priority order.
///
/// All unstructured Tasks are created with explicit `@MainActor`
/// isolation so direct access to `@Published` state is synchronous —
/// Swift 5.9's default `Task.init` does NOT inherit actor isolation
/// otherwise, which would force every state mutation through
/// `MainActor.run { ... }`.
@MainActor
final class AppViewModel: ObservableObject {

    @Published private(set) var state: ConnectionState = .idle
    @Published private(set) var baseUrl: String?
    @Published private(set) var errorMsg: String?
    @Published private(set) var isLanTransitioning = false

    let networkMonitor = AtlasNetworkMonitor()
    let phoneTracker = AtlasPhoneTracker()

    private let defaults = UserDefaults.standard
    private var probeTask: Task<Void, Never>?
    private var bgRetryTask: Task<Void, Never>?
    private var notificationPollTask: Task<Void, Never>?
    private var ssidObserver: AnyCancellable?
    private var versionObserver: AnyCancellable?
    private var lanSwitchPending = false
    private var debounceTask: Task<Void, Never>?

    private static let hotspotKey = "hotspot_urls"
    private static let lanKey     = "lan_urls"
    private static let legacyKey  = "base_url"
    private static let lastMessageKey      = "notification_last_message_key"
    private static let batteryLowKey       = "notification_battery_low_sent"
    private static let batteryChargedKey   = "notification_battery_charge_high_sent"
    private static let lanTransitionTimeout: TimeInterval = 120

    init() {
        networkMonitor.start()
        observeNetworkChanges()
        restoreAndProbe()
    }

    // MARK: - Public surface

    func applySetupResult(foundUrl: String, hotspotUrls: [String], lanUrls: [String]) {
        defaults.set(hotspotUrls.joined(separator: ","), forKey: Self.hotspotKey)
        defaults.set(lanUrls.joined(separator: ","),     forKey: Self.lanKey)
        defaults.removeObject(forKey: Self.legacyKey)
        cancelAllJobs()
        lanSwitchPending = false
        isLanTransitioning = false
        let resolved = toWebReachableUrl(foundUrl)
        baseUrl  = resolved
        state    = .connected
        errorMsg = nil
        startNotificationPolling(resolved)
        phoneTracker.updateBaseUrl(resolved)
        phoneTracker.start()
    }

    func retry() {
        bgRetryTask?.cancel()
        lanSwitchPending = false
        isLanTransitioning = false
        let hotspot = savedUrls(Self.hotspotKey)
        let lan     = savedUrls(Self.lanKey)
        let legacy  = (defaults.string(forKey: Self.legacyKey)).map { [normalize($0)] } ?? []
        let all = (hotspot + lan + legacy).removingDuplicates()
        if !all.isEmpty {
            probeAll(prioritizeByNetwork(
                hotspot: hotspot.isEmpty ? legacy : hotspot,
                lan: lan.isEmpty ? (hotspot.isEmpty ? [] : legacy) : lan
            ))
        } else if let url = baseUrl {
            probeSingle(url)
        }
    }

    func connectToManualIp(_ rawInput: String) {
        let input = rawInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { return }
        lanSwitchPending = false
        isLanTransitioning = false
        let url: String
        if input.hasPrefix("http://") || input.hasPrefix("https://") {
            url = normalize(input)
        } else if input.contains(":") && !input.hasPrefix("[") {
            url = normalize("http://\(input)")
        } else {
            url = normalize("https://\(input)")
        }
        let existing = savedUrls(Self.lanKey)
        if !existing.contains(url) {
            defaults.set(([url] + existing).joined(separator: ","), forKey: Self.lanKey)
        }
        probeSingle(url)
    }

    /// Restart the setup flow.  Acts as a "redo" — if Atlas is currently reachable
    /// over a LAN we ask it to bring its hotspot back up (single-radio Atlas drops
    /// the LAN as a side effect) and clear the saved LAN URLs so the wizard
    /// always starts from the hotspot pairing step.  The hotspot request is
    /// fire-and-forget: the connection often dies mid-response on single-radio
    /// Atlas, and Atlas's own auto-hotspot fallback (~30 s with no known LAN)
    /// covers any case where the request never lands.
    func showSetupWizard() {
        let captured = baseUrl
        cancelAllJobs()
        lanSwitchPending = false
        isLanTransitioning = false

        if let captured, !Self.isHotspotUrlString(captured) {
            Task.detached {
                _ = try? await withTimeout(seconds: 4) {
                    try await AtlasApi.startHotspot(base: captured)
                }
            }
        }

        defaults.removeObject(forKey: Self.lanKey)
        defaults.removeObject(forKey: Self.legacyKey)
        baseUrl  = nil
        state    = .idle
        errorMsg = nil
    }

    func disconnect() {
        cancelAllJobs()
        lanSwitchPending = false
        isLanTransitioning = false
        defaults.removeObject(forKey: Self.hotspotKey)
        defaults.removeObject(forKey: Self.lanKey)
        defaults.removeObject(forKey: Self.legacyKey)
        baseUrl  = nil
        state    = .idle
        errorMsg = nil
    }

    // MARK: - LAN-switch entry points (called from JS bridge)

    func onLanSwitchInitiated() {
        let captured = baseUrl
        lanSwitchPending = true
        cancelAllJobs()
        MdnsResolver.clearCache()
        isLanTransitioning = true
        state = .checking
        errorMsg = nil

        bgRetryTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let prefetch = Task.detached { [weak self] in
                await self?.prefetchAtlasBootstrapUrls(baseUrl: captured)
            }
            await self.lanTransitionLoop(hintIp: nil, isPending: true)
            prefetch.cancel()
        }
    }

    func onLanSwitchConfirmed(newUrls: [String], isPending: Bool, hintIp: String?) {
        cancelAllJobs()
        lanSwitchPending = false
        MdnsResolver.clearCache()

        let normalized = newUrls.map { normalize($0) }.filter { !$0.isEmpty }
        if !normalized.isEmpty {
            let existing = savedUrls(Self.lanKey)
            let merged = (normalized + existing).removingDuplicates()
            defaults.set(merged.joined(separator: ","), forKey: Self.lanKey)
        }

        if !isPending {
            isLanTransitioning = false
            let hot = savedUrls(Self.hotspotKey)
            let lan = savedUrls(Self.lanKey)
            probeAll((lan + hot).removingDuplicates())
        } else {
            bgRetryTask = Task { @MainActor [weak self] in
                await self?.lanTransitionLoop(hintIp: hintIp, isPending: true)
            }
        }
    }

    // MARK: - Pre-fetch

    /// Tries the lightweight `/api/wifi/my_ips` first, then falls back to
    /// `/api/mobile/bootstrap` — both populate `lan_urls` so the
    /// transition loop's S1 fast-probe pass finds Atlas in under a second
    /// on dual-radio installs.  Runs while the hotspot is still alive so
    /// short timeouts are OK.
    private func prefetchAtlasBootstrapUrls(baseUrl: String?) async {
        guard let baseUrl, !baseUrl.isEmpty else { return }

        var collected: [String] = []
        if let resp = try? await withTimeout(seconds: 3, operation: {
            try await AtlasApi.getMyIps(base: baseUrl, probe: true)
        }) {
            collected.append(contentsOf: resp.urls)
        }
        if collected.isEmpty,
           let manifest = try? await withTimeout(seconds: 4, operation: {
               try await AtlasApi.getBootstrap(base: baseUrl, probe: true)
           }),
           let urls = manifest.api?.baseUrls {
            collected.append(contentsOf: urls)
        }

        let cleaned = collected
            .map { AtlasApiClient.normalize($0) }
            .filter { !$0.contains("10.42.0.1") && !$0.contains("atlas_navigate") }
            .removingDuplicates()
        guard !cleaned.isEmpty else { return }

        let existing = savedUrls(Self.lanKey)
        let merged = (cleaned + existing).removingDuplicates()
        defaults.set(merged.joined(separator: ","), forKey: Self.lanKey)
    }

    // MARK: - LAN transition loop

    /// Concurrent discovery loop for the hotspot → LAN transition.
    /// See Android `AppViewModel.lanTransitionLoop` for the rationale
    /// behind each strategy. Strategies (UDP beacon is primary, port-5000
    /// sweep is the deterministic fallback; mDNS / Bonjour is supplementary):
    ///   • S1  — direct probe
    ///   • S2  — UDP beacon shout-and-receive (PRIMARY)
    ///   • S2b — port-5000 fingerprint sweep
    ///   • S3  — targeted /24 sweep (with hint)
    ///   • S4  — /api/wifi/status polling
    ///   • S5  — Bonjour as a 5 s supplementary check
    private func lanTransitionLoop(hintIp: String?, isPending: Bool) async {
        let deadline = Date().addingTimeInterval(Self.lanTransitionTimeout)

        // A non-blank hint IP that points at the hotspot range is junk during a
        // pending transition — refuse it before it pollutes the targeted /24
        // scan or the direct probe.
        let effectiveHint: String? = {
            guard let hintIp, !hintIp.isEmpty else { return nil }
            if isPending && Self.isHotspotHost(hintIp) { return nil }
            return hintIp
        }()

        while Date() < deadline {
            if Task.isCancelled { return }
            MdnsResolver.clearCache()
            let lan = savedUrls(Self.lanKey)
            let hotspot = savedUrls(Self.hotspotKey)
            let gateway = networkMonitor.gatewayUrl()

            var directCandidates: [String] = []
            if let effectiveHint {
                directCandidates.append(normalize("https://\(effectiveHint)"))
                directCandidates.append(normalize("http://\(effectiveHint):5000"))
            }
            if let gateway { directCandidates.append(gateway) }
            directCandidates.append(contentsOf: lan)
            if !isPending { directCandidates.append(contentsOf: hotspot) }
            directCandidates.append("https://atlas.local")
            directCandidates.append("http://atlas.local:5000")
            directCandidates = directCandidates.removingDuplicates()
            let directForBackground = directCandidates    // immutable copy for child tasks
            let pendingFlag = isPending

            // Reject any URL that resolves to a hotspot host while pending — it
            // would otherwise win the task group, cancel every other strategy,
            // and force an empty iteration.
            @Sendable func sift(_ url: String?) -> String? {
                guard let url, !url.isEmpty else { return nil }
                return (pendingFlag && Self.isHotspotUrlString(url)) ? nil : url
            }

            let rawFound = await withTaskGroup(of: String?.self, returning: String?.self) { group in
                // S1 — direct probe
                group.addTask { sift(await Self.probeUrlsFast(directForBackground)) }
                // S2 (PRIMARY) — UDP beacon shout-and-receive. Atlas beacons
                // at 1 Hz for 90 s after /api/wifi/connect, so in-app LAN
                // switches are found within ~1 s of Atlas rebinding — same
                // strategy the setup wizard already uses. The reply is
                // re-verified over TCP (/api/device) before it can win.
                group.addTask {
                    guard let hit = await AtlasBeacon.discover(timeout: 14, excludeHotspot: pendingFlag) else { return nil }
                    return sift(await Self.probeUrlsFast([hit]))
                }
                // S2b — port-5000 fingerprint sweep across every candidate
                // /24, hotspot prefixes excluded while pending. Bounded so
                // each iteration takes ~20 s and the loop can retry many
                // times within the 120 s deadline. Deterministic fallback
                // for older Atlas firmware without the beacon.
                group.addTask {
                    let sweep: String? = (try? await withTimeout(seconds: 20) {
                        await AtlasDiscovery.findAtlasByPortSweep(excludeHotspot: pendingFlag)
                    }) ?? nil
                    return sift(sweep)
                }
                // S3 — targeted /24 sweep (uses effective hint, hotspot-
                //      excluded while pending).
                if let effectiveHint {
                    group.addTask {
                        let r: String? = (try? await withTimeout(seconds: 15) {
                            await AtlasDiscovery.findAtlasByTargetSubnet(hintIp: effectiveHint, excludeHotspot: pendingFlag)
                        }) ?? nil
                        return sift(r)
                    }
                }
                // S4 — wifi-status polling
                group.addTask { sift(await Self.pollWifiSwitchStatus(directForBackground)) }
                // S5 — Bonjour / DNS-SD as a supplementary 5 s check only.
                //      Single-radio Atlas's avahi re-announce on a brand-
                //      new LAN is unreliable, so we never gate on it.
                group.addTask { sift(await AtlasDiscovery.findAtlasOnLan(timeout: 5)) }

                for await result in group {
                    if let result {
                        group.cancelAll()
                        return result
                    }
                }
                return nil
            }

            // Defence-in-depth — sift() should already have filtered, but if a
            // future change relaxes the in-task check we still refuse hotspot
            // URLs as the loop's final winner.
            let found = (rawFound != nil && isPending && Self.isHotspotUrlString(rawFound!)) ? nil : rawFound
            if let found {
                persistDiscoveredUrl(found)
                let resolved = toWebReachableUrl(found)
                // The switch is over — without this, handleNetworkChange would
                // swallow the NEXT genuine Wi-Fi change (the flag is normally
                // cleared by onLanSwitchConfirmed, but on single-radio Atlas the
                // confirm response usually dies with the hotspot).
                lanSwitchPending = false
                isLanTransitioning = false
                baseUrl = resolved
                state   = .connected
                errorMsg = nil
                startNotificationPolling(resolved)
                phoneTracker.updateBaseUrl(resolved)
                phoneTracker.start()
                return
            }

            if Task.isCancelled { return }
            let remaining = deadline.timeIntervalSinceNow
            if remaining > 3 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }

        lanSwitchPending = false
        isLanTransitioning = false
        errorMsg = "Could not find Atlas on the new network. Tap retry or enter the Atlas IP manually."
        state = .failed
        startBackgroundRetry()
    }

    nonisolated private static func pollWifiSwitchStatus(_ candidates: [String]) async -> String? {
        let deadline = Date().addingTimeInterval(45)
        while Date() < deadline {
            if Task.isCancelled { return nil }
            for url in candidates {
                if Task.isCancelled { return nil }
                let status = try? await withTimeout(seconds: 2) {
                    try await AtlasApi.getWifiStatus(base: url, probe: true)
                }
                guard let switchState = status?.wifiSwitch, !switchState.pending else { continue }

                var confirmed: [String] = []
                if let ip = switchState.result?.ip, !ip.isEmpty {
                    confirmed.append(AtlasApiClient.normalize("https://\(ip)"))
                    confirmed.append(AtlasApiClient.normalize("http://\(ip):5000"))
                }
                if let hintIp = switchState.result?.hintIp, !hintIp.isEmpty {
                    confirmed.append(AtlasApiClient.normalize("https://\(hintIp)"))
                    confirmed.append(AtlasApiClient.normalize("http://\(hintIp):5000"))
                }
                if let urls = switchState.result?.accessUrls {
                    confirmed.append(contentsOf: urls.map { AtlasApiClient.normalize($0) })
                }
                confirmed.append(AtlasApiClient.normalize(url))
                // Filter hotspot URLs — `result.ip` should always be the new
                // LAN address, but we never want to advertise the dying
                // hotspot as the winner if the backend ever emits one here.
                let unique = confirmed
                    .removingDuplicates()
                    .filter { !$0.isEmpty && !Self.isHotspotUrlString($0) }
                if let found = await probeUrlsFast(unique) { return found }
            }
            if Task.isCancelled { return nil }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
        return nil
    }

    // MARK: - Probing

    private func probeSingle(_ url: String) {
        cancelAllJobs()
        state = .checking
        probeTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let success = (try? await withTimeout(seconds: 8, operation: {
                _ = try await AtlasApi.getDevice(base: url, probe: true)
                return true
            })) ?? false
            if success {
                let resolved = self.toWebReachableUrl(url)
                self.baseUrl = resolved
                self.state = .connected
                self.errorMsg = nil
                self.startNotificationPolling(resolved)
                self.phoneTracker.updateBaseUrl(resolved)
                self.phoneTracker.start()
            } else {
                self.errorMsg = "Connection timed out"
                self.state = .failed
                self.startBackgroundRetry()
            }
        }
    }

    private func probeAll(_ urls: [String]) {
        cancelAllJobs()
        state = .checking
        probeTask = Task { @MainActor [weak self] in
            guard let self else { return }
            var withGateway: [String] = []
            if let gw = self.networkMonitor.gatewayUrl() { withGateway.append(gw) }
            withGateway.append(contentsOf: urls)
            withGateway.append("https://atlas.local")
            withGateway.append("http://atlas.local:5000")
            let candidates = withGateway.removingDuplicates()
            let found = await Self.discoverAtlasFast(candidates: candidates)
            if let found {
                self.persistDiscoveredUrl(found)
                let resolved = self.toWebReachableUrl(found)
                self.baseUrl = resolved
                self.state = .connected
                self.errorMsg = nil
                self.startNotificationPolling(resolved)
                self.phoneTracker.updateBaseUrl(resolved)
                self.phoneTracker.start()
            } else {
                self.errorMsg = "Could not reach Atlas — join the atlas_navigate hotspot or confirm Atlas is on your LAN."
                self.state = .failed
                self.startBackgroundRetry()
            }
        }
    }

    nonisolated private static func discoverAtlasFast(candidates: [String]) async -> String? {
        await withTaskGroup(of: String?.self, returning: String?.self) { group in
            // Direct-probe known URLs first (fast path on familiar LANs).
            group.addTask { await Self.probeUrlsFast(candidates) }
            // UDP beacon — answers Atlas's shout-and-receive responder even
            // outside the post-switch broadcast window; re-verified over TCP.
            group.addTask {
                guard let hit = await AtlasBeacon.discover(timeout: 4) else { return nil }
                return await Self.probeUrlsFast([hit])
            }
            // Port-5000 fingerprint sweep — deterministic fallback.
            group.addTask {
                (try? await withTimeout(seconds: 20) {
                    await AtlasDiscovery.findAtlasByPortSweep()
                }) ?? nil
            }
            // mDNS as a 5 s supplementary check; never the gate.
            group.addTask { await AtlasDiscovery.findAtlasOnLan(timeout: 5) }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
            }
            return nil
        }
    }

    /// Fast-path probe — short timeouts, a **continuous** 16-task sliding
    /// window (matching Android's `Semaphore(16)` in `AppViewModel.probeUrlsFast`),
    /// returns the first URL to answer `/api/device` successfully and cancels
    /// the rest. Unlike a fixed-chunk drain, one slow URL never stalls the
    /// others: a freed slot is refilled from the next URL immediately.
    nonisolated static func probeUrlsFast(_ urls: [String]) async -> String? {
        guard !urls.isEmpty else { return nil }
        let maxInFlight = 16
        return await withTaskGroup(of: String?.self, returning: String?.self) { group in
            var iter = urls.makeIterator()
            var inFlight = 0
            while inFlight < maxInFlight, let url = iter.next() {
                group.addTask { await probeOneFast(url) }
                inFlight += 1
            }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
                if let next = iter.next() {
                    group.addTask { await probeOneFast(next) }
                }
            }
            return nil
        }
    }

    /// One `/api/device` probe with the `.local` → 2 s / else 1.5 s per-URL
    /// timeout used by [probeUrlsFast].
    nonisolated private static func probeOneFast(_ url: String) async -> String? {
        let timeout: TimeInterval = url.contains(".local") ? 2 : 1.5
        do {
            return try await withTimeout(seconds: timeout) {
                _ = try await AtlasApi.getDevice(base: url, probe: true)
                return url
            }
        } catch {
            return nil
        }
    }

    // MARK: - Background retry

    private func startBackgroundRetry() {
        bgRetryTask?.cancel()
        bgRetryTask = Task { @MainActor [weak self] in
            let delays: [UInt64] = [5_000_000_000, 10_000_000_000, 20_000_000_000, 30_000_000_000]
            var attempt = 0
            while self?.state == .failed {
                let nanos = delays[min(attempt, delays.count - 1)]
                try? await Task.sleep(nanoseconds: nanos)
                attempt += 1
                if Task.isCancelled { return }
                guard let self else { return }
                guard self.state == .failed else { break }

                let hot = self.savedUrls(Self.hotspotKey)
                let lan = self.savedUrls(Self.lanKey)
                let legacy = (self.defaults.string(forKey: Self.legacyKey)).map { [self.normalize($0)] } ?? []
                let ordered = self.prioritizeByNetwork(
                    hotspot: hot.isEmpty ? legacy : hot,
                    lan: lan.isEmpty ? (hot.isEmpty ? [] : legacy) : lan
                )
                var candidates: [String] = []
                if let gw = self.networkMonitor.gatewayUrl() { candidates.append(gw) }
                candidates.append(contentsOf: ordered)
                candidates.append("https://atlas.local")
                candidates.append("http://atlas.local:5000")
                let unique = candidates.removingDuplicates()
                if let found = await Self.discoverAtlasFast(candidates: unique),
                   self.state == .failed {
                    self.persistDiscoveredUrl(found)
                    let resolved = self.toWebReachableUrl(found)
                    self.baseUrl = resolved
                    self.state = .connected
                    self.errorMsg = nil
                    self.startNotificationPolling(resolved)
                    self.phoneTracker.updateBaseUrl(resolved)
                    self.phoneTracker.start()
                    break
                }
            }
        }
    }

    // MARK: - Network change handling

    private func observeNetworkChanges() {
        ssidObserver = networkMonitor.$ssid.dropFirst().sink { [weak self] _ in
            self?.scheduleNetworkChangeReprobe()
        }
        versionObserver = networkMonitor.$version.dropFirst().sink { [weak self] _ in
            self?.scheduleNetworkChangeReprobe()
        }
    }

    private func scheduleNetworkChangeReprobe() {
        debounceTask?.cancel()
        debounceTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled else { return }
            self?.handleNetworkChange()
        }
    }

    private func handleNetworkChange() {
        MdnsResolver.clearCache()
        let wasLanSwitch = lanSwitchPending
        lanSwitchPending = false
        switch state {
        case _ where wasLanSwitch:
            return
        case .connected:
            networkChangeRetry()
        case .failed:
            retry()
        default:
            return
        }
    }

    private func networkChangeRetry() {
        cancelAllJobs()
        probeTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let current = self.baseUrl
            if let current {
                let stillAlive = (try? await withTimeout(seconds: 2) {
                    _ = try await AtlasApi.getDevice(base: current, probe: true)
                    return true
                }) ?? false
                if stillAlive {
                    let resolved = self.toWebReachableUrl(current)
                    if resolved != current { self.baseUrl = resolved }
                    self.startNotificationPolling(resolved)
                    return
                }
            }

            let hot = self.savedUrls(Self.hotspotKey)
            let lan = self.savedUrls(Self.lanKey)
            let legacy = (self.defaults.string(forKey: Self.legacyKey)).map { [self.normalize($0)] } ?? []
            let ordered = self.prioritizeByNetwork(
                hotspot: hot.isEmpty ? legacy : hot,
                lan: lan.isEmpty ? (hot.isEmpty ? [] : legacy) : lan
            )
            var candidates: [String] = []
            if let gw = self.networkMonitor.gatewayUrl() { candidates.append(gw) }
            candidates.append(contentsOf: ordered)
            candidates.append("https://atlas.local")
            candidates.append("http://atlas.local:5000")
            let unique = candidates.removingDuplicates()

            if let found = await Self.discoverAtlasFast(candidates: unique) {
                self.persistDiscoveredUrl(found)
                let resolved = self.toWebReachableUrl(found)
                self.baseUrl = resolved
                self.state   = .connected
                self.errorMsg = nil
                self.startNotificationPolling(resolved)
                self.phoneTracker.updateBaseUrl(resolved)
                self.phoneTracker.start()
            } else {
                self.errorMsg = "Lost connection to Atlas — join the atlas_navigate hotspot or confirm Atlas is on your LAN."
                self.state = .failed
                self.startBackgroundRetry()
            }
        }
    }

    // MARK: - Notification polling

    private func startNotificationPolling(_ baseUrl: String) {
        notificationPollTask?.cancel()
        notificationPollTask = Task { @MainActor [weak self] in
            guard let self else { return }
            // A single failed poll must NOT tear the connection down: a
            // momentary stall (or an nginx 429/503 while the WebView is
            // hammering tiles) used to cascade into networkChangeRetry and
            // a spurious "can't connect" seconds after a successful connect.
            // Only consecutive *transport* failures mean Atlas is gone.
            var consecutiveFailures = 0
            while self.state == .connected {
                if Task.isCancelled { return }
                switch await self.pollNotifications(base: baseUrl) {
                case .ok, .serverAlive:
                    consecutiveFailures = 0
                case .unreachable:
                    consecutiveFailures += 1
                    if consecutiveFailures >= 3 {
                        MdnsResolver.clearCache()
                        self.networkChangeRetry()
                        return
                    }
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    private enum PollOutcome { case ok, serverAlive, unreachable }

    private func pollNotifications(base: String) async -> PollOutcome {
        do {
            let device   = try await AtlasApi.getDevice(base: base)
            let messages = (try? await AtlasApi.getMessages(base: base)) ?? []
            maybeNotifyForNewMessage(messages: messages, device: device)
            maybeNotifyForBattery(device)
            return .ok
        } catch let err as AtlasApiError {
            // An HTTP status (rate limit, transient 5xx) is still a response
            // FROM Atlas — the box is reachable, just busy. Never count it
            // toward teardown.
            if case .http = err { return .serverAlive }
            return .unreachable
        } catch {
            return .unreachable
        }
    }

    private func maybeNotifyForNewMessage(messages: [AtlasMessage], device: DeviceInfo) {
        guard let latest = messages.first else { return }
        let latestKey = messageKey(latest)
        let stored = defaults.string(forKey: Self.lastMessageKey)
        if stored == nil {
            defaults.set(latestKey, forKey: Self.lastMessageKey)
            return
        }
        if latestKey == stored { return }
        defaults.set(latestKey, forKey: Self.lastMessageKey)
        if let from = latest.fromId, from == device.myNodeId { return }
        let title  = latest.isDirect != 0 ? "New direct message" : "New Atlas message"
        let sender = (latest.fromId?.isEmpty == false) ? latest.fromId! : "Unknown sender"
        let text   = (latest.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let body   = text.isEmpty ? "Message received from \(sender)." : "\(sender): \(text)"
        AtlasNotificationManager.showMessage(title: title, body: body)
    }

    private func maybeNotifyForBattery(_ device: DeviceInfo) {
        guard let pct = device.batteryPct else { return }
        let charging = isCharging(device)
        let lowSent       = defaults.bool(forKey: Self.batteryLowKey)
        let chargedSent   = defaults.bool(forKey: Self.batteryChargedKey)

        if pct <= 20 {
            if !lowSent { AtlasNotificationManager.showBatteryLow(percent: pct) }
            defaults.set(true, forKey: Self.batteryLowKey)
        } else if lowSent {
            defaults.set(false, forKey: Self.batteryLowKey)
        }

        if charging && pct >= 90 {
            if !chargedSent { AtlasNotificationManager.showBatteryCharged(percent: pct) }
            defaults.set(true, forKey: Self.batteryChargedKey)
        } else if chargedSent && (!charging || pct < 90) {
            defaults.set(false, forKey: Self.batteryChargedKey)
        }
    }

    private func isCharging(_ device: DeviceInfo) -> Bool {
        let phase  = device.batteryPhase?.lowercased()
        let status = device.batteryStatus?.lowercased()
        return phase == "charging" || status == "charging"
    }

    private func messageKey(_ message: AtlasMessage) -> String {
        if let id = message.packetId { return String(id) }
        return "\(message.rxTime):\(message.fromId ?? "?"):\(message.text ?? "")"
    }

    // MARK: - Helpers

    private func cancelAllJobs() {
        probeTask?.cancel(); probeTask = nil
        bgRetryTask?.cancel(); bgRetryTask = nil
        notificationPollTask?.cancel(); notificationPollTask = nil
    }

    private func restoreAndProbe() {
        let hot = savedUrls(Self.hotspotKey)
        let lan = savedUrls(Self.lanKey)
        let legacy = (defaults.string(forKey: Self.legacyKey)).map { [normalize($0)] } ?? []
        let all = (hot + lan + legacy).removingDuplicates()
        guard !all.isEmpty else { return }
        probeAll(prioritizeByNetwork(
            hotspot: hot.isEmpty ? legacy : hot,
            lan: lan.isEmpty ? (hot.isEmpty ? [] : legacy) : lan
        ))
    }

    private func savedUrls(_ key: String) -> [String] {
        guard let raw = defaults.string(forKey: key) else { return [] }
        return raw.split(separator: ",").map(String.init).filter { !$0.isEmpty }
    }

    private func prioritizeByNetwork(hotspot: [String], lan: [String]) -> [String] {
        if networkMonitor.isOnAtlasHotspot {
            return (hotspot + lan).removingDuplicates()
        } else {
            return (lan + hotspot).removingDuplicates()
        }
    }

    private func persistDiscoveredUrl(_ url: String) {
        let lan = savedUrls(Self.lanKey)
        let hot = savedUrls(Self.hotspotKey)
        var toAdd: [String] = []
        if !lan.contains(url) && !hot.contains(url) { toAdd.append(url) }
        let webUrl = toWebReachableUrl(url)
        if webUrl != url && !lan.contains(webUrl) && !hot.contains(webUrl) { toAdd.append(webUrl) }
        if !toAdd.isEmpty {
            let merged = (lan + toAdd).removingDuplicates()
            defaults.set(merged.joined(separator: ","), forKey: Self.lanKey)
        }
    }

    private func normalize(_ url: String) -> String { AtlasApiClient.normalize(url) }

    /// True if [url] resolves to one of Atlas's hotspot subnets.
    nonisolated static func isHotspotUrlString(_ url: String) -> Bool {
        url.contains("10.42.0.")   ||
        url.contains("192.168.4.") ||
        url.contains("192.168.43.")
    }

    /// True if [host] (a bare IPv4 string) lives in a hotspot prefix.
    nonisolated static func isHotspotHost(_ host: String) -> Bool {
        let parts = host.split(separator: ".").map(String.init)
        guard parts.count >= 3 else { return false }
        let pfx = parts[0..<3].joined(separator: ".")
        return pfx == "10.42.0" || pfx == "192.168.4" || pfx == "192.168.43"
    }

    /// iOS WKWebView CAN resolve `.local` hostnames via Bonjour, unlike
    /// Android's WebView, so this normally returns the URL unchanged.  We
    /// still try a manual mDNS swap when an IP is available — it makes the
    /// initial page load deterministic during the first hotspot → LAN
    /// handoff while iOS's `mdnsd` cache is still warming up.
    func toWebReachableUrl(_ url: String) -> String {
        let normalized = normalize(url)
        guard let parsed = URL(string: normalized),
              let host   = parsed.host?.lowercased(),
              host.hasSuffix(".local") else { return normalized }
        guard let ip = MdnsResolver.resolve(host) else { return normalized }
        let port = (parsed.port.map { ":\($0)" }) ?? ""
        let scheme = parsed.scheme ?? "https"
        return "\(scheme)://\(ip)\(port)/"
    }
}

// MARK: - Helpers shared with view models

extension Array where Element: Hashable {
    func removingDuplicates() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}

/// Throws `AtlasApiError.timeout` if [operation] doesn't complete within
/// [seconds].  Used everywhere a callsite needs a hard deadline.
func withTimeout<T: Sendable>(
    seconds: TimeInterval,
    operation: @escaping @Sendable () async throws -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await operation() }
        group.addTask {
            try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            throw AtlasApiError.timeout
        }
        let result = try await group.next()!
        group.cancelAll()
        return result
    }
}

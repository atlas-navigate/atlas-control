import Foundation
import Combine
import NetworkExtension

/// Steps mirror the Android `SetupStep` enum exactly.
enum SetupStep {
    /// Welcome / feature overview.
    case welcome
    /// User is instructed to join the atlas_navigate hotspot; app polls in background.
    case hotspotConnect
    /// Atlas answered from the hotspot; show device info, let user open the app.
    case pairing
    /// User opted to connect Atlas to a LAN while the hotspot stays active.
    case lanProvision
    /// Setup complete — `AppViewModel` takes over.
    case done
}

/// Confirmed reachable Atlas URL + bootstrap manifest pulled from the device.
struct AtlasDiscoveryResult {
    let foundUrl: String
    let manifest: BootstrapManifest
}

/// SwiftUI port of the Android `SetupViewModel`.  Drives the on-boarding
/// wizard from welcome → hotspot search → pairing → optional LAN
/// provisioning, then hands the persistent URL set to `AppViewModel`.
@MainActor
final class SetupViewModel: ObservableObject {

    @Published var step: SetupStep = .welcome
    @Published var isSearching = false
    @Published var errorMsg: String?
    @Published var manualUrl: String = ""
    @Published private(set) var discovery: AtlasDiscoveryResult?

    @Published var hotspotSsid: String
    @Published var hotspotPassword: String

    @Published var lanConnecting = false
    @Published var lanConnectDone = false
    @Published var lanConnectError: String?
    @Published var lanConnectSsid: String = ""
    @Published var lanHandoffPending = false

    private let defaults = UserDefaults.standard
    private var searchTask: Task<Void, Never>?
    private var hotspotDiscovery: AtlasDiscoveryResult?
    private var lanDiscoveredUrl: String?

    private static let hotspotSsidKey     = "hotspot_ssid"
    private static let hotspotPasswordKey = "hotspot_password"

    init() {
        hotspotSsid     = UserDefaults.standard.string(forKey: Self.hotspotSsidKey)     ?? "atlas_navigate"
        hotspotPassword = UserDefaults.standard.string(forKey: Self.hotspotPasswordKey) ?? "password"
    }

    // MARK: - Navigation

    func goToHotspotStep() { step = .hotspotConnect }

    /// Full wizard reset.  This view model outlives the wizard (it's an
    /// app-lifetime `@StateObject`), so every piece of discovery and
    /// LAN-provision state must be cleared here — a stale `discovery` or
    /// `lanConnectDone` would otherwise reopen the wizard on an old step
    /// pointing at a dead URL.
    func resetForReconnect() {
        searchTask?.cancel()
        isSearching = false
        discovery = nil
        hotspotDiscovery = nil
        lanDiscoveredUrl = nil
        errorMsg = nil
        lanConnecting = false
        lanConnectDone = false
        lanConnectError = nil
        lanHandoffPending = false
        step = .hotspotConnect
    }

    // MARK: - Hotspot search

    /// Background loop that probes Atlas's hotspot gateway, the device's
    /// own gateway, and `atlas.local`.  Advances to `.pairing` on first
    /// success.  Safe to call multiple times; cancels any running search.
    func startHotspotSearch() {
        searchTask?.cancel()
        isSearching = true
        errorMsg = nil

        searchTask = Task { [weak self] in
            guard let self else { return }
            let result = await self.searchForAtlas()
            await MainActor.run {
                self.isSearching = false
                if let result {
                    self.discovery = result
                    self.hotspotDiscovery = result
                    self.step = .pairing
                } else {
                    self.errorMsg =
                        "Atlas not found.\n\n" +
                        "Make sure your phone is connected to the \"\(self.hotspotSsid)\" Wi-Fi network, then tap Search again."
                }
            }
        }
    }

    func stopSearch() {
        searchTask?.cancel()
        isSearching = false
    }

    func retrySearch() {
        errorMsg = nil
        startHotspotSearch()
    }

    /// Manual address entry — accepts bare host, `host:port`, or full URL.
    func connectManual(onFoundInWizard: @escaping () -> Void) {
        let raw = manualUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        let target = raw.isEmpty ? "atlas.local" : raw
        searchTask?.cancel()
        isSearching = true
        errorMsg = nil

        searchTask = Task { [weak self] in
            guard let self else { return }
            var candidates: [String] = []
            if target.hasPrefix("http://") || target.hasPrefix("https://") {
                candidates.append(target)
            } else {
                candidates.append("https://\(target)")
                candidates.append("http://\(target):5000")
                candidates.append("http://\(target)")
            }
            candidates.append(contentsOf: self.hotspotCandidates())
            candidates.append("https://atlas.local")
            candidates.append("http://atlas.local:5000")
            let unique = candidates.removingDuplicates()

            let found = await self.tryEachUrl(unique)
            await MainActor.run {
                self.isSearching = false
                if let found {
                    self.discovery = found
                    self.hotspotDiscovery = found
                    self.step = .pairing
                    onFoundInWizard()
                } else {
                    self.errorMsg =
                        "Could not reach Atlas at \"\(target)\".\n" +
                        "Join the \(self.hotspotSsid) hotspot first, then try again."
                }
            }
        }
    }

    // MARK: - LAN provisioning

    func goToLanProvision() {
        lanConnectDone = false
        lanConnectError = nil
        lanHandoffPending = false
        lanDiscoveredUrl = nil
        step = .lanProvision
    }

    /// Tells Atlas (over the hotspot link we already have) to join [ssid].
    /// Atlas's single Wi-Fi radio means it must drop the hotspot to do this;
    /// the backend returns `{pending:true}` immediately and switches in the
    /// background.
    ///
    /// Discovery runs concurrent strategies (mirroring
    /// `AppViewModel.lanTransitionLoop`) so the wizard reconnects no matter
    /// which IP the new LAN's DHCP assigns:
    ///   • Pre-fetch LAN IPs via `/api/wifi/my_ips` while the hotspot is alive
    ///   • Direct probe of hint + access URLs from the connect response
    ///   • Port-5000 fingerprint sweep across every common /24 (PRIMARY)
    ///   • Targeted /24 sweep of the hint IP's subnet
    ///   • Periodic `/api/wifi/status` polling so the confirmed final IP is
    ///     used once Atlas reports its switch is complete
    ///   • Bonjour / mDNS as a short supplementary check only
    func connectToLan(ssid: String, password: String) {
        guard let disc = discovery else { return }
        lanConnecting = true
        lanConnectError = nil
        lanConnectDone = false
        lanHandoffPending = false
        lanConnectSsid = ssid

        searchTask?.cancel()
        let baseUrl = disc.foundUrl
        searchTask = Task { [weak self] in
            guard let self else { return }
            // Kick off the bootstrap pre-fetch immediately so any LAN IPs Atlas
            // already knows about (dual-radio installs) are saved BEFORE the
            // hotspot drops.  Cancelled once the connect response arrives.
            let prefetch = Task { [weak self] in
                await self?.prefetchAtlasIpsBeforeSwitch(baseUrl: baseUrl)
            }

            // Send /api/wifi/connect with a tight 4 s deadline.  On single-radio
            // Atlas the hotspot can drop between the SYN being accepted and
            // Flask flushing the response, so a transport failure here does NOT
            // mean the request was lost — it almost always reached Atlas, which
            // is now running nmcli to join the new LAN.  We treat nil/timeout
            // the same as "pending" and start the discovery loop unconditionally.
            let resp = await self.sendConnectRequest(baseUrl: baseUrl, ssid: ssid, password: password)
            prefetch.cancel()

            // Only surface an error when Atlas explicitly rejected the request
            // (bad password, missing ssid, etc).  Discovery cannot rescue those.
            if let resp, !resp.pending, !resp.ok {
                await MainActor.run {
                    self.lanConnecting = false
                    self.lanConnectError = resp.error ?? "Could not connect to \(ssid)"
                }
                return
            }

            // Either Atlas accepted (pending/ok) or the POST failed at the
            // transport layer.  Both paths run discovery — the former because
            // we need to find Atlas's new IP, the latter because Atlas almost
            // certainly received the request before the hotspot dropped.
            if let resp {
                await self.seedLanCandidatesFromConnectResponse(hintIp: resp.hintIp, accessUrls: resp.accessUrls)
            }
            await MainActor.run { self.lanHandoffPending = true }
            await self.runLanDiscoveryLoop(
                ssid: ssid,
                hintIp: resp?.hintIp,
                responseAccessUrls: resp?.accessUrls ?? []
            )
        }
    }

    /// Issues POST `/api/wifi/connect` with a 4 s outer deadline.  Returns
    /// the decoded response or `nil` if the call timed out / errored at the
    /// transport layer.  Callers must treat `nil` as "Atlas may have received
    /// the request" and proceed to discovery — see `connectToLan` for the
    /// rationale.
    private func sendConnectRequest(
        baseUrl: String,
        ssid: String,
        password: String
    ) async -> WifiConnectResponse? {
        try? await withTimeout(seconds: 4) {
            try await AtlasApi.connectWifi(
                base: baseUrl,
                request: WifiConnectRequest(
                    ssid: ssid,
                    password: password,
                    stopHotspot: true,
                    background: true
                )
            )
        }
    }

    /// Pre-fetches Atlas's currently bound IPs while the hotspot is still
    /// alive.  On dual-radio installs the LAN IP is saved before the hotspot
    /// drops so the direct-probe pass of `runLanDiscoveryLoop` hits Atlas in
    /// well under a second.  On single-radio installs the call returns just
    /// the hotspot IP, which we filter out — the loop then falls back to
    /// mDNS / subnet scan.
    private func prefetchAtlasIpsBeforeSwitch(baseUrl: String) async {
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
        let cleaned = collected.filter { url in
            !url.contains("10.42.0.1") &&
            !url.contains("192.168.4.1") &&
            !url.contains("192.168.43.1") &&
            !url.contains("atlas_navigate")
        }
        if !cleaned.isEmpty {
            await mergeLanCandidateUrls(cleaned)
        }
    }

    /// Records hint IP + access URLs returned by `/api/wifi/connect` so
    /// subsequent direct-probe passes can hit them immediately.
    private func seedLanCandidatesFromConnectResponse(hintIp: String?, accessUrls: [String]?) async {
        var urls: [String] = []
        if let hintIp, !hintIp.isEmpty {
            urls.append("https://\(hintIp)")
            urls.append("http://\(hintIp):5000")
        }
        if let accessUrls { urls.append(contentsOf: accessUrls) }
        if !urls.isEmpty { await mergeLanCandidateUrls(urls) }
    }

    /// Discovery loop run after Atlas accepts the LAN switch request.
    /// Loops every 3 s for up to 3 minutes, running every strategy
    /// concurrently each pass.  First successful **non-hotspot** discovery
    /// wins — a strategy that only sees the dying hotspot returns nil so the
    /// other strategies can still find Atlas's confirmed LAN address.
    private func runLanDiscoveryLoop(
        ssid: String,
        hintIp: String?,
        responseAccessUrls: [String]
    ) async {
        let deadline = Date().addingTimeInterval(180)
        var found: AtlasDiscoveryResult? = nil
        var iteration = 0

        // A non-blank hint IP that happens to be in the hotspot range is junk —
        // refuse it before it pollutes the targeted /24 scan or direct probe.
        let effectiveHint: String? = {
            guard let hintIp, !hintIp.isEmpty else { return nil }
            return Self.isHotspotUrl("https://\(hintIp)") ? nil : hintIp
        }()

        while Date() < deadline && found == nil && !Task.isCancelled {
            MdnsResolver.clearCache()

            // Build the direct-probe candidate list every pass so newly
            // discovered URLs (from `/api/wifi/status`) get included
            // immediately.
            var direct: [String] = []
            if let effectiveHint {
                direct.append("https://\(effectiveHint)")
                direct.append("http://\(effectiveHint):5000")
            }
            direct.append(contentsOf: responseAccessUrls)
            direct.append(contentsOf: pairedLanCandidates())
            direct.append("https://atlas.local")
            direct.append("http://atlas.local:5000")
            let directCandidates = direct
                .map { AtlasApiClient.normalize($0) }
                .filter { !$0.isEmpty && !Self.isHotspotUrl($0) }
                .removingDuplicates()

            // Reject any URL that resolves to a hotspot host.  Hotspot URLs are
            // not eligible winners during a pending switch — they would cancel
            // the task group and waste an iteration of the outer loop.
            // [label] is used to log which strategy submitted the URL so we
            // can verify from logs that every strategy actually ran in parallel.
            @Sendable func sift(_ label: String, _ url: String?) -> String? {
                guard let url, !url.isEmpty else {
                    NSLog("[AtlasDiscovery] \(label) returned nil")
                    return nil
                }
                if Self.isHotspotUrl(url) {
                    NSLog("[AtlasDiscovery] \(label) returned hotspot URL (rejected): \(url)")
                    return nil
                }
                NSLog("[AtlasDiscovery] \(label) candidate \(url)")
                return url
            }

            iteration += 1
            let strategies = "S1+S2+S2b" + (effectiveHint != nil ? "+S3" : "") + "+S4+S5"
            NSLog("[AtlasDiscovery] iteration #\(iteration) starting (\(Int(deadline.timeIntervalSinceNow))s left); running \(strategies) in parallel")

            // Run every strategy concurrently; the first to return a non-nil
            // non-hotspot URL wins and the rest are cancelled.
            let candidate: String? = await withTaskGroup(of: String?.self, returning: String?.self) { group in
                // S1 — direct probe
                group.addTask { sift("S1-direct", await AppViewModel.probeUrlsFast(directCandidates)) }
                // S2 (PRIMARY) — UDP beacon "shout-and-receive". Listens
                // continuously for the full iteration window (15 s) and
                // re-fires probe bursts every 3 s, so Atlas is found
                // within ~1 s of rebinding on the new LAN regardless of
                // when in the iteration that happens. Falls through
                // silently on older Atlas firmware that doesn't run the
                // beacon, letting S2b take over.
                group.addTask {
                    sift("S2-beacon", await AtlasBeacon.discover(timeout: 14, excludeHotspot: true))
                }
                // S2b — port-5000 fingerprint sweep across every candidate
                // /24, hotspot prefixes excluded. Bounded to 20 s so the
                // loop retries many times within the 180 s deadline. Acts
                // as the deterministic fallback when the beacon path is
                // unavailable or Atlas hasn't yet rebound to the new LAN.
                group.addTask {
                    let r: String? = (try? await withTimeout(seconds: 20) {
                        await AtlasDiscovery.findAtlasByPortSweep(excludeHotspot: true)
                    }) ?? nil
                    return sift("S2b-sweep", r)
                }
                // S3 — targeted /24 sweep (uses effective hint, hotspot-excluded)
                if let effectiveHint {
                    group.addTask {
                        let r: String? = (try? await withTimeout(seconds: 15) {
                            await AtlasDiscovery.findAtlasByTargetSubnet(hintIp: effectiveHint, excludeHotspot: true)
                        }) ?? nil
                        return sift("S3-target", r)
                    }
                }
                // S4 — `/api/wifi/status` polling
                group.addTask { [weak self] in
                    sift("S4-wifistatus", await self?.pollWifiSwitchStatus(candidates: directCandidates) ?? nil)
                }
                // S5 — Bonjour / DNS-SD as a 5 s supplementary check only.
                //      Single-radio Atlas's avahi re-announce on a brand-
                //      new LAN is unreliable, so we never gate on it.
                group.addTask { sift("S5-mdns", await AtlasDiscovery.findAtlasOnLan(timeout: 5)) }
                for await result in group {
                    if let result {
                        NSLog("[AtlasDiscovery] WINNER \(result) — cancelling siblings")
                        group.cancelAll()
                        return result
                    }
                }
                return nil
            }

            if let candidate, !Self.isHotspotUrl(candidate) {
                // Confirm with a full bootstrap fetch so the wizard has a
                // populated manifest for `completeLanSetup`.
                found = await tryEachUrl([candidate])
            }

            if found == nil {
                let remaining = deadline.timeIntervalSinceNow
                if remaining > 3 {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                }
            }
        }

        await MainActor.run {
            self.lanHandoffPending = false
            self.lanConnecting = false
            if let found {
                self.discovery = found
                self.lanDiscoveredUrl = found.foundUrl
                self.lanConnectDone = true
            } else {
                self.lanConnectError =
                    "Could not reach Atlas on \"\(ssid)\".\n\n" +
                    "Make sure your phone is connected to \(ssid), then tap retry."
            }
        }
    }

    /// Polls `/api/wifi/status` at every URL we know about until the
    /// `wifiSwitch` result reports completion, then returns the first
    /// confirmed access URL that responds.
    private func pollWifiSwitchStatus(candidates: [String]) async -> String? {
        let deadline = Date().addingTimeInterval(120)
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
                let unique = confirmed
                    .removingDuplicates()
                    .filter { !$0.isEmpty && !Self.isHotspotUrl($0) }
                if unique.isEmpty { continue }
                await mergeLanCandidateUrls(unique)
                if let match = await AppViewModel.probeUrlsFast(unique) {
                    return match
                }
            }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
        return nil
    }

    /// Persists newly discovered LAN URLs into the same `UserDefaults` key
    /// `AppViewModel` reads on startup, so when the user finishes the wizard
    /// the URLs are already known.  Filters out hotspot URLs.
    private func mergeLanCandidateUrls(_ urls: [String]) async {
        let cleaned = urls
            .map { AtlasApiClient.normalize($0) }
            .filter { !$0.isEmpty && !Self.isHotspotUrl($0) }
            .removingDuplicates()
        guard !cleaned.isEmpty else { return }
        await MainActor.run {
            let key = "lan_urls"
            let raw = self.defaults.string(forKey: key) ?? ""
            let existing = raw.split(separator: ",").map(String.init).filter { !$0.isEmpty }
            let merged = (cleaned + existing).removingDuplicates()
            if merged != existing {
                self.defaults.set(merged.joined(separator: ","), forKey: key)
            }
        }
    }

    // MARK: - Finish setup

    /// Persists URL buckets and immediately transitions `AppViewModel` to
    /// `.connected` using the URL we just confirmed works.
    func completeSetup(appVm: AppViewModel) {
        guard let disc = discovery else { return }
        let manifestUrls = disc.manifest.api?.baseUrls ?? []
        var hotspotUrls: [String] = [disc.foundUrl]
        for url in manifestUrls where Self.isHotspotUrl(url) { hotspotUrls.append(url) }
        if let ip = Self.extractIp(disc.foundUrl) {
            hotspotUrls.append("http://\(ip):5000/")
            hotspotUrls.append("https://\(ip)/")
        }
        var lanUrls: [String] = manifestUrls.filter { !Self.isHotspotUrl($0) }
        lanUrls.append("https://atlas.local")
        lanUrls.append("http://atlas.local:5000")

        appVm.applySetupResult(
            foundUrl: disc.foundUrl,
            hotspotUrls: hotspotUrls.removingDuplicates(),
            lanUrls: lanUrls.removingDuplicates()
        )
    }

    /// Like `completeSetup` but preserves the original hotspot URLs (so
    /// reconnecting back to the hotspot still works) and adds the LAN URL
    /// we just discovered.
    func completeLanSetup(appVm: AppViewModel) {
        guard let lanDisc = discovery else { return }
        let hotDisc = hotspotDiscovery ?? lanDisc
        let hotManifest = hotDisc.manifest.api?.baseUrls ?? []
        let lanManifest = lanDisc.manifest.api?.baseUrls ?? []

        var hotspotUrls: [String] = [hotDisc.foundUrl]
        for url in hotManifest where Self.isHotspotUrl(url) { hotspotUrls.append(url) }
        if let ip = Self.extractIp(hotDisc.foundUrl) {
            hotspotUrls.append("http://\(ip):5000/")
            hotspotUrls.append("https://\(ip)/")
        }

        var lanUrls: [String] = []
        if let url = lanDiscoveredUrl { lanUrls.append(url) }
        lanUrls.append(contentsOf: lanManifest.filter { !Self.isHotspotUrl($0) })
        lanUrls.append("https://atlas.local")
        lanUrls.append("http://atlas.local:5000")

        appVm.applySetupResult(
            foundUrl: lanDisc.foundUrl,
            hotspotUrls: hotspotUrls.removingDuplicates(),
            lanUrls: lanUrls.removingDuplicates()
        )
    }

    /// Convenience: programmatically join the Atlas hotspot using
    /// NEHotspotConfiguration so the user doesn't have to leave the app.
    /// On iOS 14+ NEHotspotConfiguration requires the Hotspot Configuration
    /// entitlement; if it's missing the call fails silently and the user
    /// can still join via the in-app Wi-Fi settings card.
    func joinAtlasHotspot() {
        let config = NEHotspotConfiguration(
            ssid: hotspotSsid,
            passphrase: hotspotPassword,
            isWEP: false
        )
        config.joinOnce = false
        NEHotspotConfigurationManager.shared.apply(config) { _ in }
    }

    // MARK: - Core search logic

    private func searchForAtlas() async -> AtlasDiscoveryResult? {
        var candidates: [String] = []
        if let gw = gatewayUrl() { candidates.append(gw) }
        candidates.append(contentsOf: hotspotCandidates())
        candidates.append("https://atlas.local")
        candidates.append("http://atlas.local:5000")
        let unique = candidates.removingDuplicates()

        if let result = await tryEachUrl(unique) { return result }
        return await discoverAtlasOnCurrentNetwork()
    }

    private func tryEachUrl(_ candidates: [String]) async -> AtlasDiscoveryResult? {
        for url in candidates {
            if Task.isCancelled { return nil }
            let manifest: BootstrapManifest? = try? await withTimeout(seconds: 5, operation: {
                if let manifest = try? await AtlasApi.getBootstrap(base: url) {
                    return manifest
                }
                if (try? await AtlasApi.getDevice(base: url)) != nil {
                    return BootstrapManifest()
                }
                return nil
            })
            guard let manifest else { continue }

            persistHotspotCredentials(manifest)
            let webUrl = resolveWebUrl(url)
            return AtlasDiscoveryResult(foundUrl: webUrl, manifest: manifest)
        }
        return nil
    }

    private func discoverAtlasOnCurrentNetwork() async -> AtlasDiscoveryResult? {
        let url: String? = await withTaskGroup(of: String?.self, returning: String?.self) { group in
            // UDP beacon — fastest path on real devices and Simulator (which
            // shares host networking, so Atlas's broadcasts arrive directly).
            group.addTask { await AtlasBeacon.discover(timeout: 4) }
            // Port-5000 fingerprint sweep — deterministic fallback.
            group.addTask {
                (try? await withTimeout(seconds: 20) {
                    await AtlasDiscovery.findAtlasByPortSweep()
                }) ?? nil
            }
            // mDNS as a 5 s supplementary check.
            group.addTask { await AtlasDiscovery.findAtlasOnLan(timeout: 5) }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
            }
            return nil
        }
        guard let url else { return nil }
        return await tryEachUrl([url])
    }

    private func persistHotspotCredentials(_ manifest: BootstrapManifest) {
        guard let info = manifest.hotspot, !info.ssid.isEmpty else { return }
        defaults.set(info.ssid,     forKey: Self.hotspotSsidKey)
        defaults.set(info.password, forKey: Self.hotspotPasswordKey)
        Task { @MainActor in
            self.hotspotSsid = info.ssid
            self.hotspotPassword = info.password
        }
    }

    // MARK: - URL helpers

    private func hotspotCandidates() -> [String] {
        [
            "http://10.42.0.1:5000",
            "https://10.42.0.1",
            "http://192.168.4.1:5000",
            "https://192.168.4.1",
            "http://192.168.43.1:5000",
            "https://192.168.43.1",
        ]
    }

    private func gatewayUrl() -> String? {
        guard let info = LocalNetworkInfo.current(), let gw = info.gateway else { return nil }
        return "http://\(gw):5000"
    }

    private func pairedLanCandidates() -> [String] {
        let manifestUrls = (hotspotDiscovery?.manifest.api?.baseUrls)
            ?? (discovery?.manifest.api?.baseUrls)
            ?? []
        var urls: [String] = manifestUrls.filter { !Self.isHotspotUrl($0) }
        if let lan = lanDiscoveredUrl { urls.append(lan) }
        urls.append("https://atlas.local")
        urls.append("http://atlas.local:5000")
        return urls.removingDuplicates()
    }

    /// Best-effort `.local` → numeric IP swap for the WebView's first load.
    /// iOS WKWebView CAN resolve Bonjour names so this is mostly redundant,
    /// but keeping the resolution warm before WebView loads avoids a cold
    /// `mdnsd` round-trip on the very first navigation.
    private func resolveWebUrl(_ url: String) -> String {
        guard let parsed = URL(string: url),
              let host = parsed.host?.lowercased(),
              host.hasSuffix(".local"),
              let ip = MdnsResolver.resolve(host) else { return url }
        let port = (parsed.port.map { ":\($0)" }) ?? ""
        let scheme = parsed.scheme ?? "https"
        return "\(scheme)://\(ip)\(port)/"
    }

    // `nonisolated` so these pure helpers can be called from the `@Sendable`
    // `sift` closure inside `runLanDiscoveryLoop` (and any other nonisolated
    // context) without an actor hop. `SetupViewModel` is `@MainActor`, which
    // would otherwise make its statics MainActor-isolated — the cause of the
    // "Call to main actor-isolated static method 'isHotspotUrl' in a
    // synchronous nonisolated context" build error.
    private nonisolated static func isHotspotUrl(_ url: String) -> Bool {
        url.contains("10.42.0.")   ||
        url.contains("192.168.4.") ||
        url.contains("192.168.43.")
    }

    private nonisolated static func extractIp(_ url: String) -> String? {
        guard let host = URL(string: url)?.host else { return nil }
        let pattern = #"^\d+\.\d+\.\d+\.\d+$"#
        return host.range(of: pattern, options: .regularExpression) != nil ? host : nil
    }
}

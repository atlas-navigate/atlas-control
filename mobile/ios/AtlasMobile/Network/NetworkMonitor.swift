import Foundation
import Network
import CoreLocation
import NetworkExtension

/// Watches the active network so `AppViewModel` can re-probe after the user
/// switches Wi-Fi.  Direct counterpart of the Android `NetworkMonitor`
/// class — exposes the current SSID (when permitted) and a monotonically
/// increasing `version` counter that ticks on every connectivity change.
@MainActor
final class AtlasNetworkMonitor: ObservableObject {

    @Published private(set) var ssid: String?
    @Published private(set) var version: Int = 0

    private let pathMonitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "atlas.network.monitor")
    private var started = false
    private let locationDelegate = LocationAuthHelper()

    /// True when the phone is associated with a network whose SSID contains
    /// "atlas" — used by `AppViewModel.prioritizeByNetwork(...)` to put the
    /// hotspot URLs first when the phone is on the Atlas hotspot.
    var isOnAtlasHotspot: Bool {
        ssid?.range(of: "atlas", options: .caseInsensitive) != nil
    }

    func start() {
        guard !started else { return }
        started = true
        // Asking for location authorization is what unlocks
        // NEHotspotNetwork.fetchCurrent() on iOS 14+.  The wizard-side flow
        // already prompts for "When in Use" location during onboarding, so
        // this is a no-op once the user grants it.
        locationDelegate.requestIfNeeded()

        pathMonitor.pathUpdateHandler = { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.version += 1
                await self.refreshSsid()
            }
        }
        pathMonitor.start(queue: queue)
        Task { @MainActor in
            await self.refreshSsid()
        }
    }

    func stop() {
        pathMonitor.cancel()
        started = false
    }

    /// Best-effort gateway URL based on the active interface's primary IPv4.
    /// Maps to Android's `WifiManager.dhcpInfo.gateway` shortcut: when the
    /// phone is on the Atlas hotspot the gateway *is* Atlas (10.42.0.1), so
    /// probing it is the fastest way to reconnect.
    func gatewayUrl() -> String? {
        guard let info = LocalNetworkInfo.current(), let gateway = info.gateway else { return nil }
        return "http://\(gateway):5000"
    }

    @MainActor
    private func refreshSsid() async {
        let resolved = await Self.fetchCurrentSsid()
        if resolved != ssid {
            ssid = resolved
        }
    }

    /// Calls `NEHotspotNetwork.fetchCurrent` and returns the SSID (or
    /// `nil` if the phone is not on Wi-Fi or the user hasn't granted
    /// location).  Wrapped in a checked continuation because the underlying
    /// API is callback-based.
    static func fetchCurrentSsid() async -> String? {
        await withCheckedContinuation { continuation in
            NEHotspotNetwork.fetchCurrent { network in
                continuation.resume(returning: network?.ssid)
            }
        }
    }
}

/// Minimal CoreLocation delegate used solely to obtain "When In Use"
/// authorization — the side-effect that lets `NEHotspotNetwork.fetchCurrent`
/// return a non-nil SSID on iOS 14+.  We don't actually need any location
/// updates from CoreLocation itself; the optional `AtlasPhoneTracker`
/// service handles that separately when the user has accepted tracking.
private final class LocationAuthHelper: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
    }

    func requestIfNeeded() {
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        default:
            break
        }
    }
}

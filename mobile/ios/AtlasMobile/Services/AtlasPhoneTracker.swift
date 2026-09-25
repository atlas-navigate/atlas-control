import CoreLocation
import Foundation
import UIKit

/// Optional: show this iPhone on Atlas's map.
///
/// Off until the owner says yes (asked once after pairing). Runs only while
/// the app is in the foreground and connected, sends at most every 30 s and
/// only after moving 50 m, over the pinned connection with the pairing token.
/// (It used to start automatically and post precise location on every 10 m
/// change, indefinitely.)
final class AtlasPhoneTracker: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var baseUrl: String?
    private var device: PairedDevice?
    private var lastSent: Date = .distantPast
    private let deviceId = "ios-" + (UIDevice.current.identifierForVendor?.uuidString.lowercased() ?? UUID().uuidString.lowercased())

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        manager.distanceFilter = 50
        manager.pausesLocationUpdatesAutomatically = true
    }

    var enabled: Bool { UserDefaults.standard.bool(forKey: "atlas.share_location") }

    func configure(baseUrl: String, device: PairedDevice) {
        self.baseUrl = baseUrl
        self.device = device
        guard enabled, UIApplication.shared.applicationState == .active else { stop(); return }
        switch manager.authorizationStatus {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways: manager.startUpdatingLocation()
        default: break
        }
    }

    func stop() { manager.stopUpdatingLocation() }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if enabled, baseUrl != nil,
           manager.authorizationStatus == .authorizedWhenInUse || manager.authorizationStatus == .authorizedAlways {
            manager.startUpdatingLocation()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard enabled, let location = locations.last, let baseUrl, let device,
              Date().timeIntervalSince(lastSent) >= 30 else { return }
        lastSent = Date()
        var payload: [String: Any] = [
            "device_id": deviceId,
            "name": String(UIDevice.current.name.prefix(60)),
            "color": "#a855f7",
            "latitude": location.coordinate.latitude,
            "longitude": location.coordinate.longitude,
            "accuracy": location.horizontalAccuracy,
        ]
        if location.verticalAccuracy >= 0 { payload["altitude"] = location.altitude }
        if location.speed >= 0 { payload["speed"] = location.speed }
        if location.course >= 0 { payload["heading"] = location.course }
        Task { await AtlasClient.postJSON(baseUrl, path: "/api/tracker/checkin", device: device, body: payload) }
    }
}

/// Message and UPS-battery notifications while the app is connected.
enum AtlasNotifier {
    static func poll(url: String, device: PairedDevice) async {
        guard let dev = await AtlasClient.getJSON(url, path: "/api/device", device: device) as? [String: Any],
              let messages = await AtlasClient.getJSON(url, path: "/api/messages", device: device) as? [[String: Any]]
        else { return }
        let defaults = UserDefaults.standard
        if let latest = messages.first {
            let key = (latest["packet_id"].map { "\($0)" }) ?? "\(latest["rx_time"] ?? ""):\(latest["from_id"] ?? "")"
            let stored = defaults.string(forKey: "atlas.last_message_key")
            defaults.set(key, forKey: "atlas.last_message_key")
            let from = latest["from_id"] as? String ?? ""
            if stored != nil, stored != key, from != (dev["my_node_id"] as? String) {
                let title = (latest["is_direct"] as? Int ?? 0) != 0 ? "New direct message" : "New Atlas message"
                let text = (latest["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                AtlasNotificationManager.showMessage(title: title,
                    body: text.isEmpty ? "Message received from \(from.isEmpty ? "Unknown sender" : from)." : "\(from): \(text)")
            }
        }
        if let pct = dev["battery_pct"] as? Int {
            let charging = (dev["battery_phase"] as? String)?.lowercased() == "charging"
                || (dev["battery_status"] as? String)?.lowercased() == "charging"
            if pct <= 20, !defaults.bool(forKey: "atlas.battery_low_sent") { AtlasNotificationManager.showBatteryLow(percent: pct) }
            defaults.set(pct <= 20, forKey: "atlas.battery_low_sent")
            if charging, pct >= 90, !defaults.bool(forKey: "atlas.battery_high_sent") {
                AtlasNotificationManager.showBatteryCharged(percent: pct)
            }
            defaults.set(charging && pct >= 90, forKey: "atlas.battery_high_sent")
        }
    }
}

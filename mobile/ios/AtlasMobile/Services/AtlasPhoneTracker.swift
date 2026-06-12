import CoreLocation
import Foundation
import UIKit

/// Optional iOS-only sidecar service: pushes the phone's current location
/// to Atlas's `/api/tracker/checkin` endpoint so the cyberdeck UI can plot
/// it on the offline map alongside other tracked nodes.
///
/// Activated automatically once `AppViewModel` reaches `.connected` — the
/// view model calls `updateBaseUrl(_:)` followed by `start()`.
final class AtlasPhoneTracker: NSObject, CLLocationManagerDelegate {

    private let manager = CLLocationManager()
    private let session: URLSession
    private var baseUrl: String?
    private var started = false
    private let deviceId = "ios-" + (UIDevice.current.identifierForVendor?.uuidString.lowercased() ?? UUID().uuidString.lowercased())

    override init() {
        self.session = URLSession(
            configuration: .default,
            delegate: AtlasURLSessionDelegate.shared,
            delegateQueue: nil
        )
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 10
        UIDevice.current.isBatteryMonitoringEnabled = true
    }

    func updateBaseUrl(_ url: String) {
        baseUrl = AtlasApiClient.normalize(url)
    }

    func start() {
        guard !started else { return }
        started = true
        let status = manager.authorizationStatus
        if status == .notDetermined {
            manager.requestWhenInUseAuthorization()
            return
        }
        guard status == .authorizedWhenInUse || status == .authorizedAlways else { return }
        manager.startUpdatingLocation()
        if let location = manager.location { sendCheckin(location) }
    }

    func stop() {
        started = false
        manager.stopUpdatingLocation()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        guard status == .authorizedWhenInUse || status == .authorizedAlways else { return }
        manager.startUpdatingLocation()
        if let location = manager.location { sendCheckin(location) }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        sendCheckin(location)
    }

    private func sendCheckin(_ location: CLLocation) {
        guard let baseUrl,
              let url = AtlasApiClient.makeUrl(base: baseUrl, path: "/api/tracker/checkin")
        else { return }
        var payload: [String: Any] = [
            "device_id": deviceId,
            "name":      UIDevice.current.name,
            "color":     "#a855f7",
            "latitude":  location.coordinate.latitude,
            "longitude": location.coordinate.longitude,
            "accuracy":  location.horizontalAccuracy,
        ]
        if location.verticalAccuracy >= 0 { payload["altitude"] = location.altitude }
        if location.speed             >= 0 { payload["speed"]    = location.speed }
        if location.course            >= 0 { payload["heading"]  = location.course }
        if let battery = batteryFraction() { payload["battery"]  = battery }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        session.dataTask(with: request).resume()
    }

    private func batteryFraction() -> Double? {
        let level = UIDevice.current.batteryLevel
        return level >= 0 ? Double(level) : nil
    }
}

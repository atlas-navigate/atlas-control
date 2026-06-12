import Foundation
import UserNotifications

/// `UNUserNotificationCenter`-based replacement for Android's
/// `AtlasNotificationManager`.  Posts the same three notification types:
///   - new mesh message
///   - Atlas battery low
///   - Atlas battery fully charged
enum AtlasNotificationManager {

    private static let messageId  = "atlas.message"
    private static let batLowId   = "atlas.battery.low"
    private static let batFullId  = "atlas.battery.charged"

    /// Asks the user for permission once.  Safe to call repeatedly — iOS
    /// no-ops if a decision has already been made.  Should be called on
    /// app launch (mirrors `MainActivity.requestNotificationPermissionIfNeeded`).
    static func requestAuthorizationIfNeeded() {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        }
    }

    static func showMessage(title: String, body: String) {
        post(identifier: messageId, title: title, body: body, sound: .default, interruption: .timeSensitive)
    }

    static func showBatteryLow(percent: Int) {
        post(
            identifier: batLowId,
            title: "Atlas battery low",
            body: "Atlas battery is at \(percent)%. Plug it in or attach the UPS module.",
            sound: .default,
            interruption: .timeSensitive
        )
    }

    static func showBatteryCharged(percent: Int) {
        post(
            identifier: batFullId,
            title: "Atlas fully charged",
            body: "Atlas battery is at \(percent)%. You can safely unplug.",
            sound: .default,
            interruption: .active
        )
    }

    // MARK: - Internal

    private static func post(
        identifier: String,
        title: String,
        body: String,
        sound: UNNotificationSound,
        interruption: UNNotificationInterruptionLevel
    ) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body  = body
        content.sound = sound
        content.interruptionLevel = interruption

        let request = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: nil                  // deliver immediately
        )
        UNUserNotificationCenter.current().add(request) { _ in }
    }
}

import SwiftUI

@main
struct AtlasMobileApp: App {
    @StateObject private var model: ConnectionModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let m = MainActor.assumeIsolated { ConnectionModel() }
        _model = StateObject(wrappedValue: m)
        AtlasNotificationManager.requestAuthorizationIfNeeded()
    }

    var body: some Scene {
        WindowGroup {
            MainView(model: model)
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: model.appBecameActive()
            case .background: model.appWentToBackground()
            default: break
            }
        }
    }
}

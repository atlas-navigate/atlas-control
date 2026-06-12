import SwiftUI

@main
struct AtlasMobileApp: App {
    @StateObject private var appVm: AppViewModel
    @StateObject private var setupVm: SetupViewModel

    init() {
        // SwiftUI calls App.init() on the main thread, but the protocol
        // requirement is non-isolated.  Constructing @MainActor view models
        // inline (`@StateObject private var x = X()`) trips strict-concurrency
        // checks on Swift 6 and can crash at runtime on Swift 5 if the model
        // touches CoreLocation / NWPathMonitor before the main actor is
        // confirmed.  `MainActor.assumeIsolated` makes the isolation explicit
        // without changing semantics.
        let (app, setup): (AppViewModel, SetupViewModel) = MainActor.assumeIsolated {
            (AppViewModel(), SetupViewModel())
        }
        _appVm   = StateObject(wrappedValue: app)
        _setupVm = StateObject(wrappedValue: setup)
        AtlasNotificationManager.requestAuthorizationIfNeeded()
    }

    var body: some Scene {
        WindowGroup {
            MainView(appVm: appVm, setupVm: setupVm)
        }
    }
}

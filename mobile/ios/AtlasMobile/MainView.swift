import SwiftUI

/// Root navigation router — direct port of the Compose `AtlasApp`
/// composable.  Animates between the wizard, the connecting screen, the
/// embedded Atlas web app, and the error fallback.
struct MainView: View {
    @ObservedObject var appVm: AppViewModel
    @ObservedObject var setupVm: SetupViewModel

    var body: some View {
        ZStack {
            AtlasTheme.background.ignoresSafeArea()

            switch appVm.state {
            case .idle:
                SetupWizardScreen(setupVm: setupVm, appVm: appVm)
                    .transition(.opacity)
            case .checking:
                ConnectingView(appVm: appVm)
                    .transition(.opacity)
            case .connected:
                AtlasWebScreen(appVm: appVm, setupVm: setupVm)
                    .transition(.opacity)
            case .failed:
                ErrorView(appVm: appVm, setupVm: setupVm)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: appVm.state)
        .preferredColorScheme(.dark)
    }
}

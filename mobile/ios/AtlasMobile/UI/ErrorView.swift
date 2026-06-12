import SwiftUI

/// SwiftUI port of the Compose `ErrorScreen` — surfaces the failure
/// message, a Retry button, and a manual-IP entry field that bypasses
/// discovery entirely.
struct ErrorView: View {
    @ObservedObject var appVm: AppViewModel
    @ObservedObject var setupVm: SetupViewModel
    @State private var manualIp: String = ""

    var body: some View {
        ZStack {
            AtlasTheme.background.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 0) {
                    Spacer().frame(height: 60)
                    Image(systemName: "wifi.slash")
                        .font(.system(size: 56))
                        .foregroundStyle(AtlasTheme.error)

                    Spacer().frame(height: 16)

                    Text("Could not reach Atlas")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(AtlasTheme.onBackground)

                    Spacer().frame(height: 8)

                    Text(errorText)
                        .font(.system(size: 14))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(AtlasTheme.muted)

                    Spacer().frame(height: 6)

                    Text("Join the atlas_navigate hotspot or ensure your phone is on the same LAN as Atlas.")
                        .font(.system(size: 12))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(AtlasTheme.muted)

                    Spacer().frame(height: 24)

                    PrimaryButton(text: "Retry", systemImage: "arrow.clockwise") {
                        appVm.retry()
                    }

                    Divider().background(AtlasTheme.muted.opacity(0.3)).padding(.vertical, 24)

                    Text("Or connect manually")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(AtlasTheme.onBackground)

                    Spacer().frame(height: 10)

                    TextField("Atlas IP  (e.g. 192.168.1.50)", text: $manualIp)
                        .textFieldStyle(AtlasTextFieldStyle())
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .submitLabel(.go)
                        .onSubmit {
                            if !manualIp.isEmpty { appVm.connectToManualIp(manualIp) }
                        }

                    Spacer().frame(height: 10)

                    Button {
                        if !manualIp.isEmpty { appVm.connectToManualIp(manualIp) }
                    } label: {
                        Text("Connect to this IP")
                            .font(.system(size: 16, weight: .bold))
                            .frame(maxWidth: .infinity)
                            .frame(height: 48)
                            .foregroundStyle(AtlasTheme.onBackground)
                            .background(AtlasTheme.surface2)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    .disabled(manualIp.isEmpty)

                    Spacer().frame(height: 16)

                    Button {
                        // Reset the wizard BEFORE flipping AppViewModel to .idle —
                        // both view models are app-lifetime objects, so without
                        // this the wizard reopens on its stale step (e.g. the old
                        // "Atlas Found!" pairing card) holding a dead URL.
                        setupVm.resetForReconnect()
                        appVm.showSetupWizard()
                    } label: {
                        Text("Restart setup")
                            .font(.system(size: 13))
                            .foregroundStyle(AtlasTheme.muted)
                    }
                    Spacer().frame(height: 32)
                }
                .padding(.horizontal, 32)
                .frame(maxWidth: 480)
            }
        }
    }

    private var errorText: String {
        if let msg = appVm.errorMsg, !msg.isEmpty { return msg }
        if let url = appVm.baseUrl { return "No response from \(url)" }
        return "Atlas is not reachable."
    }
}

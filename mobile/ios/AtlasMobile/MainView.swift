import SwiftUI

struct MainView: View {
    @ObservedObject var model: ConnectionModel
    @ObservedObject private var localNetwork = LocalNetworkAccess.shared

    var body: some View {
        ZStack(alignment: .bottom) {
            AtlasTheme.background.ignoresSafeArea()
            switch model.screen {
            case .onboarding:
                OnboardingView(model: model)
            case .locating(let message):
                WaitingView(message: message, detail: nil)
            case .restarting(let message):
                WaitingView(message: message, detail: "Atlas reconnects by itself — keep this app open.")
            case .connected(let url):
                AtlasWebScreen(model: model, baseUrl: url).id(url).ignoresSafeArea(edges: .bottom)
            case .paired(_, let name, let hostname):
                YourAtlasAddressView(name: name, hostname: hostname) { model.continueAfterPairing() }
            case .notFound:
                ScrollView {
                    ProblemView(icon: "wifi.slash", title: "Can't find \(model.deviceName)",
                                body: "Make sure Atlas is switched on and this iPhone is on the same Wi-Fi, or on Atlas's own Wi-Fi (its name and password are on the label). The app keeps looking and connects as soon as Atlas is reachable.",
                                primary: ("Try again", { model.relocate() }), secondary: nil)
                    VStack(spacing: 12) {
                        if localNetwork.denied { LocalNetworkDeniedCard() }
                        DeviceDetailsView(name: model.deviceName, hostname: model.deviceHostname,
                                          lastAddress: model.lastAddress)
                    }
                    .padding(.horizontal, 24)
                }
            case .securityWarning:
                ProblemView(icon: "exclamationmark.shield",
                            title: "Security warning",
                            body: "\(model.deviceName) is answering with a different security key than when you connected it. If this Atlas was reset or replaced, connect it again. Otherwise, something on this network may be pretending to be your Atlas — don't continue.",
                            primary: ("Connect again", { model.forgetDevice() }),
                            secondary: ("Try again", { model.relocate() }))
            case .unpaired:
                ProblemView(icon: "link.badge.plus",
                            title: "Connect this iPhone again",
                            body: "\(model.deviceName) no longer recognises this iPhone — it was removed in Settings → Connected devices, or Atlas was reset. Connecting again takes a moment: the setup code on the label, Atlas's own Wi-Fi, or an Allow from another device.",
                            primary: ("Connect again", { model.reconnectRemoved() }), secondary: nil)
            }
            if let notice = model.notice {
                Text(notice).padding(.horizontal, 16).padding(.vertical, 10)
                    .background(AtlasTheme.surface2).clipShape(Capsule())
                    .foregroundColor(AtlasTheme.onBackground).padding(.bottom, 30)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: model.screen)
        .preferredColorScheme(.dark)
        .alert("Show this iPhone on Atlas's map?", isPresented: $model.askLocationSharing) {
            Button("Share while the app is open") { model.setLocationSharing(true) }
            Button("Not now", role: .cancel) { model.setLocationSharing(false) }
        } message: {
            Text("Atlas can show where this iPhone is on its map, only while the Atlas app is open. You can change this in iOS Settings.")
        }
    }
}

struct WaitingView: View {
    let message: String
    let detail: String?
    var body: some View {
        VStack(spacing: 16) {
            ProgressView().tint(AtlasTheme.primary)
            Text(message).font(.headline).multilineTextAlignment(.center).foregroundColor(AtlasTheme.onBackground)
            if let detail {
                Text(detail).font(.footnote).multilineTextAlignment(.center).foregroundColor(AtlasTheme.muted)
            }
        }
        .padding(32)
    }
}

struct ProblemView: View {
    let icon: String
    let title: String
    let body_: String
    let primary: (String, () -> Void)
    let secondary: (String, () -> Void)?

    init(icon: String, title: String, body: String, primary: (String, () -> Void), secondary: (String, () -> Void)?) {
        self.icon = icon; self.title = title; self.body_ = body; self.primary = primary; self.secondary = secondary
    }

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: icon).font(.system(size: 48)).foregroundColor(AtlasTheme.warning)
            Text(title).font(.title3.bold()).multilineTextAlignment(.center).foregroundColor(AtlasTheme.onBackground)
            Text(body_).font(.subheadline).multilineTextAlignment(.center).foregroundColor(AtlasTheme.onSurface)
            Button(primary.0, action: primary.1).buttonStyle(.borderedProminent)
            if let secondary { Button(secondary.0, action: secondary.1) }
        }
        .padding(32)
    }
}

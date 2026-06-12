import SwiftUI

/// SwiftUI port of the Compose `ConnectingScreen`.  Same design language —
/// pulsing glow ring + spinner — and surfaces a manual-IP entry field
/// after 20 s of unsuccessful LAN scanning so the user can bypass discovery.
struct ConnectingView: View {
    @ObservedObject var appVm: AppViewModel
    @State private var elapsed: Int = 0
    @State private var manualIp: String = ""
    @State private var pulse = false
    @State private var ringScale: CGFloat = 0.85
    @State private var glowAlpha: Double = 0.15
    @State private var timer: Timer?

    private var showManualEntry: Bool {
        appVm.isLanTransitioning && elapsed >= 20
    }

    var body: some View {
        ZStack {
            RadialGradient(
                colors: [Color(red: 0x0F / 255.0, green: 0x1F / 255.0, blue: 0x38 / 255.0), AtlasTheme.background],
                center: .center,
                startRadius: 80,
                endRadius: 800
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [AtlasTheme.primary.opacity(glowAlpha), .clear],
                                center: .center,
                                startRadius: 0,
                                endRadius: 80
                            )
                        )
                        .frame(width: 130, height: 130)
                        .scaleEffect(ringScale)
                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [AtlasTheme.primaryDeep, Color(red: 0x0C / 255.0, green: 0x1A / 255.0, blue: 0x2E / 255.0)],
                                center: .center,
                                startRadius: 0,
                                endRadius: 60
                            )
                        )
                        .frame(width: 80, height: 80)
                        .overlay(
                            Circle().stroke(AtlasTheme.primary.opacity(0.35), lineWidth: 1)
                        )
                    Image(systemName: "antenna.radiowaves.left.and.right")
                        .font(.system(size: 38))
                        .foregroundStyle(AtlasTheme.primary)
                }
                .frame(height: 130)

                Spacer().frame(height: 28)
                Text("Atlas Control")
                    .font(.system(size: 28, weight: .bold))
                    .foregroundStyle(AtlasTheme.onBackground)
                Spacer().frame(height: 6)
                Text(appVm.isLanTransitioning ? "Searching for Atlas on LAN…" : "Connecting…")
                    .font(.system(size: 14))
                    .foregroundStyle(AtlasTheme.muted)
                Spacer().frame(height: 36)
                ProgressView()
                    .tint(AtlasTheme.primary)
                    .scaleEffect(1.2)

                if showManualEntry {
                    VStack(spacing: 14) {
                        Divider().background(AtlasTheme.muted.opacity(0.3)).padding(.bottom, 4)
                        Text("Still searching… (\(elapsed)s)")
                            .font(.system(size: 12))
                            .foregroundStyle(AtlasTheme.muted)
                        Text("Can't find Atlas automatically?")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(AtlasTheme.onBackground)
                            .multilineTextAlignment(.center)
                        Text("Enter the Atlas IP address or hostname from your LAN.")
                            .font(.system(size: 12))
                            .foregroundStyle(AtlasTheme.muted)
                            .multilineTextAlignment(.center)
                        TextField("Atlas IP  (e.g. 192.168.1.50)", text: $manualIp)
                            .textFieldStyle(AtlasTextFieldStyle())
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                            .submitLabel(.go)
                            .onSubmit {
                                if !manualIp.isEmpty { appVm.connectToManualIp(manualIp) }
                            }
                        PrimaryButton(text: "Connect to this IP", enabled: !manualIp.isEmpty) {
                            appVm.connectToManualIp(manualIp)
                        }
                    }
                    .padding(.top, 36)
                    .padding(.horizontal, 4)
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .padding(.horizontal, 32)
            .frame(maxWidth: 480)
        }
        .onAppear { startAnimations() }
        .onDisappear { stopAnimations() }
        .onChange(of: appVm.isLanTransitioning) { _, _ in
            elapsed = 0
        }
    }

    private func startAnimations() {
        withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: true)) {
            ringScale = 1.15
            glowAlpha = 0.55
        }
        elapsed = 0
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            elapsed += 1
        }
    }

    private func stopAnimations() {
        timer?.invalidate()
        timer = nil
    }
}

import SwiftUI
import UIKit

/// SwiftUI port of the Android `SetupWizardScreen`.
struct SetupWizardScreen: View {
    @ObservedObject var setupVm: SetupViewModel
    @ObservedObject var appVm: AppViewModel

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [AtlasTheme.background, AtlasTheme.surface],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            content
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal:   .move(edge: .leading).combined(with: .opacity)
                ))
        }
        // Drive the step transition above — without an animation tied to
        // `step` the `.transition` is inert and steps snap instead of sliding.
        // Mirrors Android's `AnimatedContent` (slide-in-from-right + fade /
        // slide-out-to-left + fade) around the wizard's `when(step)`.
        .animation(.easeInOut(duration: 0.3), value: setupVm.step)
    }

    @ViewBuilder
    private var content: some View {
        switch setupVm.step {
        case .welcome:        WelcomeStep(setupVm: setupVm)
        case .hotspotConnect: HotspotConnectStep(setupVm: setupVm)
        case .pairing:        PairingStep(setupVm: setupVm, appVm: appVm)
        case .lanProvision:   LanProvisionStep(setupVm: setupVm, appVm: appVm)
        case .done:           Color.clear   // AppViewModel drives navigation
        }
    }
}

// MARK: - Step 1 — Welcome

private struct WelcomeStep: View {
    @ObservedObject var setupVm: SetupViewModel

    var body: some View {
        WizardScroll {
            Spacer().frame(height: 40)

            Image(systemName: "antenna.radiowaves.left.and.right")
                .font(.system(size: 88))
                .foregroundStyle(AtlasTheme.primary)
                .frame(maxWidth: .infinity)

            Spacer().frame(height: 24)

            Text("Atlas Control")
                .font(.system(size: 34, weight: .heavy))
                .foregroundStyle(AtlasTheme.onBackground)
                .frame(maxWidth: .infinity)

            Text("Your offline field cyberdeck")
                .font(.system(size: 15))
                .foregroundStyle(AtlasTheme.muted)
                .frame(maxWidth: .infinity)

            Spacer().frame(height: 36)

            VStack(alignment: .leading, spacing: 14) {
                FeatureLine(systemImage: "map", text: "Offline maps + OSRM routing — no internet needed")
                FeatureLine(systemImage: "antenna.radiowaves.left.and.right", text: "Meshtastic mesh radio — text and position sharing")
                FeatureLine(systemImage: "brain", text: "Local AI assistant (Ollama) — runs entirely on-device")
                FeatureLine(systemImage: "location.fill", text: "GPS tracking with UBX dead-reckoning")
                FeatureLine(systemImage: "wifi", text: "Hotspot + LAN — connects wherever Atlas can be reached")
            }
            .padding(20)
            .atlasCard()

            Spacer().frame(height: 36)

            VStack(alignment: .leading, spacing: 8) {
                Text("How setup works")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(AtlasTheme.onBackground)
                Spacer().frame(height: 2)
                NumberedRow(number: "1", text: "Connect your phone to the Atlas Wi-Fi hotspot")
                NumberedRow(number: "2", text: "The app finds Atlas and saves the connection")
                NumberedRow(number: "3", text: "Atlas can also join your home / office LAN — both networks work automatically")
            }
            .padding(16)
            .atlasCard()

            Spacer().frame(height: 36)

            PrimaryButton(text: "Get Started") {
                setupVm.goToHotspotStep()
            }

            Spacer().frame(height: 32)
        }
    }
}

private struct NumberedRow: View {
    let number: String
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                Circle().fill(AtlasTheme.primary.opacity(0.15))
                Text(number)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(AtlasTheme.primary)
            }
            .frame(width: 24, height: 24)
            Text(text)
                .font(.system(size: 13))
                .foregroundStyle(AtlasTheme.onSurface)
        }
    }
}

private struct FeatureLine: View {
    let systemImage: String
    let text: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage)
                .foregroundStyle(AtlasTheme.secondary)
                .frame(width: 22, height: 22)
            Text(text)
                .font(.system(size: 14))
                .foregroundStyle(AtlasTheme.onSurface)
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Step 2 — Hotspot connect

private struct HotspotConnectStep: View {
    @ObservedObject var setupVm: SetupViewModel
    @State private var showAdvanced = false
    @State private var manualUrl = ""
    @State private var pulse = false

    var body: some View {
        WizardScroll {
            Spacer().frame(height: 36)

            Image(systemName: "wifi.router.fill")
                .font(.system(size: 80))
                .foregroundStyle(setupVm.isSearching ? AtlasTheme.tertiary : AtlasTheme.primary)
                .scaleEffect(setupVm.isSearching && pulse ? 1.12 : 1.0)
                .animation(setupVm.isSearching ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true) : .default, value: pulse)
                .frame(maxWidth: .infinity)
                .onAppear { pulse.toggle() }
                .onChange(of: setupVm.isSearching) { _, _ in pulse.toggle() }

            Spacer().frame(height: 20)

            Text("Connect to Atlas Hotspot")
                .font(.system(size: 26, weight: .bold))
                .foregroundStyle(AtlasTheme.onBackground)
                .frame(maxWidth: .infinity)

            Text("Connect your phone to the Atlas Wi-Fi network, then this app will find Atlas automatically.")
                .font(.system(size: 14))
                .multilineTextAlignment(.center)
                .foregroundStyle(AtlasTheme.muted)
                .padding(.top, 6)

            Spacer().frame(height: 28)

            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    Image(systemName: "wifi").foregroundStyle(AtlasTheme.secondary)
                    Text("Atlas Hotspot")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(AtlasTheme.onBackground)
                }
                CredentialRow(label: "Network (SSID)", value: setupVm.hotspotSsid)
                CredentialRow(label: "Password",       value: setupVm.hotspotPassword)
                Button {
                    setupVm.joinAtlasHotspot()
                } label: {
                    HStack {
                        Image(systemName: "arrow.up.forward.app")
                        Text("Join with Atlas Hotspot")
                            .font(.system(size: 14))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(AtlasTheme.background.opacity(0.5))
                    .foregroundStyle(AtlasTheme.primary)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(AtlasTheme.primary, lineWidth: 1))
                }
                Button {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                } label: {
                    HStack {
                        Image(systemName: "arrow.up.forward.app")
                        Text("Open Wi-Fi Settings")
                            .font(.system(size: 14))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(AtlasTheme.background.opacity(0.5))
                    .foregroundStyle(AtlasTheme.primary)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(AtlasTheme.primary, lineWidth: 1))
                }
            }
            .padding(18)
            .atlasCard()

            Spacer().frame(height: 20)

            HStack(spacing: 12) {
                if setupVm.isSearching {
                    ProgressView().tint(AtlasTheme.tertiary)
                } else {
                    Image(systemName: setupVm.errorMsg != nil ? "exclamationmark.triangle" : "magnifyingglass")
                        .foregroundStyle(setupVm.errorMsg != nil ? AtlasTheme.error : AtlasTheme.muted)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(setupVm.isSearching
                         ? "Searching for Atlas…"
                         : (setupVm.errorMsg != nil ? "Atlas not found" : "Ready to search"))
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(AtlasTheme.onBackground)
                    Text(setupVm.isSearching
                         ? "Checking gateway and known hotspot IPs…"
                         : "Once you join \(setupVm.hotspotSsid), tap Search")
                        .font(.system(size: 12))
                        .foregroundStyle(AtlasTheme.muted)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .atlasCard()

            if let err = setupVm.errorMsg {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(AtlasTheme.error)
                    Text(err).font(.system(size: 13)).foregroundStyle(AtlasTheme.error)
                    Spacer(minLength: 0)
                }
                .padding(14)
                .atlasCard()
                .padding(.top, 12)
            }

            Spacer().frame(height: 20)

            PrimaryButton(
                text: setupVm.isSearching ? "Searching…" : "Search",
                systemImage: setupVm.isSearching ? nil : "magnifyingglass",
                enabled: !setupVm.isSearching
            ) {
                setupVm.retrySearch()
            }

            Spacer().frame(height: 16)

            Button {
                withAnimation { showAdvanced.toggle() }
            } label: {
                HStack(spacing: 4) {
                    Text(showAdvanced ? "Hide manual address" : "Enter address manually")
                        .font(.system(size: 13))
                        .foregroundStyle(AtlasTheme.muted)
                    Image(systemName: showAdvanced ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11))
                        .foregroundStyle(AtlasTheme.muted)
                }
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity)
            }

            if showAdvanced {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Manual address")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(AtlasTheme.onBackground)
                    Text("Use if Atlas is on a LAN with a known address.")
                        .font(.system(size: 12))
                        .foregroundStyle(AtlasTheme.muted)
                    TextField("atlas.local  or  192.168.1.x", text: $manualUrl)
                        .textFieldStyle(AtlasTextFieldStyle())
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .submitLabel(.go)
                        .onChange(of: manualUrl) { _, new in
                            setupVm.manualUrl = new
                        }
                        .onSubmit { setupVm.connectManual {} }
                    Button {
                        setupVm.connectManual {}
                    } label: {
                        Text("Connect to this address")
                            .font(.system(size: 14))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .foregroundStyle(AtlasTheme.primary)
                            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(AtlasTheme.primary, lineWidth: 1))
                    }
                    .disabled(setupVm.isSearching)
                }
                .padding(16)
                .atlasCard()
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Spacer().frame(height: 32)
        }
        .onAppear {
            if !setupVm.isSearching && setupVm.discovery == nil {
                setupVm.startHotspotSearch()
            }
            manualUrl = setupVm.manualUrl
        }
    }
}

private struct CredentialRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label).font(.system(size: 13)).foregroundStyle(AtlasTheme.muted)
            Spacer(minLength: 0)
            Text(value)
                .font(.system(size: 15, weight: .semibold, design: .monospaced))
                .foregroundStyle(AtlasTheme.onBackground)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 12)
        .background(AtlasTheme.background.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

// MARK: - Step 3 — Pairing

private struct PairingStep: View {
    @ObservedObject var setupVm: SetupViewModel
    @ObservedObject var appVm: AppViewModel

    var body: some View {
        WizardScroll {
            let manifest = setupVm.discovery?.manifest ?? BootstrapManifest()
            let deviceName = (manifest.device?.name).flatMap { $0.isEmpty ? nil : $0 } ?? "Atlas Control"
            let shortName  = (manifest.device?.shortName).flatMap { $0.isEmpty ? nil : $0 } ?? "ATLS"
            let caps       = manifest.capabilities ?? [:]
            let hotspot    = manifest.hotspot
            let foundUrl   = setupVm.discovery?.foundUrl ?? ""
            let allUrls    = manifest.api?.baseUrls ?? [foundUrl]
            let lanUrls    = allUrls.filter { !isHotspotUrl($0) }

            Spacer().frame(height: 40)

            ZStack {
                Circle().fill(AtlasTheme.secondary.opacity(0.12))
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 56))
                    .foregroundStyle(AtlasTheme.secondary)
            }
            .frame(width: 88, height: 88)
            .frame(maxWidth: .infinity)

            Spacer().frame(height: 20)

            Text("Atlas Found!")
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(AtlasTheme.onBackground)
                .frame(maxWidth: .infinity)

            Text("Ready to connect — tap Open Atlas to launch the full interface.")
                .font(.system(size: 14))
                .multilineTextAlignment(.center)
                .foregroundStyle(AtlasTheme.muted)
                .padding(.top, 4)

            Spacer().frame(height: 28)

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "antenna.radiowaves.left.and.right").foregroundStyle(AtlasTheme.primary)
                    Text("Device").font(.system(size: 15, weight: .semibold)).foregroundStyle(AtlasTheme.onBackground)
                }
                InfoRow(label: "Name",       value: deviceName)
                InfoRow(label: "Short name", value: shortName)
                if !foundUrl.isEmpty { InfoRow(label: "Address", value: foundUrl) }
            }
            .padding(18)
            .atlasCard()

            if !caps.isEmpty {
                Spacer().frame(height: 14)
                VStack(alignment: .leading, spacing: 12) {
                    Text("Capabilities").font(.system(size: 14, weight: .semibold)).foregroundStyle(AtlasTheme.onBackground)
                    HStack(spacing: 8) {
                        CapBadge(label: "Mesh", active: caps["mesh"] == true)
                        CapBadge(label: "GPS",  active: caps["gps"] == true)
                        CapBadge(label: "AI",   active: caps["ai"] == true)
                        CapBadge(label: "Nav",  active: caps["navigation"] == true)
                        CapBadge(label: "WiFi", active: caps["wifi"] == true)
                    }
                }
                .padding(18)
                .atlasCard()
            }

            if let hotspot, hotspot.active && !hotspot.ssid.isEmpty {
                Spacer().frame(height: 14)
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "wifi.router").foregroundStyle(AtlasTheme.tertiary)
                        Text("Hotspot Active").font(.system(size: 14, weight: .semibold)).foregroundStyle(AtlasTheme.onBackground)
                    }
                    InfoRow(label: "SSID", value: hotspot.ssid)
                    if !hotspot.password.isEmpty { InfoRow(label: "Password", value: hotspot.password) }
                }
                .padding(18)
                .atlasCard()
            }

            if !lanUrls.isEmpty {
                Spacer().frame(height: 14)
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "network").foregroundStyle(AtlasTheme.secondary)
                        Text("LAN Access").font(.system(size: 14, weight: .semibold)).foregroundStyle(AtlasTheme.onBackground)
                    }
                    Text("Atlas is also reachable on your local network. The app will auto-switch between hotspot and LAN.")
                        .font(.system(size: 12))
                        .foregroundStyle(AtlasTheme.muted)
                    ForEach(Array(lanUrls.prefix(3)), id: \.self) { url in
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark.circle").foregroundStyle(AtlasTheme.secondary)
                            Text(url).font(.system(size: 12, design: .monospaced)).foregroundStyle(AtlasTheme.onSurface)
                        }
                    }
                }
                .padding(18)
                .atlasCard()
            }

            Spacer().frame(height: 28)

            PrimaryButton(text: "Open Atlas", systemImage: "arrow.up.forward.app") {
                setupVm.completeSetup(appVm: appVm)
            }

            Spacer().frame(height: 32)
        }
    }
}

private struct InfoRow: View {
    let label: String
    let value: String
    var body: some View {
        HStack {
            Text(label).font(.system(size: 13)).foregroundStyle(AtlasTheme.muted)
            Spacer(minLength: 8)
            Text(value)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(AtlasTheme.onBackground)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 3)
    }
}

private struct CapBadge: View {
    let label: String
    let active: Bool
    var body: some View {
        Text(label)
            .font(.system(size: 11, weight: active ? .semibold : .regular))
            .foregroundStyle(active ? AtlasTheme.secondary : AtlasTheme.muted)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(active ? AtlasTheme.secondary.opacity(0.15) : AtlasTheme.surface2)
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .stroke(active ? AtlasTheme.secondary.opacity(0.5) : AtlasTheme.muted.opacity(0.3), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

// MARK: - Step 4 — LAN provision

private struct LanProvisionStep: View {
    @ObservedObject var setupVm: SetupViewModel
    @ObservedObject var appVm: AppViewModel
    @State private var ssid = ""
    @State private var password = ""
    @State private var showPassword = false

    var body: some View {
        WizardScroll {
            Spacer().frame(height: 36)

            Image(systemName: "network")
                .font(.system(size: 72))
                .foregroundStyle(AtlasTheme.primary)
                .frame(maxWidth: .infinity)

            Spacer().frame(height: 16)

            Text("Connect Atlas to LAN")
                .font(.system(size: 26, weight: .bold))
                .foregroundStyle(AtlasTheme.onBackground)
                .frame(maxWidth: .infinity)

            Text("Enter the Wi-Fi network Atlas should join. After Atlas leaves its hotspot, connect your phone to the same LAN and the app will stay paired to this Atlas.")
                .font(.system(size: 14))
                .multilineTextAlignment(.center)
                .foregroundStyle(AtlasTheme.muted)
                .padding(.top, 6)

            Spacer().frame(height: 24)

            if setupVm.lanConnectDone {
                successBlock
            } else if setupVm.lanConnecting {
                connectingBlock
            } else {
                formBlock
            }

            Spacer().frame(height: 32)
        }
    }

    private var successBlock: some View {
        VStack(spacing: 14) {
            ZStack {
                Circle().fill(AtlasTheme.secondary.opacity(0.12))
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 44))
                    .foregroundStyle(AtlasTheme.secondary)
            }
            .frame(width: 72, height: 72)
            Text("Atlas joined \(setupVm.lanConnectSsid)")
                .font(.system(size: 18, weight: .bold))
                .foregroundStyle(AtlasTheme.secondary)
                .multilineTextAlignment(.center)
            Text("Atlas is now reachable on \(setupVm.lanConnectSsid). Your phone is connected to the same network.")
                .font(.system(size: 13))
                .foregroundStyle(AtlasTheme.muted)
                .multilineTextAlignment(.center)
            Spacer().frame(height: 14)
            PrimaryButton(text: "Open Atlas", systemImage: "arrow.up.forward.app") {
                setupVm.completeLanSetup(appVm: appVm)
            }
        }
    }

    private var connectingBlock: some View {
        VStack(spacing: 16) {
            VStack(spacing: 14) {
                ProgressView().tint(AtlasTheme.primary).controlSize(.large)
                if setupVm.lanHandoffPending {
                    Text("Atlas is switching to \(setupVm.lanConnectSsid)…")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(AtlasTheme.onBackground)
                        .multilineTextAlignment(.center)
                    Text("Atlas dropped its hotspot to join \(setupVm.lanConnectSsid). Connect your phone to \"\(setupVm.lanConnectSsid)\" using the button below — the app will reconnect automatically.")
                        .font(.system(size: 12))
                        .foregroundStyle(AtlasTheme.muted)
                        .multilineTextAlignment(.center)
                } else {
                    Text("Connecting Atlas to \(setupVm.lanConnectSsid)…")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(AtlasTheme.onBackground)
                        .multilineTextAlignment(.center)
                    Text("Sending request to Atlas…")
                        .font(.system(size: 12))
                        .foregroundStyle(AtlasTheme.muted)
                        .multilineTextAlignment(.center)
                }
            }
            .padding(24)
            .atlasCard()

            if setupVm.lanHandoffPending {
                Button {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                } label: {
                    HStack {
                        Image(systemName: "wifi")
                        Text("Switch to \(setupVm.lanConnectSsid) in Wi-Fi Settings")
                            .font(.system(size: 14, weight: .medium))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundStyle(AtlasTheme.primary)
                    .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(AtlasTheme.primary, lineWidth: 1))
                }
            }
        }
    }

    private var formBlock: some View {
        VStack(spacing: 14) {
            if let err = setupVm.lanConnectError {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(AtlasTheme.error)
                    Text(err).font(.system(size: 13)).foregroundStyle(AtlasTheme.error)
                    Spacer(minLength: 0)
                }
                .padding(14)
                .atlasCard()
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("LAN credentials")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(AtlasTheme.onBackground)
                Text("Use the exact SSID and password for the LAN you want Atlas to join. Leave the password blank only for an open network.")
                    .font(.system(size: 12))
                    .foregroundStyle(AtlasTheme.muted)

                TextField("LAN SSID", text: $ssid)
                    .textFieldStyle(AtlasTextFieldStyle())
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .submitLabel(.next)

                HStack {
                    if showPassword {
                        TextField("LAN password", text: $password)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                    } else {
                        SecureField("LAN password", text: $password)
                    }
                    Button {
                        showPassword.toggle()
                    } label: {
                        Image(systemName: showPassword ? "eye.slash" : "eye")
                            .foregroundStyle(AtlasTheme.muted)
                    }
                }
                .textFieldStyle(AtlasTextFieldStyle())

                Text("Atlas remains paired to the device you already found on the hotspot. This step only changes which Wi-Fi network that same Atlas uses.")
                    .font(.system(size: 12))
                    .foregroundStyle(AtlasTheme.muted)
            }
            .padding(16)
            .atlasCard()

            Spacer().frame(height: 6)

            PrimaryButton(text: "Connect", systemImage: "wifi", enabled: !ssid.trimmingCharacters(in: .whitespaces).isEmpty) {
                setupVm.connectToLan(ssid: ssid.trimmingCharacters(in: .whitespaces), password: password)
            }

            Button {
                setupVm.completeSetup(appVm: appVm)
            } label: {
                Text("Skip for now")
                    .font(.system(size: 14))
                    .foregroundStyle(AtlasTheme.muted)
            }
            .padding(.top, 4)
        }
    }
}

// MARK: - Shared building blocks

private struct WizardScroll<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        ScrollView {
            VStack(spacing: 0, content: { content })
                .padding(.horizontal, 24)
        }
    }
}

struct AtlasTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .font(.system(size: 15))
            .foregroundStyle(AtlasTheme.onBackground)
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(AtlasTheme.background.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(AtlasTheme.muted.opacity(0.4), lineWidth: 1)
            )
    }
}

struct PrimaryButton: View {
    let text: String
    var systemImage: String? = nil
    var enabled: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let systemImage {
                    Image(systemName: systemImage).font(.system(size: 16))
                }
                Text(text).font(.system(size: 16, weight: .bold))
            }
            .frame(maxWidth: .infinity)
            .frame(height: 54)
            .background(enabled ? AtlasTheme.primary : AtlasTheme.muted.opacity(0.25))
            .foregroundStyle(enabled ? AtlasTheme.background : AtlasTheme.muted.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .disabled(!enabled)
    }
}

private func isHotspotUrl(_ url: String) -> Bool {
    url.contains("10.42.0.")   ||
    url.contains("192.168.4.") ||
    url.contains("192.168.43.")
}

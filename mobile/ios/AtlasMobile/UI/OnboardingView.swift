import SwiftUI

/// First run: find the Atlas and pair with it once.
///
///   Open app → Atlas appears → tap Connect → (one more tap / scan) → Atlas opens.
struct OnboardingView: View {
    @ObservedObject var model: ConnectionModel
    @ObservedObject private var localNetwork = LocalNetworkAccess.shared
    @State private var scanning = false
    @State private var advanced = false
    @State private var address = ""
    @State private var code = ""
    @State private var wifiPasswordTyped = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Connect to your Atlas").font(.title.bold()).foregroundColor(AtlasTheme.onBackground)
                Text("You only do this once. No account or password is needed.")
                    .font(.subheadline).foregroundColor(AtlasTheme.muted)

                if !localNetwork.primed && model.nearby.isEmpty && !model.searching && model.target == nil {
                    LocalNetworkPrimer { model.startSearching() }
                } else if let label = model.joiningWifi {
                    joinWifi(label)
                } else if let match = model.matchCode {
                    approvalWait(match)
                } else if let t = model.target {
                    pairCard(t)
                } else {
                    Button { scanning = true } label: {
                        Label("Scan the code on your Atlas", systemImage: "qrcode.viewfinder")
                            .frame(maxWidth: .infinity).padding(.vertical, 14)
                    }
                    .buttonStyle(.borderedProminent)

                    if localNetwork.denied { LocalNetworkDeniedCard() }
                    HStack {
                        Text("Atlas devices nearby").font(.headline).foregroundColor(AtlasTheme.onBackground)
                        Spacer()
                        if model.searching { ProgressView() } else { Button("Search again") { model.searchNearby() } }
                    }
                    if model.nearby.isEmpty && !model.searching {
                        Text("None found yet. Make sure Atlas is switched on and this iPhone is on the same Wi-Fi, or on Atlas's own Wi-Fi (its name and password are on the label).")
                            .font(.footnote).foregroundColor(AtlasTheme.muted)
                    }
                    ForEach(model.nearby) { s in
                        Button { model.choose(s.baseUrl, expectedPin: s.pin) } label: {
                            HStack {
                                Image(systemName: "wifi.router").foregroundColor(AtlasTheme.primary)
                                VStack(alignment: .leading) {
                                    Text(s.name).foregroundColor(AtlasTheme.onBackground).bold()
                                    // Its own .local name (also on the label) tells two nearby Atlases apart.
                                    Text(s.hostname ?? String(s.deviceId.prefix(8))).font(.caption).foregroundColor(AtlasTheme.muted)
                                }
                                Spacer()
                                Text("Connect").bold().foregroundColor(AtlasTheme.primary)
                            }
                            .padding()
                            .atlasCard(corner: 12)
                        }
                    }
                    Button(advanced ? "Hide advanced options" : "Advanced options") { advanced.toggle() }
                    if advanced {
                        TextField("atlas-7kq3pm.local or 192.168.1.20", text: $address)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .keyboardType(.URL)
                            .textFieldStyle(.roundedBorder)
                        Text("Its .local name is on the label and in Atlas's Settings.")
                            .font(.caption).foregroundColor(AtlasTheme.muted)
                        Button("Connect to this address") {
                            if !address.trimmingCharacters(in: .whitespaces).isEmpty { model.chooseTyped(address) }
                        }
                    }
                }
                if let error = model.pairingError {
                    Text(error).foregroundColor(AtlasTheme.error).font(.subheadline)
                }
            }
            .padding(24)
        }
        .background(AtlasTheme.background.ignoresSafeArea())
        .sheet(isPresented: $scanning) {
            ZStack(alignment: .bottom) {
                QrScannerView { text in
                    scanning = false
                    if let label = QrPayload.parse(text) { model.pairWithLabel(label) }
                }
                .ignoresSafeArea()
                Button("Cancel") { scanning = false }.padding(24).foregroundColor(.white)
            }
        }
    }

    private func pairCard(_ t: PairingTarget) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(t.info.name).font(.title3.bold()).foregroundColor(AtlasTheme.onBackground)
            if t.onAtlasHotspot {
                HStack { ProgressView(); Text("Connecting…").foregroundColor(AtlasTheme.muted) }
            } else {
                Text("Enter the setup code from the label on your Atlas:").foregroundColor(AtlasTheme.onSurface)
                TextField("XXXX-XXXX-XXXX", text: Binding(
                    get: { code },
                    set: { typed in
                        wifiPasswordTyped = SetupCode.looksLikeWifiPassword(typed)
                        code = SetupCode.format(typed)
                    }))
                    .font(.system(.title3, design: .monospaced))
                    .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                Text(wifiPasswordTyped ? "That's the Wi-Fi password. Use the code labelled Setup code."
                                       : "3 groups of 4 capitals and digits — not the Wi-Fi password")
                    .font(.footnote)
                    .foregroundColor(wifiPasswordTyped ? AtlasTheme.warning : AtlasTheme.muted)
                Button("Connect") { model.pairWithCode(code) }
                    .buttonStyle(.borderedProminent)
                    .disabled(!SetupCode.isComplete(code))
                Button("Scan the label instead") { scanning = true }
                Button("Ask someone using this Atlas to allow this iPhone") { model.askForApproval() }
            }
            Button("Back") { model.cancelPairing() }
        }
        .padding(18)
        .atlasCard()
    }

    private func approvalWait(_ match: String) -> some View {
        VStack(spacing: 14) {
            Text("On a phone or computer already using this Atlas, tap Allow. Check both screens show:")
                .multilineTextAlignment(.center).foregroundColor(AtlasTheme.onSurface)
            Text(match).font(.system(size: 40, weight: .bold, design: .monospaced)).kerning(8)
                .foregroundColor(AtlasTheme.onBackground)
            Text("If the codes differ, tap Don't allow — someone may be interfering with your network.")
                .font(.caption).multilineTextAlignment(.center).foregroundColor(AtlasTheme.muted)
            ProgressView()
            Button("Cancel") { model.cancelPairing() }
        }
        .frame(maxWidth: .infinity)
    }

    private func joinWifi(_ label: AtlasLabel) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Joining Atlas's Wi-Fi").font(.title3.bold()).foregroundColor(AtlasTheme.onBackground)
            Text(label.ssid ?? "Atlas").font(.title2.bold()).foregroundColor(AtlasTheme.primary)
            Text("Tap Join when iOS asks. Atlas connects automatically afterwards.")
                .foregroundColor(AtlasTheme.onSurface)
            HStack { ProgressView(); Text("Waiting for the Wi-Fi connection…").font(.footnote).foregroundColor(AtlasTheme.muted) }
            Button("Cancel") { model.cancelPairing() }
        }
    }
}

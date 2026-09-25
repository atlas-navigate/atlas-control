import SwiftUI

/// First run: find the Atlas and pair with it once.
///
///   Open app → Atlas appears → tap Connect → (one more tap / scan) → Atlas opens.
struct OnboardingView: View {
    @ObservedObject var model: ConnectionModel
    @State private var scanning = false
    @State private var advanced = false
    @State private var address = ""
    @State private var code = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Connect to your Atlas").font(.title.bold()).foregroundColor(AtlasTheme.onBackground)
                Text("You only do this once. No account or password is needed.")
                    .font(.subheadline).foregroundColor(AtlasTheme.muted)

                if let label = model.joiningWifi {
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
                                    Text(String(s.deviceId.prefix(8))).font(.caption).foregroundColor(AtlasTheme.muted)
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
                        TextField("Atlas address (e.g. 192.168.1.20)", text: $address)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .textFieldStyle(.roundedBorder)
                        Button("Connect to this address") {
                            let a = address.replacingOccurrences(of: "https://", with: "")
                                .replacingOccurrences(of: "http://", with: "")
                                .trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
                            if !a.isEmpty { model.choose("https://\(a)") }
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
                TextField("XXXX-XXXX-XXXX", text: $code)
                    .font(.system(.title3, design: .monospaced))
                    .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                Button("Connect") { model.pairWithCode(code) }
                    .buttonStyle(.borderedProminent)
                    .disabled(code.filter { $0.isLetter || $0.isNumber }.count < 8)
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

import SwiftUI
import UIKit

/// Shown once, right after pairing: this Atlas's own address on the local network.
struct YourAtlasAddressView: View {
    let name: String
    let hostname: String
    let onContinue: () -> Void
    @State private var copied = false

    var body: some View {
        VStack(spacing: 16) {
            Text("\(name) is connected").font(.title2.bold()).multilineTextAlignment(.center)
                .foregroundColor(AtlasTheme.onBackground)
            Text("This app finds Atlas by itself from now on. On the same Wi-Fi, any phone or computer can also open it at its own address:")
                .multilineTextAlignment(.center).foregroundColor(AtlasTheme.onSurface)
            HStack {
                Text(hostname).font(.system(.title3, design: .monospaced).bold()).foregroundColor(AtlasTheme.primary)
                    .lineLimit(1).minimumScaleFactor(0.7)
                Button {
                    UIPasteboard.general.string = hostname
                    copied = true
                } label: {
                    Image(systemName: copied ? "checkmark" : "doc.on.doc")
                }
                .accessibilityLabel("Copy address")
            }
            .padding(.horizontal, 18).padding(.vertical, 14)
            .background(AtlasTheme.surface2)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            Text("No need to remember an IP address, which your router may change. It's also in Atlas's Settings → Local Device Address.")
                .font(.footnote).multilineTextAlignment(.center).foregroundColor(AtlasTheme.muted)
            Button(action: onContinue) {
                Text("Open Atlas").bold().frame(maxWidth: .infinity).padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(28)
    }
}

/// The paired Atlas's address details while it can't be reached.
struct DeviceDetailsView: View {
    let name: String
    let hostname: String?
    let lastAddress: String?
    var connected = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Local device address").font(.caption.bold()).foregroundColor(AtlasTheme.muted)
            row("Device name", name)
            if let hostname {
                HStack {
                    row("Local hostname", hostname)
                    Button { UIPasteboard.general.string = hostname } label: { Image(systemName: "doc.on.doc") }
                        .accessibilityLabel("Copy hostname")
                }
            }
            row("Last IP address", lastAddress ?? "—")
            HStack {
                Text("Status").font(.subheadline).foregroundColor(AtlasTheme.muted).frame(width: 120, alignment: .leading)
                Circle().fill(connected ? AtlasTheme.success : AtlasTheme.error).frame(width: 9, height: 9)
                Text(connected ? "Connected" : "Disconnected").foregroundColor(AtlasTheme.onBackground)
            }
        }
        .padding(16)
        .atlasCard(corner: 12)
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.subheadline).foregroundColor(AtlasTheme.muted).frame(width: 120, alignment: .leading)
            Text(value).bold().foregroundColor(AtlasTheme.onBackground).lineLimit(1).minimumScaleFactor(0.7)
        }
    }
}

/// Before the first search: why iOS is about to ask for Local Network access.
struct LocalNetworkPrimer: View {
    let onContinue: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Find Atlas on your Wi-Fi", systemImage: "wifi").font(.headline)
                .foregroundColor(AtlasTheme.onBackground)
            Text("Atlas lives on your local network, not on the internet. Next, iOS asks whether this app may find devices on your network — tap Allow so it can find your Atlas. Nothing leaves your network.")
                .foregroundColor(AtlasTheme.onSurface)
            Button(action: onContinue) {
                Text("Continue").bold().frame(maxWidth: .infinity).padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(18)
        .atlasCard()
    }
}

/// Local Network access was turned off for this app.
struct LocalNetworkDeniedCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Allow access to your Wi-Fi network", systemImage: "wifi.exclamationmark").font(.headline)
                .foregroundColor(AtlasTheme.onBackground)
            Text("iOS is blocking this app from finding devices on your network, so it can't reach Atlas. Turn on Local Network for Atlas Control in Settings.")
                .font(.subheadline).foregroundColor(AtlasTheme.onSurface)
            Button("Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(16)
        .atlasCard(corner: 12)
    }
}

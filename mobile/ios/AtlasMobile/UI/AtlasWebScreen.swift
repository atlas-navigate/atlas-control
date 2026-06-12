import SwiftUI
import WebKit

/// Hosts the Atlas web app in a `WKWebView` and exposes the same JavaScript
/// surface the Android port does, so the React app's existing
/// `window.AtlasAndroid.*` bridge continues to work unchanged.
struct AtlasWebScreen: View {
    @ObservedObject var appVm: AppViewModel
    @ObservedObject var setupVm: SetupViewModel
    @StateObject private var coordinator = AtlasWebCoordinator()
    @State private var pageError = false
    @State private var isLoading = true
    @State private var loadingProgress: Double = 0

    private var homeUrl: URL? {
        guard let base = appVm.baseUrl else { return URL(string: "http://atlas.local:5000/mobile") }
        var trimmed = base
        if !trimmed.hasSuffix("/") { trimmed += "/" }
        return URL(string: trimmed + "mobile")
    }

    var body: some View {
        ZStack(alignment: .top) {
            AtlasTheme.background.ignoresSafeArea()

            if let url = homeUrl {
                AtlasWebViewRepresentable(
                    url: url,
                    coordinator: coordinator,
                    appVm: appVm,
                    setupVm: setupVm,
                    isLoading: $isLoading,
                    loadingProgress: $loadingProgress,
                    pageError: $pageError
                )
                .ignoresSafeArea(edges: .bottom)
            }

            if isLoading {
                GeometryReader { proxy in
                    Rectangle()
                        .fill(AtlasTheme.primary)
                        .frame(width: proxy.size.width * loadingProgress, height: 2)
                        .animation(.easeInOut(duration: 0.2), value: loadingProgress)
                }
                .frame(height: 2)
            }

            if isLoading && loadingProgress < 0.15 {
                ProgressView()
                    .tint(AtlasTheme.primary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            if pageError {
                pageErrorOverlay
            }
        }
        .background(AtlasTheme.background)
        .onChange(of: appVm.state) { _, newState in
            if newState == .connected && pageError {
                pageError = false
                coordinator.reload()
            }
        }
        .onChange(of: appVm.baseUrl) { _, _ in
            pageError = false
            if let url = homeUrl {
                coordinator.load(url: url)
            }
        }
    }

    private var pageErrorOverlay: some View {
        ZStack {
            AtlasTheme.background.opacity(0.95).ignoresSafeArea()
            VStack(spacing: 20) {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 48))
                    .foregroundStyle(AtlasTheme.error)
                Text("Atlas unreachable")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(AtlasTheme.onBackground)
                Text("Make sure your phone is on the atlas_navigate hotspot or the same LAN as Atlas.")
                    .font(.system(size: 13))
                    .foregroundStyle(AtlasTheme.muted)
                    .multilineTextAlignment(.center)
                Button {
                    pageError = false
                    appVm.retry()
                } label: {
                    HStack {
                        Image(systemName: "arrow.clockwise")
                        Text("Retry").bold()
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 48)
                    .background(AtlasTheme.primary)
                    .foregroundStyle(AtlasTheme.background)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
            }
            .padding(28)
            .frame(maxWidth: .infinity)
            .atlasCard(corner: 16)
            .padding(.horizontal, 32)
        }
    }
}

// MARK: - WKWebView bridge

@MainActor
final class AtlasWebCoordinator: NSObject, ObservableObject {
    fileprivate weak var webView: WKWebView?

    func load(url: URL) {
        webView?.stopLoading()
        webView?.load(URLRequest(url: url))
    }

    func reload() {
        webView?.stopLoading()
        webView?.reload()
    }
}

/// `UIViewRepresentable` wrapper around `WKWebView`.  The user-content
/// controller injects a `window.AtlasAndroid` shim with the same four
/// methods Android exposes, so the React app's existing bridge calls
/// (`reconnect`, `showSetupWizard`, `lanSwitching`, `lanSwitchedTo`) work
/// without server-side changes.
struct AtlasWebViewRepresentable: UIViewRepresentable {
    let url: URL
    let coordinator: AtlasWebCoordinator
    let appVm: AppViewModel
    let setupVm: SetupViewModel
    @Binding var isLoading: Bool
    @Binding var loadingProgress: Double
    @Binding var pageError: Bool

    func makeCoordinator() -> Bridge {
        Bridge(parent: self)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        let userContent = WKUserContentController()

        // Bridge that React calls via window.AtlasAndroid.*  We register a
        // single message channel ("atlas") and switch on the operation name
        // sent in the payload.
        userContent.add(context.coordinator, name: "atlas")

        // Inject window.AtlasAndroid before the page's own scripts run so
        // any feature-detection succeeds on first paint.
        let bridgeJs = """
        (function() {
            const post = (op, payload) => {
                try {
                    window.webkit.messageHandlers.atlas.postMessage(Object.assign({op}, payload || {}));
                } catch (e) {}
            };
            const bridge = {
                reconnect:      function() { post('reconnect'); },
                showSetupWizard:function() { post('showSetupWizard'); },
                lanSwitching:   function() { post('lanSwitching'); },
                lanSwitchedTo:  function(urlsJson, isPending, hintIp) {
                    post('lanSwitchedTo', { urlsJson: String(urlsJson), isPending: !!isPending, hintIp: String(hintIp || '') });
                }
            };
            // The web app branches on window.AtlasAndroid; we also expose
            // window.AtlasIOS so any iOS-only checks the React app may add
            // later still see us.
            window.AtlasAndroid = bridge;
            window.AtlasIOS = bridge;
        })();
        """
        let bridgeScript = WKUserScript(
            source: bridgeJs,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: false
        )
        userContent.addUserScript(bridgeScript)

        configuration.userContentController = userContent
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        // The web app's `isAtlasMobileClient()` matches `AtlasMobile(Android|IOS)/`
        // in the user agent.  `applicationNameForUserAgent` is the supported way
        // to append a suffix without replacing the Safari/iOS portion.
        configuration.applicationNameForUserAgent = "AtlasMobileIOS/1.0"
        // Persist DOM storage / cookies so the React app keeps client-side
        // settings between launches; we still bypass the HTTP cache on every
        // request below so a stale page never sticks around.

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0x09 / 255.0, green: 0x11 / 255.0, blue: 0x1C / 255.0, alpha: 1.0)
        webView.scrollView.backgroundColor = webView.backgroundColor
        webView.scrollView.bounces = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.allowsBackForwardNavigationGestures = true
        webView.allowsLinkPreview = false
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.addObserver(context.coordinator, forKeyPath: #keyPath(WKWebView.estimatedProgress), options: .new, context: nil)

        coordinator.webView = webView
        webView.load(Self.cacheBypassingRequest(url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        if let current = webView.url, current.absoluteString != url.absoluteString {
            webView.load(Self.cacheBypassingRequest(url))
        }
    }

    /// Match Android's `cacheMode = LOAD_NO_CACHE`: ignore both the local
    /// disk cache and any upstream cache.  Local storage / cookies are still
    /// preserved by the persistent data store.
    static func cacheBypassingRequest(_ url: URL) -> URLRequest {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        return request
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Bridge) {
        webView.removeObserver(coordinator, forKeyPath: #keyPath(WKWebView.estimatedProgress))
        webView.stopLoading()
        webView.configuration.userContentController.removeAllScriptMessageHandlers()
    }

    // MARK: - Bridge / nav delegate

    final class Bridge: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let parent: AtlasWebViewRepresentable

        init(parent: AtlasWebViewRepresentable) {
            self.parent = parent
        }

        // MARK: WKNavigationDelegate

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            DispatchQueue.main.async {
                self.parent.isLoading = true
                self.parent.loadingProgress = 0.05
                self.parent.pageError = false
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            DispatchQueue.main.async {
                self.parent.isLoading = false
                self.parent.loadingProgress = 1
            }
            // Defensive panel collapse — matches the Android version's safety
            // net so any cached open-state survives a hot reload.
            let collapseJs = """
            (function(){try{
                var c=document.querySelector('.contacts-panel-toggle.open'); if(c) c.click();
                var n=document.querySelector('.nav-directions-toggle.open');  if(n) n.click();
            }catch(e){}})();
            """
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                webView.evaluateJavaScript(collapseJs, completionHandler: nil)
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            handleLoadFailure()
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            handleLoadFailure()
        }

        private func handleLoadFailure() {
            DispatchQueue.main.async {
                self.parent.isLoading = false
                self.parent.pageError = true
            }
        }

        /// Accept Atlas's self-signed TLS certificate for `https://` Atlas
        /// hosts — the iOS counterpart of Android's
        /// `onReceivedSslError { handler.proceed() }`. Without this, an
        /// `https://` Atlas page (served by nginx with a self-signed cert)
        /// fails to load in the WebView. External hosts (e.g. map-attribution
        /// links) keep normal certificate validation.
        func webView(
            _ webView: WKWebView,
            didReceive challenge: URLAuthenticationChallenge,
            completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
        ) {
            guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
                  let trust = challenge.protectionSpace.serverTrust else {
                completionHandler(.performDefaultHandling, nil)
                return
            }
            if isAtlasHostName(challenge.protectionSpace.host.lowercased(),
                               currentHost: webView.url?.host) {
                completionHandler(.useCredential, URLCredential(trust: trust))
            } else {
                completionHandler(.performDefaultHandling, nil)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow); return
            }
            // Keep the Atlas UI and all of its own navigation inside the
            // WebView; only a deliberate tap on a link to a *different,
            // non-Atlas* host leaves the app, and mailto/tel/sms go to the
            // system handler.
            switch url.scheme?.lowercased() {
            case "http", "https":
                // Initial loads, client-side redirects, form submits and any
                // other programmatic navigation (navigationType != .linkActivated)
                // ALWAYS stay in-app — otherwise the Atlas page itself gets
                // bounced to Safari whenever its host falls outside the static
                // private-range allowlist (a custom router hostname, a VPN/CGNAT
                // address, a `.lan`/`.home` domain, etc.). Only a tapped link to
                // a genuinely external host opens Safari.
                let isMainFrame = navigationAction.targetFrame?.isMainFrame ?? false
                if !isMainFrame
                    || navigationAction.navigationType != .linkActivated
                    || isAtlasHost(url, currentHost: webView.url?.host) {
                    decisionHandler(.allow)
                } else {
                    UIApplication.shared.open(url, options: [:], completionHandler: nil)
                    decisionHandler(.cancel)
                }
            case "mailto", "tel", "sms":
                UIApplication.shared.open(url, options: [:], completionHandler: nil)
                decisionHandler(.cancel)
            default:
                decisionHandler(.allow)
            }
        }

        // MARK: WKUIDelegate

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            // target="_blank" / window.open(): keep Atlas content in the same
            // view; send genuinely external links (e.g. the map-attribution
            // links) to Safari instead of replacing the Atlas UI in-app.
            if let url = navigationAction.request.url {
                if isAtlasHost(url, currentHost: webView.url?.host) {
                    webView.load(URLRequest(url: url))
                } else {
                    UIApplication.shared.open(url, options: [:], completionHandler: nil)
                }
            }
            return nil
        }

        // MARK: WKScriptMessageHandler

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "atlas",
                  let payload = message.body as? [String: Any],
                  let op = payload["op"] as? String else { return }
            let appVm = parent.appVm
            let setupVm = parent.setupVm
            DispatchQueue.main.async {
                switch op {
                case "reconnect":
                    appVm.retry()
                case "showSetupWizard":
                    setupVm.resetForReconnect()
                    appVm.showSetupWizard()
                case "lanSwitching":
                    appVm.onLanSwitchInitiated()
                case "lanSwitchedTo":
                    let urlsJson = (payload["urlsJson"] as? String) ?? "[]"
                    let isPending = (payload["isPending"] as? Bool) ?? false
                    let hintIp = (payload["hintIp"] as? String) ?? ""
                    let urls: [String] = (try? JSONSerialization.jsonObject(with: Data(urlsJson.utf8)) as? [String]) ?? []
                    appVm.onLanSwitchConfirmed(
                        newUrls: urls,
                        isPending: isPending,
                        hintIp: hintIp.isEmpty ? nil : hintIp
                    )
                default:
                    break
                }
            }
        }

        // MARK: KVO

        override func observeValue(
            forKeyPath keyPath: String?,
            of object: Any?,
            change: [NSKeyValueChangeKey: Any]?,
            context: UnsafeMutableRawPointer?
        ) {
            guard keyPath == #keyPath(WKWebView.estimatedProgress),
                  let webView = object as? WKWebView else { return }
            let value = webView.estimatedProgress
            DispatchQueue.main.async {
                self.parent.loadingProgress = value
                self.parent.isLoading = value < 1
            }
        }

        // MARK: Helpers

        private func isAtlasHost(_ url: URL, currentHost: String? = nil) -> Bool {
            guard let host = url.host?.lowercased() else { return false }
            return isAtlasHostName(host, currentHost: currentHost)
        }

        /// True when [host] is the Atlas device. Mirrors Android's
        /// `isAtlasHost` (the `atlas.local`/`atlas`/`localhost` names, any
        /// `.local` mDNS name, and private / link-local IPv4 ranges) — plus a
        /// trust of [currentHost], the host of the page already loaded in the
        /// WebView. That trust is what lets a discovered Atlas address which
        /// sits *outside* the static private ranges (a custom router hostname,
        /// a VPN/CGNAT address) still count as Atlas, so the UI never escapes
        /// to Safari.
        private func isAtlasHostName(_ host: String, currentHost: String? = nil) -> Bool {
            if let currentHost, !currentHost.isEmpty, host == currentHost.lowercased() { return true }
            if ["atlas.local", "atlas", "localhost"].contains(host) { return true }
            if host.hasSuffix(".local") { return true }
            // Numeric IPv4 — treat private and link-local as Atlas hosts.
            let parts = host.split(separator: ".").compactMap { Int($0) }
            if parts.count == 4 {
                let a = parts[0], b = parts[1]
                if a == 10 { return true }
                if a == 172 && (16...31).contains(b) { return true }
                if a == 192 && b == 168 { return true }
                if a == 169 && b == 254 { return true }
                if a == 127 { return true }
            }
            return false
        }
    }
}

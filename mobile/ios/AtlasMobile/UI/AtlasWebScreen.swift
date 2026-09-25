import SwiftUI
import WebKit

/// The Atlas web UI in a WKWebView that trusts only the paired Atlas's key.
struct AtlasWebScreen: UIViewRepresentable {
    @ObservedObject var model: ConnectionModel
    let baseUrl: String

    func makeCoordinator() -> Coordinator { Coordinator(model: model, baseUrl: baseUrl) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        content.add(context.coordinator, name: "atlas")
        // window.AtlasAndroid / window.AtlasIOS, as the web UI expects. Main
        // frame only: nothing embedded may call into the app.
        let bridge = """
        (function() {
          const post = (op, payload) => { try { window.webkit.messageHandlers.atlas.postMessage(Object.assign({op}, payload || {})); } catch (e) {} };
          const bridge = {
            reconnect: function() { post('reconnect'); },
            showSetupWizard: function() { post('reconnect'); },
            lanSwitching: function() { post('lanSwitching'); },
            lanSwitchedTo: function(urlsJson, isPending, hintIp) {
              post('lanSwitchedTo', { urlsJson: String(urlsJson), hintIp: String(hintIp || '') });
            }
          };
          window.AtlasAndroid = bridge;
          window.AtlasIOS = bridge;
        })();
        """
        content.addUserScript(WKUserScript(source: bridge, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController = content
        config.applicationNameForUserAgent = "AtlasMobileIOS/2.0"
        config.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0x09 / 255.0, green: 0x11 / 255.0, blue: 0x1C / 255.0, alpha: 1)
        webView.scrollView.backgroundColor = webView.backgroundColor
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        context.coordinator.load(into: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let model: ConnectionModel
        let origin: String
        let host: String?

        init(model: ConnectionModel, baseUrl: String) {
            self.model = model
            self.origin = AtlasClient.normalize(baseUrl)
            self.host = URL(string: baseUrl)?.host
        }

        @MainActor func load(into webView: WKWebView) {
            guard let device = model.device, let url = URL(string: "\(origin)/mobile"), let host else { return }
            // The pairing token rides as the same HttpOnly cookie a browser would get.
            let cookie = HTTPCookie(properties: [
                .name: "atlas_client", .value: device.token, .domain: host, .path: "/",
                .secure: "TRUE", HTTPCookiePropertyKey("HttpOnly"): "TRUE",
                .maximumAge: "315360000", .sameSitePolicy: HTTPCookieStringPolicy.sameSiteStrict,
            ])
            let store = webView.configuration.websiteDataStore.httpCookieStore
            if let cookie {
                store.setCookie(cookie) { webView.load(URLRequest(url: url)) }
            } else {
                webView.load(URLRequest(url: url))
            }
        }

        private func isAtlas(_ url: URL?) -> Bool { url?.scheme == "https" && url?.host == host }

        // Atlas's certificate is self-signed: accept it only if it is the key we paired with.
        func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                     completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
            guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
                  challenge.protectionSpace.host == host,
                  let trust = challenge.protectionSpace.serverTrust,
                  let pin = AtlasTrust.spkiPin(trust)
            else { completionHandler(.performDefaultHandling, nil); return }
            let expected = MainActor.assumeIsolated { model.device?.pin }
            if pin == expected {
                completionHandler(.useCredential, URLCredential(trust: trust))
            } else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                Task { @MainActor in self.model.relocate() }
            }
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            let url = action.request.url
            if isAtlas(url) { decisionHandler(.allow); return }
            if let url, ["http", "https", "mailto", "tel", "sms", "maps"].contains(url.scheme ?? "") {
                UIApplication.shared.open(url)
            }
            decisionHandler(.cancel)            // javascript:, file:, data: … never navigate
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            Task { @MainActor in self.model.relocate("Reconnecting to \(self.model.deviceName)…") }
        }

        // Camera and microphone are never needed by the Atlas web UI.
        func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            decisionHandler(.deny)
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, isAtlas(message.frameInfo.request.url),
                  let body = message.body as? [String: Any], let op = body["op"] as? String else { return }
            Task { @MainActor in
                switch op {
                case "reconnect": self.model.relocate()
                case "lanSwitching": self.model.atlasSwitchingNetworks()
                case "lanSwitchedTo":
                    var urls = (try? JSONSerialization.jsonObject(with: Data((body["urlsJson"] as? String ?? "[]").utf8))) as? [String] ?? []
                    if let hint = body["hintIp"] as? String, !hint.isEmpty { urls.append("https://\(hint)") }
                    self.model.atlasSwitchedNetworks(urls)
                default: break
                }
            }
        }
    }
}

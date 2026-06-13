package com.atlascontrol.mobile.ui.web

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.atlascontrol.mobile.*
import com.atlascontrol.mobile.setup.SetupViewModel
import com.atlascontrol.mobile.ui.AppViewModel
import com.atlascontrol.mobile.ui.ConnectionState
import java.net.Inet6Address
import java.net.URI

// Renderer-recovery tuning. Android kills the WebView renderer process under
// memory pressure (didCrash=false). The old handler reloaded the *same* heavy
// page instantly with no cap, so one kill became an infinite
// reload→re-OOM→kill spiral that took the whole app process down
// ("Channel is unrecoverably broken"). We now reload with escalating backoff
// and give up after a few rapid kills, surfacing the error overlay instead.
private const val RENDERER_CRASH_BACKOFF_MS = 1_500L       // base; escalates per consecutive kill
private const val RENDERER_CRASH_MAX_BACKOFF_MS = 30_000L  // cap — never reload faster than this under sustained pressure
private const val RENDERER_CRASH_RESET_MS = 60_000L        // a kill this long after the last is transient → reset escalation

private class AtlasJsBridge(
    private val vm: AppViewModel,
    private val setupVm: SetupViewModel,
) {
    @android.webkit.JavascriptInterface
    fun reconnect() {
        android.os.Handler(android.os.Looper.getMainLooper()).post { vm.retry() }
    }

    @android.webkit.JavascriptInterface
    fun showSetupWizard() {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            setupVm.resetForReconnect()
            vm.showSetupWizard()
        }
    }

    @android.webkit.JavascriptInterface
    fun lanSwitching() {
        android.os.Handler(android.os.Looper.getMainLooper()).post { vm.onLanSwitchInitiated() }
    }

    /**
     * Called after /api/wifi/connect responds, carrying the access URLs the
     * server returned for Atlas's new LAN address.
     *
     * [urlsJson]  — JSON array of URL strings (may include the real LAN IP and/or atlas.local).
     * [isPending] — true when Atlas is still switching in the background.
     * [hintIp]    — the last-known IP Atlas had on this SSID (empty string if unknown).
     */
    @android.webkit.JavascriptInterface
    fun lanSwitchedTo(urlsJson: String, isPending: Boolean, hintIp: String = "") {
        val urls = try {
            val arr = org.json.JSONArray(urlsJson)
            (0 until arr.length()).map { arr.getString(it) }
        } catch (_: Exception) { emptyList() }
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            vm.onLanSwitchConfirmed(urls, isPending, hintIp.takeIf { it.isNotBlank() })
        }
    }
}

@Composable
fun AtlasWebScreen(appVm: AppViewModel, setupVm: SetupViewModel) {
    val context = LocalContext.current
    val baseUrl by appVm.baseUrl.collectAsState()
    val connectionState by appVm.state.collectAsState()
    val homeUrl by remember {
        derivedStateOf {
            val base = baseUrl ?: "http://atlas.local:5000/"
            val b    = if (base.endsWith("/")) base else "$base/"
            "${b}mobile"
        }
    }
    val initialUrl = homeUrl

    var canGoBack       by remember { mutableStateOf(false) }
    var isLoading       by remember { mutableStateOf(true) }
    var loadingProgress by remember { mutableIntStateOf(0) }
    var pageError       by remember { mutableStateOf(false) }

    val webView = remember {
        WebView(context).apply {
            overScrollMode = WebView.OVER_SCROLL_NEVER
            setBackgroundColor(android.graphics.Color.parseColor("#09111C"))
            settings.userAgentString = "${settings.userAgentString} AtlasMobileAndroid/1.0"
        }
    }

    var currentWebUrl by remember { mutableStateOf(initialUrl) }
    LaunchedEffect(homeUrl) {
        if (homeUrl != currentWebUrl) {
            currentWebUrl = homeUrl
            webView.loadUrl(homeUrl)
            pageError = false
        }
    }

    LaunchedEffect(connectionState, homeUrl, pageError) {
        if (connectionState == ConnectionState.CONNECTED && pageError) {
            pageError = false
            webView.stopLoading()
            appVm.retry()
        }
    }

    BackHandler(enabled = canGoBack) { webView.goBack() }

    DisposableEffect(webView) {
        onDispose {
            webView.stopLoading()
            webView.destroy()
        }
    }

    Box(Modifier.fillMaxSize().background(AtlasBackground).statusBarsPadding()) {

        // Full-screen WebView — no top bar
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory  = {
                webView.addJavascriptInterface(AtlasJsBridge(appVm, setupVm), "AtlasAndroid")
                configureWebView(
                    webView           = webView,
                    onLoadingChanged  = { isLoading = it },
                    onProgressChanged = { loadingProgress = it },
                    onUrlChanged      = { canGoBack = webView.canGoBack() },
                    onTitleChanged    = {},
                    onPageError       = { pageError = true; isLoading = false },
                    getHomeUrl        = { homeUrl },
                )
                webView.loadUrl(initialUrl)
                webView
            },
            update = { canGoBack = it.canGoBack() },
        )

        // Thin progress bar sitting at the top of content area
        if (isLoading) {
            LinearProgressIndicator(
                progress = { loadingProgress.coerceIn(0, 100) / 100f },
                color    = AtlasPrimary,
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.TopStart),
            )
        }

        // Initial-load spinner
        if (isLoading && loadingProgress < 15) {
            CircularProgressIndicator(
                color    = AtlasPrimary,
                modifier = Modifier.align(Alignment.Center),
            )
        }

        // Page error overlay
        AnimatedVisibility(
            visible  = pageError,
            enter    = fadeIn(),
            exit     = fadeOut(),
            modifier = Modifier.fillMaxSize(),
        ) {
            Box(
                Modifier
                    .fillMaxSize()
                    .background(AtlasBackground.copy(alpha = 0.95f)),
                contentAlignment = Alignment.Center,
            ) {
                Card(
                    modifier = Modifier.padding(32.dp).fillMaxWidth(),
                    shape    = RoundedCornerShape(16.dp),
                    colors   = CardDefaults.cardColors(containerColor = AtlasSurface2),
                ) {
                    Column(
                        Modifier.padding(28.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Icon(Icons.Default.WifiOff, null, tint = AtlasError, modifier = Modifier.size(48.dp))
                        Spacer(Modifier.height(16.dp))
                        Text(
                            "Atlas unreachable",
                            fontSize   = 18.sp,
                            fontWeight = FontWeight.Bold,
                            color      = AtlasOnBg,
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Make sure your phone is on the atlas_navigate hotspot or the same LAN as Atlas.",
                            fontSize  = 13.sp,
                            color     = AtlasMuted,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        )
                        Spacer(Modifier.height(24.dp))
                        Button(
                            onClick  = { pageError = false; appVm.retry() },
                            modifier = Modifier.fillMaxWidth().height(48.dp),
                            shape    = RoundedCornerShape(10.dp),
                            colors   = ButtonDefaults.buttonColors(
                                containerColor = AtlasPrimary,
                                contentColor   = AtlasBackground,
                            ),
                        ) {
                            Icon(Icons.Default.Refresh, null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Retry", fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
    }
}

// ─── WebView configuration ────────────────────────────────────────────────────

@SuppressLint("SetJavaScriptEnabled")
private fun configureWebView(
    webView: WebView,
    onLoadingChanged: (Boolean) -> Unit,
    onProgressChanged: (Int) -> Unit,
    onUrlChanged: (String) -> Unit,
    onTitleChanged: (String) -> Unit,
    onPageError: () -> Unit,
    getHomeUrl: () -> String,
) {
    if (webView.tag == "configured") return
    webView.tag = "configured"

    val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    // Captured by the WebViewClient's onRenderProcessGone below.
    var rendererCrashCount = 0
    var lastRendererCrashMs = 0L

    webView.settings.apply {
        javaScriptEnabled                = true
        domStorageEnabled                = true
        databaseEnabled                  = true
        loadsImagesAutomatically         = true
        mixedContentMode                 = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        useWideViewPort                  = true
        loadWithOverviewMode             = true
        builtInZoomControls              = false
        displayZoomControls              = false
        mediaPlaybackRequiresUserGesture = false
        allowFileAccess                  = true
        allowContentAccess               = true
        setSupportMultipleWindows(false)
        cacheMode                        = android.webkit.WebSettings.LOAD_NO_CACHE
    }

    webView.webViewClient = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            onUrlChanged(url)
            return when (request.url.scheme?.lowercase()) {
                "http", "https" ->
                    if (!request.isForMainFrame || isAtlasHost(url)) false
                    else { openExternalApp(view, request.url); true }
                "mailto", "tel", "sms" -> { openExternalApp(view, request.url); true }
                else -> false
            }
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            onLoadingChanged(true)
            onProgressChanged(5)
            url?.let(onUrlChanged)
        }

        override fun onPageFinished(view: WebView, url: String?) {
            onLoadingChanged(false)
            onProgressChanged(100)
            onTitleChanged(view.title ?: "Atlas Control")
            url?.let(onUrlChanged)
            // Collapse any panels that are open on load.  isAtlasMobileClient() in the
            // web app already initialises them closed for Android, but this JS runs after
            // React renders as a definitive safety net (e.g. if a cached open-state
            // survives a hot-reload or the UA check misfires).
            // Probing view.url to detect a destroyed WebView itself throws
            // "Application attempted to call on a destroyed WebView" (the page
            // can be torn down within this 800ms window). Just attempt the JS
            // and swallow the IllegalStateException if the view is already gone.
            mainHandler.postDelayed({
                runCatching {
                    view.evaluateJavascript(
                        "(function(){try{" +
                            "var c=document.querySelector('.contacts-panel-toggle.open');if(c)c.click();" +
                            "var n=document.querySelector('.nav-directions-toggle.open');if(n)n.click();" +
                            "}catch(e){}})()",
                        null,
                    )
                }
            }, 800L)
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError,
        ) {
            if (request.isForMainFrame) onPageError()
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            if (isAtlasHost(error.url)) handler.proceed() else handler.cancel()
        }

        // When Android kills the renderer process (OOM / memory reclaim), recover
        // instead of letting the OS take down the whole application. Returning
        // true means "we handled it"; we then reload on an escalating, capped
        // backoff (never instantly, never permanently giving up) so the app
        // survives and self-heals once memory pressure subsides.
        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            android.util.Log.w("AtlasWebView", "Renderer process gone (didCrash=${detail.didCrash()})")
            val now = android.os.SystemClock.elapsedRealtime()
            // A kill long after the previous one is transient (backgrounding,
            // a one-off memory spike) — reset the escalation so the common case
            // (single reclaim) recovers in ~1.5s.
            if (now - lastRendererCrashMs > RENDERER_CRASH_RESET_MS) rendererCrashCount = 0
            lastRendererCrashMs = now
            rendererCrashCount++

            // Escalating, capped backoff. The fatal old behaviour reloaded the
            // same heavy page INSTANTLY on every kill, so a page that exhausts
            // memory took the whole app down in a tight reload→re-OOM loop
            // ("Channel is unrecoverably broken"). Capping the cadence keeps the
            // app alive and lets it recover automatically once pressure subsides
            // (which is exactly the didCrash=false / memory-reclaim case).
            // Reload getHomeUrl() rather than view.url, which can be stale/null
            // after a kill; runCatching guards the rare device where touching a
            // post-mortem WebView throws.
            val backoff = (RENDERER_CRASH_BACKOFF_MS * rendererCrashCount)
                .coerceAtMost(RENDERER_CRASH_MAX_BACKOFF_MS)
            mainHandler.postDelayed({
                runCatching {
                    view.clearCache(false)
                    view.loadUrl(getHomeUrl())
                }
            }, backoff)
            return true
        }
    }

    webView.webChromeClient = object : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            onProgressChanged(newProgress)
            onLoadingChanged(newProgress < 100)
        }

        override fun onReceivedTitle(view: WebView, title: String?) {
            onTitleChanged(title ?: "Atlas Control")
        }

        override fun onPermissionRequest(request: PermissionRequest) {
            request.grant(request.resources)
        }

        override fun onGeolocationPermissionsShowPrompt(
            origin: String?,
            callback: GeolocationPermissions.Callback?,
        ) {
            callback?.invoke(origin, true, false)
        }
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

private fun openExternalApp(webView: WebView, uri: Uri) {
    val intent = Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { ContextCompat.startActivity(webView.context, intent, null) }
}

private fun isAtlasHost(url: String): Boolean {
    val host = runCatching { URI(url).host?.lowercase() }.getOrNull() ?: return false
    if (host == "atlas.local" || host == "atlas" || host == "localhost" || host.endsWith(".local")) return true

    // IPv4 literal — classify by range WITHOUT a DNS lookup. This runs on the
    // main thread (shouldOverrideUrlLoading / onReceivedSslError), so a blocking
    // InetAddress.getByName on a hostname would jank the UI. Parsing the literal
    // ourselves also lets us include the CGNAT range (100.64.0.0/10) that
    // Tailscale uses — Java's isSiteLocalAddress() excludes it, which is why
    // reaching Atlas over Tailscale (e.g. 100.110.198.239) was being treated as
    // an external site and kicked out to the browser / SSL-cancelled.
    val octets = host.split(".")
    if (octets.size == 4) {
        val n = octets.map { it.toIntOrNull() }
        if (n.all { it != null && it in 0..255 }) {
            val a = n[0]!!; val b = n[1]!!
            return when {
                a == 10                       -> true   // 10.0.0.0/8 (private + Atlas hotspot)
                a == 172 && b in 16..31       -> true   // 172.16.0.0/12 (private + Docker)
                a == 192 && b == 168          -> true   // 192.168.0.0/16 (private)
                a == 100 && b in 64..127      -> true   // 100.64.0.0/10 (CGNAT / Tailscale)
                a == 127                      -> true   // loopback
                a == 169 && b == 254          -> true   // link-local
                else                          -> false
            }
        }
    }

    // IPv6 literal (rare for Atlas). Only resolve when the host actually looks
    // like one, so an arbitrary external hostname never triggers a main-thread
    // DNS lookup.
    if (host.contains(":")) {
        return runCatching {
            val addr = java.net.InetAddress.getByName(host)
            addr is Inet6Address &&
                (addr.isLoopbackAddress || addr.isSiteLocalAddress || addr.isLinkLocalAddress)
        }.getOrDefault(false)
    }
    return false
}

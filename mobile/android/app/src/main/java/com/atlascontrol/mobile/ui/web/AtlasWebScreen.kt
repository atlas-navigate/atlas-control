package com.atlascontrol.mobile.ui.web

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.webkit.CookieManager
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
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.atlascontrol.mobile.*
import com.atlascontrol.mobile.network.AtlasTrust
import com.atlascontrol.mobile.ui.ConnectionViewModel
import java.net.URI

// Renderer-recovery tuning. Android kills the WebView renderer under memory
// pressure; reload with escalating, capped backoff instead of a tight
// reload → re-OOM → kill loop that takes the whole app down.
private const val RENDERER_CRASH_BACKOFF_MS = 1_500L
private const val RENDERER_CRASH_MAX_BACKOFF_MS = 30_000L
private const val RENDERER_CRASH_RESET_MS = 60_000L

/** Calls the Atlas web UI makes into the app (window.AtlasAndroid). */
private class AtlasJsBridge(private val vm: ConnectionViewModel, private val context: android.content.Context) {
    private val main = android.os.Handler(android.os.Looper.getMainLooper())

    /** Settings → Local Device Address "Copy" (the WebView's clipboard API is unreliable here). */
    @android.webkit.JavascriptInterface
    fun copyText(text: String) {
        val clean = text.take(256)
        main.post { com.atlascontrol.mobile.ui.copyToClipboard(context, "Atlas", clean) }
    }

    @android.webkit.JavascriptInterface
    fun reconnect() { main.post { vm.relocate() } }

    /** Atlas is about to leave its hotspot / change Wi-Fi networks. */
    @android.webkit.JavascriptInterface
    fun lanSwitching() { main.post { vm.onAtlasSwitchingNetworks() } }

    /** Addresses Atlas expects to have on its new network (tried first). */
    @android.webkit.JavascriptInterface
    fun lanSwitchedTo(urlsJson: String, isPending: Boolean, hintIp: String = "") {
        val urls = runCatching {
            val arr = org.json.JSONArray(urlsJson)
            (0 until arr.length()).map { arr.getString(it) }
        }.getOrDefault(emptyList()) + listOfNotNull(hintIp.takeIf { it.isNotBlank() }?.let { "https://$it" })
        main.post { vm.onAtlasSwitchedNetworks(urls) }
    }

    /** Older web UI: "set up Wi-Fi again" — just find Atlas wherever it is now. */
    @android.webkit.JavascriptInterface
    fun showSetupWizard() { main.post { vm.relocate("Looking for ${vm.deviceName}…") } }
}

@Composable
fun AtlasWebScreen(vm: ConnectionViewModel, baseUrl: String) {
    val context = LocalContext.current
    val device = vm.currentDevice() ?: return
    val origin = remember(baseUrl) { baseUrl.trimEnd('/') }
    val homeUrl = "$origin/mobile"

    var canGoBack by remember { mutableStateOf(false) }
    var progress by remember { mutableIntStateOf(0) }

    val webView = remember {
        WebView(context).apply {
            overScrollMode = WebView.OVER_SCROLL_NEVER
            setBackgroundColor(android.graphics.Color.parseColor("#09111C"))
            settings.userAgentString = "${settings.userAgentString} AtlasMobileAndroid/2.0"
        }
    }

    // The pairing token rides as the same HttpOnly cookie a browser would get.
    LaunchedEffect(origin) {
        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        cm.setCookie(origin, "atlas_client=${device.token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=315360000")
        cm.flush()
        webView.loadUrl(homeUrl)
    }

    BackHandler(enabled = canGoBack) { webView.goBack() }
    DisposableEffect(webView) { onDispose { webView.stopLoading(); webView.destroy() } }

    Box(Modifier.fillMaxSize().background(AtlasBackground).statusBarsPadding()) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = {
                webView.addJavascriptInterface(AtlasJsBridge(vm, context.applicationContext), "AtlasAndroid")
                configureWebView(webView, origin, device.pin,
                    onProgress = { progress = it },
                    onNavigated = { canGoBack = webView.canGoBack() },
                    onMainFrameError = { vm.relocate("Reconnecting to ${vm.deviceName}…") },
                    onWrongCertificate = { vm.relocate() },
                    getHomeUrl = { homeUrl })
                webView
            },
            update = { canGoBack = it.canGoBack() },
        )
        if (progress in 1..99) {
            LinearProgressIndicator(progress = { progress / 100f }, color = AtlasPrimary,
                modifier = Modifier.fillMaxWidth().align(Alignment.TopStart))
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
private fun configureWebView(
    webView: WebView,
    origin: String,
    pin: String,
    onProgress: (Int) -> Unit,
    onNavigated: () -> Unit,
    onMainFrameError: () -> Unit,
    onWrongCertificate: () -> Unit,
    getHomeUrl: () -> String,
) {
    if (webView.tag == "configured") return
    webView.tag = "configured"
    val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    var rendererCrashCount = 0
    var lastRendererCrashMs = 0L
    val atlasHost = runCatching { URI(origin).host }.getOrNull()

    webView.settings.apply {
        javaScriptEnabled = true
        domStorageEnabled = true
        loadsImagesAutomatically = true
        mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        useWideViewPort = true
        loadWithOverviewMode = true
        builtInZoomControls = false
        displayZoomControls = false
        mediaPlaybackRequiresUserGesture = true
        allowFileAccess = false
        allowContentAccess = false
        setSupportMultipleWindows(false)
        cacheMode = WebSettings.LOAD_DEFAULT        // hashed bundles make caching safe
    }

    fun isAtlas(url: String?): Boolean =
        url != null && runCatching { URI(url).let { it.scheme == "https" && it.host == atlasHost } }.getOrDefault(false)

    webView.webViewClient = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            onNavigated()
            return when (request.url.scheme?.lowercase()) {
                "https", "http" -> if (isAtlas(url)) false else { openExternal(view, request.url); true }
                "mailto", "tel", "sms", "geo" -> { openExternal(view, request.url); true }
                else -> true                                      // javascript:, file:, intent: … never
            }
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) { onProgress(5) }
        override fun onPageFinished(view: WebView, url: String?) { onProgress(100); onNavigated() }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) onMainFrameError()
        }

        /** Atlas's certificate is self-signed; accept it only if it is the key we paired with. */
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            val cert = error.certificate?.x509Certificate
            val matches = cert != null && isAtlas(error.url) && AtlasTrust.spkiPin(cert) == pin
            if (matches) handler.proceed() else { handler.cancel(); if (isAtlas(error.url)) onWrongCertificate() }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            val now = android.os.SystemClock.elapsedRealtime()
            if (now - lastRendererCrashMs > RENDERER_CRASH_RESET_MS) rendererCrashCount = 0
            lastRendererCrashMs = now
            rendererCrashCount++
            val backoff = (RENDERER_CRASH_BACKOFF_MS * rendererCrashCount).coerceAtMost(RENDERER_CRASH_MAX_BACKOFF_MS)
            mainHandler.postDelayed({ runCatching { view.loadUrl(getHomeUrl()) } }, backoff)
            return true
        }
    }

    webView.webChromeClient = object : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) = onProgress(newProgress)

        // Camera/microphone are never needed by the Atlas web UI.
        override fun onPermissionRequest(request: PermissionRequest) = request.deny()

        // Location (e.g. "share my phone's position") only for Atlas itself.
        override fun onGeolocationPermissionsShowPrompt(o: String?, callback: GeolocationPermissions.Callback?) {
            callback?.invoke(o, isAtlas(o), false)
        }
    }
}

private fun openExternal(webView: WebView, uri: Uri) {
    runCatching {
        ContextCompat.startActivity(webView.context, Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), null)
    }
}

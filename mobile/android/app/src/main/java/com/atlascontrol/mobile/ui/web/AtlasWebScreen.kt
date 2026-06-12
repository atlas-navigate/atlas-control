package com.atlascontrol.mobile.ui.web

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
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
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.URI

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
) {
    if (webView.tag == "configured") return
    webView.tag = "configured"

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
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                if (view.url != null) {
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
    return runCatching {
        val addr = java.net.InetAddress.getByName(host)
        when (addr) {
            is Inet4Address -> addr.isAnyLocalAddress || addr.isLoopbackAddress || addr.isSiteLocalAddress
            is Inet6Address -> addr.isAnyLocalAddress || addr.isLoopbackAddress || addr.isSiteLocalAddress
            else -> false
        }
    }.getOrDefault(false)
}

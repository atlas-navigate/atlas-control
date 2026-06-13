package com.atlascontrol.mobile.setup

import android.app.Application
import android.content.Context
import android.net.wifi.WifiManager
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.atlascontrol.mobile.network.ApiClient
import com.atlascontrol.mobile.network.AtlasBeacon
import com.atlascontrol.mobile.network.AtlasRepository
import com.atlascontrol.mobile.network.BootstrapManifest
import com.atlascontrol.mobile.network.MdnsDns
import com.atlascontrol.mobile.network.NsdHelper
import com.atlascontrol.mobile.network.WifiConnectRequest
import com.atlascontrol.mobile.network.WifiConnectResponse
import com.atlascontrol.mobile.ui.AppViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.net.URI
import java.util.concurrent.atomic.AtomicReference

// ── Step enum ─────────────────────────────────────────────────────────────────

enum class SetupStep {
    /** Welcome / feature overview. */
    WELCOME,
    /** User is instructed to join the atlas_navigate hotspot; app polls in background. */
    HOTSPOT_CONNECT,
    /** Atlas answered from the hotspot; show device info, let user open the app. */
    PAIRING,
    /** User opted to connect Atlas to a LAN while the hotspot stays active. */
    LAN_PROVISION,
    /** Setup complete — AppViewModel takes over. */
    DONE,
}

// ── Discovery result ─────────────────────────────────────────────────────────

data class AtlasDiscovery(
    /** Browser-safe URL that was confirmed reachable (numeric IP where possible). */
    val foundUrl: String,
    val manifest: BootstrapManifest,
)

// ── ViewModel ─────────────────────────────────────────────────────────────────

class SetupViewModel(application: Application) : AndroidViewModel(application) {

    private val prefs = application.getSharedPreferences("atlas_prefs", Context.MODE_PRIVATE)

    // ── Hotspot credentials — seeded from SharedPreferences, updated on every
    //    successful bootstrap so a password change is reflected automatically. ──

    val hotspotSsid     = MutableStateFlow(prefs.getString("hotspot_ssid",     "atlas_navigate") ?: "atlas_navigate")
    val hotspotPassword = MutableStateFlow(prefs.getString("hotspot_password", "password")       ?: "password")

    // ── State ──────────────────────────────────────────────────────────────────

    val step       = MutableStateFlow(SetupStep.WELCOME)
    val isSearching = MutableStateFlow(false)
    val errorMsg    = MutableStateFlow<String?>(null)
    val manualUrl   = MutableStateFlow("")

    private val _discovery = MutableStateFlow<AtlasDiscovery?>(null)
    val discovery: StateFlow<AtlasDiscovery?> = _discovery.asStateFlow()

    // ── LAN provisioning state ─────────────────────────────────────────────────

    val lanConnecting     = MutableStateFlow(false)
    val lanConnectDone    = MutableStateFlow(false)
    val lanConnectError   = MutableStateFlow<String?>(null)
    val lanConnectSsid    = MutableStateFlow("")
    // True while the background connect is in flight and we're waiting for the
    // phone to join the LAN so we can reconnect to the already paired Atlas.
    val lanHandoffPending = MutableStateFlow(false)
    private val _lanDiscoveredUrl = MutableStateFlow<String?>(null)

    private var searchJob: Job? = null

    /** Preserved from the initial hotspot discovery so LAN setup can keep the original hotspot IPs. */
    private var hotspotDiscovery: AtlasDiscovery? = null

    // ── Navigation helpers ────────────────────────────────────────────────────

    fun goToHotspotStep() {
        step.value = SetupStep.HOTSPOT_CONNECT
    }

    /**
     * Full wizard reset.  This view model outlives the wizard, so every piece
     * of discovery and LAN-provision state must be cleared — a stale discovery
     * or lanConnectDone would otherwise reopen the wizard on an old step
     * pointing at a dead URL.
     */
    fun resetForReconnect() {
        stopSearch()
        _discovery.value        = null
        hotspotDiscovery        = null
        _lanDiscoveredUrl.value = null
        errorMsg.value          = null
        lanConnecting.value     = false
        lanConnectDone.value    = false
        lanConnectError.value   = null
        lanHandoffPending.value = false
        step.value              = SetupStep.HOTSPOT_CONNECT
    }

    // ── Hotspot search ────────────────────────────────────────────────────────

    /**
     * Starts a background loop that repeatedly probes the hotspot and known
     * Atlas addresses. Advances to [SetupStep.PAIRING] on first success.
     * Safe to call multiple times — cancels any running job first.
     */
    fun startHotspotSearch() {
        searchJob?.cancel()
        isSearching.value = true
        errorMsg.value    = null

        searchJob = viewModelScope.launch {
            val result = searchForAtlas()
            isSearching.value = false
            if (result != null) {
                _discovery.value = result
                hotspotDiscovery  = result   // preserve for completeLanSetup
                step.value = SetupStep.PAIRING
            } else {
                errorMsg.value =
                    "Atlas not found.\n\n" +
                    "Make sure your phone is connected to the \"${hotspotSsid.value}\" WiFi network, then tap Search again."
            }
        }
    }

    fun stopSearch() {
        searchJob?.cancel()
        isSearching.value = false
    }

    /** Retry search — resets error and starts fresh. */
    fun retrySearch() {
        errorMsg.value = null
        startHotspotSearch()
    }

    // ── Manual / advanced connect ─────────────────────────────────────────────

    fun connectManual(onFoundInWizard: () -> Unit) {
        val raw = manualUrl.value.trim().ifBlank { "atlas.local" }
        searchJob?.cancel()
        isSearching.value = true
        errorMsg.value    = null

        searchJob = viewModelScope.launch {
            val candidates = buildList {
                when {
                    raw.startsWith("http://") || raw.startsWith("https://") -> add(raw)
                    else -> {
                        // http://IP:5000 first — Atlas's plain-HTTP Flask port,
                        // and the URL the WebView can actually load. https is a
                        // last-resort fallback (probe client trusts all certs).
                        add("http://$raw:5000")
                        add("http://$raw")
                        add("https://$raw")
                    }
                }
                addAll(hotspotCandidates())
                add("http://atlas.local:5000")
            }.distinct()

            val found = tryEachUrl(candidates)
            isSearching.value = false
            if (found != null) {
                _discovery.value = found
                hotspotDiscovery  = found   // preserve for completeLanSetup
                step.value = SetupStep.PAIRING
                onFoundInWizard()
            } else {
                errorMsg.value =
                    "Could not reach Atlas at \"$raw\".\n" +
                    "Join the ${hotspotSsid.value} hotspot first, then try again."
            }
        }
    }

    // ── LAN provisioning ─────────────────────────────────────────────────────

    fun goToLanProvision() {
        lanConnectDone.value    = false
        lanConnectError.value   = null
        lanHandoffPending.value = false
        _lanDiscoveredUrl.value = null
        step.value = SetupStep.LAN_PROVISION
    }

    /**
     * Tells Atlas to join [ssid]. Single-radio Jetson can't run the
     * hotspot and a LAN connection simultaneously, so the backend always
     * returns {pending:true} immediately (before dropping the hotspot)
     * and joins [ssid] in a background thread.
     *
     * Discovery runs concurrent strategies (mirroring
     * [AppViewModel.lanTransitionLoop]) so the wizard reconnects no
     * matter which IP the new LAN's DHCP assigns:
     *
     *   • Pre-fetch LAN IPs via /api/wifi/my_ips while the hotspot is alive
     *   • Direct probe of hint + access URLs from the connect response
     *   • Port-5000 fingerprint sweep across every common /24 (PRIMARY)
     *   • Targeted /24 sweep of the hint IP's subnet
     *   • Periodic /api/wifi/status polling so the confirmed final IP is
     *     used once Atlas reports its switch is complete
     *   • mDNS / NSD as a short supplementary check only
     */
    fun connectToLan(ssid: String, password: String) {
        val disc = _discovery.value ?: return
        lanConnecting.value     = true
        lanConnectError.value   = null
        lanConnectDone.value    = false
        lanHandoffPending.value = false
        lanConnectSsid.value    = ssid
        viewModelScope.launch(Dispatchers.IO) {
            // Kick off the bootstrap pre-fetch immediately so any LAN IPs Atlas
            // already knows about (dual-radio installs) are saved BEFORE the
            // hotspot drops.  Cancelled once the connect response arrives.
            val prefetchJob = launch { prefetchAtlasIpsBeforeSwitch(disc.foundUrl) }

            // Send /api/wifi/connect with a tight 4 s deadline.  On single-radio
            // Atlas the hotspot can drop between Atlas accepting our SYN and Flask
            // flushing the response, so a transport failure here does NOT mean the
            // request was lost — it almost always reached Atlas, and Atlas is now
            // running nmcli to join the new LAN.  We treat null/timeout the same
            // as "pending" and start the discovery loop unconditionally.
            val resp = sendConnectRequest(disc.foundUrl, ssid, password)
            prefetchJob.cancel()

            // Only surface an error when Atlas explicitly rejected the request
            // (bad password, missing ssid, etc).  Discovery cannot rescue those.
            if (resp != null && !resp.pending && !resp.ok) {
                lanConnecting.value   = false
                lanConnectError.value = resp.error ?: "Could not connect to $ssid"
                return@launch
            }

            // Either Atlas accepted (pending/ok) or the POST failed at the
            // transport layer.  Both paths run discovery — the former because
            // we need to find Atlas's new IP, the latter because Atlas almost
            // certainly received the request before the hotspot dropped.
            if (resp != null) {
                seedLanCandidatesFromConnectResponse(resp.hintIp, resp.accessUrls)
            }
            lanHandoffPending.value = true
            runLanDiscoveryLoop(ssid, resp?.hintIp, resp?.accessUrls.orEmpty())
        }
    }

    /**
     * Issues POST /api/wifi/connect with a 4 s outer deadline.  Returns the
     * decoded response or null if the call timed out / errored at the transport
     * layer.  Callers must treat null as "Atlas may have received the request"
     * and proceed to discovery — see [connectToLan] for the rationale.
     */
    private suspend fun sendConnectRequest(
        baseUrl: String,
        ssid: String,
        password: String,
    ): WifiConnectResponse? = withTimeoutOrNull(4_000L) {
        AtlasRepository(ApiClient.create(baseUrl)).connectWifi(
            WifiConnectRequest(
                ssid        = ssid,
                password    = password,
                stopHotspot = true,
                background  = true,
            )
        ).getOrNull()
    }

    /**
     * Pre-fetches Atlas's currently bound IPs while the hotspot is still alive.
     *
     * On dual-radio Atlas installs (rare on the Jetson but possible with a USB
     * dongle) this means the LAN IP is saved before the hotspot drops, so the
     * direct-probe pass of [runLanDiscoveryLoop] hits Atlas in well under a
     * second — no subnet scan required.
     *
     * On single-radio Atlas the call usually returns only the hotspot IP, which
     * we filter out.  In that case the loop falls back to mDNS / subnet scan,
     * which is the expected path.
     */
    private suspend fun prefetchAtlasIpsBeforeSwitch(baseUrl: String) {
        val repo = AtlasRepository(ApiClient.createForProbe(baseUrl))
        val myIps: List<String> = withTimeoutOrNull(3_000L) {
            runCatching { repo.getMyIps().getOrNull()?.urls }.getOrNull()
        }.orEmpty()
        val bootstrap: List<String> = if (myIps.isNotEmpty()) emptyList() else withTimeoutOrNull(4_000L) {
            runCatching { repo.getBootstrap().getOrNull()?.api?.baseUrls }.getOrNull()
        }.orEmpty()
        val raw = (myIps + bootstrap)
            .filter { url ->
                !url.contains("10.42.0.1") &&
                !url.contains("192.168.4.1") &&
                !url.contains("192.168.43.1") &&
                !url.contains("atlas_navigate")
            }
        if (raw.isNotEmpty()) {
            mergeLanCandidateUrls(raw)
        }
    }

    /**
     * Records hint IP + access URLs returned by /api/wifi/connect so subsequent
     * direct-probe passes can hit them immediately.
     */
    private fun seedLanCandidatesFromConnectResponse(hintIp: String?, accessUrls: List<String>?) {
        val urls = buildList {
            if (!hintIp.isNullOrBlank()) {
                add("https://$hintIp")
                add("http://$hintIp:5000")
            }
            accessUrls?.forEach { add(it) }
        }
        if (urls.isNotEmpty()) mergeLanCandidateUrls(urls)
    }

    /**
     * Discovery loop run after Atlas accepts the LAN switch request.
     *
     * Loops every 3 s for up to 2 minutes, running every strategy concurrently
     * each pass.  First successful discovery wins and the wizard advances.
     *
     * Why this is safe even before the user switches WiFi: each strategy uses
     * the shared probe client (900 ms timeouts, fail-fast on connection
     * refused), so retries are cheap.  Once the phone joins the new LAN the
     * very next pass finds Atlas via subnet scan or hint probe.
     */
    private suspend fun runLanDiscoveryLoop(
        ssid: String,
        hintIp: String?,
        responseAccessUrls: List<String>,
    ) {
        val app = getApplication<Application>()
        val deadline = System.currentTimeMillis() + 180_000L  // 3 minutes
        var found: AtlasDiscovery? = null

        // A non-null hint IP that happens to be in the hotspot range is junk —
        // refuse it before it pollutes the targeted /24 scan or the direct probe.
        val effectiveHint: String? = hintIp
            ?.takeIf { it.isNotBlank() }
            ?.takeUnless { isHotspotUrl("https://$it") }

        // Reject any non-blank URL that resolves to a hotspot host.  Used by
        // every concurrent strategy so a hotspot URL never sets `winner` during
        // a pending switch — that would cancel the scope and waste an iteration
        // in the next 3 s sleep instead of letting S4 (`/api/wifi/status`) or
        // S5 (mDNS) actually find Atlas's confirmed LAN address.
        // [label] identifies which strategy (S1–S5) submitted the URL so we
        // can log which path won the iteration; this is the only way to
        // verify from logs that every strategy actually ran in parallel.
        fun claim(scope: CoroutineScope, winner: AtomicReference<String?>, label: String, url: String?) {
            if (url.isNullOrBlank()) {
                android.util.Log.d("AtlasDiscovery", "$label returned null")
                return
            }
            if (isHotspotUrl(url)) {
                android.util.Log.d("AtlasDiscovery", "$label returned hotspot URL (rejected): $url")
                return
            }
            if (winner.compareAndSet(null, url)) {
                android.util.Log.i("AtlasDiscovery", "$label WON with $url — cancelling siblings")
                scope.cancel()
            } else {
                android.util.Log.d("AtlasDiscovery", "$label found $url after another strategy already won")
            }
        }

        var iteration = 0
        while (System.currentTimeMillis() < deadline && found == null) {
            iteration++
            MdnsDns.clearCache()
            val remainingMs = deadline - System.currentTimeMillis()
            if (remainingMs <= 0L) break
            android.util.Log.i(
                "AtlasDiscovery",
                "iteration #$iteration starting (${remainingMs / 1000}s left); running S1+S2+S2b" +
                    (if (effectiveHint != null) "+S3" else "") + "+S4+S5 in parallel"
            )

            // Build the direct-probe candidate list every pass so newly
            // discovered URLs (from /api/wifi/status) get included immediately.
            val directCandidates = buildList {
                if (!effectiveHint.isNullOrBlank()) {
                    add("https://$effectiveHint")
                    add("http://$effectiveHint:5000")
                }
                responseAccessUrls.forEach { add(it) }
                addAll(pairedLanCandidates())
                add("http://atlas.local:5000")
            }.map { normalizeUrl(it) }
                .filter { it.isNotBlank() && !isHotspotUrl(it) }
                .distinct()

            val winner = AtomicReference<String?>(null)

            try {
                coroutineScope {
                    val scope = this

                    // S1 — direct probe of hint + access + saved + atlas.local URLs
                    launch(Dispatchers.IO) {
                        claim(scope, winner, "S1-direct", probeUrlsFast(directCandidates))
                    }

                    // S2 (PRIMARY) — UDP beacon "shout-and-receive". Listens
                    // continuously for the full iteration window (15 s) and
                    // re-fires probe bursts every 3 s, so Atlas is found
                    // within ~1 s of rebinding on the new LAN regardless of
                    // when in the iteration that happens. Falls through
                    // silently on older Atlas firmware that doesn't run the
                    // beacon, letting S2b take over.
                    launch(Dispatchers.IO) {
                        val r = withTimeoutOrNull(minOf(15_000L, remainingMs)) {
                            AtlasBeacon.discover(app, timeoutMs = 14_000L, excludeHotspot = true)
                        }
                        claim(scope, winner, "S2-beacon", r)
                    }

                    // S2b — port-5000 fingerprint sweep across every
                    // candidate /24, hotspot prefixes excluded. Bounded to 20 s
                    // per iteration so the loop retries many times within the
                    // 180 s deadline. Acts as the deterministic fallback when
                    // the beacon path is unavailable or Atlas hasn't yet
                    // rebound to the new LAN.
                    launch(Dispatchers.IO) {
                        val r = withTimeoutOrNull(minOf(20_000L, remainingMs)) {
                            NsdHelper.findAtlasByPortSweep(app, excludeHotspot = true)
                        }
                        claim(scope, winner, "S2b-sweep", r)
                    }

                    // S3 — targeted /24 sweep when we have a usable (non-hotspot) hint
                    if (!effectiveHint.isNullOrBlank()) {
                        launch(Dispatchers.IO) {
                            val r = withTimeoutOrNull(minOf(15_000L, remainingMs)) {
                                NsdHelper.findAtlasByTargetSubnet(effectiveHint, excludeHotspot = true)
                            }
                            claim(scope, winner, "S3-target", r)
                        }
                    }

                    // S4 — poll /api/wifi/status; once Atlas reports the switch
                    // is complete the result.ip / result.accessUrls give us the
                    // confirmed final IP without waiting for a sweep.
                    launch(Dispatchers.IO) {
                        pollWifiSwitchStatus(directCandidates, scope, winner)
                    }

                    // S5 — mDNS as a supplementary 5 s check only. Avahi on a
                    // single-radio Jetson is unreliable on a brand-new LAN so
                    // we don't gate the loop on it.
                    launch(Dispatchers.IO) {
                        val r = NsdHelper.findAtlasOnLan(app, timeoutMs = 5_000L)
                        claim(scope, winner, "S5-mdns", r)
                    }
                }
            } catch (_: CancellationException) {
                // expected — winner cancels the scope to stop the other strategies
            }

            val candidateUrl = winner.get()
            if (candidateUrl != null && !isHotspotUrl(candidateUrl)) {
                // Confirm with a full bootstrap fetch so the manifest is up to date.
                found = tryEachUrl(listOf(candidateUrl))
            }

            if (found == null) {
                val remaining = deadline - System.currentTimeMillis()
                if (remaining > 3_000L) delay(3_000L)
            }
        }

        lanHandoffPending.value = false
        lanConnecting.value     = false
        if (found != null) {
            _discovery.value        = found
            _lanDiscoveredUrl.value = found.foundUrl
            lanConnectDone.value    = true
        } else {
            lanConnectError.value =
                "Could not reach Atlas on \"$ssid\".\n\n" +
                "Make sure your phone is connected to $ssid, then tap retry."
        }
    }

    /**
     * Polls /api/wifi/status at [candidateUrls] until the wifiSwitch result
     * reports it has finished, then probes every confirmed access URL.  When a
     * match is found [winner] is set and the parent [scope] is cancelled.
     */
    private suspend fun pollWifiSwitchStatus(
        candidateUrls: List<String>,
        scope: CoroutineScope,
        winner: AtomicReference<String?>,
    ) {
        val deadline = System.currentTimeMillis() + 120_000L
        while (System.currentTimeMillis() < deadline && winner.get() == null) {
            for (url in candidateUrls) {
                if (winner.get() != null) return
                val status = withTimeoutOrNull(2_000L) {
                    runCatching {
                        AtlasRepository(ApiClient.createForProbe(url)).getWifiStatus().getOrNull()
                    }.getOrNull()
                } ?: continue

                val switchState = status.wifiSwitch ?: continue
                if (switchState.pending) continue

                val confirmed = buildList {
                    switchState.result?.ip?.takeIf { it.isNotBlank() }?.let { ip ->
                        add(normalizeUrl("https://$ip"))
                        add(normalizeUrl("http://$ip:5000"))
                    }
                    switchState.result?.hintIp?.takeIf { it.isNotBlank() }?.let { ip ->
                        add(normalizeUrl("https://$ip"))
                        add(normalizeUrl("http://$ip:5000"))
                    }
                    switchState.result?.accessUrls?.forEach { add(normalizeUrl(it)) }
                    add(normalizeUrl(url))
                }.distinct().filter { it.isNotBlank() && !isHotspotUrl(it) }

                if (confirmed.isNotEmpty()) {
                    mergeLanCandidateUrls(confirmed)
                    val match = probeUrlsFast(confirmed)
                    if (match != null && winner.compareAndSet(null, match)) {
                        android.util.Log.i("AtlasDiscovery", "S4-wifistatus WON with $match — cancelling siblings")
                        scope.cancel()
                        return
                    }
                }
            }
            if (winner.get() == null) delay(3_000L)
        }
    }

    /**
     * Fast parallel probe of [urls] — mirrors AppViewModel.probeUrlsFast.
     * Returns the first URL whose /api/device responds OK, or null.
     */
    private suspend fun probeUrlsFast(urls: List<String>): String? {
        if (urls.isEmpty()) return null
        return withContext(Dispatchers.IO) {
            val found = AtomicReference<String?>(null)
            val limit = Semaphore(16)
            coroutineScope {
                urls.map { url ->
                    async {
                        if (found.get() != null) return@async
                        limit.withPermit {
                            if (found.get() != null) return@withPermit
                            val timeout = if (url.contains(".local")) 2_000L else 1_500L
                            val ok = withTimeoutOrNull(timeout) {
                                runCatching {
                                    AtlasRepository(ApiClient.createForProbe(url)).getDevice().isSuccess
                                }.getOrElse { false }
                            } ?: false
                            if (ok) found.compareAndSet(null, url)
                        }
                    }
                }.awaitAll()
            }
            found.get()
        }
    }

    /**
     * Persists newly discovered LAN URLs into the same shared-preferences
     * bucket that AppViewModel reads on startup, so when the user finishes the
     * wizard the URLs are already known.  Filters out hotspot URLs.
     */
    private fun mergeLanCandidateUrls(urls: List<String>) {
        val cleaned = urls
            .map { normalizeUrl(it) }
            .filter { it.isNotBlank() && !isHotspotUrl(it) }
            .distinct()
        if (cleaned.isEmpty()) return
        synchronized(this) {
            val raw      = prefs.getString("lan_urls", null).orEmpty()
            val existing = raw.split(",").filter { it.isNotBlank() }
            val merged   = (cleaned + existing).distinct()
            if (merged != existing) {
                prefs.edit().putString("lan_urls", merged.joinToString(",")).apply()
            }
        }
    }

    private fun normalizeUrl(url: String): String {
        var u = url.trim()
        if (u.isBlank()) return u
        if (!u.startsWith("http://", ignoreCase = true) &&
            !u.startsWith("https://", ignoreCase = true)
        ) {
            // Default schemeless input to http://…:5000, not https. Atlas's
            // Flask serves plain HTTP on :5000; these URLs get persisted into
            // lan_urls and loaded by the WebView (chromium), which cannot load
            // Atlas's missing/self-signed TLS. Insert :5000 after the HOST,
            // before any path — appending to the whole string would mangle
            // "ip/path" into "http://ip/path:5000".
            val slash = u.indexOf('/')
            val host  = if (slash < 0) u else u.take(slash)
            val rest  = if (slash < 0) "" else u.substring(slash)
            u = "http://" + host + (if (host.contains(":")) "" else ":5000") + rest
        }
        if (!u.endsWith("/")) u = "$u/"
        return u
    }

    /** Returns the current WiFi SSID, or null if unavailable or permission denied. */
    private fun readCurrentSsid(): String? = runCatching {
        val wm = getApplication<Application>().getSystemService(Context.WIFI_SERVICE) as WifiManager
        val raw = wm.connectionInfo?.ssid?.removeSurrounding("\"") ?: return@runCatching null
        if (raw.isBlank() || raw == "<unknown ssid>") null else raw
    }.getOrNull()

    // ── Finish setup ─────────────────────────────────────────────────────────

    /**
     * Stores all discovered URLs in [AppViewModel] and transitions immediately
     * to CONNECTED using the URL we just confirmed works.  No re-probe needed.
     * Call when the user taps "Open Atlas" on the Pairing step.
     */
    fun completeSetup(appVm: AppViewModel) {
        val disc = _discovery.value ?: return

        // Separate the URLs from the bootstrap manifest into hotspot vs LAN buckets.
        val manifestUrls = disc.manifest.api?.baseUrls ?: emptyList()
        val hotspotUrls  = buildList {
            add(disc.foundUrl)
            manifestUrls.filter { isHotspotUrl(it) }.forEach { add(it) }
            // Always include the bare-IP :5000 variant so the WebView (no mDNS) can reach it
            extractIp(disc.foundUrl)?.let { ip ->
                add("http://$ip:5000/")
                add("https://$ip/")
            }
        }.distinct()
        val lanUrls = buildList {
            manifestUrls.filterNot { isHotspotUrl(it) }.forEach { add(it) }
            add("http://atlas.local:5000")
        }.distinct()

        // Use applySetupResult so we go straight to CONNECTED with the confirmed URL
        // instead of re-probing (which could fail if gateway detection is unreliable).
        appVm.applySetupResult(
            foundUrl    = disc.foundUrl,
            hotspotUrls = hotspotUrls,
            lanUrls     = lanUrls,
        )
    }

    /**
     * Like [completeSetup] but also includes the LAN URL discovered during
     * provisioning so Atlas is immediately reachable via both hotspot and LAN.
     *
     * hotspotUrls are taken from the ORIGINAL hotspot discovery so that switching
     * back to the atlas_navigate hotspot later still works.  The LAN discovery
     * (`_discovery.value`) is only used for the LAN URL bucket.
     */
    fun completeLanSetup(appVm: AppViewModel) {
        val lanDisc     = _discovery.value ?: return
        val hotDisc     = hotspotDiscovery ?: lanDisc   // prefer original hotspot result
        val hotManifest = hotDisc.manifest.api?.baseUrls ?: emptyList()
        val lanManifest = lanDisc.manifest.api?.baseUrls ?: emptyList()

        val hotspotUrls = buildList {
            add(hotDisc.foundUrl)
            hotManifest.filter { isHotspotUrl(it) }.forEach { add(it) }
            extractIp(hotDisc.foundUrl)?.let { ip ->
                add("http://$ip:5000/")
                add("https://$ip/")
            }
        }.distinct()

        val lanUrls = buildList {
            _lanDiscoveredUrl.value?.let { add(it) }
            addAll(lanManifest.filterNot { isHotspotUrl(it) })
            add("http://atlas.local:5000")
        }.distinct()

        appVm.applySetupResult(
            foundUrl    = lanDisc.foundUrl,
            hotspotUrls = hotspotUrls,
            lanUrls     = lanUrls,
        )
    }

    // ── Core search logic ─────────────────────────────────────────────────────

    private suspend fun searchForAtlas(): AtlasDiscovery? = withContext(Dispatchers.IO) {
        val candidates = buildList {
            // 1. DHCP gateway — instant, works on hotspot AND LAN
            gatewayUrl()?.let { add(it) }
            // 2. Known NetworkManager hotspot gateway IPs
            addAll(hotspotCandidates())
            // 3. mDNS hostname fallback
            add("http://atlas.local:5000")
        }.distinct()

        tryEachUrl(candidates) ?: discoverAtlasOnCurrentNetwork()
    }

    private suspend fun tryEachUrl(candidates: List<String>): AtlasDiscovery? {
        for (url in candidates) {
            val result = withTimeoutOrNull(5_000L) {
                runCatching {
                    val repo = AtlasRepository(ApiClient.create(url))
                    // Prefer bootstrap — gives us device info + all access URLs in one call.
                    val manifest = repo.getBootstrap().getOrElse {
                        // Older firmware may not have /api/mobile/bootstrap; fall back to
                        // /api/device just to confirm Atlas is alive.
                        repo.getDevice().getOrNull() ?: return@runCatching null
                        BootstrapManifest()
                    } ?: return@runCatching null

                    // Persist hotspot credentials so a password change is picked up
                    // automatically the next time the user opens the setup wizard.
                    persistHotspotCredentials(manifest)

                    val webUrl = resolveWebUrl(url)
                    AtlasDiscovery(foundUrl = webUrl, manifest = manifest)
                }.getOrNull()
            }
            if (result != null) return result
        }
        return null
    }

    /**
     * Atlas can acquire a different LAN IP after leaving hotspot mode.  If the known
     * URLs and atlas.local no longer answer, use the Android-side LAN discovery stack
     * so setup can complete on routers where mDNS is absent or delayed.
     */
    private suspend fun discoverAtlasOnCurrentNetwork(): AtlasDiscovery? = withContext(Dispatchers.IO) {
        val app = getApplication<Application>()
        // Try the UDP beacon first (a few seconds), then fall through to the
        // existing port-5000 sweep + mDNS via [NsdHelper.findAtlas]. The
        // beacon is the fast path on real devices where Atlas's announce
        // window or heartbeat shouts reach the phone directly.
        val beaconUrl = withTimeoutOrNull(5_000L) { AtlasBeacon.discover(app, timeoutMs = 4_000L) }
        if (beaconUrl != null) {
            val confirmed = tryEachUrl(listOf(beaconUrl))
            if (confirmed != null) return@withContext confirmed
        }
        val discoveredUrl = withTimeoutOrNull(45_000L) { NsdHelper.findAtlas(app) } ?: return@withContext null
        tryEachUrl(listOf(discoveredUrl))
    }

    /**
     * Saves the SSID and password returned by the bootstrap manifest into
     * SharedPreferences.  These values are read back on the next cold start
     * so the wizard always shows the current credentials even if they changed.
     */
    private fun persistHotspotCredentials(manifest: BootstrapManifest) {
        val ssid = manifest.hotspot?.ssid?.takeIf { it.isNotBlank() } ?: return
        val pw   = manifest.hotspot.password   // may be blank — still persist the SSID
        prefs.edit()
            .putString("hotspot_ssid",     ssid)
            .putString("hotspot_password", pw)
            .apply()
        hotspotSsid.value     = ssid
        hotspotPassword.value = pw
    }

    // ── URL helpers ───────────────────────────────────────────────────────────

    /** Typical NetworkManager Soft AP gateway IPs. */
    private fun hotspotCandidates(): List<String> = listOf(
        "http://10.42.0.1:5000",
        "https://10.42.0.1",
        "http://192.168.4.1:5000",
        "https://192.168.4.1",
        "http://192.168.43.1:5000",
        "https://192.168.43.1",
    )

    private fun gatewayUrl(): String? = runCatching {
        val app = getApplication<Application>()
        val wm  = app.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val gw  = wm.dhcpInfo?.gateway ?: 0
        if (gw == 0) return null
        val ip = "%d.%d.%d.%d".format(
            gw and 0xFF, gw shr 8 and 0xFF, gw shr 16 and 0xFF, gw shr 24 and 0xFF,
        )
        "http://$ip:5000"
    }.getOrNull()

    private fun pairedLanCandidates(): List<String> {
        val hotDisc = hotspotDiscovery ?: _discovery.value
        val manifestUrls = hotDisc?.manifest?.api?.baseUrls ?: emptyList()
        return buildList {
            manifestUrls.filterNot { isHotspotUrl(it) }.forEach { add(it) }
            _lanDiscoveredUrl.value?.let { add(it) }
            add("http://atlas.local:5000")
        }.distinct()
    }

    private fun isHotspotUrl(url: String): Boolean =
        url.contains("10.42.0.")   ||
        url.contains("192.168.4.") ||
        url.contains("192.168.43.")

    private fun extractIp(url: String): String? = runCatching {
        val host = URI(url).host ?: return null
        if (host.matches(Regex("""\d+\.\d+\.\d+\.\d+"""))) host else null
    }.getOrNull()

    /**
     * Android's Chromium WebView cannot resolve .local hostnames via mDNS.
     * After OkHttp confirms an atlas.local URL works, swap the hostname for
     * the resolved numeric IP so the WebView can open the same page.
     */
    private fun resolveWebUrl(url: String): String {
        val parsed = runCatching { URI(url) }.getOrNull() ?: return url
        val host   = parsed.host?.lowercase() ?: return url
        if (!host.endsWith(".local")) return url
        val ip = runCatching { MdnsDns.lookup(host).firstOrNull()?.hostAddress }
            .getOrNull()?.takeIf { it.isNotBlank() } ?: return url
        val port   = if (parsed.port == -1) "" else ":${parsed.port}"
        return "${parsed.scheme}://$ip$port/"
    }
}

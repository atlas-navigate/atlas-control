package com.atlascontrol.mobile.ui

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.atlascontrol.mobile.network.ApiClient
import com.atlascontrol.mobile.network.AtlasBeacon
import com.atlascontrol.mobile.network.AtlasMessage
import com.atlascontrol.mobile.network.AtlasRepository
import com.atlascontrol.mobile.network.DeviceInfo
import com.atlascontrol.mobile.network.MdnsDns
import com.atlascontrol.mobile.network.NsdHelper
import com.atlascontrol.mobile.network.NetworkMonitor
import com.atlascontrol.mobile.notifications.AtlasNotificationManager
import kotlinx.coroutines.cancel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.net.URI
import java.util.concurrent.atomic.AtomicReference

enum class ConnectionState { IDLE, CHECKING, CONNECTED, FAILED }

/**
 * Manages Atlas connectivity across two independent networks:
 *  - The Atlas Soft AP / hotspot (atlas_navigate, gateway 10.42.0.1)
 *  - Any LAN that both the phone and Atlas are connected to
 *
 * URLs from both sources are persisted in SharedPreferences.  On startup and
 * after every WiFi change, [probeAll] tries them in network-aware priority order.
 *
 * ## LAN-switch flow (hotspot → LAN)
 *
 * 1. JS calls [onLanSwitchInitiated] before the /api/wifi/connect POST.
 *    We immediately fetch Atlas's current bootstrap manifest to capture any
 *    LAN IPs it already has (dual-radio case).  Concurrently we start
 *    [lanTransitionLoop] so scanning begins before the hotspot drops.
 *
 * 2. JS calls [onLanSwitchConfirmed] once /api/wifi/connect responds.
 *    We restart [lanTransitionLoop] with the server-supplied hintIp so the
 *    targeted /24 scan runs first.
 *
 * 3. [lanTransitionLoop] runs six concurrent strategies per pass:
 *    S1  — direct probe of known/pre-fetched/hint URLs
 *    S2  — UDP beacon shout-and-receive (PRIMARY; re-verified over TCP)
 *    S2b — port-5000 fingerprint sweep across every candidate /24
 *    S3  — targeted /24 scan of hintIp's subnet
 *    S4  — wifi-status polling: fetches /api/wifi/status at known URLs to
 *          get Atlas's confirmed new IP once the switch completes
 *    S5  — NSD (mDNS) as a short supplementary check
 *
 * ## Emulator compatibility (Panda, AVD, BlueStacks, LDPlayer, Genymotion)
 *
 * Emulators route LAN traffic through the host machine regardless of their
 * virtual subnet.  S4 always scans all extended LAN prefixes so Atlas is found
 * no matter which subnet the emulator is on.
 *
 * The previous failure mode: each probe created a NEW OkHttpClient instance
 * (dispatcher, SSLContext, ConnectionPool…).  With ~7 600 candidates per scan
 * pass the GC pressure on Android caused 900 ms timeouts to fire from GC stalls
 * rather than real network latency, making every probe appear to fail.
 *
 * The fix: all probes share ONE [ApiClient.probeClient] OkHttpClient.  Retrofit
 * wrappers (one per base URL) are cheap proxy objects around that shared client.
 */
class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val prefs = application.getSharedPreferences("atlas_prefs", Context.MODE_PRIVATE)

    val networkMonitor = NetworkMonitor(application)

    // ── Public state ──────────────────────────────────────────────────────────

    private val _state    = MutableStateFlow(ConnectionState.IDLE)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val _baseUrl  = MutableStateFlow<String?>(null)
    val baseUrl: StateFlow<String?> = _baseUrl.asStateFlow()

    private val _errorMsg = MutableStateFlow<String?>(null)
    val errorMsg: StateFlow<String?> = _errorMsg.asStateFlow()

    /**
     * True while a LAN-switch transition is in progress.  ConnectingScreen uses
     * this to show "Searching for Atlas on LAN…" and surface a manual-IP entry
     * field after 20 s.
     */
    private val _isLanTransitioning = MutableStateFlow(false)
    val isLanTransitioning: StateFlow<Boolean> = _isLanTransitioning.asStateFlow()

    private var probeJob:            Job? = null
    private var bgRetryJob:          Job? = null
    private var notificationPollJob: Job? = null

    private val reconnectMutex = Mutex()

    @Volatile private var lanSwitchPending = false

    // ── Init ──────────────────────────────────────────────────────────────────

    init {
        networkMonitor.start()
        restoreAndProbe()

        combine(networkMonitor.ssid, networkMonitor.networkVersion) { ssid, v -> "$ssid:$v" }
            .drop(1)
            .debounce(3_000L)
            .onEach { handleNetworkChange() }
            .launchIn(viewModelScope)
    }

    override fun onCleared() {
        super.onCleared()
        notificationPollJob?.cancel()
        networkMonitor.stop()
    }

    // ── Setup wizard entry point ──────────────────────────────────────────────

    fun applySetupResult(foundUrl: String, hotspotUrls: List<String>, lanUrls: List<String>) {
        prefs.edit()
            .putString("hotspot_urls", hotspotUrls.joinToString(","))
            .putString("lan_urls",     lanUrls.joinToString(","))
            .remove("base_url")
            .apply()
        cancelAllJobs()
        _isLanTransitioning.value = false
        val resolvedUrl = toWebReachableUrl(foundUrl)
        startNotificationPolling(resolvedUrl)
        _baseUrl.value  = resolvedUrl
        _state.value    = ConnectionState.CONNECTED
        _errorMsg.value = null
    }

    fun saveUrls(hotspotUrls: List<String>, lanUrls: List<String>) {
        prefs.edit()
            .putString("hotspot_urls", hotspotUrls.joinToString(","))
            .putString("lan_urls",     lanUrls.joinToString(","))
            .remove("base_url")
            .apply()
        probeAll(prioritizeByNetwork(hotspotUrls, lanUrls))
    }

    // ── Single-URL connect ────────────────────────────────────────────────────

    fun connect(rawUrl: String) {
        val url = normalizeUrl(rawUrl)
        _baseUrl.value = url
        probeSingle(url)
    }

    // ── Retry / disconnect ────────────────────────────────────────────────────

    fun retry() {
        bgRetryJob?.cancel()
        _isLanTransitioning.value = false
        val hotspot = savedUrls("hotspot_urls")
        val lan     = savedUrls("lan_urls")
        val legacy  = prefs.getString("base_url", null)?.let { listOf(normalizeUrl(it)) } ?: emptyList()
        val all = (hotspot + lan + legacy).distinct()
        if (all.isNotEmpty()) {
            probeAll(prioritizeByNetwork(
                hotspot.ifEmpty { legacy },
                lan.ifEmpty { if (hotspot.isEmpty()) emptyList() else legacy }
            ))
        } else {
            _baseUrl.value?.let { probeSingle(it) }
        }
    }

    /**
     * Called from the manual-IP entry UI.  Accepts bare IPs, IP:port, or full URLs.
     */
    fun connectToManualIp(rawInput: String) {
        val input = rawInput.trim()
        if (input.isBlank()) return
        _isLanTransitioning.value = false
        val url = when {
            input.startsWith("http://") || input.startsWith("https://") -> normalizeUrl(input)
            input.contains(":") && !input.startsWith("[")               -> normalizeUrl("http://$input")
            else                                                        -> normalizeUrl("https://$input")
        }
        val existing = savedUrls("lan_urls")
        if (!existing.contains(url)) {
            prefs.edit().putString("lan_urls", (listOf(url) + existing).joinToString(",")).apply()
        }
        probeSingle(url)
    }

    /**
     * Restart the setup flow.  Acts as a "redo" — if Atlas is currently reachable
     * over a LAN we ask it to bring its hotspot back up (single-radio Atlas drops
     * the LAN as a side effect) and clear the saved LAN URLs so the wizard always
     * starts from the hotspot pairing step.  The hotspot request is fire-and-
     * forget: the connection often dies mid-response on single-radio Atlas, and
     * Atlas's own auto-hotspot fallback (~30 s with no known LAN) covers any case
     * where the request never lands.
     */
    fun showSetupWizard() {
        val captured = _baseUrl.value
        cancelAllJobs()
        _isLanTransitioning.value = false

        if (captured != null && !isHotspotUrlAtlas(captured)) {
            viewModelScope.launch(Dispatchers.IO) {
                withTimeoutOrNull(4_000L) {
                    runCatching {
                        AtlasRepository(ApiClient.createForProbe(captured)).startHotspot()
                    }
                }
            }
        }

        prefs.edit()
            .remove("lan_urls")
            .remove("base_url")
            .apply()
        _baseUrl.value  = null
        _state.value    = ConnectionState.IDLE
        _errorMsg.value = null
    }

    fun disconnect() {
        cancelAllJobs()
        _isLanTransitioning.value = false
        prefs.edit()
            .remove("hotspot_urls")
            .remove("lan_urls")
            .remove("base_url")
            .apply()
        _baseUrl.value  = null
        _state.value    = ConnectionState.IDLE
        _errorMsg.value = null
    }

    // ── LAN-switch entry points ───────────────────────────────────────────────

    /**
     * Called from the JS bridge when the user initiates a LAN switch (before
     * /api/wifi/connect is sent).
     *
     * Two things happen immediately:
     *  a) Bootstrap pre-fetch — while the hotspot is still up we call
     *     /api/mobile/bootstrap to get ALL of Atlas's current network IPs.
     *     Any non-hotspot IPs are saved to lan_urls so the scan loop picks
     *     them up on its very first pass.  This is the fast path when Atlas
     *     already has a LAN connection (dual-radio).
     *  b) [lanTransitionLoop] starts — scanning begins before /api/wifi/connect
     *     even returns, overlapping Atlas's nmcli work with our scanning.
     *
     * Emulator note: the emulator's virtual network never changes (all traffic
     * routes through the host), so we never rely on WiFi-change callbacks during
     * a LAN switch.  The loop runs until Atlas is found or the 90 s deadline.
     */
    fun onLanSwitchInitiated() {
        val currentUrl = _baseUrl.value   // capture before cancelAllJobs clears nothing (it doesn't clear _baseUrl)
        lanSwitchPending = true
        cancelAllJobs()
        MdnsDns.clearCache()
        _isLanTransitioning.value = true
        _state.value    = ConnectionState.CHECKING
        _errorMsg.value = null

        bgRetryJob = viewModelScope.launch(Dispatchers.IO) {
            // Pre-fetch concurrently so the loop starts immediately.
            // If the fetch completes before the second loop pass the lan_urls
            // will be populated; otherwise the loop finds Atlas via subnet scan.
            // isPending=true so S1 never probes the dying hotspot URL and reports
            // a false "connected" result that would drop seconds later.
            val prefetchJob = launch { prefetchAtlasBootstrapUrls(currentUrl) }
            lanTransitionLoop(null, isPending = true)
            prefetchJob.cancel()
        }
    }

    /**
     * Called from the JS bridge after /api/wifi/connect responds.
     *
     * For non-pending switches (Atlas already on LAN): saves the confirmed URLs
     * and probes immediately.
     *
     * For pending switches (single-radio, hotspot dropping): cancels the hint-less
     * loop from [onLanSwitchInitiated] and restarts it with the server-supplied
     * hintIp so the targeted /24 scan runs first.  If hintIp is null (first-ever
     * switch to this SSID) the full multi-subnet scan handles discovery.
     */
    fun onLanSwitchConfirmed(newUrls: List<String>, isPending: Boolean, hintIp: String? = null) {
        cancelAllJobs()
        lanSwitchPending = false
        MdnsDns.clearCache()

        val normalized = newUrls.map { normalizeUrl(it) }.filter { it.isNotBlank() }
        if (normalized.isNotEmpty()) {
            val existing = savedUrls("lan_urls")
            prefs.edit()
                .putString("lan_urls", (normalized + existing).distinct().joinToString(","))
                .apply()
        }

        if (!isPending) {
            _isLanTransitioning.value = false
            val hotspot = savedUrls("hotspot_urls")
            val lan     = savedUrls("lan_urls")
            probeAll((lan + hotspot).distinct())
        } else {
            bgRetryJob = viewModelScope.launch(Dispatchers.IO) {
                lanTransitionLoop(hintIp, isPending = true)
            }
        }
    }

    // ── Pre-fetch ─────────────────────────────────────────────────────────────

    /**
     * Pre-fetches Atlas's current LAN IPs while the hotspot is still up.
     *
     * Tries /api/wifi/my_ips first (lighter, returns IPs from all active
     * interfaces) then falls back to /api/mobile/bootstrap (heavier but
     * contains baseUrls including all reachable addresses).
     *
     * Any non-hotspot IPs are saved to lan_urls so the transition loop's S1
     * direct-probe pass can find Atlas in under 1 s when Atlas is already
     * dual-radio (hotspot + LAN simultaneously).
     *
     * On single-radio Atlas (only hotspot active) we get no new LAN IPs and
     * just continue relying on the subnet scan.
     *
     * Timeout: 4 s total.  If the fetch fails or is cancelled (e.g. because
     * onLanSwitchConfirmed fires and calls cancelAllJobs) we proceed without
     * extra IPs — the subnet scan is the reliable fallback.
     */
    private suspend fun prefetchAtlasBootstrapUrls(baseUrl: String?) {
        if (baseUrl.isNullOrBlank()) return
        // Use the shared probe client (900 ms timeouts) — the prefetch only needs
        // to succeed while the hotspot is alive (fast), and we want it to fail
        // immediately when the hotspot is already gone rather than blocking for
        // the full 5-second connect timeout of ApiClient.create().
        val repo = AtlasRepository(ApiClient.createForProbe(baseUrl))

        // Try the lightweight /api/wifi/my_ips endpoint first.
        val myIpUrls: List<String> = withTimeoutOrNull(3_000L) {
            runCatching { repo.getMyIps().getOrNull()?.urls }.getOrNull()
        }?.orEmpty() ?: emptyList()

        // Fall back to bootstrap manifest if my_ips returned nothing.
        val bootstrapUrls: List<String> = if (myIpUrls.isNotEmpty()) emptyList() else {
            withTimeoutOrNull(4_000L) {
                runCatching {
                    repo.getBootstrap().getOrNull()?.api?.baseUrls
                }.getOrNull()
            }?.orEmpty() ?: emptyList()
        }

        val raw = (myIpUrls + bootstrapUrls)
            .map { normalizeUrl(it) }
            .filter { url ->
                // Drop hotspot gateway IPs — they disappear after the switch.
                !url.contains("10.42.0.1") &&
                !url.contains("atlas_navigate")
            }
            .distinct()
            .takeIf { it.isNotEmpty() }
            ?: return

        val existing = savedUrls("lan_urls")
        prefs.edit()
            .putString("lan_urls", (raw + existing).distinct().joinToString(","))
            .apply()
    }

    // ── LAN transition loop ───────────────────────────────────────────────────

    /**
     * Discovery loop for the hotspot → LAN transition.
     *
     * Strategies run concurrently per pass; first to respond wins and
     * immediately cancels the others.
     *
     *  S1 — direct probe of hint + pre-fetched + saved URLs (fast path
     *       when Atlas is already on a LAN we know about)
     *  S2 — port-5000 fingerprint sweep, the *primary* discovery path.
     *       Probes `http://prefix.host:5000/api/device` across the
     *       device's own /24 plus every common home / lab / VPN prefix
     *       and only accepts hosts whose response carries Atlas's
     *       `app == "atlas-control"` fingerprint.
     *  S3 — targeted /24 sweep of hintIp's subnet (also port-5000-only,
     *       fingerprint-validated)
     *  S4 — wifi-status polling: fetches /api/wifi/status at known URLs
     *       every 3 s; when Atlas reports its switch is complete the
     *       confirmed new IP is probed immediately
     *  S5 — mDNS as a supplementary check with a short 5 s window.
     *       Single-radio Atlas's avahi re-announcement on a brand-new
     *       LAN is unreliable, so we never gate the loop on it.
     *
     * Each iteration is bounded to ~20 s so the loop can retry many
     * times within the 120 s deadline — important when Atlas hasn't
     * finished joining the new LAN yet on the first iteration.
     *
     * All IP probes share [ApiClient.probeClient] — a single OkHttpClient
     * — so a sweep of thousands of hosts doesn't churn through object
     * allocations or starve the GC.
     *
     * [isPending] — true when the hotspot is actively dropping (the
     * server returned pending=true or we know the hotspot is about to
     * stop). When pending, hotspot URLs and prefixes are excluded so we
     * never falsely report "CONNECTED" to a dying hotspot.
     */
    private suspend fun lanTransitionLoop(hintIp: String?, isPending: Boolean = false) {
        val deadline = System.currentTimeMillis() + LAN_TRANSITION_TIMEOUT_MS

        // A non-blank hint IP that points at the hotspot range is junk during a
        // pending transition — refuse it before it pollutes the targeted /24 scan.
        val effectiveHint: String? = hintIp?.takeIf {
            it.isNotBlank() && !(isPending && isHotspotHost(it))
        }

        // Reject any URL that resolves to a hotspot host while pending — they
        // would set `winner`, cancel the scope, and waste an iteration.
        fun claim(scope: CoroutineScope, winner: AtomicReference<String?>, url: String?) {
            if (url.isNullOrBlank()) return
            if (isPending && isHotspotUrlAtlas(url)) return
            if (winner.compareAndSet(null, url)) scope.cancel()
        }

        while (System.currentTimeMillis() < deadline) {
            MdnsDns.clearCache()
            val lan     = savedUrls("lan_urls")
            val hotspot = savedUrls("hotspot_urls")

            val directCandidates = buildList {
                if (effectiveHint != null) {
                    add(normalizeUrl("https://$effectiveHint"))
                    add(normalizeUrl("http://$effectiveHint:5000"))
                }
                networkMonitor.gatewayUrl()?.let { add(it) }
                addAll(lan)
                // Omit hotspot URLs during a pending transition — the hotspot is
                // dropping and connecting to it would give a false CONNECTED result.
                if (!isPending) addAll(hotspot)
                add("https://atlas.local")
                add("http://atlas.local:5000")
            }.distinct()

            val app    = getApplication<Application>()
            val winner = AtomicReference<String?>(null)

            try {
                coroutineScope {
                    val scope = this

                    // S1: direct probe — hint + pre-fetched + saved URLs
                    launch(Dispatchers.IO) {
                        claim(scope, winner, probeUrlsFast(directCandidates))
                    }

                    // S2 (PRIMARY): UDP beacon shout-and-receive. Atlas beacons
                    // at 1 Hz for 90 s after /api/wifi/connect, so in-app LAN
                    // switches are found within ~1 s of Atlas rebinding — same
                    // strategy the setup wizard already uses. The reply is
                    // re-verified over TCP (/api/device) before it can win.
                    launch(Dispatchers.IO) {
                        AtlasBeacon.discover(app, timeoutMs = 14_000L, excludeHotspot = isPending)?.let { hit ->
                            claim(scope, winner, probeUrlsFast(listOf(hit)))
                        }
                    }

                    // S2b: port-5000 fingerprint sweep across every
                    // candidate /24. Bounded to 20 s so the loop can retry
                    // ~5 times within the 120 s deadline — important when
                    // Atlas hasn't finished joining the new LAN on iteration 1.
                    // Hotspot prefixes are excluded during a pending transition.
                    // Deterministic fallback for firmware without the beacon.
                    launch(Dispatchers.IO) {
                        val r = withTimeoutOrNull(20_000L) {
                            NsdHelper.findAtlasByPortSweep(app, excludeHotspot = isPending)
                        }
                        claim(scope, winner, r)
                    }

                    // S3: targeted /24 sweep of the hint subnet (fast path
                    //     when the backend gave us Atlas's predicted IP).
                    if (effectiveHint != null) {
                        launch(Dispatchers.IO) {
                            val r = withTimeoutOrNull(15_000L) {
                                NsdHelper.findAtlasByTargetSubnet(effectiveHint, excludeHotspot = isPending)
                            }
                            claim(scope, winner, r)
                        }
                    }

                    // S4: wifi-status polling — when Atlas completes its
                    // background switch it stores the new IP in
                    // wifiSwitch.result; we read it from /api/wifi/status
                    // at each known URL and probe immediately.
                    launch(Dispatchers.IO) {
                        pollWifiSwitchStatus(directCandidates, scope, winner)
                    }

                    // S5: mDNS / NSD as a supplementary check only.
                    // Single-radio Atlas's avahi announcement on a brand-new
                    // LAN is unreliable, so a short 5 s window is enough —
                    // anything longer just delays the next iteration.
                    launch(Dispatchers.IO) {
                        val r = NsdHelper.findAtlasOnLan(app, timeoutMs = 5_000L)
                        claim(scope, winner, r)
                    }
                }
            } catch (e: CancellationException) {
                if (winner.get() == null) throw e
            }

            val rawFound = winner.get()
            // Defence-in-depth: even with claim() filtering, a hotspot URL that
            // somehow slipped through (e.g. arrived via S5's pollWifiSwitchStatus
            // before a future change relaxed its own filter) is rejected here.
            val found = if (rawFound != null && isPending && isHotspotUrlAtlas(rawFound)) null else rawFound
            if (found != null) {
                persistDiscoveredUrl(found)
                val resolved = toWebReachableUrl(found)
                startNotificationPolling(resolved)
                // The switch is over — without this, handleNetworkChange would
                // swallow the NEXT genuine WiFi change (the flag is normally
                // cleared by onLanSwitchConfirmed, but on single-radio Atlas the
                // confirm response usually dies with the hotspot).
                lanSwitchPending = false
                _isLanTransitioning.value = false
                _baseUrl.value  = resolved
                _state.value    = ConnectionState.CONNECTED
                _errorMsg.value = null
                return
            }

            val remaining = deadline - System.currentTimeMillis()
            if (remaining > 3_000L) delay(3_000L)
        }

        lanSwitchPending = false
        _isLanTransitioning.value = false
        _errorMsg.value = "Could not find Atlas on the new network. Tap retry or enter the Atlas IP manually."
        _state.value    = ConnectionState.FAILED
        startBackgroundRetry()
    }

    /**
     * Polls /api/wifi/status at [candidateUrls] every 3 s.
     *
     * When Atlas's background WiFi switch completes, its wifiSwitch.result
     * contains the confirmed new IP and accessUrls.  We extract those URLs,
     * probe them directly, and signal the winner when one responds.
     *
     * This short-circuits the subnet scan for dual-radio Atlas or for cases
     * where we have a recent hint IP that matches the new LAN address.
     */
    private suspend fun pollWifiSwitchStatus(
        candidateUrls: List<String>,
        scope: CoroutineScope,
        winner: AtomicReference<String?>,
    ) {
        val deadline = System.currentTimeMillis() + 45_000L
        while (System.currentTimeMillis() < deadline && winner.get() == null) {
            for (url in candidateUrls) {
                if (winner.get() != null) return
                val status = withTimeoutOrNull(2_000L) {
                    runCatching {
                        AtlasRepository(ApiClient.createForProbe(url)).getWifiStatus().getOrNull()
                    }.getOrNull()
                } ?: continue

                val switchState = status.wifiSwitch ?: continue
                if (switchState.pending) continue  // switch not done yet

                // Switch completed — collect confirmed access URLs.
                // We deliberately filter hotspot URLs even though `result.ip`
                // should always be the new LAN IP: if the backend ever emits a
                // hotspot IP here (or `url` itself is the dying hotspot we
                // polled through), we must not advertise it as the winner.
                val confirmedUrls = buildList {
                    switchState.result?.ip?.takeIf { it.isNotBlank() }?.let { ip ->
                        add(normalizeUrl("https://$ip"))
                        add(normalizeUrl("http://$ip:5000"))
                    }
                    switchState.result?.hintIp?.takeIf { it.isNotBlank() }?.let { ip ->
                        add(normalizeUrl("https://$ip"))
                        add(normalizeUrl("http://$ip:5000"))
                    }
                    switchState.result?.accessUrls?.forEach { add(normalizeUrl(it)) }
                    // The URL that just answered is also a valid candidate
                    add(normalizeUrl(url))
                }.distinct().filter { it.isNotBlank() && !isHotspotUrlAtlas(it) }

                val found = probeUrlsFast(confirmedUrls)
                if (found != null && winner.compareAndSet(null, found)) {
                    scope.cancel()
                    return
                }
            }
            if (winner.get() == null) delay(3_000L)
        }
    }

    companion object {
        private const val LAN_TRANSITION_TIMEOUT_MS = 120_000L
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun cancelAllJobs() {
        probeJob?.cancel()
        bgRetryJob?.cancel()
        notificationPollJob?.cancel()
    }

    private fun restoreAndProbe() {
        val hotspot = savedUrls("hotspot_urls")
        val lan     = savedUrls("lan_urls")
        val legacy  = prefs.getString("base_url", null)?.let { listOf(normalizeUrl(it)) } ?: emptyList()
        val all = (hotspot + lan + legacy).distinct()
        if (all.isNotEmpty()) {
            probeAll(prioritizeByNetwork(
                hotspot.ifEmpty { legacy },
                lan.ifEmpty { if (hotspot.isEmpty()) emptyList() else legacy }
            ))
        }
    }

    private fun savedUrls(key: String): List<String> =
        prefs.getString(key, null)?.split(",")?.filter { it.isNotBlank() } ?: emptyList()

    private fun prioritizeByNetwork(
        hotspotUrls: List<String>,
        lanUrls: List<String>,
    ): List<String> =
        if (networkMonitor.isOnAtlasHotspot)
            (hotspotUrls + lanUrls).distinct()
        else
            (lanUrls + hotspotUrls).distinct()

    private fun handleNetworkChange() {
        MdnsDns.clearCache()
        val wasLanSwitch = lanSwitchPending
        lanSwitchPending = false

        when {
            wasLanSwitch -> {
                // The lanTransitionLoop already handles this.  Ignore the event
                // so we don't cancel the running loop.
            }
            _state.value == ConnectionState.CONNECTED -> networkChangeRetry()
            _state.value == ConnectionState.FAILED    -> retry()
            else -> { /* IDLE / CHECKING — leave in peace */ }
        }
    }

    private fun networkChangeRetry() {
        cancelAllJobs()
        probeJob = viewModelScope.launch {
            reconnectMutex.withLock {
                val current = _baseUrl.value
                if (current != null) {
                    val stillAlive = withTimeoutOrNull(2_000L) {
                        AtlasRepository(ApiClient.createForProbe(current)).getDevice().isSuccess
                    } ?: false
                    if (stillAlive) {
                        val resolved = toWebReachableUrl(current)
                        if (resolved != current) _baseUrl.value = resolved
                        startNotificationPolling(resolved)
                        return@withLock
                    }
                }

                val hotspot = savedUrls("hotspot_urls")
                val lan     = savedUrls("lan_urls")
                val legacy  = prefs.getString("base_url", null)?.let { listOf(normalizeUrl(it)) } ?: emptyList()
                val ordered = prioritizeByNetwork(
                    hotspot.ifEmpty { legacy },
                    lan.ifEmpty { if (hotspot.isEmpty()) emptyList() else legacy }
                )
                val candidates = buildList {
                    networkMonitor.gatewayUrl()?.let { add(it) }
                    addAll(ordered)
                    add("https://atlas.local")
                    add("http://atlas.local:5000")
                }.distinct()

                val found = discoverAtlasFast(candidates)
                if (found != null) {
                    persistDiscoveredUrl(found)
                    val resolvedUrl = toWebReachableUrl(found)
                    startNotificationPolling(resolvedUrl)
                    _baseUrl.value  = resolvedUrl
                    _state.value    = ConnectionState.CONNECTED
                    _errorMsg.value = null
                } else {
                    _errorMsg.value = "Lost connection to Atlas — join the atlas_navigate hotspot or confirm Atlas is on your LAN."
                    _state.value = ConnectionState.FAILED
                    startBackgroundRetry()
                }
            }
        }
    }

    private fun startBackgroundRetry() {
        bgRetryJob?.cancel()
        bgRetryJob = viewModelScope.launch {
            val delays = listOf(5_000L, 10_000L, 20_000L, 30_000L)
            var attempt = 0
            while (_state.value == ConnectionState.FAILED) {
                delay(delays.getOrElse(attempt) { 30_000L })
                attempt++
                if (_state.value != ConnectionState.FAILED) break

                val hotspot = savedUrls("hotspot_urls")
                val lan     = savedUrls("lan_urls")
                val legacy  = prefs.getString("base_url", null)?.let { listOf(normalizeUrl(it)) } ?: emptyList()
                val ordered = prioritizeByNetwork(
                    hotspot.ifEmpty { legacy },
                    lan.ifEmpty { if (hotspot.isEmpty()) emptyList() else legacy }
                )
                val candidates = buildList {
                    networkMonitor.gatewayUrl()?.let { add(it) }
                    addAll(ordered)
                    add("https://atlas.local")
                    add("http://atlas.local:5000")
                }.distinct()

                val found = discoverAtlasFast(candidates)
                if (found != null && _state.value == ConnectionState.FAILED) {
                    persistDiscoveredUrl(found)
                    val resolvedUrl = toWebReachableUrl(found)
                    startNotificationPolling(resolvedUrl)
                    _baseUrl.value  = resolvedUrl
                    _state.value    = ConnectionState.CONNECTED
                    _errorMsg.value = null
                    break
                }
            }
        }
    }

    private fun probeSingle(url: String) {
        cancelAllJobs()
        _state.value = ConnectionState.CHECKING
        probeJob = viewModelScope.launch {
            val result = withTimeoutOrNull(8_000L) {
                AtlasRepository(ApiClient.createForProbe(url)).getDevice()
            }
            if (result?.isSuccess == true) {
                val resolvedUrl = toWebReachableUrl(url)
                startNotificationPolling(resolvedUrl)
                _baseUrl.value  = resolvedUrl
                _state.value    = ConnectionState.CONNECTED
                _errorMsg.value = null
            } else {
                _errorMsg.value = result?.exceptionOrNull()?.message ?: "Connection timed out"
                _state.value    = ConnectionState.FAILED
                startBackgroundRetry()
            }
        }
    }

    private fun probeAll(urls: List<String>) {
        cancelAllJobs()
        _state.value = ConnectionState.CHECKING
        probeJob = viewModelScope.launch {
            reconnectMutex.withLock {
                val withGateway = buildList {
                    networkMonitor.gatewayUrl()?.let { add(it) }
                    addAll(urls)
                    add("https://atlas.local")
                    add("http://atlas.local:5000")
                }.distinct()

                val found = discoverAtlasFast(withGateway)
                if (found != null) {
                    persistDiscoveredUrl(found)
                    val resolvedUrl = toWebReachableUrl(found)
                    startNotificationPolling(resolvedUrl)
                    _baseUrl.value  = resolvedUrl
                    _state.value    = ConnectionState.CONNECTED
                    _errorMsg.value = null
                } else {
                    _errorMsg.value = "Could not reach Atlas — join the atlas_navigate hotspot or confirm Atlas is on your LAN."
                    _state.value    = ConnectionState.FAILED
                    startBackgroundRetry()
                }
            }
        }
    }

    /**
     * Probes a small list of known URLs in parallel using the shared probe
     * client.  Intended for direct candidates (hint IP, saved URLs, atlas.local)
     * rather than full subnet scans.
     *
     * Concurrency: 16 simultaneous probes, 1.5 s timeout per URL.
     * For .local hostnames the timeout is 2 s to allow for mDNS resolution.
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
     * Legacy probe function kept for [discoverAtlasFast] callers that use the
     * full client (non-scan paths: startup, network-change reconnect).
     */
    private suspend fun probeUrls(urls: List<String>): String? {
        if (urls.isEmpty()) return null
        return withContext(Dispatchers.IO) {
            val found = AtomicReference<String?>(null)
            val limit = Semaphore(8)
            coroutineScope {
                urls.map { url ->
                    async {
                        if (found.get() != null) return@async
                        limit.withPermit {
                            if (found.get() != null) return@withPermit
                            val timeout = if (url.contains(".local")) 2_000L else 3_000L
                            val ok = withTimeoutOrNull(timeout) {
                                runCatching {
                                    AtlasRepository(ApiClient.create(url)).getDevice().isSuccess
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

    private suspend fun discoverAtlasFast(candidateUrls: List<String>): String? {
        val app    = getApplication<Application>()
        val winner = AtomicReference<String?>(null)
        try {
            coroutineScope {
                val scope = this
                // Direct-probe known URLs first (fast path on familiar LANs).
                launch(Dispatchers.IO) {
                    probeUrls(candidateUrls)?.let {
                        if (winner.compareAndSet(null, it)) scope.cancel()
                    }
                }
                // UDP beacon — answers Atlas's shout-and-receive responder even
                // outside the post-switch broadcast window; re-verified over TCP.
                launch(Dispatchers.IO) {
                    AtlasBeacon.discover(app, timeoutMs = 4_000L)?.let { hit ->
                        probeUrlsFast(listOf(hit))?.let {
                            if (winner.compareAndSet(null, it)) scope.cancel()
                        }
                    }
                }
                // Port-5000 fingerprint sweep — deterministic fallback.
                launch(Dispatchers.IO) {
                    withTimeoutOrNull(20_000L) { NsdHelper.findAtlasByPortSweep(app) }?.let {
                        if (winner.compareAndSet(null, it)) scope.cancel()
                    }
                }
                // mDNS as a supplementary 5 s check; never the gate.
                launch(Dispatchers.IO) {
                    NsdHelper.findAtlasOnLan(app, timeoutMs = 5_000L)?.let {
                        if (winner.compareAndSet(null, it)) scope.cancel()
                    }
                }
            }
        } catch (e: CancellationException) {
            if (winner.get() == null) throw e
        }
        return winner.get()
    }

    private fun persistDiscoveredUrl(url: String) {
        val lan     = savedUrls("lan_urls")
        val hotspot = savedUrls("hotspot_urls")
        val toAdd   = mutableListOf<String>()
        if (!lan.contains(url) && !hotspot.contains(url)) toAdd += url
        val webUrl = toWebReachableUrl(url)
        if (webUrl != url && !lan.contains(webUrl) && !hotspot.contains(webUrl)) toAdd += webUrl
        if (toAdd.isNotEmpty()) {
            prefs.edit().putString("lan_urls", (lan + toAdd).distinct().joinToString(",")).apply()
        }
    }

    private fun normalizeUrl(url: String): String {
        var u = url.trim()
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://$u"
        if (!u.endsWith("/")) u = "$u/"
        return u
    }

    /** True if [url] resolves to one of Atlas's hotspot subnets. */
    private fun isHotspotUrlAtlas(url: String): Boolean =
        url.contains("10.42.0.")   ||
        url.contains("192.168.4.") ||
        url.contains("192.168.43.")

    /** True if [host] (a bare IPv4 string) lives in a hotspot prefix. */
    private fun isHotspotHost(host: String): Boolean {
        val parts = host.split(".")
        if (parts.size < 3) return false
        val pfx = parts.take(3).joinToString(".")
        return pfx == "10.42.0" || pfx == "192.168.4" || pfx == "192.168.43"
    }

    private fun startNotificationPolling(baseUrl: String) {
        if (notificationPollJob?.isActive == true && _baseUrl.value == baseUrl) return
        notificationPollJob?.cancel()
        notificationPollJob = viewModelScope.launch {
            val repo = AtlasRepository(ApiClient.create(baseUrl))
            while (_state.value == ConnectionState.CONNECTED) {
                try {
                    pollNotifications(repo)
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Throwable) {}
                delay(10_000L)
            }
        }
    }

    private suspend fun pollNotifications(repo: AtlasRepository) {
        val device = repo.getDevice().getOrNull() ?: run {
            if (_state.value == ConnectionState.CONNECTED) {
                MdnsDns.clearCache()
                networkChangeRetry()
            }
            return
        }
        val messages = repo.getMessages().getOrNull().orEmpty()
        maybeNotifyForNewMessage(messages, device)
        maybeNotifyForBattery(device)
    }

    private fun maybeNotifyForNewMessage(messages: List<AtlasMessage>, device: DeviceInfo) {
        val latest    = messages.firstOrNull() ?: return
        val latestKey = messageKey(latest)
        val storedKey = prefs.getString("notification_last_message_key", null)
        if (storedKey == null) {
            prefs.edit().putString("notification_last_message_key", latestKey).apply()
            return
        }
        if (latestKey == storedKey) return
        prefs.edit().putString("notification_last_message_key", latestKey).apply()
        if (latest.fromId != null && latest.fromId == device.myNodeId) return
        val title  = if (latest.isDirect != 0) "New direct message" else "New Atlas message"
        val sender = latest.fromId?.takeIf { it.isNotBlank() } ?: "Unknown sender"
        val text   = latest.text?.trim().orEmpty()
        val body   = if (text.isBlank()) "Message received from $sender." else "$sender: $text"
        AtlasNotificationManager.showMessageNotification(getApplication(), title, body)
    }

    private fun maybeNotifyForBattery(device: DeviceInfo) {
        val pct     = device.batteryPct ?: return
        val charging = isCharging(device)
        val lowSent        = prefs.getBoolean("notification_battery_low_sent", false)
        val chargeHighSent = prefs.getBoolean("notification_battery_charge_high_sent", false)

        if (pct <= 20) {
            if (!lowSent) AtlasNotificationManager.showBatteryLowNotification(getApplication(), pct)
            prefs.edit().putBoolean("notification_battery_low_sent", true).apply()
        } else if (lowSent) {
            prefs.edit().putBoolean("notification_battery_low_sent", false).apply()
        }

        if (charging && pct >= 90) {
            if (!chargeHighSent) AtlasNotificationManager.showBatteryChargedNotification(getApplication(), pct)
            prefs.edit().putBoolean("notification_battery_charge_high_sent", true).apply()
        } else if (chargeHighSent && (!charging || pct < 90)) {
            prefs.edit().putBoolean("notification_battery_charge_high_sent", false).apply()
        }
    }

    private fun isCharging(device: DeviceInfo): Boolean {
        val phase  = device.batteryPhase?.lowercase()
        val status = device.batteryStatus?.lowercase()
        return phase == "charging" || status == "charging"
    }

    private fun messageKey(message: AtlasMessage): String {
        val packetId = message.packetId?.toString()
        return packetId ?: "${message.rxTime}:${message.fromId}:${message.text}"
    }

    /**
     * OkHttp can resolve atlas.local through MdnsDns, but Android WebView cannot.
     * Swap .local hosts for the resolved numeric IP before loading the page.
     */
    private fun toWebReachableUrl(url: String): String {
        val normalized = normalizeUrl(url)
        val parsed = runCatching { URI(normalized) }.getOrNull() ?: return normalized
        val host   = parsed.host?.lowercase() ?: return normalized
        if (!host.endsWith(".local")) return normalized
        val ip = runCatching { MdnsDns.lookup(host).firstOrNull()?.hostAddress }
            .getOrNull()
            ?.takeIf { it.isNotBlank() } ?: return normalized
        val port = if (parsed.port == -1) "" else ":${parsed.port}"
        return "${parsed.scheme}://$ip$port/"
    }
}

package com.atlascontrol.mobile.ui

import android.app.Application
import android.content.Context
import android.os.Build
import android.webkit.CookieManager
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.atlascontrol.mobile.network.AtlasClient
import com.atlascontrol.mobile.network.AtlasDiscovery
import com.atlascontrol.mobile.network.AtlasInfo
import com.atlascontrol.mobile.network.AtlasLabel
import com.atlascontrol.mobile.network.AtlasTrust
import com.atlascontrol.mobile.network.DeviceStore
import com.atlascontrol.mobile.network.PairedDevice
import com.atlascontrol.mobile.network.PairingException
import com.atlascontrol.mobile.network.Sighting
import com.atlascontrol.mobile.notifications.AtlasNotificationManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject

/** What the app is showing. */
sealed interface Screen {
    /** No Atlas paired yet: find one and pair. */
    data object Onboarding : Screen
    /** Looking for the paired Atlas (after launch, a network change, a reboot). */
    data class Locating(val message: String) : Screen
    data class Connected(val baseUrl: String) : Screen
    /** Atlas is up but restarting (update or reboot); we wait and reconnect. */
    data class Restarting(val message: String) : Screen
    /** Could not find the paired Atlas on any reachable network. */
    data object NotFound : Screen
    /** The paired Atlas presented a different TLS key. */
    data object SecurityWarning : Screen
    /** The same Atlas (same key) no longer accepts this phone: removed in Settings, or Atlas was reset. */
    data object Unpaired : Screen
}

/** One pairing attempt in the onboarding flow. */
data class PairingTarget(val info: AtlasInfo, val onAtlasHotspot: Boolean)

/**
 * Owns the connection to the one Atlas this phone is paired with.
 *
 * The Atlas is remembered by device id + TLS key pin + pairing token, never
 * by address. Whenever the connection drops (IP change, network switch,
 * reboot, update) it is found again by id and confirmed by its key; the
 * owner never re-adds it, clears data or types an address. A different key
 * for the same device id is a security warning, never accepted silently.
 * Pairing is asked for again only when Atlas itself ended it — the phone was
 * removed in Settings → Connected devices, or Atlas was factory reset.
 */
class ConnectionViewModel(app: Application) : AndroidViewModel(app) {
    private val store = DeviceStore(app)
    private val prefs = app.getSharedPreferences("atlas_notifications", Context.MODE_PRIVATE)

    private val _screen = MutableStateFlow<Screen>(Screen.Locating("Connecting to Atlas…"))
    val screen: StateFlow<Screen> = _screen.asStateFlow()

    private val _events = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val events = _events.asSharedFlow()          // one-off notices ("Update complete.")

    // Onboarding state
    private val _nearby = MutableStateFlow<List<Sighting>>(emptyList())
    val nearby: StateFlow<List<Sighting>> = _nearby.asStateFlow()
    private val _searching = MutableStateFlow(false)
    val searching: StateFlow<Boolean> = _searching.asStateFlow()
    private val _target = MutableStateFlow<PairingTarget?>(null)
    val target: StateFlow<PairingTarget?> = _target.asStateFlow()
    private val _pairingError = MutableStateFlow<String?>(null)
    val pairingError: StateFlow<String?> = _pairingError.asStateFlow()
    private val _matchCode = MutableStateFlow<String?>(null)
    val matchCode: StateFlow<String?> = _matchCode.asStateFlow()
    private val _joinWifi = MutableStateFlow<AtlasLabel?>(null)
    val joinWifi: StateFlow<AtlasLabel?> = _joinWifi.asStateFlow()

    val pairedName: String? get() = store.load()?.name

    private var device: PairedDevice? = store.load()
    private var monitorJob: Job? = null
    private var locateJob: Job? = null
    private var updateInProgress = false
    private var pendingUrls: List<String> = emptyList()

    init {
        if (device == null) {
            _screen.value = Screen.Onboarding
            searchNearby()
        } else {
            relocate()
        }
    }

    val deviceName: String get() = device?.name ?: "Atlas"
    fun currentDevice(): PairedDevice? = device

    // ── connecting ───────────────────────────────────────────────────────
    fun relocate(message: String = "Connecting to ${deviceName}…") {
        val dev = device ?: run { _screen.value = Screen.Onboarding; return }
        locateJob?.cancel()
        monitorJob?.cancel()
        _screen.value = if (updateInProgress) Screen.Restarting(RESTARTING_FOR_UPDATE) else Screen.Locating(message)
        locateJob = viewModelScope.launch {
            var attempt = 0
            val ctx = getApplication<Application>()
            while (isActive) {
                for (u in pendingUrls) if (AtlasClient.confirm(u, dev)) { connected(u); return@launch }
                val url = AtlasDiscovery.locate(ctx, dev, thorough = attempt >= 2)
                if (url != null) { connected(url); return@launch }
                if (identityChanged(ctx, dev)) { _screen.value = Screen.SecurityWarning; return@launch }
                attempt++
                if (attempt == 4 && !updateInProgress) _screen.value = Screen.NotFound
                delay(minOf(2_000L * attempt, 15_000L))
            }
        }
    }

    /** The same device id answering with a different key: re-provisioned or impersonated. */
    private suspend fun identityChanged(ctx: Context, dev: PairedDevice): Boolean {
        val candidates = AtlasDiscovery.discover(ctx, 3_000L).filter { it.deviceId == dev.deviceId }
        for (s in candidates) {
            val info = AtlasClient.probe(s.baseUrl) ?: continue
            if (info.deviceId == dev.deviceId && info.observedPin != dev.pin) return true
        }
        return false
    }

    private fun connected(url: String) {
        store.rememberUrl(url)
        device = device?.copy(lastUrl = url)
        pendingUrls = emptyList()
        if (updateInProgress) {
            updateInProgress = false
            _events.tryEmit("Update complete.")
        }
        _screen.value = Screen.Connected(url)
        startMonitor(url)
    }

    /** While connected: notice restarts/updates quickly and keep notifications flowing. */
    private fun startMonitor(url: String) {
        monitorJob?.cancel()
        val dev = device ?: return
        monitorJob = viewModelScope.launch {
            if (AtlasClient.stillPaired(url, dev) == false) { removed(); return@launch }
            var misses = 0
            while (isActive) {
                delay(10_000L)
                val stage = AtlasClient.updateStage(url, dev)
                if (stage in UPDATE_STAGES) updateInProgress = true
                val healthy = try {
                    AtlasClient.health(url, dev)
                } catch (e: Exception) {
                    _screen.value = Screen.SecurityWarning
                    return@launch
                }
                when (healthy) {
                    true -> {
                        misses = 0
                        if (AtlasClient.stillPaired(url, dev) == false) { removed(); return@launch }
                        runCatching { withContext(Dispatchers.IO) { pollNotifications(url, dev) } }
                    }
                    false -> {
                        _screen.value = Screen.Restarting(
                            if (updateInProgress) RESTARTING_FOR_UPDATE else "Atlas is restarting…")
                        waitForReturn(url)
                        return@launch
                    }
                    null -> if (++misses >= 2) {
                        relocate(if (updateInProgress) RESTARTING_FOR_UPDATE else "Reconnecting to ${deviceName}…")
                        return@launch
                    }
                }
            }
        }
    }

    private suspend fun waitForReturn(url: String) {
        val dev = device ?: return
        repeat(120) {
            delay(3_000L)
            when (runCatching { AtlasClient.health(url, dev) }.getOrNull()) {
                true -> { connected(url); return }
                null -> { relocate(RESTARTING_FOR_UPDATE.takeIf { updateInProgress } ?: "Reconnecting…"); return }
                else -> Unit
            }
        }
        relocate()
    }

    private fun removed() {
        locateJob?.cancel()
        _screen.value = Screen.Unpaired
    }

    /**
     * "Connect again" after Atlas ended this phone's pairing. It is the same
     * Atlas with the same key, so the new pairing trusts only that key.
     */
    fun reconnectRemoved() {
        val dev = device ?: run { forgetDevice(); return }
        monitorJob?.cancel(); locateJob?.cancel()
        clearPairing()
        _screen.value = Screen.Onboarding
        searchNearby()
        dev.lastUrl?.let { choose(it, expectedPin = dev.pin) }
    }

    // Called from the web UI (AtlasAndroid bridge) around Atlas Wi-Fi changes.
    fun onAtlasSwitchingNetworks() {
        _screen.value = Screen.Locating("Atlas is switching networks. If your phone was on Atlas's Wi-Fi, " +
            "it will reconnect to your usual network — this can take a minute.")
    }

    fun onAtlasSwitchedNetworks(urls: List<String>) {
        pendingUrls = urls.filter { it.startsWith("https://") }
        relocate("Finding ${deviceName} on its new network…")
    }

    // ── onboarding ───────────────────────────────────────────────────────
    fun searchNearby() {
        if (_searching.value) return
        _searching.value = true
        viewModelScope.launch {
            val ctx = getApplication<Application>()
            val found = AtlasDiscovery.discover(ctx, 5_000L).toMutableList()
            AtlasDiscovery.gatewayUrl(ctx)?.let { gw ->
                if (found.none { it.baseUrl == gw }) {
                    AtlasClient.probe(gw)?.let { found += Sighting(it.deviceId, it.name, it.advertisedPin, gw, "hotspot") }
                }
            }
            _nearby.value = found.distinctBy { it.deviceId }
            _searching.value = false
        }
    }

    fun scanNetwork() {
        if (_searching.value) return
        _searching.value = true
        viewModelScope.launch {
            val infos = AtlasDiscovery.scanSubnet(getApplication())
            _nearby.value = (_nearby.value + infos.map { Sighting(it.deviceId, it.name, it.advertisedPin, it.baseUrl, "scan") })
                .distinctBy { it.deviceId }
            _searching.value = false
        }
    }

    /** "Tap Connect" on a discovered Atlas (or an address typed in Advanced). */
    fun choose(baseUrl: String, expectedPin: String? = null) {
        _pairingError.value = null
        viewModelScope.launch {
            val info = AtlasClient.probe(baseUrl, 3_000L)
            if (info == null) {
                _pairingError.value = if (AtlasClient.isOlderAtlas(baseUrl)) OLDER_ATLAS
                    else "Couldn't reach Atlas at that address."
                return@launch
            }
            unsupported(info)?.let { _pairingError.value = it; return@launch }
            if (expectedPin != null && expectedPin != info.observedPin) {
                _pairingError.value = "This Atlas presented an unexpected security key. Don't connect to it."
                return@launch
            }
            val onHotspot = AtlasDiscovery.gatewayUrl(getApplication())?.let { AtlasClient.normalize(it) } ==
                AtlasClient.normalize(info.baseUrl)
            _target.value = PairingTarget(info, onHotspot)
            if (onHotspot) pairViaHotspot()
        }
    }

    fun cancelPairing() {
        _target.value = null
        _matchCode.value = null
        _pairingError.value = null
        _joinWifi.value = null
    }

    fun pairViaHotspot() = pair { t -> AtlasClient.claimViaHotspot(t.info.baseUrl, t.info.observedPin, phoneName()) }

    fun pairWithCode(code: String) = pair { t -> AtlasClient.claim(t.info.baseUrl, t.info.observedPin, code, phoneName()) }

    fun askForApproval() {
        val t = _target.value ?: return
        _pairingError.value = null
        viewModelScope.launch {
            try {
                val req = AtlasClient.requestApproval(t.info.baseUrl, t.info.observedPin, phoneName())
                _matchCode.value = AtlasClient.matchCode(t.info.observedPin, req.serverNonce, req.clientNonce)
                // Atlas keeps a request for 3 minutes. A busy or restarting
                // Atlas ("retry") is waited out, not reported as a failure.
                val deadline = System.currentTimeMillis() + APPROVAL_WAIT_MS
                var status = "pending"
                while (System.currentTimeMillis() < deadline) {
                    delay(if (status == "retry") 5_000L else 2_000L)
                    if (_matchCode.value == null) return@launch
                    status = AtlasClient.pollApproval(t.info.baseUrl, t.info.observedPin, req)
                    when {
                        status.startsWith("approved:") -> { finishPairing(t, status.removePrefix("approved:")); return@launch }
                        status == "denied" -> { _pairingError.value = "The request was declined."; _matchCode.value = null; return@launch }
                        status == "expired" || status == "unknown" || status == "issued" -> break
                    }
                }
                _pairingError.value = "The request timed out. Try again."
                _matchCode.value = null
            } catch (e: Exception) {
                _pairingError.value = e.message ?: "Couldn't ask Atlas."
                _matchCode.value = null
            }
        }
    }

    /** A scanned label QR code: the pin makes the very first connection trusted. */
    fun pairWithLabel(label: AtlasLabel) {
        _pairingError.value = null
        viewModelScope.launch {
            val ctx = getApplication<Application>()
            val sightings = AtlasDiscovery.discover(ctx, 4_000L)
            val candidates = sightings.filter { label.deviceId == null || it.deviceId == label.deviceId }.map { it.baseUrl } +
                listOfNotNull(AtlasDiscovery.gatewayUrl(ctx))
            for (url in candidates.distinct()) {
                val info = AtlasClient.probe(url, 3_000L) ?: continue
                if (label.pin != null && info.observedPin != label.pin) continue
                if (label.deviceId != null && info.deviceId != label.deviceId) continue
                unsupported(info)?.let { _pairingError.value = it; return@launch }
                _target.value = PairingTarget(info, false)
                if (label.setupCode != null) pairWithCode(label.setupCode) else pairViaHotspot()
                return@launch
            }
            if (label.ssid != null) {
                // Not on the same network: join Atlas's own Wi-Fi first.
                _joinWifi.value = label
                waitForAtlasWifi(label)
            } else {
                _pairingError.value = "Couldn't find that Atlas. Make sure it is switched on and nearby."
            }
        }
    }

    private suspend fun waitForAtlasWifi(label: AtlasLabel) {
        val ctx = getApplication<Application>()
        repeat(120) {
            delay(2_000L)
            if (_joinWifi.value == null) return
            val gw = AtlasDiscovery.gatewayUrl(ctx) ?: return@repeat
            val info = AtlasClient.probe(gw, 2_000L) ?: return@repeat
            if (label.pin != null && info.observedPin != label.pin) return@repeat
            _joinWifi.value = null
            unsupported(info)?.let { _pairingError.value = it; return }
            _target.value = PairingTarget(info, true)
            if (label.setupCode != null) pairWithCode(label.setupCode) else pairViaHotspot()
            return
        }
    }

    private fun pair(block: suspend (PairingTarget) -> String) {
        val t = _target.value ?: return
        _pairingError.value = null
        viewModelScope.launch {
            try {
                finishPairing(t, block(t))
            } catch (e: PairingException) {
                _pairingError.value = e.message
            } catch (e: Exception) {
                _pairingError.value = "Couldn't connect to Atlas. ${e.message ?: ""}".trim()
            }
        }
    }

    private fun finishPairing(t: PairingTarget, token: String) {
        val dev = PairedDevice(t.info.deviceId, t.info.name, t.info.observedPin, token, t.info.baseUrl)
        store.save(dev)
        device = dev
        cancelPairing()
        connected(t.info.baseUrl)
    }

    /** Forget this Atlas (security warning → pair again, or owner's choice). */
    fun forgetDevice() {
        monitorJob?.cancel(); locateJob?.cancel()
        clearPairing()
        _screen.value = Screen.Onboarding
        searchNearby()
    }

    /** Drop the token everywhere this app kept it: the Keystore-sealed store and the web view's cookie. */
    private fun clearPairing() {
        store.forget()
        device = null
        CookieManager.getInstance().removeAllCookies(null)
    }

    /** Why this app can't pair with that Atlas, or null if it can. */
    private fun unsupported(info: AtlasInfo): String? =
        if (info.pairingVersion > AtlasClient.PAIRING_VERSION)
            "This Atlas uses a newer way to connect. Update the Atlas app, then try again."
        else null

    private fun phoneName(): String =
        (Build.MANUFACTURER.replaceFirstChar { it.uppercase() } + " " + Build.MODEL).take(60)

    // ── notifications (messages, UPS battery) ────────────────────────────
    private fun pollNotifications(url: String, dev: PairedDevice) {
        val client = AtlasTrust.pinnedClient(dev.pin, dev.token, 8)
        val deviceInfo = client.newCall(Request.Builder().url("$url/api/device").build()).execute().use {
            if (!it.isSuccessful) return
            JSONObject(it.body?.string() ?: "{}")
        }
        val messages = client.newCall(Request.Builder().url("$url/api/messages").build()).execute().use {
            if (!it.isSuccessful) return
            JSONArray(it.body?.string() ?: "[]")
        }
        val ctx = getApplication<Application>()
        if (messages.length() > 0) {
            val latest = messages.getJSONObject(0)
            val key = latest.optString("packet_id").ifBlank { "${latest.optLong("rx_time")}:${latest.optString("from_id")}" }
            val stored = prefs.getString("last_message_key", null)
            prefs.edit().putString("last_message_key", key).apply()
            val from = latest.optString("from_id")
            if (stored != null && stored != key && from != deviceInfo.optString("my_node_id")) {
                val title = if (latest.optInt("is_direct") != 0) "New direct message" else "New Atlas message"
                val text = latest.optString("text").trim()
                AtlasNotificationManager.showMessageNotification(ctx, title,
                    if (text.isBlank()) "Message received from ${from.ifBlank { "Unknown sender" }}." else "$from: $text")
            }
        }
        if (deviceInfo.has("battery_pct") && !deviceInfo.isNull("battery_pct")) {
            val pct = deviceInfo.optInt("battery_pct")
            val charging = deviceInfo.optString("battery_phase").equals("charging", true) ||
                deviceInfo.optString("battery_status").equals("charging", true)
            val lowSent = prefs.getBoolean("battery_low_sent", false)
            if (pct <= 20 && !lowSent) AtlasNotificationManager.showBatteryLowNotification(ctx, pct)
            prefs.edit().putBoolean("battery_low_sent", pct <= 20).apply()
            val highSent = prefs.getBoolean("battery_high_sent", false)
            if (charging && pct >= 90 && !highSent) AtlasNotificationManager.showBatteryChargedNotification(ctx, pct)
            prefs.edit().putBoolean("battery_high_sent", charging && pct >= 90).apply()
        }
    }

    companion object {
        const val RESTARTING_FOR_UPDATE = "Atlas is installing an update and restarting."
        const val OLDER_ATLAS = "This Atlas is running older software. Update Atlas first " +
            "(on the Atlas itself: Settings → Software Update), then connect this phone."
        private const val APPROVAL_WAIT_MS = 180_000L
        private val UPDATE_STAGES = setOf("preparing", "installing", "restarting", "validating", "rolling_back", "verifying")
    }
}

package com.atlascontrol.mobile.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.net.Inet4Address
import java.util.concurrent.atomic.AtomicReference
import kotlin.coroutines.resume

/**
 * LAN discovery for Atlas.
 *
 * The reliable strategy is **a fingerprint-validated port-5000 HTTP sweep**.
 * Single-radio Atlas (Jetson Orin Nano) tends to drop or delay its avahi
 * announcement when joining a brand-new LAN, so mDNS is too fragile to be
 * the primary path. Instead, [findAtlasByPortSweep] enumerates every host
 * across the device's own /24 plus a curated list of common home / lab /
 * Docker / VPN prefixes and probes each one's `/api/device` endpoint. A
 * candidate only counts as "Atlas" when the response carries the
 * `app == "atlas-control"` fingerprint (or, for older firmware, looks
 * structurally like Atlas's `DeviceInfo`).
 *
 * Why port 5000 only? Atlas's Flask listens on `0.0.0.0:5000`. The previous
 * sweep also probed `https://prefix.host` (port 443), which:
 *   - rarely succeeds (nginx isn't always reachable on a brand-new LAN
 *     before it re-binds),
 *   - doubles the candidate count, and
 *   - lets random TLS-on-443 services false-positive when paired with a
 *     weak fingerprint check.
 *
 * mDNS / NSD remains as an opportunistic supplementary path
 * ([findAtlasOnLan]) but should never be the loop's only chance.
 *
 * **Emulator note:** Android Studio AVD / Panda emulators NAT through the
 * host. The emulator's "self IP" (10.0.2.16) is irrelevant for finding
 * Atlas — only the host's LAN matters. We always scan [EXTENDED_PREFIXES]
 * to cover that case regardless of what `getifaddrs` reports.
 *
 * All probes share a single [ApiClient.probeClient] OkHttpClient so a
 * 5 000-host sweep doesn't churn through OkHttpClient instances and
 * starve the GC (the original cause of false 900 ms timeouts on Panda).
 */
object NsdHelper {

    // Sweep the full /24 worth of hosts in roughly two batches at 700 ms
    // each. On emulators most probes return ECONNREFUSED in <5 ms, so the
    // effective time is dominated by the few live hosts we hit.
    private const val SWEEP_CONCURRENCY = 256
    private const val SWEEP_TIMEOUT_MS = 700L

    // Atlas's Flask app port. nginx / TLS on 443 is intentionally NOT swept
    // — see file-level docs.
    private const val ATLAS_PORT = 5000

    /**
     * Curated list of /24 prefixes the sweep always covers in addition to
     * the device's own /24. Built from the de-facto defaults shipped by
     * common home routers, mesh systems, and developer tooling so the
     * mobile app finds Atlas no matter what the user's router happens to
     * use as its DHCP pool.
     */
    private val EXTENDED_PREFIXES = listOf(
        "192.168.1",
        "192.168.0",
        "192.168.2",
        "192.168.3",
        "192.168.4",
        "192.168.5",
        "192.168.10",
        "192.168.11",
        "192.168.20",
        "192.168.50",
        "192.168.68",
        "192.168.86",
        "192.168.88",
        "192.168.100",
        "192.168.123",
        "192.168.168",
        "10.0.0",
        "10.0.1",
        "10.1.0",
        "10.1.1",
        "10.10.0",
        "10.10.1",
        "10.20.0",
        "10.42.0",
        "172.16.0",
        "172.16.1",
        "172.17.0",
        "172.20.0",
    )

    /**
     * Subnets Atlas's hotspot can occupy. When a caller passes
     * `excludeHotspot=true` the sweep skips these so a still-alive
     * 10.42.0.1 hotspot doesn't beat the real LAN address during a
     * pending switch.
     */
    private val HOTSPOT_PREFIXES = listOf("10.42.0", "192.168.4", "192.168.43")

    private fun isHotspotPrefix(prefix: String): Boolean = prefix in HOTSPOT_PREFIXES

    private fun isHotspotIp(ip: String): Boolean {
        val dotted = ip.split(".")
        if (dotted.size < 3) return false
        return dotted.take(3).joinToString(".") in HOTSPOT_PREFIXES
    }

    private fun formatHostForUrl(host: String): String =
        if (host.contains(":") && !host.startsWith("[")) "[$host]" else host

    // ── Public API ────────────────────────────────────────────────────────────

    fun getGatewayUrl(context: Context): String? = try {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val lp = cm?.activeNetwork?.let { cm.getLinkProperties(it) } ?: return null
        val gw = lp.routes
            .mapNotNull { it.gateway }
            .filterIsInstance<Inet4Address>()
            .firstOrNull { !it.isLoopbackAddress }
            ?.hostAddress ?: return null
        if (gw.startsWith("0.") || gw == "255.255.255.255") null
        else "https://${formatHostForUrl(gw)}"
    } catch (_: Exception) { null }

    /**
     * **Primary** LAN discovery path: an HTTP-only port-5000 sweep across
     * every candidate /24, fingerprint-validated against Atlas's
     * `/api/device` response.
     *
     * Designed to fit comfortably inside one ~10-second iteration of the
     * LAN-transition loop so the loop can retry many times within its
     * 120 s deadline — important when Atlas hasn't finished joining the
     * new LAN yet on the first iteration.
     *
     * @param excludeHotspot when true, skips Atlas's hotspot prefixes
     *   (10.42.0 / 192.168.4 / 192.168.43). Required during a pending
     *   hotspot→LAN switch so the dying hotspot can't masquerade as the
     *   winner.
     */
    suspend fun findAtlasByPortSweep(
        context: Context,
        excludeHotspot: Boolean = false,
    ): String? = coroutineScope {
        val candidates = buildPortSweepCandidates(context, excludeHotspot)
        if (candidates.isEmpty()) return@coroutineScope null
        sweepAtlasFingerprint(candidates)
    }

    /**
     * Targeted /24 port-5000 sweep starting at [hintIp] (probes the hint
     * host first). Used when the backend handed us a hint address (the
     * IP Atlas predicts it'll bind to on the new LAN) so the very first
     * iteration can find Atlas in a few hundred milliseconds without
     * sweeping every prefix.
     */
    suspend fun findAtlasByTargetSubnet(
        hintIp: String,
        excludeHotspot: Boolean = false,
    ): String? = coroutineScope {
        val parts = hintIp.split(".")
        if (parts.size != 4) return@coroutineScope null
        val prefix = parts.take(3).joinToString(".")
        if (excludeHotspot && isHotspotPrefix(prefix)) return@coroutineScope null
        val hintHost = parts.last().toIntOrNull() ?: return@coroutineScope null
        val hosts = buildList {
            add(hintHost)
            addAll((1..254).filter { it != hintHost })
        }
        val candidates = hosts.map { h -> "http://$prefix.$h:$ATLAS_PORT" }
        sweepAtlasFingerprint(candidates)
    }

    /**
     * Backwards-compatible alias kept for callers that haven't migrated to
     * the new name. Identical to [findAtlasByPortSweep].
     */
    suspend fun findAtlasBySubnetScan(
        context: Context,
        excludeHotspot: Boolean = false,
    ): String? = findAtlasByPortSweep(context, excludeHotspot)

    /**
     * mDNS / DNS-SD discovery via Android's [NsdManager].
     *
     * **Supplementary** path only — Atlas's avahi announcements on a
     * single-radio Jetson are unreliable enough on a brand-new LAN that
     * the loop can't depend on this. Callers should pass a tight timeout
     * (~5 s) and run it in parallel with [findAtlasByPortSweep].
     *
     * A [WifiManager.MulticastLock] is held for the duration of the browse
     * so the AVD/Panda emulator (and stock Android) doesn't filter
     * incoming mDNS replies on 224.0.0.251:5353.
     */
    suspend fun findAtlasOnLan(context: Context, timeoutMs: Long = 5_000L): String? {
        val nsdManager = context.getSystemService(Context.NSD_SERVICE) as? NsdManager
            ?: return null

        val wm = context.applicationContext
            .getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val mLock = wm?.createMulticastLock("AtlasNsdHelper")?.apply {
            setReferenceCounted(false)
            runCatching { acquire() }
        }

        return try {
            withTimeoutOrNull(timeoutMs) {
                val winner = AtomicReference<String?>(null)
                try {
                    coroutineScope {
                        val scope = this
                        launch(Dispatchers.IO) {
                            discoverService(nsdManager, "_https._tcp.")?.let {
                                if (winner.compareAndSet(null, it)) scope.cancel()
                            }
                        }
                        launch(Dispatchers.IO) {
                            discoverService(nsdManager, "_http._tcp.")?.let {
                                if (winner.compareAndSet(null, it)) scope.cancel()
                            }
                        }
                    }
                } catch (e: kotlinx.coroutines.CancellationException) {
                    if (winner.get() == null) throw e
                }
                winner.get()
            }
        } finally {
            runCatching { mLock?.release() }
        }
    }

    /** Sequential fallback used by setup wizard's first-run search. */
    suspend fun findAtlas(context: Context): String? {
        getGatewayUrl(context)?.let { gateway ->
            val ok = withTimeoutOrNull(1_500L) {
                withContext(Dispatchers.IO) {
                    AtlasRepository(ApiClient.createForProbe(gateway)).getDevice()
                        .map(::looksLikeAtlas).getOrElse { false }
                }
            } ?: false
            if (ok) return gateway
        }
        findAtlasByPortSweep(context)?.let { return it }
        return findAtlasOnLan(context, timeoutMs = 5_000L)
    }

    // ── Fingerprint-validated sweep core ──────────────────────────────────────

    /**
     * True if [info] looks like a response from Atlas Control.
     *
     * Strong signal: the explicit `app == "atlas-control"` field stamped
     * by the backend in [app.py][/api/device].
     *
     * Fallback for older Atlas firmware that doesn't set the field: the
     * response decoded into a [DeviceInfo] without exception AND at least
     * one Atlas-shaped field is populated (mesh node id, owner name, or
     * hardware string). Random IoT / printer / router endpoints that
     * happen to 200 on /api/device almost never satisfy this.
     */
    private fun looksLikeAtlas(info: DeviceInfo): Boolean {
        if (info.app == "atlas-control") return true
        return info.myNodeId?.isNotBlank() == true ||
                info.name.isNotBlank() ||
                info.hardware.isNotBlank()
    }

    /**
     * Probes [candidates] in parallel using the shared probe client and
     * returns the first URL whose `/api/device` answer satisfies
     * [looksLikeAtlas]. All other in-flight probes are cancelled when a
     * winner is found.
     *
     * Probes that fail to decode as [DeviceInfo] are rejected — that's
     * what makes this safe to run against thousands of unknown hosts.
     */
    private suspend fun sweepAtlasFingerprint(candidates: List<String>): String? = coroutineScope {
        val found = AtomicReference<String?>(null)
        val limit = Semaphore(SWEEP_CONCURRENCY)

        candidates.map { url ->
            async(Dispatchers.IO) {
                if (found.get() != null) return@async
                limit.withPermit {
                    if (found.get() != null) return@withPermit
                    val ok = withTimeoutOrNull(SWEEP_TIMEOUT_MS) {
                        runCatching {
                            val info = AtlasRepository(ApiClient.createForProbe(url)).getDevice()
                                .getOrNull() ?: return@runCatching false
                            looksLikeAtlas(info)
                        }.getOrElse { false }
                    } ?: false
                    if (ok) found.compareAndSet(null, url)
                }
            }
        }.awaitAll()

        found.get()
    }

    // ── Sweep candidate-list builder ──────────────────────────────────────────

    /**
     * Builds the list of `http://prefix.host:5000` URLs the port-5000
     * sweep will probe. Always non-empty — even when the device has no
     * active IPv4 network (the brief window between a hotspot dropping
     * and a new LAN associating) we still scan [EXTENDED_PREFIXES] so
     * the next iteration of the discovery loop finds Atlas without
     * waiting for a network-available callback.
     *
     * Interleaving order: gateway-host first (Atlas is usually the
     * gateway when in hotspot mode and harmless otherwise), then host
     * .1 across every prefix, then .2 across every prefix, and so on.
     * A typical home LAN has Atlas at .50 or .100 — interleaving keeps
     * the time-to-find proportional to the host offset, not to the
     * number of prefixes searched.
     */
    private fun buildPortSweepCandidates(
        context: Context,
        excludeHotspot: Boolean,
    ): List<String> {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

        var selfPrefix: String? = null
        var selfHost: Int? = null
        var gatewayHost: Int? = null

        if (cm != null) {
            val lp = cm.activeNetwork?.let { cm.getLinkProperties(it) }
            val linkAddr = lp?.linkAddresses
                ?.firstOrNull { it.address is Inet4Address && !it.address.isLoopbackAddress }
            if (linkAddr != null) {
                val selfIp = linkAddr.address.hostAddress
                val selfParts = selfIp?.split(".")
                if (selfParts?.size == 4) {
                    selfPrefix = selfParts.take(3).joinToString(".")
                    selfHost = selfParts.last().toIntOrNull()
                    if (selfPrefix != null && selfHost != null) {
                        gatewayHost = lp?.routes
                            ?.mapNotNull { it.gateway }
                            ?.filterIsInstance<Inet4Address>()
                            ?.firstOrNull { !it.isLoopbackAddress }
                            ?.hostAddress
                            ?.split(".")
                            ?.takeIf { it.size == 4 && it.take(3).joinToString(".") == selfPrefix }
                            ?.last()?.toIntOrNull()
                    }
                }
            }
        }

        val effectiveSelfPrefix =
            if (excludeHotspot && selfPrefix != null && isHotspotPrefix(selfPrefix!!)) null
            else selfPrefix
        val effectiveGatewayHost = if (effectiveSelfPrefix == null) null else gatewayHost

        val prefixes = buildList {
            effectiveSelfPrefix?.let { add(it) }
            for (p in EXTENDED_PREFIXES) {
                if (excludeHotspot && isHotspotPrefix(p)) continue
                add(p)
            }
        }.distinct()

        return buildList {
            if (effectiveGatewayHost != null && effectiveGatewayHost in 1..254 && effectiveGatewayHost != selfHost) {
                for (prefix in prefixes) {
                    add("http://$prefix.$effectiveGatewayHost:$ATLAS_PORT")
                }
            }
            for (host in 1..254) {
                if (host == selfHost || host == effectiveGatewayHost) continue
                for (prefix in prefixes) {
                    add("http://$prefix.$host:$ATLAS_PORT")
                }
            }
        }
    }

    // ── NSD service discovery (supplementary) ────────────────────────────────

    private suspend fun discoverService(nsdManager: NsdManager, type: String): String? =
        withTimeoutOrNull(5_000L) {
            suspendCancellableCoroutine { cont ->
                var discoveryListener: NsdManager.DiscoveryListener? = null

                val resolveListener = object : NsdManager.ResolveListener {
                    override fun onResolveFailed(si: NsdServiceInfo, error: Int) {
                        if (cont.isActive) cont.resume(null)
                    }
                    override fun onServiceResolved(si: NsdServiceInfo) {
                        val ip = si.host?.hostAddress
                        if (ip == null) { if (cont.isActive) cont.resume(null); return }
                        val scheme = if (type.startsWith("_https")) "https" else "http"
                        val portSuffix = if (si.port == 443 || si.port == 80) "" else ":${si.port}"
                        if (cont.isActive) cont.resume("$scheme://${formatHostForUrl(ip)}$portSuffix")
                    }
                }

                discoveryListener = object : NsdManager.DiscoveryListener {
                    override fun onDiscoveryStarted(t: String) {}
                    override fun onDiscoveryStopped(t: String) {}
                    override fun onStopDiscoveryFailed(t: String, e: Int) {}
                    override fun onStartDiscoveryFailed(t: String, e: Int) {
                        if (cont.isActive) cont.resume(null)
                    }
                    override fun onServiceLost(si: NsdServiceInfo) {}
                    override fun onServiceFound(si: NsdServiceInfo) {
                        if (si.serviceName.lowercase().contains("atlas")) {
                            runCatching { nsdManager.stopServiceDiscovery(this) }
                            nsdManager.resolveService(si, resolveListener)
                        }
                    }
                }

                cont.invokeOnCancellation {
                    runCatching { nsdManager.stopServiceDiscovery(discoveryListener!!) }
                }

                runCatching {
                    nsdManager.discoverServices(
                        type, NsdManager.PROTOCOL_DNS_SD, discoveryListener!!
                    )
                }.onFailure { if (cont.isActive) cont.resume(null) }
            }
        }
}

package com.atlascontrol.mobile.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * Finding Atlas without anyone typing an address.
 *
 *  1. mDNS / DNS-SD: Atlas advertises `_atlas._tcp` with its device id and TLS
 *     key pin in TXT records.
 *  2. The Atlas LAN beacon (UDP 5050), which also covers the seconds after
 *     Atlas switches networks, before mDNS settles.
 *  3. Atlas's own hotspot gateway, when the phone is on it.
 *  4. Last resort, and only for a paired device or on request: HTTPS probes
 *     of the phone's own /24 for `/api/device/info`.
 *
 * A paired Atlas is recognised by device id and confirmed with its pinned
 * key, so an IP change (DHCP, a new router, hotspot ↔ Wi-Fi) is just a new
 * address for the same trusted device.
 */
object AtlasDiscovery {
    private const val SERVICE_TYPE = "_atlas._tcp."
    private val resolveMutex = Mutex()

    suspend fun discover(context: Context, timeoutMs: Long = 5_000L): List<Sighting> = coroutineScope {
        val nsd = async { browseNsd(context, timeoutMs) }
        val beacon = async { AtlasBeacon.listen(context, timeoutMs) }
        (nsd.await() + beacon.await()).distinctBy { it.deviceId }
    }

    /** Where is the paired Atlas right now? Returns a confirmed base URL. */
    suspend fun locate(context: Context, device: PairedDevice, thorough: Boolean = false): String? {
        device.lastUrl?.let { if (AtlasClient.confirm(it, device)) return it }
        for (s in discover(context, 5_000L).filter { it.deviceId == device.deviceId }) {
            if (AtlasClient.confirm(s.baseUrl, device)) return s.baseUrl
        }
        gatewayUrl(context)?.let { if (AtlasClient.confirm(it, device)) return it }
        if (thorough) {
            for (info in scanSubnet(context).filter { it.deviceId == device.deviceId }) {
                if (AtlasClient.confirm(info.baseUrl, device)) return info.baseUrl
            }
        }
        return null
    }

    /** The phone's DHCP gateway as an https URL (Atlas, when on its hotspot). */
    fun gatewayUrl(context: Context): String? {
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        @Suppress("DEPRECATION")
        val gw = wm?.dhcpInfo?.gateway ?: 0
        if (gw == 0) return null
        return "https://${gw and 0xff}.${gw shr 8 and 0xff}.${gw shr 16 and 0xff}.${gw shr 24 and 0xff}"
    }

    /** HTTPS probe of the phone's own /24 (user-initiated "Scan network"). */
    suspend fun scanSubnet(context: Context): List<AtlasInfo> = withContext(Dispatchers.IO) {
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        @Suppress("DEPRECATION")
        val ip = wm?.dhcpInfo?.ipAddress ?: 0
        if (ip == 0) return@withContext emptyList()
        val prefix = "${ip and 0xff}.${ip shr 8 and 0xff}.${ip shr 16 and 0xff}"
        val gate = Semaphore(32)
        (1..254).map { host ->
            async { gate.withPermit { AtlasClient.probe("https://$prefix.$host", 900) } }
        }.awaitAll().filterNotNull()
    }

    // ── DNS-SD ───────────────────────────────────────────────────────────
    private suspend fun browseNsd(context: Context, timeoutMs: Long): List<Sighting> {
        val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as? NsdManager
            ?: return emptyList()
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val lock = wm?.createMulticastLock("AtlasNsd")?.apply { setReferenceCounted(false); runCatching { acquire() } }
        val services = java.util.Collections.synchronizedList(mutableListOf<NsdServiceInfo>())
        val listener = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) { services += info }
            override fun onServiceLost(info: NsdServiceInfo) {}
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        }
        try {
            runCatching { nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener) }
            delay(timeoutMs * 2 / 3)
        } finally {
            runCatching { nsd.stopServiceDiscovery(listener) }
        }
        val out = mutableListOf<Sighting>()
        for (info in services.toList().distinctBy { it.serviceName }) {
            val resolved = resolve(nsd, info) ?: continue
            val host = resolved.host?.hostAddress ?: continue
            val attrs = resolved.attributes ?: emptyMap()
            val id = attrs["id"]?.toString(Charsets.UTF_8) ?: continue
            val fp = attrs["fp"]?.toString(Charsets.UTF_8)
            val url = if (host.contains(':')) "https://[$host]" else "https://$host"
            out += Sighting(id, resolved.serviceName ?: "Atlas", fp, url, "mdns")
        }
        runCatching { lock?.release() }
        return out
    }

    @Suppress("DEPRECATION")
    private suspend fun resolve(nsd: NsdManager, info: NsdServiceInfo): NsdServiceInfo? =
        resolveMutex.withLock {           // NsdManager resolves one service at a time
            withTimeoutOrNull(3_000L) {
                suspendCancellableCoroutine { cont ->
                    nsd.resolveService(info, object : NsdManager.ResolveListener {
                        override fun onServiceResolved(s: NsdServiceInfo) { if (cont.isActive) cont.resume(s) }
                        override fun onResolveFailed(s: NsdServiceInfo, errorCode: Int) { if (cont.isActive) cont.resume(null) }
                    })
                }
            }
        }
}

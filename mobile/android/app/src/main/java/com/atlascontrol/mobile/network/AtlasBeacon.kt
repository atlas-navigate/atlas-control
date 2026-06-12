package com.atlascontrol.mobile.network

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import kotlin.random.Random

/**
 * UDP "shout-and-receive" discovery client.
 *
 * Pairs with [lan_beacon.py] on the Atlas backend. On every call, the client:
 *
 *   1. Opens a single UDP socket bound to ``0.0.0.0:0`` with broadcast enabled.
 *   2. **Shouts** ``ATLAS-DISCOVER\n{"nonce":"<hex>"}`` to:
 *        - the global broadcast (255.255.255.255:5050)
 *        - every per-prefix gateway-IP (``<prefix>.1:5050``) in [BroadcastTargets]
 *        - every host on the curated /24 prefixes (``<prefix>.<host>:5050``)
 *      The unicast fan-out is what makes this work on the Android AVD: the
 *      emulator NAT cannot route incoming broadcasts from the host LAN, but
 *      it does forward outbound unicast UDP and routes the reply back through
 *      the same NAT session.
 *   3. Listens for replies on the same socket. The first packet whose JSON
 *      body satisfies the Atlas fingerprint (``app == "atlas-control"``)
 *      wins; its ``accessUrls`` are returned.
 *   4. Holds a [WifiManager.MulticastLock] for the duration so the
 *      AVD / stock Android Wi-Fi multicast filter doesn't drop returning
 *      packets in the rare case Atlas replies via multicast.
 *
 * The returned URL is a *candidate* — callers re-verify it via
 * [AtlasRepository.getDevice] (TCP /api/device fingerprint) before applying
 * it to the wizard's discovery state.
 *
 * Backwards-compat: on Atlas firmware older than this change the beacon
 * doesn't exist; the mobile side simply times out and falls through to the
 * existing port-5000 sweep.
 */
object AtlasBeacon {

    private const val TAG = "AtlasBeacon"
    private const val BEACON_PORT = 5050
    private const val PROBE_TOKEN = "ATLAS-DISCOVER"
    /** Resend probe bursts every [BURST_INTERVAL_MS] until the deadline.
     *  Without this, a single first-burst miss (Atlas not yet up on the new
     *  LAN, or AVD-side packet loss during a Wi-Fi-state transition) means
     *  the entire ``discover()`` call returns nil even if Atlas comes up
     *  mid-window. With it, AVD is guaranteed multiple shots and real
     *  devices benefit too. */
    private const val BURST_INTERVAL_MS = 3_000L

    /** Shared with [NsdHelper] — mirrors that file's curated /24 list so the
     *  beacon and port sweep cover the same address space. */
    private val EXTENDED_PREFIXES = listOf(
        "192.168.1",  "192.168.0",  "192.168.2",  "192.168.3",  "192.168.5",
        "192.168.4",  "192.168.10", "192.168.11", "192.168.20", "192.168.50",
        "192.168.68", "192.168.86", "192.168.88", "192.168.100","192.168.123",
        "192.168.168","10.0.0",     "10.0.1",     "10.1.0",     "10.1.1",
        "10.10.0",    "10.10.1",    "10.20.0",    "10.42.0",    "172.16.0",
        "172.16.1",   "172.17.0",   "172.20.0",
    )

    private val HOTSPOT_PREFIXES = setOf("10.42.0", "192.168.4", "192.168.43")

    /**
     * Run a discovery cycle. Returns the access URL of the first beacon
     * reply that looks like Atlas, or null on timeout.
     *
     * @param timeoutMs how long to listen for replies, in milliseconds.
     *   Typical values: 3 000 ms inside the LAN-transition loop (which
     *   re-runs us), 8 000 ms for a one-shot first-launch search.
     * @param excludeHotspot if true, skips probes targeting Atlas's hotspot
     *   prefixes — required during a pending hotspot→LAN switch so the
     *   dying hotspot doesn't beat the real LAN address.
     */
    suspend fun discover(
        context: Context,
        timeoutMs: Long = 4_000L,
        excludeHotspot: Boolean = false,
    ): String? = withContext(Dispatchers.IO) {
        val wm = context.applicationContext
            .getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val mLock = wm?.createMulticastLock("AtlasBeacon")?.apply {
            setReferenceCounted(false)
            runCatching { acquire() }
        }

        val socket = runCatching {
            DatagramSocket().apply {
                broadcast = true
                soTimeout = 250  // tight loop so we react quickly to the deadline
            }
        }.getOrElse {
            runCatching { mLock?.release() }
            return@withContext null
        }

        try {
            val nonce = Random.nextLong().toString(16).padStart(16, '0').takeLast(16)
            val probe = "$PROBE_TOKEN\n{\"nonce\":\"$nonce\"}".toByteArray()
            val targets = buildProbeTargets(excludeHotspot = excludeHotspot)

            val winner = CompletableDeferred<String?>()
            val deadline = System.currentTimeMillis() + timeoutMs

            coroutineScope {
                // Sender — fire repeated probe bursts every BURST_INTERVAL_MS
                // until the deadline. Each burst sweeps every target; the
                // unicast fan-out is what makes this work on the Android AVD
                // (which can't receive broadcast packets from the host LAN
                // through QEMU NAT, but DOES forward outbound unicast UDP
                // and routes the reply back through the same NAT session).
                launch(Dispatchers.IO) {
                    var burstNum = 0
                    while (isActive && !winner.isCompleted &&
                           System.currentTimeMillis() < deadline) {
                        burstNum++
                        for (target in targets) {
                            if (!isActive || winner.isCompleted) return@launch
                            try {
                                val addr = InetAddress.getByName(target)
                                socket.send(DatagramPacket(probe, probe.size, addr, BEACON_PORT))
                            } catch (_: Exception) {
                                // Some target literals (e.g. "255.255.255.255"
                                // on a no-route interface) raise EHOSTUNREACH.
                                // Ignore and continue — other targets still
                                // cover Atlas.
                            }
                        }
                        Log.d(TAG, "burst $burstNum: ${targets.size} probes sent")
                        // Sleep BURST_INTERVAL_MS, but in small slices so we
                        // react to a winner promptly.
                        val nextBurstAt = System.currentTimeMillis() + BURST_INTERVAL_MS
                        while (isActive && !winner.isCompleted &&
                               System.currentTimeMillis() < nextBurstAt &&
                               System.currentTimeMillis() < deadline) {
                            delay(150L)
                        }
                    }
                }

                // Receiver — drain the socket until timeout or a winner is
                // found. Validates each packet's JSON before claiming. Runs
                // continuously across all probe bursts so a heartbeat shout
                // from Atlas (1 Hz on real devices) is caught even between
                // our own bursts.
                launch(Dispatchers.IO) {
                    val buf = ByteArray(2048)
                    while (isActive && !winner.isCompleted &&
                           System.currentTimeMillis() < deadline) {
                        try {
                            val pkt = DatagramPacket(buf, buf.size)
                            socket.receive(pkt)
                            val url = parseBeaconReply(pkt, nonce)
                            if (url != null && winner.complete(url)) {
                                Log.i(TAG, "beacon reply from $url")
                                socket.close()  // unblock sender
                                return@launch
                            }
                        } catch (_: SocketTimeoutException) {
                            // expected — keeps the loop responsive
                        } catch (_: Exception) {
                            return@launch
                        }
                    }
                    winner.complete(null)
                }
            }

            withTimeoutOrNull(timeoutMs + 1_000L) { winner.await() }
        } finally {
            runCatching { socket.close() }
            runCatching { mLock?.release() }
        }
    }

    /**
     * Parse a beacon reply packet. Returns the highest-quality access URL,
     * or null if the packet doesn't look like an Atlas beacon.
     *
     * The beacon advertises ``accessUrls`` (URLs Atlas thinks the apps
     * should connect to) but those may include hotspot URLs and may be
     * stale on a freshly bound interface. As a safety net we synthesize
     * ``http://<sourceIp>:5000`` from the UDP packet's source address —
     * that is guaranteed to be the IP Atlas is actually answering on.
     */
    private fun parseBeaconReply(pkt: DatagramPacket, expectedNonce: String): String? {
        val body = String(pkt.data, 0, pkt.length, Charsets.UTF_8).trim()
        val obj = runCatching { JSONObject(body) }.getOrNull() ?: return null
        if (obj.optString("app") != "atlas-control") return null

        // If the reply echoes a nonce, only trust matching ones — protects
        // against stale replies arriving in the next probe cycle.
        val replyNonce = obj.optString("nonce", "")
        if (replyNonce.isNotEmpty() && replyNonce != expectedNonce) return null

        val sourceIp = pkt.address?.hostAddress
        if (!sourceIp.isNullOrBlank() &&
            !sourceIp.startsWith("0.") &&
            sourceIp != "255.255.255.255") {
            return "http://$sourceIp:5000"
        }
        // Fallback: pick the first non-hotspot accessUrl.
        val urls = obj.optJSONArray("accessUrls") ?: return null
        for (i in 0 until urls.length()) {
            val u = urls.optString(i)
            if (u.isNotBlank() && !looksLikeHotspot(u)) return u
        }
        return null
    }

    private fun looksLikeHotspot(url: String): Boolean =
        HOTSPOT_PREFIXES.any { p -> url.contains("$p.") }

    /** Build the target IP list for the probe fan-out. */
    private fun buildProbeTargets(excludeHotspot: Boolean): List<String> {
        val targets = LinkedHashSet<String>()
        targets.add("255.255.255.255")  // global broadcast (real devices)

        // Per-prefix .1 (gateway) — cheap and almost always live on real LANs.
        for (prefix in EXTENDED_PREFIXES) {
            if (excludeHotspot && prefix in HOTSPOT_PREFIXES) continue
            targets.add("$prefix.1")
        }
        // Full /24 fan-out for AVD (no broadcast routing) and any router that
        // assigns Atlas a non-.1 host. UDP send is cheap; 28 prefixes × 254
        // hosts ≈ 7 112 packets, ~1.5 MB total wire bytes.
        for (host in 2..254) {
            for (prefix in EXTENDED_PREFIXES) {
                if (excludeHotspot && prefix in HOTSPOT_PREFIXES) continue
                targets.add("$prefix.$host")
            }
        }
        return targets.toList()
    }

    /**
     * Run [discover] in parallel with [NsdHelper.findAtlasByPortSweep] and
     * return whichever wins first. Convenience wrapper that the setup
     * wizard's runLanDiscoveryLoop uses as a single "find Atlas now" call.
     */
    suspend fun discoverFastest(
        context: Context,
        timeoutMs: Long = 8_000L,
        excludeHotspot: Boolean = false,
    ): String? = coroutineScope {
        val a = async { discover(context, timeoutMs = timeoutMs, excludeHotspot = excludeHotspot) }
        val b = async {
            withTimeoutOrNull(timeoutMs) {
                NsdHelper.findAtlasByPortSweep(context, excludeHotspot = excludeHotspot)
            }
        }
        listOf(a, b).awaitAll().firstOrNull { it != null }
    }
}

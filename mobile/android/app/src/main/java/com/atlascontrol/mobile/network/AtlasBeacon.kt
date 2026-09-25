package com.atlascontrol.mobile.network

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import kotlin.random.Random

/** An Atlas seen on the network, not yet verified. */
data class Sighting(
    val deviceId: String,
    val name: String,
    val pin: String?,
    val baseUrl: String,
    val via: String,
)

/**
 * UDP discovery against lan_beacon.py (port 5050).
 *
 * Asks with a broadcast (plus the phone's own subnet broadcast) and also
 * hears Atlas's unsolicited announcements, which arrive every second right
 * after Atlas changes networks. Replies (beacon v2) carry the device id and
 * TLS key pin; the reply's source address is where Atlas is. Nothing is
 * trusted from a sighting — the caller confirms with the pinned key.
 */
object AtlasBeacon {
    private const val PORT = 5050
    private const val PROBE = "ATLAS-DISCOVER"

    suspend fun listen(context: Context, timeoutMs: Long = 4_000L): List<Sighting> = withContext(Dispatchers.IO) {
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val lock = wm?.createMulticastLock("AtlasBeacon")?.apply { setReferenceCounted(false); runCatching { acquire() } }
        val found = linkedMapOf<String, Sighting>()
        val socket = runCatching {
            DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 250
                // Bind to 5050 when free so Atlas's broadcast announcements
                // reach us too; otherwise an ephemeral port still gets replies.
                runCatching { bind(InetSocketAddress(PORT)) }.onFailure { bind(InetSocketAddress(0)) }
            }
        }.getOrElse { runCatching { lock?.release() }; return@withContext emptyList() }
        try {
            val nonce = Random.nextLong().toString(16)
            val probe = "$PROBE\n{\"nonce\":\"$nonce\"}".toByteArray()
            val targets = broadcastTargets(wm)
            val deadline = System.currentTimeMillis() + timeoutMs
            var nextProbe = 0L
            val buf = ByteArray(4096)
            while (System.currentTimeMillis() < deadline) {
                if (System.currentTimeMillis() >= nextProbe) {
                    for (t in targets) runCatching { socket.send(DatagramPacket(probe, probe.size, t, PORT)) }
                    nextProbe = System.currentTimeMillis() + 1_500L
                }
                val pkt = DatagramPacket(buf, buf.size)
                try { socket.receive(pkt) } catch (_: SocketTimeoutException) { continue }
                val text = String(pkt.data, 0, pkt.length, Charsets.UTF_8)
                if (text.startsWith(PROBE)) continue                 // our own broadcast echoed back
                val o = runCatching { JSONObject(text) }.getOrNull() ?: continue
                if (o.optString("app") != "atlas-control" || o.optInt("v") < 2) continue
                val id = o.optString("id")
                if (id.isBlank()) continue
                val host = pkt.address?.hostAddress ?: continue
                val url = if (host.contains(':')) "https://[$host]" else "https://$host"
                found[id] = Sighting(id, o.optString("name", "Atlas"), o.optString("fp").ifBlank { null }, url, "beacon")
            }
        } finally {
            socket.close()
            runCatching { lock?.release() }
        }
        found.values.toList()
    }

    private fun broadcastTargets(wm: WifiManager?): List<InetAddress> {
        val out = mutableListOf(InetAddress.getByName("255.255.255.255"))
        @Suppress("DEPRECATION")
        val dhcp = wm?.dhcpInfo
        if (dhcp != null && dhcp.ipAddress != 0 && dhcp.netmask != 0) {
            val bcast = (dhcp.ipAddress and dhcp.netmask) or dhcp.netmask.inv()
            val b = ByteArray(4) { i -> (bcast shr (i * 8) and 0xff).toByte() }
            runCatching { out += InetAddress.getByAddress(b) }
        }
        return out
    }
}

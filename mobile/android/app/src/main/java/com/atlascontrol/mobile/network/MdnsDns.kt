package com.atlascontrol.mobile.network

import okhttp3.Dns
import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * OkHttp [Dns] implementation that resolves ".local" hostnames via a direct
 * mDNS query to 224.0.0.251:5353.  Android's system DNS resolver silently
 * drops ".local" queries, so this bypasses it entirely.
 *
 * The QU (unicast-response) bit is set so the responder replies directly
 * to our socket rather than to the multicast group — no MulticastSocket
 * or group membership required.
 *
 * For all other hostnames the call falls through to [Dns.SYSTEM].
 *
 * Results are cached for [CACHE_TTL] ms so that successive calls within the
 * same probe cycle (OkHttp connection + toWebReachableUrl) reuse the same IP
 * without firing a second multicast query that may race or be dropped.
 */
object MdnsDns : Dns {

    private const val MDNS_ADDR  = "224.0.0.251"
    private const val MDNS_PORT  = 5353
    private const val TIMEOUT_MS = 1_500
    private const val CACHE_TTL  = 60_000L   // reuse last known IP for 60 s

    @Volatile private var cachedHostname = ""
    @Volatile private var cachedIp       = ""
    @Volatile private var cacheExpiry    = 0L

    /** Clears the cache so the next lookup sends a fresh mDNS query. */
    fun clearCache() {
        cachedHostname = ""
        cachedIp       = ""
        cacheExpiry    = 0L
    }

    override fun lookup(hostname: String): List<InetAddress> {
        if (!hostname.endsWith(".local")) return Dns.SYSTEM.lookup(hostname)

        val now = System.currentTimeMillis()
        // Fast path: return cached result while still fresh.
        if (hostname == cachedHostname && now < cacheExpiry && cachedIp.isNotBlank()) {
            runCatching { return listOf(InetAddress.getByName(cachedIp)) }
        }
        // Direct mDNS UDP query.
        resolveMdns(hostname)?.let { ip ->
            runCatching {
                val addr = InetAddress.getByName(ip)
                cachedHostname = hostname
                cachedIp       = ip
                cacheExpiry    = now + CACHE_TTL
                return listOf(addr)
            }
        }
        // Fallback: Android system resolver (native mDNS on Android 12+).
        return runCatching { Dns.SYSTEM.lookup(hostname) }.getOrElse { emptyList() }
    }

    /**
     * Sends a DNS A-record query with the QU bit to the mDNS multicast group
     * and returns the first IPv4 address from the answer section, or null.
     */
    private fun resolveMdns(hostname: String): String? = try {
        val fqdn  = if (hostname.endsWith(".")) hostname else "$hostname."
        val query = buildQuery(fqdn)

        DatagramSocket().use { socket ->
            val dest = InetAddress.getByName(MDNS_ADDR)
            socket.send(DatagramPacket(query, query.size, dest, MDNS_PORT))

            val buf = ByteArray(4096)
            val deadline = System.currentTimeMillis() + TIMEOUT_MS
            while (System.currentTimeMillis() < deadline) {
                val remaining = (deadline - System.currentTimeMillis()).coerceAtLeast(50L).toInt()
                socket.soTimeout = remaining
                try {
                    val pkt = DatagramPacket(buf, buf.size)
                    socket.receive(pkt)
                    parseARecord(buf, pkt.length)?.let { return it }
                } catch (_: java.net.SocketTimeoutException) { break }
            }
            null
        }
    } catch (_: Exception) { null }

    // ─── DNS packet builder ───────────────────────────────────────────────────

    private fun buildQuery(fqdn: String): ByteArray {
        val out = ByteArrayOutputStream()
        // Header
        out.write(byteArrayOf(0x00, 0x00))                       // Transaction ID = 0 (mDNS)
        out.write(byteArrayOf(0x00, 0x00))                       // Flags: standard query
        out.write(byteArrayOf(0x00, 0x01))                       // QDCOUNT = 1
        out.write(byteArrayOf(0x00, 0x00, 0x00, 0x00, 0x00, 0x00)) // AN / NS / AR = 0
        // Question: encode each label
        for (label in fqdn.trimEnd('.').split('.')) {
            out.write(label.length)
            out.write(label.toByteArray(Charsets.US_ASCII))
        }
        out.write(0x00)                                          // root label
        out.write(byteArrayOf(0x00, 0x01))                       // QTYPE  = A
        out.write(byteArrayOf(0x80.toByte(), 0x01))              // QCLASS = IN | QU bit
        return out.toByteArray()
    }

    // ─── DNS response parser ──────────────────────────────────────────────────

    private fun parseARecord(data: ByteArray, length: Int): String? {
        if (length < 12) return null
        val anCount = ((data[6].toInt() and 0xff) shl 8) or (data[7].toInt() and 0xff)
        if (anCount == 0) return null

        // Skip past the question section
        var pos = 12
        pos = skipName(data, pos, length)
        pos += 4  // QTYPE + QCLASS

        // Walk answer records looking for the first A (type 1) record
        repeat(anCount) {
            if (pos >= length) return@repeat
            pos = skipName(data, pos, length)
            if (pos + 10 > length) return@repeat

            val type  = ((data[pos    ].toInt() and 0xff) shl 8) or (data[pos + 1].toInt() and 0xff)
            val rdLen = ((data[pos + 8].toInt() and 0xff) shl 8) or (data[pos + 9].toInt() and 0xff)
            pos += 10

            if (type == 1 && rdLen == 4 && pos + 4 <= length) {
                return "%d.%d.%d.%d".format(
                    data[pos    ].toInt() and 0xff,
                    data[pos + 1].toInt() and 0xff,
                    data[pos + 2].toInt() and 0xff,
                    data[pos + 3].toInt() and 0xff
                )
            }
            pos += rdLen
        }
        return null
    }

    /** Advances [start] past a DNS name field (handles both labels and pointer compression). */
    private fun skipName(data: ByteArray, start: Int, length: Int): Int {
        var pos = start
        while (pos < length) {
            val b = data[pos].toInt() and 0xff
            when {
                b == 0         -> return pos + 1       // null terminator
                b and 0xC0 == 0xC0 -> return pos + 2  // pointer — two bytes total
                else           -> pos += b + 1         // label: length byte + N chars
            }
        }
        return pos
    }
}

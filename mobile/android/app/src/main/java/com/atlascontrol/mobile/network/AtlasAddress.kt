package com.atlascontrol.mobile.network

/**
 * Atlas addresses as people type them and as Atlas advertises them.
 *
 * Every Atlas has its own .local name (atlas-7kq3pm.local). Atlas
 * advertises it in DNS-SD (TXT host=), in its LAN beacon and in
 * /api/device/info; the app shows it to the owner and uses it as one more
 * way to find a paired Atlas. The connection itself always goes to an IP
 * address (Android's WebView can't resolve .local names) and is trusted by
 * the pinned key, never by the name.
 */
object AtlasAddress {
    private val LABEL = Regex("[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
    private val IPV4 = Regex("""\d{1,3}(\.\d{1,3}){3}""")

    /** "atlas-7kq3pm" or "atlas-7kq3pm.local" (any case) → "atlas-7kq3pm.local"; null if not a name. */
    fun fqdn(raw: String?): String? {
        val h = raw?.trim()?.trimEnd('.')?.lowercase() ?: return null
        val bare = h.removeSuffix(".local")
        if (!LABEL.matches(bare)) return null
        return "$bare.local"
    }

    /** What someone typed under Advanced, normalised. */
    data class Typed(val url: String, val host: String, val isLocalName: Boolean)

    /**
     * Accepts "atlas-7kq3pm", "atlas-7kq3pm.local", "192.168.1.20",
     * "192.168.1.20:8443", "https://atlas-7kq3pm.local/mobile", "[fd00::5]".
     * Returns null for anything else.
     */
    fun parse(input: String): Typed? {
        val s = input.trim().removePrefix("https://").removePrefix("http://").substringBefore('/').trim()
        if (s.isEmpty() || s.any { it.isWhitespace() }) return null
        val ipv6 = s.startsWith("[")
        val rawHost = if (ipv6) s.substringAfter('[').substringBefore(']') else s.substringBefore(':')
        val port = if (ipv6) s.substringAfter("]", "").removePrefix(":") else s.substringAfter(':', "")
        if (port.isNotEmpty() && (port.toIntOrNull() ?: 0) !in 1..65535) return null
        val suffix = if (port.isEmpty() || port == "443") "" else ":$port"
        if (ipv6) {
            if (rawHost.isEmpty() || rawHost.any { !(it.isLetterOrDigit() || it == ':') }) return null
            return Typed("https://[$rawHost]$suffix", rawHost, false)
        }
        if (IPV4.matches(rawHost)) {
            if (rawHost.split('.').any { it.toInt() > 255 }) return null
            return Typed("https://$rawHost$suffix", rawHost, false)
        }
        val host = rawHost.lowercase().trimEnd('.')
        // A bare name ("atlas-7kq3pm") is an Atlas .local name; a dotted one
        // other than .local is an ordinary DNS name (a VPN or a router entry).
        val name = if ('.' !in host) fqdn(host) ?: return null
            else if (host.split('.').all { LABEL.matches(it) }) host else return null
        return Typed("https://$name$suffix", name, name.endsWith(".local"))
    }

    /** "https://192.168.1.20/mobile" → "192.168.1.20" (for showing an address). */
    fun hostOf(url: String?): String? {
        val s = url?.removePrefix("https://")?.removePrefix("http://")?.substringBefore('/') ?: return null
        return s.ifBlank { null }
    }
}

package com.atlascontrol.mobile.network

/**
 * What the QR code on an Atlas label contains:
 *
 *   ATLAS1:<device id>:<setup code>:<TLS key pin>:<hotspot SSID>:<hotspot password>
 *
 * The pin lets the app trust exactly this unit from the first connection; the
 * setup code pairs it; the hotspot credentials let the app join Atlas's own
 * Wi-Fi when the phone is not on the same network. A standard Wi-Fi QR
 * ("WIFI:T:WPA;S:…;P:…;;", also on the label) is understood too.
 */
data class AtlasLabel(
    val deviceId: String?,
    val setupCode: String?,
    val pin: String?,
    val ssid: String?,
    val password: String?,
)

object QrPayload {
    fun parse(text: String): AtlasLabel? {
        val t = text.trim()
        if (t.startsWith("ATLAS1:")) {
            // The password is last and may itself contain ':'.
            val parts = t.removePrefix("ATLAS1:").split(":", limit = 5)
            if (parts.size < 3) return null
            return AtlasLabel(
                deviceId = parts[0].ifBlank { null },
                setupCode = parts[1].ifBlank { null },
                pin = parts[2].ifBlank { null },
                ssid = parts.getOrNull(3)?.ifBlank { null },
                password = parts.getOrNull(4)?.ifBlank { null },
            )
        }
        if (t.startsWith("WIFI:")) {
            val fields = mutableMapOf<String, String>()
            var i = 5
            while (i < t.length) {
                val colon = t.indexOf(':', i)
                if (colon < 0) break
                val key = t.substring(i, colon)
                val sb = StringBuilder()
                var j = colon + 1
                while (j < t.length && t[j] != ';') {
                    if (t[j] == '\\' && j + 1 < t.length) { sb.append(t[j + 1]); j += 2 } else { sb.append(t[j]); j++ }
                }
                fields[key] = sb.toString()
                i = j + 1
            }
            val ssid = fields["S"] ?: return null
            return AtlasLabel(null, null, null, ssid, fields["P"])
        }
        return null
    }
}

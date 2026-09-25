package com.atlascontrol.mobile.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** Public identity an Atlas shows before pairing (/api/device/info). */
data class AtlasInfo(
    val baseUrl: String,
    val deviceId: String,
    val name: String,
    val advertisedPin: String?,
    /** The key this connection actually presented. */
    val observedPin: String,
    val version: String?,
    val pairingRequired: Boolean,
)

class PairingException(message: String, val status: Int = 0) : Exception(message)

/** Plain HTTPS calls to Atlas. All blocking work runs on Dispatchers.IO. */
object AtlasClient {
    private val JSON = "application/json".toMediaType()

    fun normalize(url: String): String = url.trim().trimEnd('/')

    /** Read the public identity of an Atlas we have not paired with yet. */
    suspend fun probe(baseUrl: String, timeoutMs: Long = 1500): AtlasInfo? = withContext(Dispatchers.IO) {
        val (client, observer) = AtlasTrust.observingClient(timeoutMs)
        runCatching {
            client.newCall(Request.Builder().url("${normalize(baseUrl)}/api/device/info").build()).execute().use { r ->
                if (!r.isSuccessful) return@runCatching null
                val o = JSONObject(r.body?.string() ?: return@runCatching null)
                if (o.optString("app") != "atlas-control") return@runCatching null
                AtlasInfo(
                    baseUrl = normalize(baseUrl),
                    deviceId = o.optString("device_id"),
                    name = o.optString("name", "Atlas"),
                    advertisedPin = o.optString("tls_spki_sha256").ifBlank { null },
                    observedPin = observer.observedPin ?: return@runCatching null,
                    version = o.optString("version").ifBlank { null },
                    pairingRequired = o.optBoolean("pairing_required", true),
                )
            }
        }.getOrNull()
    }

    /** Is the paired Atlas answering at this address with its pinned key? */
    suspend fun confirm(baseUrl: String, device: PairedDevice, timeoutSec: Long = 3): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                AtlasTrust.pinnedClient(device.pin, device.token, timeoutSec)
                    .newCall(Request.Builder().url("${normalize(baseUrl)}/api/device/info").build())
                    .execute().use { r ->
                        r.isSuccessful && JSONObject(r.body?.string() ?: "{}").optString("device_id") == device.deviceId
                    }
            }.getOrDefault(false)
        }

    private fun post(client: OkHttpClient, url: String, body: JSONObject): JSONObject {
        client.newCall(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build())
            .execute().use { r ->
                val o = runCatching { JSONObject(r.body?.string() ?: "{}") }.getOrDefault(JSONObject())
                if (!r.isSuccessful) throw PairingException(o.optString("error", "Atlas said no (${r.code})"), r.code)
                return o
            }
    }

    /** Pair with the label's setup code. Returns the bearer token. */
    suspend fun claim(baseUrl: String, pin: String, setupCode: String, name: String): String =
        withContext(Dispatchers.IO) {
            val o = post(AtlasTrust.pinnedClient(pin, null), "${normalize(baseUrl)}/api/pair/claim",
                JSONObject().put("setup_code", setupCode).put("name", name).put("kind", "android"))
            o.optString("token").ifBlank { throw PairingException("Atlas did not return a token") }
        }

    /** Pair because this phone is on Atlas's own Wi-Fi. Returns the bearer token. */
    suspend fun claimViaHotspot(baseUrl: String, pin: String, name: String): String = withContext(Dispatchers.IO) {
        val o = post(AtlasTrust.pinnedClient(pin, null), "${normalize(baseUrl)}/api/pair/hotspot",
            JSONObject().put("name", name).put("kind", "android"))
        o.optString("token").ifBlank { throw PairingException("Atlas did not return a token") }
    }

    data class ApprovalRequest(val requestId: String, val secret: String, val serverNonce: String,
                               val clientNonce: String, val matchCode: String)

    /** Ask an already-paired device to allow this phone. */
    suspend fun requestApproval(baseUrl: String, pin: String, name: String): ApprovalRequest =
        withContext(Dispatchers.IO) {
            val nonce = java.util.UUID.randomUUID().toString()
            val o = post(AtlasTrust.pinnedClient(pin, null), "${normalize(baseUrl)}/api/pair/request",
                JSONObject().put("name", name).put("kind", "android").put("client_nonce", nonce))
            ApprovalRequest(o.getString("request_id"), o.getString("secret"), o.getString("server_nonce"),
                nonce, o.getString("match_code"))
        }

    /** "pending" | "approved:<token>" | "denied" | "expired" | "unknown" */
    suspend fun pollApproval(baseUrl: String, pin: String, req: ApprovalRequest): String = withContext(Dispatchers.IO) {
        val url = "${normalize(baseUrl)}/api/pair/request/${req.requestId}?secret=" +
            java.net.URLEncoder.encode(req.secret, "UTF-8") + "&kind=android"
        AtlasTrust.pinnedClient(pin, null).newCall(Request.Builder().url(url).build()).execute().use { r ->
            val o = JSONObject(r.body?.string() ?: "{}")
            val status = o.optString("status", "unknown")
            if (status == "approved") "approved:${o.optString("token")}" else status
        }
    }

    /**
     * The 4-digit code this phone should display, computed from the key it
     * actually saw — it only matches the code on the approving device if
     * nobody is relaying the connection with a different certificate.
     */
    fun matchCode(observedPin: String, serverNonce: String, clientNonce: String): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
            .digest("atlas-pair|$observedPin|$serverNonce|$clientNonce".toByteArray())
        val v = ((digest[0].toLong() and 0xff) shl 24) or ((digest[1].toLong() and 0xff) shl 16) or
            ((digest[2].toLong() and 0xff) shl 8) or (digest[3].toLong() and 0xff)
        return "%04d".format(v % 10000)
    }

    /** Update/restart status nginx serves even while the Atlas app is down. */
    suspend fun updateStage(baseUrl: String, device: PairedDevice): String? = withContext(Dispatchers.IO) {
        runCatching {
            AtlasTrust.pinnedClient(device.pin, device.token, 4)
                .newCall(Request.Builder().url("${normalize(baseUrl)}/update-status.json").build())
                .execute().use { r -> if (r.isSuccessful) JSONObject(r.body?.string() ?: "{}").optString("stage") else null }
        }.getOrNull()
    }

    /** true = the Atlas app answers; false = reachable but restarting; null = not reachable. */
    suspend fun health(baseUrl: String, device: PairedDevice): Boolean? = withContext(Dispatchers.IO) {
        try {
            AtlasTrust.pinnedClient(device.pin, device.token, 4)
                .newCall(Request.Builder().url("${normalize(baseUrl)}/api/health").build())
                .execute().use { r -> r.isSuccessful }
        } catch (e: javax.net.ssl.SSLPeerUnverifiedException) {
            throw e
        } catch (e: javax.net.ssl.SSLHandshakeException) {
            if (e.cause is AtlasTrust.PinMismatchException) throw e
            null
        } catch (_: Exception) {
            null
        }
    }
}

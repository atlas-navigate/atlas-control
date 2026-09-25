package com.atlascontrol.mobile.network

import android.util.Base64
import okhttp3.OkHttpClient
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/**
 * How the app trusts an Atlas.
 *
 * Every Atlas has its own self-signed TLS key, made on the production line.
 * The app pins the SHA-256 of that key's SubjectPublicKeyInfo — the same value
 * Atlas publishes as `tls_spki_sha256` and prints (inside the QR code) on the
 * label. Once a device is paired, connections succeed only if the server
 * presents exactly that key; the address or hostname is irrelevant, so DHCP
 * changes and hotspot/LAN switches never matter, and a different key is a
 * security warning, never a silent accept.
 *
 * Before pairing, [ObservingTrustManager] lets the app read the public
 * `/api/device/info` of an Atlas it has not met yet and records which key it
 * saw. Nothing secret is ever sent over such a connection.
 */
object AtlasTrust {

    /** base64url(no padding) of SHA-256(SubjectPublicKeyInfo DER). */
    fun spkiPin(cert: X509Certificate): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(cert.publicKey.encoded)
        return Base64.encodeToString(digest, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
    }

    class PinMismatchException(val expected: String, val actual: String) :
        CertificateException("Atlas presented an unexpected certificate")

    /** Accepts only a leaf certificate whose key matches [expectedPin]. */
    class PinnedTrustManager(private val expectedPin: String) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("client certificates are not used")

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("no certificate")
            val actual = spkiPin(leaf)
            if (!MessageDigest.isEqual(actual.toByteArray(), expectedPin.toByteArray())) {
                throw PinMismatchException(expectedPin, actual)
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    /** Pre-pairing only: records the key an unknown Atlas presents. */
    class ObservingTrustManager : X509TrustManager {
        @Volatile var observedPin: String? = null
            private set

        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("client certificates are not used")

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("no certificate")
            observedPin = spkiPin(leaf)
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private fun socketFactory(tm: X509TrustManager): SSLSocketFactory =
        SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), SecureRandom()) }.socketFactory

    /**
     * Client for a paired Atlas: pinned key, bearer token on every request.
     * Hostname verification is replaced by the pin (Atlas is reached by
     * whatever address it has today; its identity is its key).
     */
    fun pinnedClient(pin: String, token: String?, readTimeoutSec: Long = 30): OkHttpClient {
        val tm = PinnedTrustManager(pin)
        return OkHttpClient.Builder()
            .sslSocketFactory(socketFactory(tm), tm)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSec, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .followRedirects(false)
            .addInterceptor { chain ->
                val req = chain.request().newBuilder()
                if (token != null) req.header("Authorization", "Bearer $token")
                chain.proceed(req.build())
            }
            .build()
    }

    /** Client for reading an unknown Atlas's public identity; returns the observer too. */
    fun observingClient(timeoutMs: Long = 1500): Pair<OkHttpClient, ObservingTrustManager> {
        val tm = ObservingTrustManager()
        val client = OkHttpClient.Builder()
            .sslSocketFactory(socketFactory(tm), tm)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .followRedirects(false)
            .build()
        return client to tm
    }
}

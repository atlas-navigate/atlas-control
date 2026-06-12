package com.atlascontrol.mobile.network

import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.*

object ApiClient {

    @Suppress("TrustAllX509TrustManager", "CustomX509TrustManager")
    private val trustAllManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private fun unsafeSslSocketFactory(): SSLSocketFactory {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(trustAllManager), SecureRandom())
        return ctx.socketFactory
    }

    /**
     * Shared OkHttpClient for subnet scanning and rapid IP probing.
     *
     * A single instance is reused for ALL probe attempts so we never create
     * thousands of OkHttpClient objects during a LAN scan.  Creating one
     * OkHttpClient per probe URL (as we did before) allocates a dispatcher
     * thread-pool, SSLContext, and ConnectionPool for every one of ~5 500
     * candidates, producing severe GC pressure on Android.  That GC pressure
     * caused 800 ms coroutine timeouts to fire from GC stalls rather than
     * real network latency, making every probe appear to fail even when
     * Atlas was reachable — the root cause of the LAN-switch failure on
     * emulators like Panda.
     *
     * Configuration:
     *  - connectTimeout 900 ms  — enough for one NAT hop through the host
     *  - readTimeout    900 ms  — we only need a tiny /api/device response
     *  - 256 idle connections   — absorbs all concurrent scan probes without
     *    teardown/setup churn between batches
     */
    val probeClient: OkHttpClient = OkHttpClient.Builder()
        .sslSocketFactory(unsafeSslSocketFactory(), trustAllManager)
        .hostnameVerifier { _, _ -> true }
        .dns(MdnsDns)
        .connectTimeout(900, TimeUnit.MILLISECONDS)
        .readTimeout(900, TimeUnit.MILLISECONDS)
        .writeTimeout(900, TimeUnit.MILLISECONDS)
        .connectionPool(ConnectionPool(256, 30, TimeUnit.SECONDS))
        .build()

    /**
     * Lightweight Retrofit wrapper around [probeClient].
     *
     * Retrofit itself is cheap to instantiate (it is just a proxy object
     * around the shared OkHttpClient), so creating one per base URL is fine.
     * The expensive object — OkHttpClient — is shared.
     */
    fun createForProbe(baseUrl: String): AtlasApi {
        val normalized = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        return Retrofit.Builder()
            .baseUrl(normalized)
            .client(probeClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(AtlasApi::class.java)
    }

    /** Full-featured client for normal API use (AI streaming, long polls, etc.). */
    fun create(baseUrl: String): AtlasApi {
        val normalized = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .sslSocketFactory(unsafeSslSocketFactory(), trustAllManager)
            .hostnameVerifier { _, _ -> true }
            .dns(MdnsDns)
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .build()

        return Retrofit.Builder()
            .baseUrl(normalized)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(AtlasApi::class.java)
    }
}

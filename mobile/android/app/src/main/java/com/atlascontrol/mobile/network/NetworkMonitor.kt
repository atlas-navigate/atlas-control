package com.atlascontrol.mobile.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.net.Inet4Address

/**
 * Monitors the active WiFi network and exposes the current SSID as a StateFlow.
 * Used by AppViewModel to prioritise hotspot vs LAN URLs when re-probing Atlas.
 */
class NetworkMonitor(private val context: Context) {

    private val _ssid = MutableStateFlow<String?>(null)

    /** Current WiFi SSID, or null when not associated. */
    val ssid: StateFlow<String?> = _ssid.asStateFlow()

    /**
     * Increments on every WiFi network-available or network-lost event, regardless
     * of whether the SSID is readable (i.e. works without location permission).
     * AppViewModel watches this so reconnect fires even when SSID stays null.
     */
    private val _networkVersion = MutableStateFlow(0)
    val networkVersion: StateFlow<Int> = _networkVersion.asStateFlow()

    /** True when the phone is connected to a network whose SSID contains "atlas". */
    val isOnAtlasHotspot: Boolean
        get() = _ssid.value?.contains("atlas", ignoreCase = true) == true

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            updateSsid()
            _networkVersion.value++
        }
        override fun onLost(network: Network) {
            updateSsid()
            _networkVersion.value++
        }
        override fun onCapabilitiesChanged(n: Network, c: NetworkCapabilities)    { updateSsid() }
    }

    fun start() {
        runCatching { connectivityManager.registerDefaultNetworkCallback(networkCallback) }
        updateSsid()
    }

    fun stop() {
        runCatching { connectivityManager.unregisterNetworkCallback(networkCallback) }
    }

    /**
     * Returns the active network's default gateway as "http://IP:5000", or null.
     * Uses ConnectivityManager.getLinkProperties() so it works on any transport —
     * WiFi, Ethernet, and the Android emulator's virtual NIC (WifiManager returns
     * zeros in the emulator because there is no real WiFi interface).
     */
    fun gatewayUrl(): String? = runCatching {
        val lp = connectivityManager.getLinkProperties(
            connectivityManager.activeNetwork ?: return null
        ) ?: return null
        val gw = lp.routes
            .mapNotNull { it.gateway }
            .filterIsInstance<Inet4Address>()
            .firstOrNull { !it.isLoopbackAddress && !it.isAnyLocalAddress }
            ?.hostAddress
            ?.takeIf { it.isNotBlank() && !it.startsWith("0.") && it != "255.255.255.255" }
            ?: return null
        "http://$gw:5000"
    }.getOrNull()

    private fun updateSsid() {
        val activeNetwork = connectivityManager.activeNetwork
        val caps = activeNetwork?.let { connectivityManager.getNetworkCapabilities(it) }
        if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) {
            _ssid.value = null
            return
        }
        val raw = runCatching {
            val wm = context.applicationContext
                .getSystemService(Context.WIFI_SERVICE) as WifiManager
            wm.connectionInfo?.ssid?.removeSurrounding("\"")
        }.getOrNull()
        _ssid.value = if (raw.isNullOrBlank() || raw == "<unknown ssid>") null else raw
    }
}

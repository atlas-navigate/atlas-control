package com.atlascontrol.mobile.network

import com.google.gson.annotations.SerializedName

data class DeviceInfo(
    @SerializedName("connected")         val connected: Boolean = false,
    @SerializedName("name")              val name: String = "",
    @SerializedName("hardware")          val hardware: String = "",
    @SerializedName("firmware")          val firmware: String = "",
    @SerializedName("my_node_id")        val myNodeId: String? = null,
    @SerializedName("battery_pct")       val batteryPct: Int? = null,
    @SerializedName("battery_status")    val batteryStatus: String? = null,
    @SerializedName("battery_phase")     val batteryPhase: String? = null,
    @SerializedName("battery_voltage")   val batteryVoltage: Double? = null,
    @SerializedName("battery_current_ma") val batteryCurrentMa: Double? = null,
    // Fingerprint stamped by Atlas's /api/device — used by the LAN port-5000
    // sweep to confirm a probed host is actually Atlas (not a stray Flask /
    // IoT server that happens to 200 on /api/device).
    @SerializedName("app")               val app: String? = null,
)

data class AtlasMessage(
    @SerializedName("from_id")    val fromId: String? = null,
    @SerializedName("to_id")      val toId: String? = null,
    @SerializedName("channel")    val channel: Int? = null,
    @SerializedName("text")       val text: String? = null,
    @SerializedName("rx_time")    val rxTime: Long = 0,
    @SerializedName("packet_id")  val packetId: Long? = null,
    @SerializedName("is_direct")  val isDirect: Int = 0,
)

// ── /api/mobile/bootstrap ─────────────────────────────────────────────────────

data class BootstrapManifest(
    @SerializedName("device")       val device: BootstrapDevice? = null,
    @SerializedName("api")          val api: BootstrapApi? = null,
    @SerializedName("hotspot")      val hotspot: HotspotInfo? = null,
    @SerializedName("capabilities") val capabilities: Map<String, Boolean>? = null,
    @SerializedName("generatedAt")  val generatedAt: Long = 0,
)

data class BootstrapDevice(
    @SerializedName("name")      val name: String = "Atlas Control",
    @SerializedName("shortName") val shortName: String = "ATLS",
)

data class BootstrapApi(
    @SerializedName("preferredBaseUrl") val preferredBaseUrl: String = "",
    @SerializedName("baseUrls")         val baseUrls: List<String> = emptyList(),
)

data class HotspotInfo(
    @SerializedName("active")   val active: Boolean = false,
    @SerializedName("ssid")     val ssid: String = "",
    @SerializedName("password") val password: String = "",
)

// ── /api/wifi/networks ────────────────────────────────────────────────────────

data class WifiNetwork(
    @SerializedName("ssid")     val ssid: String = "",
    @SerializedName("signal")   val signal: Int = 0,
    @SerializedName("security") val security: String = "",
    @SerializedName("in_use")   val inUse: Boolean = false,
)

data class WifiNetworksResponse(
    @SerializedName("networks") val networks: List<WifiNetwork> = emptyList(),
    @SerializedName("error")    val error: String? = null,
)

// ── /api/wifi/connect ─────────────────────────────────────────────────────────

data class WifiConnectRequest(
    @SerializedName("ssid")        val ssid: String,
    @SerializedName("password")    val password: String,
    @SerializedName("stopHotspot") val stopHotspot: Boolean = false,
    @SerializedName("background")  val background: Boolean = false,
)

data class WifiConnectResponse(
    @SerializedName("ok")         val ok: Boolean = false,
    @SerializedName("pending")    val pending: Boolean = false,
    @SerializedName("error")      val error: String? = null,
    @SerializedName("message")    val message: String? = null,
    @SerializedName("accessUrls") val accessUrls: List<String>? = null,
    @SerializedName("hintIp")     val hintIp: String? = null,
)

// ── /api/wifi/status ──────────────────────────────────────────────────────────

data class WifiStatusResponse(
    @SerializedName("accessUrls") val accessUrls: List<String>? = null,
    @SerializedName("wifiSwitch") val wifiSwitch: WifiSwitchState? = null,
)

data class WifiSwitchState(
    @SerializedName("pending") val pending: Boolean = true,
    @SerializedName("ok")      val ok: Boolean? = null,
    @SerializedName("result")  val result: WifiSwitchResult? = null,
)

// ── /api/wifi/my_ips ──────────────────────────────────────────────────────────

data class MyIpsResponse(
    @SerializedName("ips")  val ips: List<String>  = emptyList(),
    @SerializedName("urls") val urls: List<String> = emptyList(),
)

data class WifiSwitchResult(
    @SerializedName("ip")         val ip: String? = null,
    @SerializedName("hintIp")     val hintIp: String? = null,
    @SerializedName("accessUrls") val accessUrls: List<String>? = null,
)

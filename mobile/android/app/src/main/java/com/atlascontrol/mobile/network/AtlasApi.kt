package com.atlascontrol.mobile.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface AtlasApi {
    @GET("api/device")
    suspend fun getDevice(): DeviceInfo

    @GET("api/messages")
    suspend fun getMessages(): List<AtlasMessage>

    @GET("api/mobile/bootstrap")
    suspend fun getBootstrap(): BootstrapManifest

    @GET("api/wifi/networks")
    suspend fun getWifiNetworks(): WifiNetworksResponse

    @POST("api/wifi/connect")
    suspend fun connectWifi(@Body request: WifiConnectRequest): WifiConnectResponse

    @GET("api/wifi/status")
    suspend fun getWifiStatus(): WifiStatusResponse

    @GET("api/wifi/my_ips")
    suspend fun getMyIps(): MyIpsResponse

    @POST("api/hotspot/start")
    suspend fun startHotspot(@Body body: Map<String, String>): okhttp3.ResponseBody
}

package com.atlascontrol.mobile.network

import kotlinx.coroutines.CancellationException

class AtlasRepository(private val api: AtlasApi) {
    suspend fun getDevice(): Result<DeviceInfo>                  = safeCall { api.getDevice() }
    suspend fun getMessages(): Result<List<AtlasMessage>>        = safeCall { api.getMessages() }
    suspend fun getBootstrap(): Result<BootstrapManifest>        = safeCall { api.getBootstrap() }
    suspend fun getWifiNetworks(): Result<WifiNetworksResponse>  = safeCall { api.getWifiNetworks() }
    suspend fun connectWifi(r: WifiConnectRequest): Result<WifiConnectResponse> = safeCall { api.connectWifi(r) }
    suspend fun getWifiStatus(): Result<WifiStatusResponse>                     = safeCall { api.getWifiStatus() }
    suspend fun getMyIps(): Result<MyIpsResponse>                               = safeCall { api.getMyIps() }
    suspend fun startHotspot(): Result<Unit>                                    = safeCall { api.startHotspot(emptyMap()); Unit }

    private suspend fun <T> safeCall(block: suspend () -> T): Result<T> =
        try {
            Result.success(block())
        } catch (e: CancellationException) {
            throw e          // never swallow coroutine cancellation
        } catch (e: Throwable) {
            Result.failure(e)
        }
}

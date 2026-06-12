package com.atlascontrol.mobile.setup

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.ParcelUuid
import android.util.Log
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.util.UUID
import kotlin.coroutines.resume

private const val TAG = "AtlasBleScanner"

private val ATLAS_SERVICE_UUID   = UUID.fromString("7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0001")
private val ATLAS_CHAR_UUID      = UUID.fromString("7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0002")

data class AtlasBlePairing(
    val deviceName: String,
    val preferredUrl: String,
    val candidateUrls: List<String>,
    val hotspotSsid: String?,
    val hotspotPassword: String?
)

@SuppressLint("MissingPermission")
class AtlasBleScanner(private val context: Context) {

    private val btAdapter: BluetoothAdapter? by lazy {
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
    }

    val isAvailable get() = btAdapter?.isEnabled == true

    /** Scan for up to [timeoutMs] ms, returns first Atlas device found. */
    suspend fun scan(timeoutMs: Long = 15_000L): AtlasBlePairing? =
        withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { cont ->
                val scanner = btAdapter?.bluetoothLeScanner ?: run {
                    cont.resume(null); return@suspendCancellableCoroutine
                }

                val filter = ScanFilter.Builder()
                    .setServiceUuid(ParcelUuid(ATLAS_SERVICE_UUID))
                    .build()
                val settings = ScanSettings.Builder()
                    .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                    .build()

                val cb = object : ScanCallback() {
                    override fun onScanResult(callbackType: Int, result: ScanResult) {
                        scanner.stopScan(this)
                        connectAndRead(result.device.address, cont::resume)
                    }
                    override fun onScanFailed(errorCode: Int) {
                        Log.e(TAG, "BLE scan failed: $errorCode")
                        cont.resume(null)
                    }
                }

                scanner.startScan(listOf(filter), settings, cb)
                cont.invokeOnCancellation { scanner.stopScan(cb) }
            }
        }

    private fun connectAndRead(address: String, callback: (AtlasBlePairing?) -> Unit) {
        val device = btAdapter?.getRemoteDevice(address) ?: run { callback(null); return }
        device.connectGatt(context, false, object : BluetoothGattCallback() {
            override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    gatt.discoverServices()
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    gatt.close()
                }
            }

            override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
                val char = gatt.getService(ATLAS_SERVICE_UUID)
                    ?.getCharacteristic(ATLAS_CHAR_UUID)
                if (char != null) gatt.readCharacteristic(char)
                else { gatt.close(); callback(null) }
            }

            @Deprecated("Used for API < 33")
            override fun onCharacteristicRead(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                status: Int
            ) {
                gatt.disconnect()
                gatt.close()
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    callback(parseManifest(characteristic.value))
                } else {
                    callback(null)
                }
            }

            override fun onCharacteristicRead(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                value: ByteArray,
                status: Int
            ) {
                gatt.disconnect()
                gatt.close()
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    callback(parseManifest(value))
                } else {
                    callback(null)
                }
            }
        })
    }

    private fun parseManifest(bytes: ByteArray?): AtlasBlePairing? {
        bytes ?: return null
        return try {
            val json = JSONObject(bytes.toString(Charsets.UTF_8))
            val name = json.optString("device_name", "Atlas")
            val preferred = json.optString("preferred_url", "")
            val candidates = mutableListOf<String>()
            json.optJSONArray("urls")?.let { arr ->
                for (i in 0 until arr.length()) candidates.add(arr.getString(i))
            }
            if (preferred.isNotBlank() && preferred !in candidates) candidates.add(0, preferred)
            AtlasBlePairing(
                deviceName       = name,
                preferredUrl     = preferred.takeIf { it.isNotBlank() } ?: candidates.firstOrNull() ?: "",
                candidateUrls    = candidates,
                hotspotSsid      = json.optString("hotspot_ssid").takeIf { it.isNotBlank() },
                hotspotPassword  = json.optString("hotspot_password").takeIf { it.isNotBlank() }
            )
        } catch (e: Exception) {
            Log.e(TAG, "BLE manifest parse error", e)
            null
        }
    }
}

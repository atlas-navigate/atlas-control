package com.atlascontrol.mobile.network

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** The one Atlas this phone is paired with. */
data class PairedDevice(
    val deviceId: String,
    val name: String,
    val pin: String,
    val token: String,
    val lastUrl: String?,
)

/**
 * Persists the paired Atlas. The pairing token is encrypted with an AES-GCM
 * key that lives in the Android Keystore and never leaves it; the rest
 * (device id, name, key pin, last address) is not secret.
 * Excluded from backups (see AndroidManifest / data_extraction_rules).
 */
class DeviceStore(context: Context) {
    private val prefs = context.getSharedPreferences("atlas_device", Context.MODE_PRIVATE)

    fun load(): PairedDevice? {
        val id = prefs.getString("device_id", null) ?: return null
        val pin = prefs.getString("pin", null) ?: return null
        val sealed = prefs.getString("token", null) ?: return null
        val token = runCatching { decrypt(sealed) }.getOrNull() ?: return null
        return PairedDevice(id, prefs.getString("name", "Atlas") ?: "Atlas", pin, token,
            prefs.getString("last_url", null))
    }

    fun save(device: PairedDevice) {
        prefs.edit()
            .putString("device_id", device.deviceId)
            .putString("name", device.name)
            .putString("pin", device.pin)
            .putString("token", encrypt(device.token))
            .putString("last_url", device.lastUrl)
            .apply()
    }

    fun rememberUrl(url: String) {
        prefs.edit().putString("last_url", url).apply()
    }

    fun forget() {
        prefs.edit().clear().apply()
    }

    // ── Keystore-backed sealing ──────────────────────────────────────────
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }

    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    private fun decrypt(sealed: String): String {
        val raw = Base64.decode(sealed, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12))
        return String(cipher.doFinal(raw, 12, raw.size - 12), Charsets.UTF_8)
    }

    private companion object {
        const val KEY_ALIAS = "atlas_pairing_token_key"
    }
}

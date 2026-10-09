package com.atlascontrol.mobile.network

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Whether Android lets this app reach devices on the local network.
 *
 * Finding and talking to Atlas needs no runtime permission today, but newer
 * Android releases can restrict local-network access (granted together with
 * "Nearby devices"). A refusal surfaces as a SecurityException or an EPERM
 * socket error; when that happens the app explains and asks, instead of
 * silently finding nothing.
 */
object LocalNetwork {
    private val _blocked = MutableStateFlow(false)
    val blocked: StateFlow<Boolean> = _blocked.asStateFlow()

    fun noteFailure(e: Throwable) {
        if (isPermissionDenial(e)) _blocked.value = true
    }

    fun noteSuccess() {
        _blocked.value = false
    }

    fun isPermissionDenial(e: Throwable): Boolean {
        var t: Throwable? = e
        while (t != null) {
            if (t is SecurityException) return true
            val m = t.message.orEmpty()
            if ("EPERM" in m || "Operation not permitted" in m || "EACCES" in m) return true
            t = t.cause
        }
        return false
    }
}

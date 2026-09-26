package com.atlascontrol.mobile.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pairing logic that must agree with Atlas itself. Vectors come from the
 * server (device_identity.match_code, identity.label_payload); the Python
 * test tests/test_mobile_contract.py keeps the two sides in step.
 * Run with ./gradlew test.
 */
class PairingLogicTest {
    @Test fun matchCodeEqualsWhatAtlasShows() {
        assertEquals("0175", AtlasClient.matchCode("q1Zb7x-pinexample_abcdefghijklmnopqrstuvwxyz0",
            "srv-nonce-1", "11111111-2222-3333-4444-555555555555"))
        assertEquals("3050", AtlasClient.matchCode("AAAA", "n", "c"))
    }

    @Test fun labelQrCarriesIdCodePinAndHotspot() {
        val label = QrPayload.parse("ATLAS1:0123abcd:H7QDM3XR9KTW:pinPIN_-x:Atlas-H7QD:k7mq-x3rt:9pwd-h2fc")!!
        assertEquals("0123abcd", label.deviceId)
        assertEquals("H7QDM3XR9KTW", label.setupCode)
        assertEquals("pinPIN_-x", label.pin)
        assertEquals("Atlas-H7QD", label.ssid)
        assertEquals("k7mq-x3rt:9pwd-h2fc", label.password)       // the password may contain ':'
    }

    @Test fun plainWifiQrIsUnderstoodToo() {
        val label = QrPayload.parse("WIFI:T:WPA;S:Atlas\;H7QD;P:k7mq-x3rt-9pwd-h2fc;;")!!
        assertEquals("Atlas;H7QD", label.ssid)
        assertEquals("k7mq-x3rt-9pwd-h2fc", label.password)
        assertNull(label.setupCode)
        assertNull(QrPayload.parse("https://example.com"))
    }

    @Test fun setupCodeIsFormattedAsTheOwnerTypes() {
        assertEquals("H7QD-M3XR-9KTW", SetupCode.format("h7qd m3xr 9ktw"))
        assertEquals("H7QD-M3", SetupCode.format("h7qd-m3"))
        assertEquals("H7QD-M3XR-9KTW", SetupCode.format("H7QD-M3XR-9KTW-EXTRA"))
        assertTrue(SetupCode.isComplete("h7qdm3xr9ktw"))
        assertFalse(SetupCode.isComplete("H7QD-M3XR"))
    }

    @Test fun theWifiPasswordIsRecognisedWhenPastedAsASetupCode() {
        assertTrue(SetupCode.looksLikeWifiPassword("k7mq-x3rt-9pwd-h2fc"))
        assertFalse(SetupCode.looksLikeWifiPassword("H7QD-M3XR-9KTW"))
    }

    @Test fun approvalAnswersMapToOutcomes() {
        assertEquals("approved:tok", AtlasClient.approvalStatus(JSONObject("""{"status":"approved","token":"tok"}""")))
        assertEquals("pending", AtlasClient.approvalStatus(JSONObject("""{"status":"pending"}""")))
        assertEquals("denied", AtlasClient.approvalStatus(JSONObject("""{"status":"denied"}""")))
        assertEquals("unknown", AtlasClient.approvalStatus(JSONObject("{}")))
    }
}

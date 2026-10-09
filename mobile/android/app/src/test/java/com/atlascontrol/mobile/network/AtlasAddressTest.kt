package com.atlascontrol.mobile.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AtlasAddressTest {
    @Test fun advertisedNamesBecomeLocalNames() {
        assertEquals("atlas-7kq3pm.local", AtlasAddress.fqdn("atlas-7kq3pm"))      // DNS-SD TXT host=
        assertEquals("atlas-7kq3pm.local", AtlasAddress.fqdn("Atlas-7KQ3PM.local")) // beacon / device info
        assertEquals("atlas-7kq3pm.local", AtlasAddress.fqdn("atlas-7kq3pm.local."))
        assertNull(AtlasAddress.fqdn(""))
        assertNull(AtlasAddress.fqdn("bad name"))
        assertNull(AtlasAddress.fqdn("-atlas"))
        assertNull(AtlasAddress.fqdn(null))
    }

    @Test fun typedNamesAndAddresses() {
        val bare = AtlasAddress.parse(" atlas-7kq3pm ")!!
        assertEquals("https://atlas-7kq3pm.local", bare.url)
        assertTrue(bare.isLocalName)
        assertEquals("atlas-7kq3pm.local", AtlasAddress.parse("https://ATLAS-7KQ3PM.local/mobile")!!.host)
        val ip = AtlasAddress.parse("192.168.1.20")!!
        assertEquals("https://192.168.1.20", ip.url)
        assertFalse(ip.isLocalName)
        assertEquals("https://192.168.1.20:8443", AtlasAddress.parse("192.168.1.20:8443")!!.url)
        assertEquals("https://192.168.1.20", AtlasAddress.parse("http://192.168.1.20:443/")!!.url)
        assertEquals("https://[fd00::5]", AtlasAddress.parse("[fd00::5]")!!.url)
        val dns = AtlasAddress.parse("atlas.home.lan")!!
        assertEquals("https://atlas.home.lan", dns.url)
        assertFalse(dns.isLocalName)
    }

    @Test fun rejectsNonsense() {
        assertNull(AtlasAddress.parse(""))
        assertNull(AtlasAddress.parse("my atlas"))
        assertNull(AtlasAddress.parse("300.1.1.1"))
        assertNull(AtlasAddress.parse("atlas-7kq3pm.local:99999"))
        assertNull(AtlasAddress.parse("atlas_7kq3pm"))
    }

    @Test fun hostOfUrl() {
        assertEquals("192.168.1.20", AtlasAddress.hostOf("https://192.168.1.20"))
        assertEquals("192.168.1.20", AtlasAddress.hostOf("https://192.168.1.20/mobile"))
        assertNull(AtlasAddress.hostOf(null))
    }

    @Test fun localNetworkDenialIsRecognised() {
        assertTrue(LocalNetwork.isPermissionDenial(SecurityException("no")))
        assertTrue(LocalNetwork.isPermissionDenial(java.net.SocketException("sendto failed: EPERM (Operation not permitted)")))
        assertTrue(LocalNetwork.isPermissionDenial(RuntimeException(java.net.SocketException("connect failed: EACCES"))))
        assertFalse(LocalNetwork.isPermissionDenial(java.net.SocketTimeoutException("timeout")))
    }
}

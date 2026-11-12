package com.homepix.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.Base64

/** The phone talks only to a computer on its own network, and nothing of the app leaves the phone. */
class PrivacyTest {
    private val fp = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { it.toByte() })

    @Test
    fun privateAddressesOnly() {
        listOf("192.168.1.5", "10.0.0.2", "172.16.0.1", "172.31.255.255", "169.254.3.4", "127.0.0.1",
            "fd12:3456::1", "fe80::1%wlan0", "[fe80::1]", "::1", "::ffff:192.168.1.5")
            .forEach { assertTrue(it, isPrivateAddress(it)) }
        listOf("203.0.113.9", "8.8.8.8", "172.32.0.1", "192.169.0.1", "100.64.0.1", "2001:db8::1",
            "::ffff:8.8.8.8", "evil.example.com", "localhost", "192.168.1.256", "", "1.2.3")
            .forEach { assertFalse(it, isPrivateAddress(it)) }
    }

    @Test
    fun aCodeKeepsOnlyPrivateAddresses() {
        val code = PairCode.parse("homepix:pair?v=1&a=203.0.113.9%2C192.168.1.5%2Cevil.example.com&p=47820&f=$fp&c=abc&n=My+laptop")!!
        assertEquals(listOf("192.168.1.5"), code.addresses)
        assertEquals("My laptop", code.name)
        assertEquals(47820, code.port)
    }

    @Test
    fun aCodeWithOnlyPublicAddressesKeepsNone() {
        val code = PairCode.parse("homepix:pair?a=203.0.113.9&p=443&f=$fp&c=x")!!
        assertTrue(code.addresses.isEmpty())
    }

    @Test
    fun notACode() {
        assertNull(PairCode.parse("https://example.com"))
        assertNull(PairCode.parse("homepix:pair?a=192.168.1.5&p=1&f=AAAA&c=x")) // (a fingerprint too short)
    }

    @Test
    fun nothingIsBackedUp() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("""android:allowBackup="false""""))
        assertTrue(manifest.contains("""android:dataExtractionRules="@xml/no_backup""""))
        val rules = File("src/main/res/xml/no_backup.xml").readText()
        listOf("cloud-backup", "device-transfer").forEach { part ->
            val block = rules.substringAfter("<$part>").substringBefore("</$part>")
            listOf("root", "file", "database", "sharedpref").forEach { assertTrue("$part $it", block.contains("domain=\"$it\" path=\".\"")) }
        }
    }

    @Test
    fun noPlaceLookupOnline() {
        val uses = File("src/main/java").walk().filter { it.extension == "kt" && it.readText().contains("Geocoder") }.toList()
        assertEquals(emptyList<File>(), uses)
    }

    @Test
    fun theAiLibrarySendsNothingToMicrosoft() {
        // ONNX Runtime's own manifest starts its telemetry at launch: taken out, and off before it loads
        val manifest = File("src/main/AndroidManifest.xml").readText()
        val starter = manifest.substringAfter("ai.onnxruntime.TelemetryInitializer", "").substringBefore("/>")
        assertTrue(starter.contains("tools:node=\"remove\""))
        val search = File("src/main/java/com/homepix/mobile/Search.kt").readText()
        assertTrue(search.indexOf("ORT_DISABLE_TELEMETRY") in 0 until search.indexOf("OrtEnvironment.getEnvironment()"))
    }
}

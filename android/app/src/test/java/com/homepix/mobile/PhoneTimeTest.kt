package com.homepix.mobile

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneId

/** A photo not in the library yet is dated by the phone: the camera's time, on the phone's clock. */
class PhoneTimeTest {
    private val israel = ZoneId.of("Asia/Jerusalem")

    @Test
    fun theCamerasTimeOnThePhonesClock() {
        // 2026-11-11 12:23:46 UTC, a Pixel's PXL_20261111_122346 name: 14:23 in Israel
        assertEquals("2026-11-11 14:23:46", phoneTime(1_794_399_826_000, 1, israel))
    }

    @Test
    fun withoutACameraTimeTheFilesTime() {
        assertEquals("2026-11-11 14:23:46", phoneTime(null, 1_794_399_826, israel))
        assertEquals("2026-11-11 14:23:46", phoneTime(0, 1_794_399_826, israel))
    }

    @Test
    fun noTimeAtAllIsNoDate() {
        assertEquals("0000-00-00 00:00:00", phoneTime(null, 0, israel))
    }
}

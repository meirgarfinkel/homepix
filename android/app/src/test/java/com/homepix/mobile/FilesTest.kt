package com.homepix.mobile

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Changing a photo, the turned photos' fingerprints, the caches and reading a library copy. */
class FilesTest {
    private fun folder() = Files.createTempDirectory("homepix").toFile().apply { deleteOnExit() }

    private fun photo(dir: File, bytes: ByteArray = ByteArray(5000) { (it % 251).toByte() }) =
        File(dir, "IMG_1.jpg").apply { writeBytes(bytes); setLastModified(1_700_000_000_000) }

    @Test
    fun aChangeCutOffPartWayLeavesThePhotoAsItWas() {
        val dir = folder()
        val f = photo(dir)
        val before = f.readBytes()
        try {
            replaceSafely(f) { copy ->
                copy.writeBytes(ByteArray(10)) // half written...
                error("the phone was switched off") // ...and stopped
            }
            fail("the failure goes on to the caller")
        } catch (e: IllegalStateException) {
            // expected
        }
        assertArrayEquals(before, f.readBytes())
        assertEquals(1_700_000_000_000, f.lastModified())
        assertEquals(listOf("IMG_1.jpg"), dir.list()!!.toList()) // (no copy left beside it)
    }

    @Test
    fun aChangedPhotoKeepsItsTimeASecondOn() {
        val dir = folder()
        val f = photo(dir)
        assertTrue(replaceSafely(f) { copy -> copy.appendBytes(byteArrayOf(1, 2, 3)); true })
        assertEquals(5003, f.length())
        assertEquals(1_700_000_001_000, f.lastModified())
        assertEquals(listOf("IMG_1.jpg"), dir.list()!!.toList())
    }

    @Test
    fun aChangeThatIsLeftOffChangesNothing() {
        val dir = folder()
        val f = photo(dir)
        assertFalse(replaceSafely(f) { copy -> copy.writeBytes(ByteArray(1)); false })
        assertEquals(5000, f.length())
        assertEquals(1_700_000_000_000, f.lastModified())
        assertEquals(listOf("IMG_1.jpg"), dir.list()!!.toList())
    }

    @Test
    fun onlyJpegsAreTurnedInTheFile() {
        assertEquals(setOf("jpg", "jpeg"), TURN_IN_FILE)
    }

    @Test
    fun aPhotoTurnedTwiceKeepsTheFingerprintTheLibraryHas() {
        val turned = HashMap<String, String>()
        assertTrue(rememberTurn(turned, "/DCIM/Camera/a.jpg", "H1"))
        assertFalse(rememberTurn(turned, "/DCIM/Camera/a.jpg", "H2"))
        assertEquals("H1", turned["/DCIM/Camera/a.jpg"])
    }

    @Test
    fun aTurnedPhotoIsLetGoOfOnceTheLibraryHasCaughtUp() {
        // turned (H1 -> H2), the computer has H2, then turned again: H2 must be remembered this time
        val turned = HashMap(mapOf("/a.jpg" to "H1", "/b.jpg" to "H5", "/gone.jpg" to "H7"))
        val done = caughtUp(turned, found = setOf("/a.jpg")) { it != "/gone.jpg" }
        assertEquals(setOf("/a.jpg", "/gone.jpg"), done.toSet())
        done.forEach { turned.remove(it) }
        assertTrue(rememberTurn(turned, "/a.jpg", "H2"))
        assertEquals(mapOf("/a.jpg" to "H2", "/b.jpg" to "H5"), turned)
    }

    @Test
    fun theCacheIsKeptUnderItsCapOldestFirst() {
        val dir = folder()
        val now = 1_700_000_000_000
        for (i in 0 until 10) File(dir, "$i.jpg").apply { writeBytes(ByteArray(100)); setLastModified(now - 3_600_000 + i * 1000L) }
        File(dir, "new.jpg").apply { writeBytes(ByteArray(100)); setLastModified(now - 1000) } // (being made)
        prune(dir, 500, now)
        assertEquals(setOf("6.jpg", "7.jpg", "8.jpg", "9.jpg", "new.jpg"), dir.list()!!.toSet())
        prune(dir, 10_000, now) // under the cap: nothing goes
        assertEquals(5, dir.list()!!.size)
    }

    @Test
    fun twoWritersOfTheSameFileNeverMix() {
        val dir = folder()
        val dst = File(dir, "a.jpg")
        writeWhole(dst) { tmp ->
            writeWhole(dst) { it.writeText("second") } // (another one, finished while this one writes)
            tmp.writeText("first")
        }
        assertEquals("first", dst.readText())
        assertEquals(listOf("a.jpg"), dir.list()!!.toList())
    }

    @Test
    fun aLibraryTheAppCantReadSaysWhatToUpdate() {
        assertNull(libraryProblem(KNOWN_VERSION, emptyList()))
        assertNull(libraryProblem(KNOWN_VERSION + 3, emptyList())) // (newer, with what the app reads: fine)
        assertNotNull(libraryProblem(KNOWN_VERSION + 1, listOf("photos.moment"))!!.let { if ("app" in it) it else null })
        assertNotNull(libraryProblem(9, listOf("photos.moment"))!!.let { if ("computer" in it) it else null })
    }
}

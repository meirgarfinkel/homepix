package com.homepix.mobile

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest

/** Sending one batch, against a stand-in computer that answers as HomePix does (homepix/core/phones.py). */
class SendingTest {
    private fun local(name: String, bytes: ByteArray): Local {
        val f = File.createTempFile("send", name).apply { writeBytes(bytes); deleteOnExit() }
        return Local(f, "Camera/$name", bytes.size.toLong(), 1_700_000_000_000)
    }

    private fun bytes(n: Int, seed: Int) = ByteArray(n) { ((it * 31 + seed) % 251).toByte() }

    private fun shaOf(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).hex()

    /** The computer: the library's fingerprints and contents, the uploads under way, what came whole. */
    private class Computer(
        val library: Set<String> = emptySet(),
        val seen: Set<String> = emptySet(),
        val quick: Boolean = true,
        val takes: (String) -> Boolean = { true }, // (a name or kind of file it takes)
    ) : Peer {
        val parts = HashMap<String, ByteArray>()
        val arrived = HashMap<String, ByteArray>()
        var cutAfter = -1 // bytes: the next upload stops after this many (the Wi-Fi went)
        var refuse = 0 // the next upload is refused with this status
        val asked = ArrayList<JSONObject>()
        var received = 0L // bytes that came, over every upload

        override fun post(path: String, body: JSONObject): JSONObject {
            asked += body
            val files = body.getJSONArray("files")
            val need = JSONArray()
            val no = JSONArray()
            val byQuick = files.length() > 0 && !files.getJSONObject(0).has("sha256")
            if (byQuick && !quick) return JSONObject().put("need", JSONArray()) // (before 1.17: no sha256, left out)
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i)
                if (!takes(f.getString("path"))) { no.put(f.getString("path")); continue }
                if (byQuick) {
                    if (f.optString("qhash") !in library) need.put(JSONObject().put("path", f.getString("path")))
                } else if (f.getString("sha256") !in seen) {
                    val have = parts[f.getString("sha256")]?.size ?: 0
                    need.put(JSONObject().put("path", f.getString("path")).put("have", have))
                }
            }
            return JSONObject().put("need", need).apply { if (quick) put("quick", true).put("refused", no) }
        }

        override fun send(f: Local, sha: String, offset: Long): Long {
            if (refuse != 0) throw Refused(refuse, "refused").also { refuse = 0 }
            val part = parts.getOrPut(sha) { ByteArray(0) }
            if (offset != part.size.toLong()) throw Refused(409, "go on from there", part.size.toLong())
            var data = FileInputStream(f.file).use { it.readBytes() }.copyOfRange(offset.toInt(), f.size.toInt())
            if (cutAfter >= 0) data = data.copyOf(minOf(cutAfter, data.size)).also { cutAfter = -1 }
            received += data.size
            val now = part + data
            if (now.size.toLong() < f.size) {
                parts[sha] = now
                return now.size.toLong()
            }
            parts.remove(sha)
            arrived[f.rel] = now
            return f.size
        }
    }

    private fun send(batch: List<Local>, c: Computer, hashed: MutableList<String> = ArrayList()): Pair<Boolean, Map<String, Boolean>> {
        val done = LinkedHashMap<String, Boolean>()
        val whole = sendBatch(batch, c, { quickHash(it.file) }, { hashed += it.rel; sha256(it.file) }) { f, sent -> done[f.rel] = sent }
        return whole to done
    }

    @Test
    fun onlyWhatTheLibraryLacksIsReadWholeAndSent() {
        val inLibrary = local("a.jpg", bytes(200_000, 1))
        val new = local("b.jpg", bytes(150_000, 2))
        val c = Computer(library = setOf(quickHash(inLibrary.file)))
        val hashed = ArrayList<String>()
        val (whole, done) = send(listOf(inLibrary, new), c, hashed)
        assertTrue(whole)
        assertEquals(listOf("Camera/b.jpg"), hashed) // (the library's one never read whole)
        assertEquals(mapOf("Camera/a.jpg" to false, "Camera/b.jpg" to true), done)
        assertArrayEquals(new.file.readBytes(), c.arrived["Camera/b.jpg"])
    }

    @Test
    fun aComputerBefore117IsAskedByContent() {
        val seenBefore = local("a.jpg", bytes(1000, 3))
        val new = local("b.jpg", bytes(1000, 4))
        val c = Computer(seen = setOf(shaOf(seenBefore.file.readBytes())), quick = false)
        val (whole, done) = send(listOf(seenBefore, new), c)
        assertTrue(whole)
        assertEquals(mapOf("Camera/a.jpg" to false, "Camera/b.jpg" to true), done)
    }

    @Test
    fun anUploadCutOffGoesOnFromWhereItStopped() {
        val video = local("v.mp4", bytes(300_000, 5))
        val c = Computer()
        c.cutAfter = 100_000
        val (whole, done) = send(listOf(video), c)
        assertFalse(whole) // (cut off: not done, so it's tried again)
        assertEquals(emptyMap<String, Boolean>(), done)
        val (again, done2) = send(listOf(video), c)
        assertTrue(again)
        assertEquals(mapOf("Camera/v.mp4" to true), done2)
        assertEquals(300_000L, c.received) // (100,000 then the other 200,000: not started over)
        assertArrayEquals(video.file.readBytes(), c.arrived["Camera/v.mp4"])
    }

    @Test
    fun whenTheComputerHasMoreThanItSaidItGoesOnFromThere() {
        val video = local("v.mp4", bytes(300_000, 6))
        val c = object : Peer {
            val inner = Computer()
            override fun post(path: String, body: JSONObject): JSONObject {
                val answer = inner.post(path, body)
                // (it says nothing came, but 50,000 bytes had: another run was sending it meanwhile)
                if (body.getJSONArray("files").getJSONObject(0).has("sha256")) {
                    inner.parts[shaOf(video.file.readBytes())] = video.file.readBytes().copyOf(50_000)
                }
                return answer
            }
            override fun send(f: Local, sha: String, offset: Long) = inner.send(f, sha, offset)
        }
        val done = LinkedHashMap<String, Boolean>()
        assertTrue(sendBatch(listOf(video), c, { quickHash(it.file) }) { f, sent -> done[f.rel] = sent })
        assertEquals(mapOf("Camera/v.mp4" to true), done)
        assertArrayEquals(video.file.readBytes(), c.inner.arrived["Camera/v.mp4"])
    }

    @Test
    fun unpairedOrAFullDiskStopsWithNothingMarkedDone() {
        for (status in listOf(401, 507)) {
            val c = Computer()
            c.refuse = status
            val done = LinkedHashMap<String, Boolean>()
            val e = assertThrows(Refused::class.java) {
                sendBatch(listOf(local("a.jpg", bytes(1000, 7))), c, { quickHash(it.file) }) { f, sent -> done[f.rel] = sent }
            }
            assertEquals(status, e.status)
            assertTrue(done.isEmpty())
        }
    }

    @Test
    fun aFileTheComputerDoesntTakeIsCountedNotMarkedDone() {
        val ok = local("a.jpg", bytes(1000, 5))
        val notes = Local(File.createTempFile("send", ".txt").apply { writeText("x"); deleteOnExit() }, "Camera/notes.txt", 1, 1)
        val c = Computer(takes = { it.endsWith(".jpg") })
        val done = LinkedHashMap<String, Boolean>()
        val no = ArrayList<String>()
        assertTrue(sendBatch(listOf(ok, notes), c, { quickHash(it.file) }, refused = { no += it.rel }) { f, sent -> done[f.rel] = sent })
        assertEquals(mapOf("Camera/a.jpg" to true), done) // (the other is asked about again next time)
        assertEquals(listOf("Camera/notes.txt"), no)
    }

    @Test
    fun anSdCardsFilesHaveAPlaceOfTheirOwn() {
        assertEquals("Camera/IMG_1.jpg", placeOf("external_primary", "DCIM/Camera/", "IMG_1.jpg"))
        assertEquals("SD 1A2B-3C4D/Camera/IMG_1.jpg", placeOf("1a2b-3c4d", "DCIM/Camera/", "IMG_1.jpg"))
    }
}

package com.homepix.mobile

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/** expected.json was made by HomePix on the computer (its tokenizer and split_matches): the phone must agree. */
class SameAsDesktopTest {
    private val expected = JSONObject(javaClass.getResource("/expected.json")!!.readText())

    private fun ints(a: JSONArray) = List(a.length()) { a.getInt(it) }

    @Test
    fun tokensMatch() {
        val tok = Tokenizer(File("src/main/assets/clip_tokenizer.json").readText())
        val cases = expected.getJSONArray("tokens")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONArray(i)
            assertEquals("tokens for '${c.getString(0)}'", ints(c.getJSONArray(1)), tok.encode(c.getString(0)))
        }
    }

    /** Expected values from HomePix's util.quick_hash, over the same made-up bytes. */
    @Test
    fun fingerprintsMatch() {
        val want = mapOf(
            1000 to "ce062558c7b68397a79393ad69ff25c16ef6d53f",
            131072 to "2dc156db0f12281360923382a69e9fcb60c9f369",
            300000 to "7bb0394929908275a38cd6a6517de1fe1306a932",
        )
        for ((n, hash) in want) {
            val f = File.createTempFile("qhash", ".bin")
            f.writeBytes(ByteArray(n) { ((it * 31 + 7) % 251).toByte() })
            assertEquals("fingerprint of $n bytes", hash, quickHash(f))
            f.delete()
        }
    }

    @Test
    fun matchesSplitTheSameWay() {
        val scores = expected.getJSONArray("scores")
        val splits = expected.getJSONArray("splits")
        for (i in 0 until scores.length()) {
            val s = scores.getJSONArray(i)
            val (clear, near) = Search.splitMatches(FloatArray(s.length()) { s.getDouble(it).toFloat() })
            assertEquals(ints(splits.getJSONArray(i).getJSONArray(0)), clear)
            assertEquals(ints(splits.getJSONArray(i).getJSONArray(1)), near)
        }
    }
}

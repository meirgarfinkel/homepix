package com.homepix.mobile

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * expected.json is made by HomePix on the computer (tests/test_phone_agrees.py, from its tokenizer,
 * split_matches, util.quick_hash and phones.qr_text): the phone must agree.
 */
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

    /** HomePix's util.quick_hash, over the same made-up bytes. */
    @Test
    fun fingerprintsMatch() {
        val want = expected.getJSONObject("qhash")
        for (n in want.keys()) {
            val f = File.createTempFile("qhash", ".bin")
            f.writeBytes(ByteArray(n.toInt()) { ((it * 31 + 7) % 251).toByte() })
            assertEquals("fingerprint of $n bytes", want.getString(n), quickHash(f))
            f.delete()
        }
    }

    /** A pairing link as HomePix writes it, read as the computer meant it. */
    @Test
    fun pairingLinkReadsTheSame() {
        val want = expected.getJSONObject("pair")
        val code = PairCode.parse(want.getString("link"))!!
        val addresses = want.getJSONArray("addresses")
        assertEquals(List(addresses.length()) { addresses.getString(it) }, code.addresses)
        assertEquals(want.getInt("port"), code.port)
        assertEquals(want.getString("fingerprint"), code.fingerprint.hex())
        assertEquals(want.getString("code"), code.code)
        assertEquals(want.getString("name"), code.name)
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

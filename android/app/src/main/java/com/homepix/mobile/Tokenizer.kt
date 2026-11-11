package com.homepix.mobile

import org.json.JSONArray
import org.json.JSONObject
import java.text.Normalizer

/** CLIP's tokenizer, ported from HomePix's clip_tokens.py: a phrase becomes the token numbers MobileCLIP reads. */
class Tokenizer(json: String) {
    private val vocab = HashMap<String, Int>()
    private val ranks = HashMap<String, Int>() // "a b" -> rank
    private val unknown: Int
    private val cache = HashMap<String, List<String>>()

    init {
        val model = JSONObject(json).getJSONObject("model")
        val v = model.getJSONObject("vocab")
        for (k in v.keys()) vocab[k] = v.getInt(k)
        val merges = model.getJSONArray("merges")
        for (r in 0 until merges.length()) {
            val m = merges.get(r)
            val key = if (m is JSONArray) m.getString(0) + " " + m.getString(1) else m as String
            ranks.putIfAbsent(key, r)
        }
        unknown = vocab.getValue(model.getString("unk_token"))
    }

    fun encode(text: String): List<Int> {
        val clean = Normalizer.normalize(text, Normalizer.Form.NFC)
            .split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").lowercase()
        val ids = mutableListOf(vocab.getValue(START))
        for (word in words(clean)) {
            val chars = word.toByteArray(Charsets.UTF_8).joinToString("") { BYTE_CHARS[it.toInt() and 0xff].toString() }
            for (token in bpe(chars)) ids.add(vocab[token] ?: unknown)
        }
        ids.add(vocab.getValue(END))
        return ids
    }

    private fun bpe(word: String): List<String> = cache.getOrPut(word) {
        var parts = word.dropLast(1).map { it.toString() } + (word.last() + "</w>")
        while (parts.size > 1) {
            var best: Pair<String, String>? = null
            var bestRank = Int.MAX_VALUE
            for (k in 0 until parts.size - 1) {
                val r = ranks[parts[k] + " " + parts[k + 1]] ?: continue
                if (r < bestRank) { bestRank = r; best = parts[k] to parts[k + 1] }
            }
            if (best == null) break
            val joined = mutableListOf<String>()
            var k = 0
            while (k < parts.size) {
                if (k < parts.size - 1 && parts[k] == best.first && parts[k + 1] == best.second) {
                    joined.add(parts[k] + parts[k + 1]); k += 2
                } else {
                    joined.add(parts[k]); k += 1
                }
            }
            parts = joined
        }
        parts
    }

    companion object {
        const val START = "<|startoftext|>"
        const val END = "<|endoftext|>"
        private val CONTRACTIONS = listOf("'s", "'t", "'re", "'ve", "'m", "'ll", "'d")

        /** GPT-2's byte -> printable character table. */
        val BYTE_CHARS: CharArray = run {
            val keep = (('!'.code..'~'.code) + ('¡'.code..'¬'.code) + ('®'.code..'ÿ'.code)).toSet()
            var extra = 0
            CharArray(256) { b -> if (b in keep) b.toChar() else (256 + extra++).toChar() }
        }

        private fun kind(cp: Int): Char = when {
            Character.isWhitespace(cp) || Character.isSpaceChar(cp) -> ' '
            Character.isLetter(cp) -> 'L'
            Character.getType(cp).let {
                it == Character.DECIMAL_DIGIT_NUMBER.toInt() || it == Character.LETTER_NUMBER.toInt() ||
                    it == Character.OTHER_NUMBER.toInt()
            } -> 'N'
            else -> 'P'
        }

        /** Contractions, runs of letters, single digits, and runs of anything else; spaces dropped. */
        fun words(text: String): List<String> {
            val out = mutableListOf<String>()
            var i = 0
            val n = text.length
            while (i < n) {
                val c = CONTRACTIONS.firstOrNull { text.startsWith(it, i) }
                if (c != null) { out.add(c); i += c.length; continue }
                val cp = text.codePointAt(i)
                val k = kind(cp)
                val step = Character.charCount(cp)
                if (k == ' ') { i += step; continue }
                if (k == 'N') { out.add(text.substring(i, i + step)); i += step; continue }
                var j = i + step
                while (j < n && kind(text.codePointAt(j)) == k) j += Character.charCount(text.codePointAt(j))
                out.add(text.substring(i, j))
                i = j
            }
            return out
        }
    }
}

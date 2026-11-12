package com.homepix.mobile

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import java.nio.LongBuffer
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sqrt

/** Search by description, the way HomePix does: the phrase becomes a vector, compared with every photo's. */
class Search(context: Context) {
    private val env = run {
        // ONNX Runtime would send usage data to Microsoft: off before it's loaded (and its starter is
        // taken out of the manifest)
        android.system.Os.setenv("ORT_DISABLE_TELEMETRY", "1", true)
        OrtEnvironment.getEnvironment().also { runCatching { it.setTelemetry(false) } }
    }
    private val session: OrtSession
    private val tokenizer: Tokenizer

    init {
        val assets = context.assets
        tokenizer = Tokenizer(assets.open("clip_tokenizer.json").bufferedReader().use { it.readText() })
        session = env.createSession(assets.open("clip_text.onnx").use { it.readBytes() }, OrtSession.SessionOptions())
    }

    private fun embed(text: String): FloatArray {
        var ids = tokenizer.encode(text)
        if (ids.size > 77) ids = ids.take(76) + ids.last() // keep the end-of-text token
        val padded = LongArray(77) { if (it < ids.size) ids[it].toLong() else 0L }
        OnnxTensor.createTensor(env, LongBuffer.wrap(padded), longArrayOf(1, 77)).use { input ->
            session.run(mapOf(session.inputNames.first() to input)).use { out ->
                @Suppress("UNCHECKED_CAST")
                return unit((out[0].value as Array<FloatArray>)[0])
            }
        }
    }

    /** The plain phrase blended with "a photo of ...": CLIP models search better that way. */
    fun query(text: String): FloatArray {
        val a = embed(text)
        val b = embed("a photo of $text")
        return unit(FloatArray(a.size) { a[it] + b[it] })
    }

    class Results(val searched: Int, val matches: List<Int>, val related: List<Int>)

    /** Items (indexes into lib.items) that match, best first. only: search just these items. */
    fun search(lib: Library, text: String, only: Set<Int>? = null): Results {
        val q = query(text)
        val v = lib.vectors
        val rows = (0 until v.item.size).filter { only == null || v.item[it] in only }
        val scores = FloatArray(rows.size) { i ->
            val off = rows[i] * Library.DIM
            var s = 0f
            for (k in 0 until Library.DIM) s += v.data[off + k] * q[k]
            s
        }
        val (clear, near) = splitMatches(scores)
        return Results(rows.size, clear.map { v.item[rows[it]] }, near.map { v.item[rows[it]] })
    }

    companion object {
        const val MATCH_STRICTNESS = 2.0f
        const val NEAR_STRICTNESS = 1.0f
        const val MAX_NEAR = 8

        fun unit(v: FloatArray): FloatArray {
            val n = sqrt(v.fold(0f) { s, x -> s + x * x }).takeIf { it > 0f } ?: 1f
            return FloatArray(v.size) { v[it] / n }
        }

        /** HomePix's split_matches: keep photos that score well above the typical one for this search. */
        fun splitMatches(scores: FloatArray): Pair<List<Int>, List<Int>> {
            val order = scores.indices.sortedByDescending { scores[it] }
            if (scores.size < 8) return order to emptyList()
            val typical = median(scores)
            val spread = max(1.4826f * median(FloatArray(scores.size) { abs(scores[it] - typical) }), 0.005f)
            val nClear = scores.count { it >= typical + MATCH_STRICTNESS * spread }
            val nNear = scores.count { it >= typical + NEAR_STRICTNESS * spread }
            return order.take(nClear) to order.subList(nClear, nNear).take(MAX_NEAR)
        }

        private fun median(v: FloatArray): Float {
            val s = v.sortedArray()
            val m = s.size / 2
            return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2f
        }
    }
}

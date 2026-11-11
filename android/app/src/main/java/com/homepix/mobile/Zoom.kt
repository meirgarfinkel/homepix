package com.homepix.mobile

import androidx.compose.animation.core.animate
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.geometry.lerp
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.toSize
import kotlin.math.max
import kotlin.math.min

/**
 * How far the photo on screen is zoomed in, and where to. Zoomed in, the photo can't be moved past its
 * edges, so once it's bigger than the screen it fills it, as in Google Photos.
 */
class ZoomState {
    var scale by mutableFloatStateOf(1f)
        private set
    var offset by mutableStateOf(Offset.Zero) // from the middle of the screen, in pixels
        private set
    private var box = Size.Zero
    private var photo = Size.Zero // the photo's own size in pixels, once it's loaded

    val zoomed get() = scale > 1.01f
    var fingers = 0 // the most fingers down in the touch going on now
        internal set

    fun reset() { scale = 1f; offset = Offset.Zero }

    fun photoSize(size: Size) { photo = if (size.isSpecified() && size.width > 0 && size.height > 0) size else Size.Zero }

    /** The photo's size on screen before zooming: fitted inside the screen. */
    private fun fitted(): Size {
        if (photo == Size.Zero || box == Size.Zero) return box
        val k = min(box.width / photo.width, box.height / photo.height)
        return Size(photo.width * k, photo.height * k)
    }

    private fun clamp(s: Float, t: Offset): Offset {
        val f = fitted()
        val mx = max(0f, (f.width * s - box.width) / 2)
        val my = max(0f, (f.height * s - box.height) / 2)
        return Offset(t.x.coerceIn(-mx, mx), t.y.coerceIn(-my, my))
    }

    /** Where the photo would sit at scale s, keeping the spot under `at` (a point on screen) in place. */
    private fun around(at: Offset, s: Float): Offset {
        val c = at - Offset(box.width / 2, box.height / 2)
        return c - (c - offset) * (s / scale)
    }

    fun transform(at: Offset, zoom: Float, pan: Offset) {
        val s = (scale * zoom).coerceIn(1f, MAX)
        offset = clamp(s, around(at, s) + pan)
        scale = s
    }

    /** A double tap: zoom in on the spot tapped, or back out. */
    suspend fun toggle(at: Offset) {
        val s0 = scale
        val t0 = offset
        val s1 = if (zoomed) 1f else DOUBLE_TAP
        val t1 = clamp(s1, around(at, s1))
        animate(0f, 1f) { v, _ ->
            scale = s0 + (s1 - s0) * v
            offset = lerp(t0, t1, v)
        }
    }

    internal fun boxSize(size: Size) { box = size }

    companion object {
        const val MAX = 8f
        const val DOUBLE_TAP = 2.5f
    }
}

private fun Size.isSpecified() = this != Size.Unspecified && !width.isNaN() && !height.isNaN()

/**
 * Pinching zooms; zoomed in, one finger moves the photo. Not zoomed in, one finger is left alone, for
 * swiping to the next photo or up to the details.
 */
fun Modifier.zoomable(z: ZoomState, key: Any, on: Boolean) = onSizeChanged { z.boxSize(it.toSize()) }.pointerInput(key, on) {
    if (!on) return@pointerInput
    awaitEachGesture {
        awaitFirstDown(requireUnconsumed = false)
        z.fingers = 1
        do {
            val e = awaitPointerEvent()
            val fingers = e.changes.count { it.pressed }
            z.fingers = max(z.fingers, fingers)
            if (fingers >= 2 || z.zoomed) {
                val zoom = e.calculateZoom()
                val pan = e.calculatePan()
                if (zoom != 1f || pan != Offset.Zero) {
                    z.transform(e.calculateCentroid(useCurrent = true), zoom, pan)
                    e.changes.forEach { if (it.positionChanged()) it.consume() }
                }
            }
        } while (e.changes.any { it.pressed })
        z.fingers = 0
    }
}

/** Draws what it's on zoomed as z says. */
fun Modifier.zoomed(z: ZoomState) = graphicsLayer {
    scaleX = z.scale
    scaleY = z.scale
    translationX = z.offset.x
    translationY = z.offset.y
}

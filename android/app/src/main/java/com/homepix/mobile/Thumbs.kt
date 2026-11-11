package com.homepix.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapRegionDecoder
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.drawable.BitmapDrawable
import android.media.ExifInterface
import android.media.ThumbnailUtils
import android.util.Size
import coil.ImageLoader
import coil.decode.DataSource
import coil.fetch.DrawableResult
import coil.fetch.FetchResult
import coil.fetch.Fetcher
import coil.key.Keyer
import coil.request.Options
import java.io.File
import java.security.MessageDigest
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * A picture of a photo or video for grids. size: its shorter side in pixels (the grid crops to squares).
 * turn: how far it was turned on this phone (Turns).
 */
data class Thumb(val file: File, val video: Boolean, val size: Int, val turn: Int = 0, val stamp: Long = file.lastModified())

/** A person's face, cut from one of their photos (turned as that photo was turned here). */
data class FaceThumb(val face: Face, val turn: Int = 0, val stamp: Long = face.file.lastModified())

const val FACE_SIZE = 384

private fun cacheFile(context: Context, key: String): File {
    val dir = File(context.cacheDir, "thumbs2").apply { mkdirs() }
    val hash = MessageDigest.getInstance("SHA-1").digest(key.toByteArray()).joinToString("") { "%02x".format(it) }
    return File(dir, "$hash.jpg")
}

/** Made once, then kept in the app's cache (keyed by the file and when it last changed). */
private fun cached(context: Context, key: String, build: () -> Bitmap): Bitmap {
    val f = cacheFile(context, key)
    BitmapFactory.decodeFile(f.path)?.let { return it }
    val bmp = build()
    val tmp = File(f.path + ".tmp")
    tmp.outputStream().use { bmp.compress(Bitmap.CompressFormat.JPEG, 90, it) }
    tmp.renameTo(f)
    return bmp
}

/** How far the photo is turned to be shown upright, in degrees clockwise. */
private fun rotation(file: File): Int = try {
    when (ExifInterface(file).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
        ExifInterface.ORIENTATION_ROTATE_90, ExifInterface.ORIENTATION_TRANSPOSE -> 90
        ExifInterface.ORIENTATION_ROTATE_180 -> 180
        ExifInterface.ORIENTATION_ROTATE_270, ExifInterface.ORIENTATION_TRANSVERSE -> 270
        else -> 0
    }
} catch (e: Exception) {
    0
}

/** The largest power of two to shrink by while keeping `have` at least `need` pixels. */
private fun sampleFor(have: Int, need: Int): Int {
    var s = 1
    while (have / (s * 2) >= need) s *= 2
    return s
}

private fun turned(bmp: Bitmap, degrees: Int): Bitmap =
    if (degrees == 0) bmp else Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(degrees.toFloat()) }, true)

/** Scaled so its shorter side is `side` pixels (never enlarged). */
private fun shortSide(bmp: Bitmap, side: Int): Bitmap {
    val s = min(bmp.width, bmp.height)
    if (s <= side) return bmp
    val k = side.toFloat() / s
    return Bitmap.createScaledBitmap(bmp, (bmp.width * k).roundToInt(), (bmp.height * k).roundToInt(), true)
}

/**
 * A video's first picture, at least `side` pixels on its shorter side: what it starts with, so the
 * viewer goes from this to the playing video without a jump (Android's own video thumbnail is a
 * picture from the middle).
 */
private fun firstFrame(file: File, side: Int): Bitmap {
    val r = android.media.MediaMetadataRetriever()
    try {
        r.setDataSource(file.path)
        val w = r.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
        val h = r.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
        val o = android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC
        val frame = if (w > 0 && h > 0 && min(w, h) > side) {
            // fits in the box, keeping its shape (a square, so a video stored on its side fits too)
            val big = (maxOf(w, h) * side.toFloat() / min(w, h)).roundToInt()
            r.getScaledFrameAtTime(0, o, big, big)
        } else r.getFrameAtTime(0, o)
        return frame ?: ThumbnailUtils.createVideoThumbnail(file, Size(side, side), null)
    } finally {
        r.release()
    }
}

/** The photo itself, decoded at the size needed (not the small preview cameras store inside it). */
private fun photoThumb(file: File, size: Int): Bitmap {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.path, bounds)
    if (bounds.outWidth <= 0) return ThumbnailUtils.createImageThumbnail(file, Size(size, size), null)
    val opts = BitmapFactory.Options().apply { inSampleSize = sampleFor(min(bounds.outWidth, bounds.outHeight), size) }
    val bmp = BitmapFactory.decodeFile(file.path, opts) ?: error("Can't read ${file.name}")
    return turned(shortSide(bmp, size), rotation(file))
}

class ThumbFetcher(private val data: Thumb, private val options: Options) : Fetcher {
    override suspend fun fetch(): FetchResult {
        val key = "${data.file.path}|${data.file.lastModified()}|${data.size}" + if (data.video) "|first" else ""
        val bmp = cached(options.context, key) {
            if (data.video) shortSide(firstFrame(data.file, data.size * 2), data.size)
            else photoThumb(data.file, data.size)
        }.let { turnedBitmap(it, data.turn) }
        return DrawableResult(BitmapDrawable(options.context.resources, bmp), isSampled = true, dataSource = DataSource.DISK)
    }

    class Factory : Fetcher.Factory<Thumb> {
        override fun create(data: Thumb, options: Options, imageLoader: ImageLoader) = ThumbFetcher(data, options)
    }
}

/**
 * Only the part of the photo around the face is decoded, at full resolution. The face's box is in the
 * upright photo, so it's turned into the stored (unrotated) photo's pixels first.
 */
private fun faceCrop(f: Face): Bitmap {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(f.file.path, bounds)
    val rawW = bounds.outWidth
    val rawH = bounds.outHeight
    if (rawW <= 0) error("Can't read ${f.file.name}")
    val deg = rotation(f.file)
    val (w, h) = if (deg == 90 || deg == 270) rawH to rawW else rawW to rawH // upright size
    // a square around the face, with room around it, in the upright photo
    val side = min(max(f.w * w, f.h * h) * 1.6f, min(w, h).toFloat())
    val cx = (f.x + f.w / 2) * w
    val cy = (f.y + f.h / 2) * h
    val l = (cx - side / 2).coerceIn(0f, w - side)
    val t = (cy - side / 2).coerceIn(0f, h - side)
    val r = l + side
    val b = t + side
    val raw = when (deg) {
        90 -> Rect(t.toInt(), (rawH - r).toInt(), b.toInt(), (rawH - l).toInt())
        180 -> Rect((rawW - r).toInt(), (rawH - b).toInt(), (rawW - l).toInt(), (rawH - t).toInt())
        270 -> Rect((rawW - b).toInt(), l.toInt(), (rawW - t).toInt(), r.toInt())
        else -> Rect(l.toInt(), t.toInt(), r.toInt(), b.toInt())
    }
    raw.intersect(0, 0, rawW, rawH)
    val opts = BitmapFactory.Options().apply { inSampleSize = sampleFor(min(raw.width(), raw.height()), FACE_SIZE) }
    val part = f.file.inputStream().use { BitmapRegionDecoder.newInstance(it)!!.decodeRegion(raw, opts) }
    return turned(shortSide(part, FACE_SIZE), deg)
}

class FaceFetcher(private val data: FaceThumb, private val options: Options) : Fetcher {
    override suspend fun fetch(): FetchResult {
        val f = data.face
        val key = "face|${f.file.path}|${f.file.lastModified()}|${f.x},${f.y},${f.w},${f.h}"
        val bmp = cached(options.context, key) { faceCrop(f) }.let { turnedBitmap(it, data.turn) }
        return DrawableResult(BitmapDrawable(options.context.resources, bmp), isSampled = true, dataSource = DataSource.DISK)
    }

    class Factory : Fetcher.Factory<FaceThumb> {
        override fun create(data: FaceThumb, options: Options, imageLoader: ImageLoader) = FaceFetcher(data, options)
    }
}

class ThumbKeyer : Keyer<Thumb> {
    override fun key(data: Thumb, options: Options) = "${data.file.path}|${data.size}|${data.turn}|${data.stamp}"
}

class FaceKeyer : Keyer<FaceThumb> {
    override fun key(data: FaceThumb, options: Options) =
        "face|${data.face.file.path}|${data.face.x},${data.face.y}|${data.turn}|${data.stamp}"
}

/** Removes thumbnails from before the app made them from the photos themselves. */
fun dropOldThumbs(context: Context) {
    File(context.cacheDir, "thumbs").deleteRecursively()
}

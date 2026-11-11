package com.homepix.mobile

import android.content.ContentUris
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.graphics.Matrix
import android.net.Uri
import android.media.ExifInterface
import android.media.MediaScannerConnection
import android.provider.MediaStore
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateSetOf
import coil.size.Size
import coil.transform.Transformation
import java.io.File

/**
 * How far the user turned each photo on this phone, in degrees clockwise, for the photos whose file
 * can't be turned (see turnInFile). Kept in the app.
 */
object Turns {
    private var prefs: SharedPreferences? = null
    private val turns = mutableStateMapOf<String, Int>()

    fun load(context: Context) {
        val p = context.getSharedPreferences("turns", Context.MODE_PRIVATE)
        prefs = p
        for ((k, v) in p.all) if (v is Int) turns[k] = v
    }

    fun of(file: File): Int = turns[file.path] ?: 0

    /** A quarter turn to the left, the way Google Photos turns. */
    fun turnLeft(file: File) {
        val d = (of(file) + 270) % 360
        if (d == 0) turns.remove(file.path) else turns[file.path] = d
        prefs?.edit()?.apply { if (d == 0) remove(file.path) else putInt(file.path, d) }?.apply()
    }
}

/**
 * The fingerprint each photo turned here had before (the one the library knows it by until the
 * computer has the turned file), so it's still found on the phone in between.
 */
object Turned {
    private fun prefs(context: Context) = context.getSharedPreferences("turned", Context.MODE_PRIVATE)

    fun remember(context: Context, qhash: String, file: File) {
        val p = prefs(context)
        // turned twice: keep the first fingerprint, the one the library has
        if (p.all.values.none { it == file.path }) p.edit().putString(qhash, file.path).apply()
    }

    fun all(context: Context): Map<String, String> = prefs(context).all.mapNotNull { (k, v) -> (v as? String)?.let { k to it } }.toMap()
}

/** Goes up each time a photo's file is changed here, so the pictures of it are made again (changed on the main thread). */
object Edits {
    var version by mutableIntStateOf(0)
}

/**
 * A quarter turn to the left, written in the photo itself: only the orientation it's shown with
 * changes, not the picture (as Google Photos turns a photo). The phone's sync takes the changed file
 * to the computer, where HomePix updates the photo with it and keeps who's in it. False if this file
 * can't be changed so (a HEIC or a mirrored photo): then it's turned in the app only (Turns).
 */
fun turnInFile(context: Context, file: File): Boolean {
    if (file.extension.lowercase() !in setOf("jpg", "jpeg", "png", "webp")) return false
    return runCatching {
        val before = quickHash(file)
        val e = ExifInterface(file.path)
        val next = when (e.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
            ExifInterface.ORIENTATION_NORMAL, ExifInterface.ORIENTATION_UNDEFINED -> ExifInterface.ORIENTATION_ROTATE_270
            ExifInterface.ORIENTATION_ROTATE_270 -> ExifInterface.ORIENTATION_ROTATE_180
            ExifInterface.ORIENTATION_ROTATE_180 -> ExifInterface.ORIENTATION_ROTATE_90
            ExifInterface.ORIENTATION_ROTATE_90 -> ExifInterface.ORIENTATION_NORMAL
            else -> return false
        }
        e.setAttribute(ExifInterface.TAG_ORIENTATION, next.toString())
        e.saveAttributes()
        Turned.remember(context, before, file)
        MediaScannerConnection.scanFile(context, arrayOf(file.path), null, null) // (the gallery shows it turned too)
        true
    }.getOrDefault(false)
}

/** How far the photo's own file says to turn it when shown, in degrees clockwise (0 if it can't say). */
fun fileTurn(file: File): Int = runCatching {
    when (ExifInterface(file.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
        ExifInterface.ORIENTATION_ROTATE_90 -> 90
        ExifInterface.ORIENTATION_ROTATE_180 -> 180
        ExifInterface.ORIENTATION_ROTATE_270 -> 270
        else -> 0
    }
}.getOrDefault(0)

/** Photos deleted on this phone while the app is open: hidden at once, before the library is read again. */
object Gone {
    val files = mutableStateSetOf<File>()
}

fun turnedBitmap(bmp: Bitmap, degrees: Int): Bitmap =
    if (degrees % 360 == 0) bmp
    else Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(degrees.toFloat()) }, true)

/** For the full-size viewer: the photo turned the way it was turned here. */
class Turn(private val degrees: Int) : Transformation {
    override val cacheKey = "turn$degrees"
    override suspend fun transform(input: Bitmap, size: Size): Bitmap = turnedBitmap(input, degrees)
}

/** The phone's media library entry for a file (what Android's trash works with), or null if it has none. */
fun mediaUri(context: Context, file: File, video: Boolean): Uri? {
    context.contentResolver.query(
        MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL),
        arrayOf(MediaStore.Files.FileColumns._ID, MediaStore.Files.FileColumns.VOLUME_NAME),
        "${MediaStore.Files.FileColumns.DATA} = ?",
        arrayOf(file.path),
        null,
    )?.use { c ->
        if (!c.moveToFirst()) return null
        val volume = c.getString(1)
        val base = if (video) MediaStore.Video.Media.getContentUri(volume) else MediaStore.Images.Media.getContentUri(volume)
        return ContentUris.withAppendedId(base, c.getLong(0))
    }
    return null
}

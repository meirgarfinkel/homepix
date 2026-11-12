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
 * computer has the turned file), so it's still found on the phone in between. Kept by the photo's path;
 * let go of once the library knows the turned file (see [Library.load]).
 */
object Turned {
    private fun prefs(context: Context) = context.getSharedPreferences("turned-files", Context.MODE_PRIVATE)

    /** Up to 0.7 they were kept the other way round (fingerprint -> path), and never let go of. */
    private fun moveOld(context: Context) {
        val old = context.getSharedPreferences("turned", Context.MODE_PRIVATE)
        if (old.all.isEmpty()) return
        val now = prefs(context).edit()
        for ((q, path) in old.all) if (path is String) now.putString(path, q)
        now.commit()
        context.deleteSharedPreferences("turned")
    }

    fun remember(context: Context, qhash: String, file: File) {
        val p = prefs(context)
        val all = HashMap(all(context))
        if (rememberTurn(all, file.path, qhash)) p.edit().putString(file.path, qhash).apply()
    }

    /** path -> the fingerprint the library knows that photo by. */
    fun all(context: Context): Map<String, String> {
        moveOld(context)
        return prefs(context).all.mapNotNull { (k, v) -> (v as? String)?.let { k to it } }.toMap()
    }

    /** The library now knows these files as they are (or they're gone): no need to remember them. */
    fun forget(context: Context, paths: Collection<String>) {
        if (paths.isEmpty()) return
        prefs(context).edit().apply { paths.forEach { remove(it) } }.apply()
    }
}

/**
 * Notes that the photo at path had fingerprint qhash before it was turned. A photo turned again before
 * the library caught up keeps its first one, the one the library has. True if it was noted.
 */
fun rememberTurn(turned: MutableMap<String, String>, path: String, qhash: String): Boolean {
    if (path in turned) return false
    turned[path] = qhash
    return true
}

/** The turned photos to let go of: the library found them by their own fingerprint, or they're gone. */
fun caughtUp(turned: Map<String, String>, found: Set<String>, exists: (String) -> Boolean): List<String> =
    turned.keys.filter { it in found || !exists(it) }

/** Goes up each time a photo's file is changed here, so the pictures of it are made again (changed on the main thread). */
object Edits {
    var version by mutableIntStateOf(0)
}

/**
 * A quarter turn to the left, written in the photo itself: only the orientation it's shown with
 * changes, not the picture (as Google Photos turns a photo). Sending takes the changed file to the
 * computer, where HomePix updates the photo with it and keeps who's in it. False if this file can't be
 * changed so (not a JPEG, or a mirrored photo): then it's turned in the app only (Turns).
 */
fun turnInFile(context: Context, file: File): Boolean {
    if (file.extension.lowercase() !in TURN_IN_FILE) return false
    return runCatching {
        val before = quickHash(file)
        val turned = replaceSafely(file) { copy ->
            val e = ExifInterface(copy.path)
            val next = when (e.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                ExifInterface.ORIENTATION_NORMAL, ExifInterface.ORIENTATION_UNDEFINED -> ExifInterface.ORIENTATION_ROTATE_270
                ExifInterface.ORIENTATION_ROTATE_270 -> ExifInterface.ORIENTATION_ROTATE_180
                ExifInterface.ORIENTATION_ROTATE_180 -> ExifInterface.ORIENTATION_ROTATE_90
                ExifInterface.ORIENTATION_ROTATE_90 -> ExifInterface.ORIENTATION_NORMAL
                else -> return@replaceSafely false
            }
            e.setAttribute(ExifInterface.TAG_ORIENTATION, next.toString())
            e.saveAttributes()
            true
        }
        if (!turned) return false
        Turned.remember(context, before, file)
        MediaScannerConnection.scanFile(context, arrayOf(file.path), null, null) // (the gallery shows it turned too)
        true
    }.getOrDefault(false)
}

/**
 * Only JPEGs are turned in the file: the viewer shows other kinds (PNG, WebP) the way their pixels are,
 * whatever their file says, so they're turned in the app.
 */
val TURN_IN_FILE = setOf("jpg", "jpeg")

/**
 * Changes a copy of file (change: false to leave it as it is) and puts the copy in its place in one
 * step, so the photo is never half written: a phone switched off part way still has the original. The
 * copy is hidden (a name starting with ".") in the same folder, so nothing sends or shows it. The file's
 * time is kept, a second on, so it's still seen as changed but keeps the day it was taken.
 */
fun replaceSafely(file: File, change: (File) -> Boolean): Boolean {
    val time = file.lastModified()
    val copy = File(file.parentFile, ".${file.name}.homepix")
    try {
        file.copyTo(copy, overwrite = true)
        if (!change(copy)) return false
        java.io.RandomAccessFile(copy, "rw").use { it.fd.sync() } // (on the disk before it replaces the photo)
        if (time > 0) copy.setLastModified(time + 1000)
        if (!copy.renameTo(file)) throw java.io.IOException("Couldn't put the changed ${file.name} in place.")
        return true
    } finally {
        copy.delete() // (gone already when it took the photo's place)
    }
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

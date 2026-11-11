package com.homepix.mobile

import android.content.Context
import android.provider.MediaStore
import java.io.File
import java.io.RandomAccessFile
import java.security.MessageDigest

/**
 * HomePix's quick fingerprint (util.quick_hash on the computer): the file's size and its first and
 * last 64 KB. Moving or renaming a photo doesn't change it, so a photo filed on the computer can be
 * matched to the original still on the phone.
 */
fun quickHash(file: File): String {
    val size = file.length()
    val sha = MessageDigest.getInstance("SHA-1")
    sha.update(size.toString().toByteArray())
    RandomAccessFile(file, "r").use { f ->
        fun part(from: Long) {
            val buf = ByteArray(minOf(65536L, size - from).toInt())
            f.seek(from)
            f.readFully(buf)
            sha.update(buf)
        }
        part(0)
        if (size > 131072) part(size - 65536)
    }
    return sha.digest().joinToString("") { "%02x".format(it) }
}

/**
 * The photos and videos on the phone (the ones its gallery knows of), by size, and their fingerprints,
 * made only for files whose size a library photo has, and kept from one start to the next.
 */
class PhonePhotos(context: Context) {
    private val bySize = HashMap<Long, MutableList<File>>()
    private val byName = HashMap<String, MutableList<File>>()
    private val cacheFile = File(context.cacheDir, "fingerprints.tsv")
    private val known = HashMap<String, String>() // "path|size|modified" -> fingerprint
    private val used = HashMap<String, String>()
    private val claimed = HashSet<File>()
    private val turned = Turned.all(context) // fingerprint before turning -> file

    init {
        val uri = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL)
        val types = "${MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE},${MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO}"
        context.contentResolver.query(
            uri,
            arrayOf(MediaStore.Files.FileColumns.DATA, MediaStore.Files.FileColumns.SIZE),
            "${MediaStore.Files.FileColumns.MEDIA_TYPE} IN ($types)",
            null,
            null,
        )?.use { c ->
            while (c.moveToNext()) {
                val path = c.getString(0) ?: continue
                val f = File(path)
                bySize.getOrPut(c.getLong(1)) { mutableListOf() } += f
                byName.getOrPut(f.name.lowercase()) { mutableListOf() } += f
            }
        }
        if (cacheFile.isFile) {
            cacheFile.forEachLine { line ->
                val i = line.lastIndexOf('\t')
                if (i > 0) known[line.substring(0, i)] = line.substring(i + 1)
            }
        }
    }

    val count: Int get() = bySize.values.sumOf { it.size }

    /** A phone file with this size and fingerprint that no other photo has taken yet, or null. */
    fun find(size: Long, qhash: String): File? {
        val same = bySize[size] ?: return null
        for (f in same) {
            if (f in claimed) continue
            val key = "${f.path}|${f.length()}|${f.lastModified()}"
            val h = known[key] ?: runCatching { quickHash(f) }.getOrNull() ?: continue
            used[key] = h
            if (h == qhash) {
                claimed += f
                return f
            }
        }
        return null
    }

    /** A photo turned in this app since the library last saw it, by the fingerprint it had before. */
    fun turnedHere(qhash: String): File? {
        val f = turned[qhash]?.let(::File)?.takeIf { it.isFile && it !in claimed } ?: return null
        claimed += f
        return f
    }

    /**
     * The phone file a photo came from, by its place in the folder the phone syncs to the computer
     * ("Camera/PXL_1.jpg"): for a photo whose file has changed since (turned here, say), so its
     * fingerprint no longer matches.
     */
    fun cameFrom(source: String): File? {
        val tail = "/" + source.trimStart('/')
        val f = byName[source.substringAfterLast('/').lowercase()]
            ?.firstOrNull { it !in claimed && it.path.endsWith(tail, ignoreCase = true) && it.isFile } ?: return null
        claimed += f
        return f
    }

    /** Keeps the fingerprints made this time (only those of files still on the phone). */
    fun save() {
        val tmp = File(cacheFile.path + ".tmp")
        tmp.bufferedWriter().use { w -> for ((k, v) in used) w.write("$k\t$v\n") }
        tmp.renameTo(cacheFile)
    }
}

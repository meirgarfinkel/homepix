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
 * The quick fingerprints made of the phone's files, kept from one start to the next ("path|size|modified"
 * -> fingerprint), for the library screen and for sending alike.
 */
object Fingerprints {
    private val known = HashMap<String, String>()
    private var loaded = false

    private fun cacheFile(context: Context) = File(context.cacheDir, "fingerprints.tsv")

    private fun load(context: Context) {
        if (loaded) return
        loaded = true
        val f = cacheFile(context)
        if (f.isFile) runCatching {
            f.forEachLine { line ->
                val i = line.lastIndexOf('\t')
                if (i > 0) known[line.substring(0, i)] = line.substring(i + 1)
            }
        }
    }

    fun key(f: File) = "${f.path}|${f.length()}|${f.lastModified()}"

    /** The file's quick fingerprint (made now if it's new or changed), or null if it can't be read. */
    fun of(context: Context, f: File): String? {
        val key = key(f)
        synchronized(this) {
            load(context)
            known[key]?.let { return it }
        }
        val h = runCatching { quickHash(f) }.getOrNull() ?: return null
        synchronized(this) { known[key] = h }
        return h
    }

    /** Keeps them on the phone; keep: the keys of files still here, or null to keep them all. */
    fun save(context: Context, keep: Set<String>? = null) {
        val lines = synchronized(this) {
            if (keep != null) known.keys.retainAll(keep)
            known.map { (k, v) -> "$k\t$v\n" }
        }
        val dst = cacheFile(context)
        val tmp = File.createTempFile("fingerprints", ".tmp", context.cacheDir) // (one of its own: two may save at once)
        runCatching {
            tmp.bufferedWriter().use { w -> lines.forEach(w::write) }
            if (!tmp.renameTo(dst)) tmp.delete()
        }.onFailure { tmp.delete() }
    }
}

/**
 * The photos and videos on the phone (the ones its gallery knows of), by size, and their fingerprints,
 * made only for files whose size a library photo has (see [Fingerprints]).
 */
class PhonePhotos(private val context: Context) {
    private val bySize = HashMap<Long, MutableList<File>>()
    private val byName = HashMap<String, MutableList<File>>()
    private val claimed = HashSet<File>()
    private val turned = Turned.all(context) // file -> its fingerprint before it was turned here
    private val turnedFrom = turned.entries.associate { (path, q) -> q to path }
    private val found = HashSet<String>() // the files found by their own fingerprint

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
    }

    val count: Int get() = bySize.values.sumOf { it.size }

    /** A phone file with this size and fingerprint that no other photo has taken yet, or null. */
    fun find(size: Long, qhash: String): File? {
        val same = bySize[size] ?: return null
        for (f in same) {
            if (f in claimed) continue
            val h = Fingerprints.of(context, f) ?: continue
            if (h == qhash) {
                claimed += f
                found += f.path
                return f
            }
        }
        return null
    }

    /** A photo turned in this app since the library last saw it, by the fingerprint it had before. */
    fun turnedHere(qhash: String): File? {
        val f = turnedFrom[qhash]?.let(::File)?.takeIf { it.isFile && it !in claimed } ?: return null
        claimed += f
        return f
    }

    /**
     * The phone file a photo came from, by its place in DCIM, where the phone sends from
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

    /**
     * Keeps the fingerprints made this time, and lets go of the photos turned here that the library now
     * knows as they are.
     */
    fun save() {
        Fingerprints.save(context)
        Turned.forget(context, caughtUp(turned, found) { File(it).isFile })
    }
}

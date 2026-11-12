package com.homepix.mobile

import java.io.File

/** The most the thumbnails and the motion photos' clips may take in the app's cache. */
const val THUMBS_CAP = 500L * 1024 * 1024
const val CLIPS_CAP = 200L * 1024 * 1024

/**
 * Deletes the oldest files in dir until what's left takes at most cap bytes (they're made again when
 * needed). Files made in the last minute are left, since one may still be in use.
 */
fun prune(dir: File, cap: Long, now: Long = System.currentTimeMillis()) {
    val files = dir.listFiles()?.filter { it.isFile } ?: return
    var total = files.sumOf { it.length() }
    if (total <= cap) return
    for (f in files.sortedBy { it.lastModified() }) {
        if (total <= cap) break
        if (now - f.lastModified() < 60_000) continue
        val n = f.length()
        if (f.delete()) total -= n
    }
}

/** Writes a file whole under a name of its own, then puts it in place (two writers never mix). */
fun writeWhole(dst: File, write: (File) -> Unit) {
    val tmp = File.createTempFile(dst.name, ".tmp", dst.parentFile)
    try {
        write(tmp)
        if (!tmp.renameTo(dst)) throw java.io.IOException("Couldn't save ${dst.name}.")
    } finally {
        tmp.delete()
    }
}

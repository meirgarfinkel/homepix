package com.homepix.mobile

import android.annotation.SuppressLint
import android.database.sqlite.SQLiteDatabase
import android.os.Environment
import android.util.Half
import org.json.JSONArray
import java.io.File
import kotlinx.coroutines.sync.withLock
import kotlin.math.max

/**
 * One photo or video in the timeline, with what HomePix knows about it: its size in pixels, the
 * description and keywords written in it, and who's in it.
 */
class Item(
    val file: File,
    val taken: String,
    val kind: String,
    val duration: Double?,
    val width: Int? = null,
    val height: Int? = null,
    val description: String? = null,
    val keywords: List<String> = emptyList(),
    private val moment: String? = null,
) {
    /**
     * When its file last changed, read with the library (not while it's shown: asking the phone's storage
     * on the screen's thread can wait seconds while it's busy, sending videos say). Read again when it's
     * turned here.
     */
    var stamp: Long = 0L

    val month: String get() = taken.take(7)

    /**
     * The time where it was taken. HomePix dates some photos by UTC (as a phone names them), and keeps
     * the local time beside that.
     */
    val local: String get() = moment ?: taken
    var people: List<String> = emptyList()
        internal set
}

/** Where a person's face is in one photo: the box as parts of the shown photo. */
class Face(val file: File, val x: Float, val y: Float, val w: Float, val h: Float, val stamp: Long)

class Person(val name: String, val items: List<Int>, val face: Face?)

/** Search vectors: 512 numbers for each item in item (indexes into Library.items), in that order. */
class Vectors(val data: FloatArray, val item: IntArray)

/**
 * Everything the phone shows, read from each library folder's portable copy (.homepix/library.db),
 * which HomePix on the computer writes and which comes along when the folder is copied.
 * items: newest first, the way HomePix's timeline orders them.
 */
class Library(
    val roots: List<File>,
    val items: List<Item>,
    val people: List<Person>,
    readVectors: () -> Vectors,
    private val watched: List<File>, // each library's copy, and the folders its photos are in
    private val stamp: String,
) {
    /**
     * The search vectors: read after the timeline shows, since only search needs them and they're most
     * of what's read. Asking before they're read waits for them.
     */
    private val vecs = lazy(LazyThreadSafetyMode.SYNCHRONIZED, readVectors)
    val vectors: Vectors get() = vecs.value
    val vectorsReady: Boolean get() = vecs.isInitialized()

    /** Has a library's copy, or a folder of photos, changed since this was read (new details came, say)? */
    fun changed(): Boolean = stampOf(watched) != stamp

    companion object {
        const val DIM = 512
        private const val COPY = ".homepix/library.db"

        /** One read of the library at a time: two at once would write the same copies in the app's storage. */
        private val reading = kotlinx.coroutines.sync.Mutex()

        /** [load] on a background thread, after any read already under way. */
        suspend fun read(context: android.content.Context, roots: List<File>, say: (String) -> Unit = {}): Library =
            reading.withLock { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { load(context, roots, say) } }

        private fun stampOf(files: List<File>) = files.joinToString("|") { "${it.lastModified()}:${it.length()}" }

        /**
         * Where the library's details are: from the paired computer when there is one (its file of this
         * phone's photos), else library folders on the phone holding a portable copy, up to 3 folders down.
         */
        fun find(context: android.content.Context): List<File> {
            val fromComputer = computerLibrary(context)
            if (Computer.load(context) != null) return if (fromComputer.isFile) listOf(fromComputer.parentFile!!.parentFile!!) else emptyList()
            val tops = mutableListOf(Environment.getExternalStorageDirectory())
            File("/storage").listFiles()?.filter { it.name != "emulated" && it.name != "self" && it.canRead() }
                ?.let { tops += it }
            val found = mutableListOf<File>()
            fun walk(dir: File, depth: Int) {
                if (File(dir, COPY).isFile) { found += dir; return }
                if (depth == 0) return
                dir.listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") && it.name != "Android" }
                    ?.sortedBy { it.name }?.forEach { walk(it, depth - 1) }
            }
            tops.forEach { walk(it, 3) }
            return found
        }

        /**
         * Reads every library folder's copy. A photo is shown from the library folder if it's there, or
         * else from wherever the same file is on the phone (the original in the camera folder, matched
         * by fingerprint), so only the small library file has to come to the phone. say: progress for the
         * loading screen. Throws [LibraryUnreadable] for a copy this app can't read.
         */
        fun load(context: android.content.Context, roots: List<File>, say: (String) -> Unit): Library {
            val cacheDir = context.cacheDir
            say("Looking at the photos on this phone…")
            val phone = PhonePhotos(context)
            android.util.Log.i("HomePix", "phone has ${phone.count} photos and videos")
            val all = mutableListOf<Item>()
            val ids = mutableListOf<Pair<File, Map<Long, Item>>>() // each copy, and its items by id
            val faces = HashMap<String, MutableMap<Item, Face?>>() // person (lowercased) -> their photos
            val names = HashMap<String, String>()
            val best = HashMap<String, Float>() // the face size of the face picked to show for each person
            val here = HashMap<File, Set<String>>() // each folder's files, listed once (quicker than a check per file)
            val folders = Folders(cacheDir)
            val matched = HashSet<File>() // the phone's folders holding photos found by fingerprint
            for ((n, root) in roots.withIndex()) {
                say("Reading ${root.name}…")
                val t0 = System.currentTimeMillis()
                val local = localCopy(root, cacheDir, n)
                android.util.Log.i("HomePix", "copy ${System.currentTimeMillis() - t0} ms")
                val db = SQLiteDatabase.openDatabase(local.path, null, SQLiteDatabase.OPEN_READONLY)
                try {
                    check(db)
                    val excluded = excluded(db)
                    val from = cameFrom(db)
                    val byId = HashMap<Long, Item>()
                    var after = -1L
                    while (true) {
                        val got = db.rawQuery(
                            "SELECT id, path, taken, kind, duration, NULL, size, qhash, width, height, description, keywords, moment " +
                                "FROM photos WHERE id > ? " +
                                "AND error IS NULL AND qhash IS NOT NULL AND kind != 'motion' ORDER BY id LIMIT 1000",
                            arrayOf(after.toString()),
                        ).use { c ->
                            var rows = 0
                            while (c.moveToNext()) {
                                rows++
                                after = c.getLong(0)
                                val path = c.getString(1)
                                if (excluded.any { path.startsWith("$it/") }) continue
                                var file = File(root, path)
                                val dir = file.parentFile!!
                                if (file.name !in here.getOrPut(dir) { folders.names(dir) }) {
                                    // not in the library folder: the same file elsewhere on the phone, or left out
                                    file = (if (c.isNull(6)) null else phone.find(c.getLong(6), c.getString(7)))
                                        ?: phone.turnedHere(c.getString(7))
                                        ?: from[path]?.let { phone.cameFrom(it) } ?: continue
                                    matched += file.parentFile!!
                                }
                                val item = Item(
                                    file,
                                    c.getString(2) ?: "0000-00-00 00:00:00",
                                    c.getString(3) ?: "photo",
                                    if (c.isNull(4)) null else c.getDouble(4),
                                    if (c.isNull(8)) null else c.getInt(8),
                                    if (c.isNull(9)) null else c.getInt(9),
                                    c.getString(10)?.takeIf { it.isNotBlank() },
                                    keywordList(c.getString(11)),
                                    c.getString(12),
                                )
                                item.stamp = file.lastModified()
                                byId[c.getLong(0)] = item
                                all += item
                                if (all.size % 2000 == 0) say("Reading ${root.name}… ${all.size} photos")
                            }
                            rows
                        }
                        if (got == 0) break
                    }
                    ids += local to byId
                    db.rawQuery(
                        "SELECT p.name, f.photo_id, f.x, f.y, f.w, f.h, f.px FROM faces f " +
                            "JOIN people p ON p.id = f.person_id WHERE f.hidden = 0",
                        null,
                    ).use { c ->
                        while (c.moveToNext()) {
                            val item = byId[c.getLong(1)] ?: continue
                            val key = c.getString(0).lowercase()
                            names.putIfAbsent(key, c.getString(0))
                            val face = Face(item.file, c.getFloat(2), c.getFloat(3), c.getFloat(4), c.getFloat(5), item.stamp)
                            val size = if (c.isNull(6)) 0f else c.getFloat(6)
                            val theirs = faces.getOrPut(key) { LinkedHashMap() }
                            if (item !in theirs) theirs[item] = null
                            if (item.kind == "photo" && size > (best[key] ?: -1f)) {
                                best[key] = size
                                theirs[item] = face
                            }
                        }
                    }
                } finally {
                    db.close()
                }
            }
            phone.save()
            folders.save()
            for ((key, theirs) in faces) for (item in theirs.keys) item.people += names.getValue(key)
            android.util.Log.i("HomePix", "read ${all.size} photos, ${here.size} folders listed, ${matched.size} phone folders matched")
            say("Putting ${all.size} photos in order…")
            // newest first across all folders (each folder's list already is)
            val order = all.withIndex().sortedWith(
                compareByDescending<IndexedValue<Item>> { it.value.taken }.thenByDescending { it.value.file.path },
            )
            val items = order.map { it.value }
            val index = HashMap<Item, Int>(items.size * 2).apply { items.forEachIndexed { i, it -> put(it, i) } }
            val people = faces.map { (key, theirs) ->
                Person(names.getValue(key), theirs.keys.map { index.getValue(it) }.sorted(), theirs.values.firstOrNull { it != null })
            }.sortedByDescending { it.items.size }
            val watched = roots.map { File(it, COPY) } + here.keys + matched
            return Library(roots, items, people, { readVectors(ids, index) }, watched, stampOf(watched))
        }

        /** Each item's search vector, from the copies the items were read from. */
        private fun readVectors(ids: List<Pair<File, Map<Long, Item>>>, index: Map<Item, Int>): Vectors {
            val t0 = System.currentTimeMillis()
            val data = ArrayList<FloatArray>()
            val item = ArrayList<Int>()
            for ((local, byId) in ids) try {
                SQLiteDatabase.openDatabase(local.path, null, SQLiteDatabase.OPEN_READONLY).use { db ->
                    // In batches by id: Android re-runs a query for each 2 MB of results, so one big
                    // query (about 1 KB of search vector per photo) would be read over and over.
                    var after = -1L
                    while (true) {
                        val got = db.rawQuery(
                            "SELECT id, clip FROM photos WHERE id > ? AND clip IS NOT NULL ORDER BY id LIMIT 1000",
                            arrayOf(after.toString()),
                        ).use { c ->
                            var rows = 0
                            while (c.moveToNext()) {
                                rows++
                                after = c.getLong(0)
                                val it = byId[after] ?: continue
                                data += halfs(c.getBlob(1))
                                item += index.getValue(it)
                            }
                            rows
                        }
                        if (got == 0) break
                    }
                }
            } catch (e: Exception) {
                // a copy replaced by a newer one while this was read: that library is read again anyway
                android.util.Log.w("HomePix", "search vectors: $e")
            }
            val vectors = FloatArray(data.size * DIM)
            data.forEachIndexed { i, v -> System.arraycopy(v, 0, vectors, i * DIM, DIM) }
            android.util.Log.i("HomePix", "search vectors for ${data.size} photos in ${System.currentTimeMillis() - t0} ms")
            return Vectors(vectors, item.toIntArray())
        }

        /** The copy is read from the app's own storage, so the one in the library is never changed or locked. */
        private fun localCopy(root: File, cacheDir: File, n: Int): File {
            val src = File(root, COPY)
            val dst = File(cacheDir, "library-$n.db")
            val stamp = File(cacheDir, "library-$n.stamp")
            val want = "${src.path}|${src.length()}|${src.lastModified()}"
            if (!dst.exists() || !stamp.exists() || stamp.readText() != want) {
                stamp.delete()
                // whole, under its own name first: an older library still reading its search data keeps the old one
                writeWhole(dst) { src.copyTo(it, overwrite = true) }
                stamp.writeText(want)
            }
            return dst
        }

        private fun check(db: SQLiteDatabase) {
            val missing = LIBRARY_NEEDS.flatMap { (table, cols) ->
                val have = db.rawQuery("PRAGMA table_info($table)", null).use { c ->
                    HashSet<String>().apply { while (c.moveToNext()) add(c.getString(1)) }
                }
                cols.filter { it !in have }.map { "$table.$it" }
            }
            val version = db.rawQuery("PRAGMA user_version", null).use { c -> if (c.moveToFirst()) c.getInt(0) else 0 }
            libraryProblem(version, missing)?.let { throw LibraryUnreadable(it) }
        }

        /**
         * Where each photo that came from a phone was in the folder that phone syncs (HomePix 1.9 and
         * later): {path in the library: "Camera/PXL_1.jpg"}.
         */
        private fun cameFrom(db: SQLiteDatabase): Map<String, String> = try {
            db.rawQuery("SELECT path, source FROM came_from", null).use { c ->
                HashMap<String, String>().apply { while (c.moveToNext()) put(c.getString(0), c.getString(1)) }
            }
        } catch (e: Exception) {
            emptyMap() // an older copy: none
        }

        /** The folders left out of the library on the computer (paths in the library folder). */
        private fun excluded(db: SQLiteDatabase): List<String> = try {
            db.rawQuery("SELECT value FROM portable WHERE key = 'excluded'", null).use { c ->
                if (!c.moveToFirst()) emptyList()
                else JSONArray(c.getString(0)).let { a -> List(a.length()) { a.getString(it).trimEnd('/') } }
            }
        } catch (e: Exception) {
            emptyList()
        }

        /** Keywords are kept as a JSON list. */
        private fun keywordList(json: String?): List<String> = try {
            if (json.isNullOrBlank()) emptyList() else JSONArray(json).let { a -> List(a.length()) { a.getString(it) } }
        } catch (e: Exception) {
            emptyList()
        }

        @SuppressLint("HalfFloat") // (widened on purpose: search works in floats)
        private fun halfs(b: ByteArray): FloatArray {
            val out = FloatArray(max(DIM, b.size / 2))
            for (i in 0 until b.size / 2) {
                val bits = ((b[2 * i + 1].toInt() and 0xff) shl 8) or (b[2 * i].toInt() and 0xff)
                out[i] = Half.toFloat(bits.toShort())
            }
            return out
        }
    }
}

/**
 * The files in each library folder, kept from one start to the next: listing a folder of photos takes
 * a tenth of a second or more, and a folder only has to be listed again when its time has changed
 * (adding, removing or renaming a file in it does that).
 */
private class Folders(cacheDir: File) {
    private val cacheFile = File(cacheDir, "folders.tsv")
    private val kept = HashMap<String, Pair<Long, Set<String>>>() // path -> (time, file names)
    private val now = HashMap<String, Pair<Long, Set<String>>>()

    init {
        runCatching {
            cacheFile.forEachLine { line ->
                val parts = line.split('\t', limit = 3)
                if (parts.size == 3) {
                    kept[parts[0]] = parts[1].toLong() to
                        (if (parts[2].isEmpty()) emptySet() else parts[2].split('/').toHashSet())
                }
            }
        }
    }

    fun names(dir: File): Set<String> {
        val time = dir.lastModified() // 0 when the folder isn't there
        kept[dir.path]?.let { (t, names) -> if (t == time && time != 0L) { now[dir.path] = t to names; return names } }
        val names = dir.list()?.toHashSet() ?: emptySet()
        // a folder changed in the last few seconds may change again within the same tick of its time
        if (time != 0L && System.currentTimeMillis() - time > 3000) now[dir.path] = time to names
        return names
    }

    /** Keeps the folders looked at this time (a file name can't hold a "/"). */
    fun save() = runCatching {
        if (now == kept) return@runCatching
        writeWhole(cacheFile) { tmp ->
            tmp.bufferedWriter().use { w ->
                for ((path, v) in now) if ('\t' !in path && '\n' !in path) {
                    w.write(path); w.write("\t"); w.write(v.first.toString()); w.write("\t")
                    w.write(v.second.joinToString("/")); w.write("\n")
                }
            }
        }
    }
}

/** A library copy this app can't read, with what to tell the user. */
class LibraryUnreadable(message: String) : Exception(message)

/** What this app reads from a library copy: anything missing, and it can't show the library. */
val LIBRARY_NEEDS = mapOf(
    "photos" to listOf(
        "id", "path", "taken", "kind", "duration", "size", "qhash", "width", "height", "description",
        "keywords", "moment", "error", "clip",
    ),
    "faces" to listOf("photo_id", "person_id", "x", "y", "w", "h", "px", "hidden"),
    "people" to listOf("id", "name"),
)

/** The newest library version this app was made with (homepix/core/db.py VERSION). */
const val KNOWN_VERSION = 15

/**
 * What to say about a library copy of this version missing these columns ("photos.moment"), or null
 * if it can be read. A newer version with every column still reads: most changes on the computer add.
 */
fun libraryProblem(version: Int, missing: List<String>): String? = when {
    missing.isEmpty() -> null
    version > KNOWN_VERSION -> "This library was saved by a newer HomePix than this app knows. Update the HomePix app on this phone."
    else -> "This library was saved by an older HomePix than this app reads. Update HomePix on the computer, then open it once."
}

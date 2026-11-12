package com.homepix.mobile

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.provider.MediaStore
import android.provider.Settings
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.time.Duration
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/*
 * Sending the phone's new photos and videos (everything in DCIM) to HomePix on the computer it's paired
 * with, over the home network: encrypted, trusting only the computer whose key was in the QR code.
 * The computer is asked first which files it still needs (by quick fingerprint, then by content for
 * the ones it doesn't have), so nothing it has is sent again, and only those are read whole. Deleting is never sent. Runs in the background whenever a photo is taken and every 15 minutes,
 * only on Wi-Fi (or another unmetered network).
 */

private const val SERVICE = "_homepix._tcp."
private const val CONNECT_MS = 3000
private const val BATCH = 20 // (asked about, and noted as done, a few at a time: a run cut short loses little)

/** The computer this phone is paired with, kept in the app's own private storage. */
data class Computer(
    val name: String,
    val addresses: List<String>,
    val port: Int,
    val fingerprint: ByteArray, // SHA-256 of the computer's certificate
    val token: String,
    val last: String = "", // the address that last worked
) {
    fun save(context: Context) {
        prefs(context).edit()
            .putString("name", name).putString("addresses", addresses.joinToString(","))
            .putInt("port", port).putString("fingerprint", fingerprint.hex())
            .putString("token", token).putString("last", last)
            .apply()
    }

    companion object {
        fun prefs(context: Context) = context.getSharedPreferences("computer", Context.MODE_PRIVATE)

        fun load(context: Context): Computer? {
            val p = prefs(context)
            val token = p.getString("token", null) ?: return null
            return Computer(
                p.getString("name", "") ?: "",
                (p.getString("addresses", "") ?: "").split(",").filter { it.isNotBlank() },
                p.getInt("port", 47820),
                (p.getString("fingerprint", "") ?: "").unhex(),
                token,
                p.getString("last", "") ?: "",
            )
        }

        fun forget(context: Context) {
            prefs(context).edit().clear().apply()
            SendState.prefs(context).edit().clear().apply()
            File(context.filesDir, "sent.tsv").delete()
            File(context.filesDir, "computer").deleteRecursively()
        }
    }
}

fun ByteArray.hex() = joinToString("") { "%02x".format(it) }
fun String.unhex() = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

/**
 * Is this a numeric address on a private network: home, office or hotel Wi-Fi (10.x, 172.16-31.x,
 * 192.168.x, IPv6 fc00::/7), link-local, or this phone? Never a web name, never a public address: the
 * phone only ever talks to a computer on the same network (the twin of the computer's phones.private).
 */
fun isPrivateAddress(host: String): Boolean {
    val h = host.removePrefix("[").removeSuffix("]").substringBefore('%')
    val v4 = Regex("^(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})$").find(h)
    if (v4 != null) {
        val b = v4.groupValues.drop(1).map { it.toInt() }
        if (b.any { it > 255 }) return false
        return b[0] == 10 || b[0] == 127 || (b[0] == 172 && b[1] in 16..31) || (b[0] == 192 && b[1] == 168) ||
            (b[0] == 169 && b[1] == 254)
    }
    if (!h.contains(':') || !h.all { it in '0'..'9' || it.lowercaseChar() in 'a'..'f' || it == ':' || it == '.' }) return false
    val a = runCatching { java.net.InetAddress.getByName(h) }.getOrNull() ?: return false // (a literal: no lookup)
    val b = a.address
    if (b.size == 4) return isPrivateAddress(a.hostAddress ?: return false) // (an IPv4-mapped address)
    return a.isLoopbackAddress || a.isLinkLocalAddress || (b[0].toInt() and 0xfe) == 0xfc
}

/** What the QR code on the computer holds (homepix:pair?a=...&p=...&f=...&c=...&n=...). */
data class PairCode(val addresses: List<String>, val port: Int, val fingerprint: ByteArray, val code: String, val name: String) {
    companion object {
        fun parse(text: String): PairCode? = runCatching {
            if (!text.startsWith("homepix:pair?")) return null
            val q = text.substringAfter('?').split('&').associate {
                val (k, v) = (it.split('=', limit = 2) + "").take(2)
                k to URLDecoder.decode(v, "UTF-8")
            }
            val f = Base64.getUrlDecoder().decode(q.getValue("f"))
            if (f.size != 32) return null
            PairCode(
                q["a"].orEmpty().split(",").filter { isPrivateAddress(it) }, // (anything else is never tried)
                q.getValue("p").toInt(), f, q.getValue("c"), q["n"].orEmpty(),
            )
        }.getOrNull()
    }
}

class Refused(val status: Int, message: String, val have: Long = -1) : IOException(message)

/** A connection to the paired computer at one address. */
class Link(private val host: String, private val port: Int, fingerprint: ByteArray, private val token: String) {
    private val ssl: SSLContext = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf(Pinned(fingerprint)), null)
    }

    private fun open(method: String, path: String): HttpsURLConnection {
        if (!isPrivateAddress(host)) throw IOException("$host isn't on this network, so nothing is sent to it.")
        val c = URL("https", host, port, path).openConnection() as HttpsURLConnection
        c.sslSocketFactory = ssl.socketFactory
        c.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true } // (the key itself is checked)
        c.connectTimeout = CONNECT_MS
        c.readTimeout = 60_000
        c.requestMethod = method
        if (token.isNotEmpty()) c.setRequestProperty("Authorization", "Bearer $token")
        return c
    }

    private fun answer(c: HttpURLConnection): JSONObject {
        val status = c.responseCode
        val text = (if (status < 400) c.inputStream else c.errorStream)?.use { it.readBytes().decodeToString() } ?: "{}"
        val body = runCatching { JSONObject(text) }.getOrElse { JSONObject() }
        if (status != 200) throw Refused(status, body.optString("error", "The computer said no ($status)."), body.optLong("have", -1))
        return body
    }

    fun post(path: String, body: JSONObject): JSONObject {
        val c = open("POST", path)
        val data = body.toString().toByteArray()
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.setFixedLengthStreamingMode(data.size)
        try {
            c.outputStream.use { it.write(data) }
            return answer(c)
        } finally {
            c.disconnect()
        }
    }

    /** Ask for the library's details about these files: written to dst when they've changed (true). */
    fun library(body: JSONObject, dst: File): String? {
        val c = open("POST", "/v1/library")
        val data = body.toString().toByteArray()
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.setFixedLengthStreamingMode(data.size)
        c.readTimeout = 120_000
        try {
            c.outputStream.use { it.write(data) }
            if (c.responseCode != 200 || c.contentType?.startsWith("application/json") == true) {
                answer(c) // (the same as before, or refused)
                return null
            }
            val tmp = File(dst.path + ".tmp")
            tmp.parentFile?.mkdirs()
            c.inputStream.use { input -> tmp.outputStream().use { input.copyTo(it, 1 shl 16) } }
            if (!tmp.renameTo(dst)) throw IOException("Couldn't keep the library's details.")
            return c.getHeaderField("X-Version")
        } finally {
            c.disconnect()
        }
    }

    /** Send a file from offset on; how much of it the computer has now (all of it: it's in place). */
    fun send(f: Local, sha: String, offset: Long): Long {
        val c = open("PUT", "/v1/file")
        c.doOutput = true
        c.setRequestProperty("X-Path", URLEncoder.encode(f.rel, "UTF-8").replace("+", "%20"))
        c.setRequestProperty("X-Size", f.size.toString())
        c.setRequestProperty("X-Mtime", f.modified.toString())
        c.setRequestProperty("X-Sha256", sha)
        c.setRequestProperty("X-Offset", offset.toString())
        c.setFixedLengthStreamingMode(f.size - offset)
        try {
            c.outputStream.use { out ->
                FileInputStream(f.file).use { input ->
                    input.channel.position(offset)
                    input.copyTo(out, 1 shl 16)
                }
            }
            return answer(c).getLong("have")
        } finally {
            c.disconnect()
        }
    }
}

/** Trusts only the certificate with this SHA-256: the paired computer's. */
private class Pinned(private val sha: ByteArray) : X509TrustManager {
    override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String?) {
        val got = MessageDigest.getInstance("SHA-256").digest(chain.first().encoded)
        if (!MessageDigest.isEqual(got, sha)) throw CertificateException("This isn't the computer this phone is paired with.")
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String?) =
        throw CertificateException("Not for clients.")

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

/** Is something answering at this address? (A quick look before a slower encrypted connection.) */
private fun answers(host: String, port: Int): Boolean = runCatching {
    Socket().use { it.connect(InetSocketAddress(host, port), CONNECT_MS); true }
}.getOrDefault(false)

/** Ask the network where the computer with this fingerprint is: (address, port), or null. */
@Suppress("DEPRECATION") // (resolveService: its replacement needs Android 14)
private fun discover(context: Context, fingerprint: ByteArray, waitMs: Long = 5000): Pair<String, Int>? {
    val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    val want = fingerprint.hex().take(16)
    val done = CountDownLatch(1)
    var found: Pair<String, Int>? = null
    val listener = object : NsdManager.DiscoveryListener {
        override fun onServiceFound(info: NsdServiceInfo) {
            nsd.resolveService(info, object : NsdManager.ResolveListener {
                override fun onServiceResolved(r: NsdServiceInfo) {
                    val f = r.attributes["f"]?.decodeToString()
                    val host = r.host?.hostAddress
                    if (f == want && host != null && found == null) {
                        found = host to r.port
                        done.countDown()
                    }
                }

                override fun onResolveFailed(info: NsdServiceInfo, error: Int) {}
            })
        }

        override fun onDiscoveryStarted(type: String) {}
        override fun onDiscoveryStopped(type: String) {}
        override fun onServiceLost(info: NsdServiceInfo) {}
        override fun onStartDiscoveryFailed(type: String, error: Int) { done.countDown() }
        override fun onStopDiscoveryFailed(type: String, error: Int) {}
    }
    nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, listener)
    done.await(waitMs, TimeUnit.MILLISECONDS)
    runCatching { nsd.stopServiceDiscovery(listener) }
    return found
}

/** Where the computer answers: the address that last worked, the QR code's, then asking the network. */
private fun reach(context: Context, addresses: List<String>, last: String, port: Int, fingerprint: ByteArray): Pair<String, Int>? {
    for (a in (listOf(last) + addresses).filter { isPrivateAddress(it) }.distinct()) {
        if (answers(a, port)) return a to port
    }
    return discover(context, fingerprint)?.takeIf { isPrivateAddress(it.first) && answers(it.first, it.second) }
}

/** Where the library's details from the paired computer are kept (read like a library folder's copy). */
fun computerLibrary(context: Context) = File(context.filesDir, "computer/.homepix/library.db")

fun phoneName(context: Context): String =
    Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME) ?: Build.MODEL

/** Pair with the computer whose QR code this is; the computer's name. Throws with a message to show. */
fun pair(context: Context, code: PairCode): String {
    Computer.load(context)?.let { throw IOException("This phone is paired with ${it.name}. Unpair it first.") }
    val (host, port) = reach(context, code.addresses, "", code.port, code.fingerprint)
        ?: throw IOException("Couldn't reach the computer. Is this phone on the same network as it (not a guest network)?")
    val answer = Link(host, port, code.fingerprint, "").post(
        "/v1/pair", JSONObject().put("code", code.code).put("name", phoneName(context)),
    )
    val computer = answer.optString("computer", code.name)
    Computer(computer, code.addresses, port, code.fingerprint, answer.getString("token"), host).save(context)
    Sending.schedule(context)
    Sending.now(context)
    return computer
}

/** A photo or video on the phone, and its place for the computer (Camera/IMG_1.jpg). */
data class Local(val file: File, val rel: String, val size: Long, val modified: Long) {
    val key get() = "$rel|$size|$modified"
}

/** Everything in DCIM that the gallery knows of and has finished writing. */
fun cameraFiles(context: Context): List<Local> {
    val out = ArrayList<Local>()
    val uri = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL)
    val types = "${MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE},${MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO}"
    context.contentResolver.query(
        uri,
        arrayOf(MediaStore.MediaColumns.DATA, MediaStore.MediaColumns.RELATIVE_PATH, MediaStore.MediaColumns.DISPLAY_NAME),
        "${MediaStore.Files.FileColumns.MEDIA_TYPE} IN ($types) AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE 'DCIM/%' " +
            "AND ${MediaStore.MediaColumns.IS_PENDING} = 0",
        null, null,
    )?.use { c ->
        while (c.moveToNext()) {
            val file = File(c.getString(0) ?: continue)
            val rel = (c.getString(1) ?: continue).removePrefix("DCIM/") + (c.getString(2) ?: continue)
            if (rel.split('/').any { it.isEmpty() || it.startsWith(".") }) continue
            val size = file.length()
            if (size <= 0) continue
            out += Local(file, rel, size, file.lastModified())
        }
    }
    return out
}

private fun sha256(f: File): String {
    val d = MessageDigest.getInstance("SHA-256")
    FileInputStream(f).use { input ->
        val buf = ByteArray(1 shl 20)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            d.update(buf, 0, n)
        }
    }
    return d.digest().hex()
}

/** How sending is going, for the app to show. */
object SendState {
    fun prefs(context: Context) = context.getSharedPreferences("sending", Context.MODE_PRIVATE)
    fun note(context: Context, vararg pairs: Pair<String, Any>) {
        val e = prefs(context).edit()
        for ((k, v) in pairs) when (v) {
            is Int -> e.putInt(k, v)
            is Long -> e.putLong(k, v)
            else -> e.putString(k, v.toString())
        }
        e.apply()
    }
}

object Sending {
    private val busy = Mutex()

    /**
     * Sending's own thread, at Android's background priority: reading and checking big videos then never
     * holds up the screen (which, at the same priority, it could for seconds on a busy phone).
     */
    private val slow = java.util.concurrent.Executors.newSingleThreadExecutor { r ->
        Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND)
            r.run()
        }, "HomePix sending")
    }.asCoroutineDispatcher()

    /** Send what the computer doesn't have yet. False: cut off part way, so try again soon. */
    suspend fun run(context: Context, stopped: () -> Boolean = { false }): Boolean {
        if (!busy.tryLock()) return true
        try {
            return withContext(slow) { sendNew(context, stopped) }
        } finally {
            busy.unlock()
        }
    }

    private fun sendNew(context: Context, stopped: () -> Boolean): Boolean {
        val computer = Computer.load(context) ?: return true
        if (metered(context)) return true // (Wi-Fi only, whatever a job was saved with)
        val now = System.currentTimeMillis()
        SendState.note(context, "tried" to now)
        val (host, port) = reach(context, computer.addresses, computer.last, computer.port, computer.fingerprint) ?: run {
            SendState.note(context, "problem" to "Couldn't reach ${computer.name}. Sending when it's on the same network.")
            return true // (not "try again soon": the next photo, or the 15-minute check, tries again)
        }
        if (host != computer.last || port != computer.port) computer.copy(last = host, port = port).save(context)
        val link = Link(host, port, computer.fingerprint, computer.token)
        // The library's details first: the computer has most of the phone's photos already (the first time,
        // checking every photo here takes a while), so they show at once.
        try {
            fetchLibrary(context, link)
        } catch (e: IOException) {
            android.util.Log.w("HomePix", "library: ${e.message}")
        }
        val sentFile = File(context.filesDir, "sent.tsv")
        val sent = HashSet<String>().apply { if (sentFile.isFile) sentFile.forEachLine { add(it) } }
        val todo = cameraFiles(context).filter { it.key !in sent }.sortedByDescending { it.modified }
        SendState.note(context, "waiting" to todo.size)
        var count = 0
        var left = todo.size
        try {
            for (batch in todo.chunked(BATCH)) {
                if (stopped()) return false
                // First by quick fingerprint (a moment each): only files the library doesn't have are read whole.
                val quick = batch.associateWith { Fingerprints.of(context, it.file) }
                val first = JSONArray()
                for ((f, q) in quick) first.put(JSONObject().put("path", f.rel).put("size", f.size).put("qhash", q ?: JSONObject.NULL))
                val answer = link.post("/v1/need", JSONObject().put("files", first))
                val wanted = if (answer.optBoolean("quick")) {
                    val need = answer.getJSONArray("need")
                    val paths = HashSet<String>().apply { for (i in 0 until need.length()) add(need.getJSONObject(i).getString("path")) }
                    batch.filter { it.rel in paths || quick[it] == null }
                } else {
                    batch // (a HomePix before 1.17 asks by content only)
                }
                val hashes = wanted.associateWith { sha256(it.file) }
                val have = HashMap<String, Long>()
                if (hashes.isNotEmpty()) {
                    val ask = JSONArray()
                    for ((f, sha) in hashes) {
                        ask.put(JSONObject().put("path", f.rel).put("size", f.size).put("sha256", sha).put("qhash", quick[f] ?: JSONObject.NULL))
                    }
                    val need = link.post("/v1/need", JSONObject().put("files", ask)).getJSONArray("need")
                    for (i in 0 until need.length()) need.getJSONObject(i).let { have[it.getString("path")] = it.getLong("have") }
                }
                for (f in batch) {
                    if (stopped()) return false
                    val from = have[f.rel]
                    val sha = hashes[f]
                    if (from != null && sha != null) {
                        val got = try {
                            link.send(f, sha, from)
                        } catch (e: Refused) {
                            if (e.status == 409 && e.have >= 0) link.send(f, sha, e.have) else throw e
                        }
                        if (got != f.size) return false
                        count++
                        SendState.note(context, "sent" to System.currentTimeMillis())
                    }
                    sent += f.key
                    sentFile.appendText(f.key + "\n")
                    left--
                }
                SendState.note(context, "waiting" to left, "problem" to "")
            }
            SendState.note(context, "problem" to "", "waiting" to 0, "count" to count)
            if (count > 0) {
                fetchLibrary(context, link)
                // and again once the computer has taken them into the library (a few seconds after they
                // come) and looked at them (faces, search): so a photo shows here a few minutes after it's taken
                later(context)
            }
            return true
        } catch (e: IOException) {
            SendState.note(context, "problem" to (e.message ?: "Sending stopped."))
            if (e is Refused && e.status == 401) Computer.prefs(context).edit().putBoolean("unpaired", true).apply()
            return false
        } finally {
            Fingerprints.save(context)
        }
    }

    /**
     * What HomePix knows about the photos on this phone (who's in them, their search data...), as a small
     * library file in the app's own storage: the computer is told the quick fingerprints of the phone's
     * photos and videos (only photos the phone has go in the file) and the places of those in DCIM, and
     * sends the file only when something's changed.
     */
    private fun fetchLibrary(context: Context, link: Link) {
        val prefs = SendState.prefs(context)
        val qhashes = JSONArray()
        val seen = HashSet<String>()
        val keys = HashSet<String>()
        val uri = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL)
        val types = "${MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE},${MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO}"
        context.contentResolver.query(
            uri, arrayOf(MediaStore.MediaColumns.DATA), "${MediaStore.Files.FileColumns.MEDIA_TYPE} IN ($types)", null, null,
        )?.use { c ->
            while (c.moveToNext()) {
                val f = File(c.getString(0) ?: continue)
                keys += Fingerprints.key(f)
                val q = Fingerprints.of(context, f) ?: continue
                if (seen.add(q)) qhashes.put(q)
            }
        }
        Fingerprints.save(context, keys) // (only those of files still here)
        val sources = JSONArray()
        cameraFiles(context).forEach { sources.put(it.rel) }
        val dst = computerLibrary(context)
        val known = prefs.getString("library", null)?.takeIf { dst.isFile } // (the computer answers "the same" to it)
        val version = link.library(
            JSONObject().put("qhashes", qhashes).put("sources", sources).put("version", known ?: JSONObject.NULL), dst,
        ) ?: return
        prefs.edit().putString("library", version).putLong("library_at", System.currentTimeMillis()).apply()
    }

    /** On mobile data, or another network that's paid for by the amount used? */
    private fun metered(context: Context): Boolean =
        (context.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager).isActiveNetworkMetered

    private val wifi get() = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED)

    /** Every 15 minutes, and soon after a photo or video is taken, on Wi-Fi. */
    fun schedule(context: Context) {
        if (Computer.load(context) == null) return
        val wm = WorkManager.getInstance(context)
        wm.enqueueUniquePeriodicWork(
            // UPDATE, not KEEP: a job saved by an older version gets today's Wi-Fi rule
            "send-every-15-min", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<SendWorker>(15, TimeUnit.MINUTES).setConstraints(wifi.build()).build(),
        )
        watch(context, ExistingWorkPolicy.KEEP)
    }

    /** Wake up when the gallery changes (once: SendWorker sets it again each time). */
    fun watch(context: Context, policy: ExistingWorkPolicy) {
        val c = wifi
            .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
            .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
            .setTriggerContentUpdateDelay(Duration.ofSeconds(10))
            .setTriggerContentMaxDelay(Duration.ofSeconds(60))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "send-new-photos", policy,
            OneTimeWorkRequestBuilder<SendWorker>().setConstraints(c).addTag("watch").build(),
        )
    }

    /** Send now (when the app opens, say), on Wi-Fi. asked: Send now was pressed (so a try already
     * waiting for its turn is replaced, not waited for). */
    fun now(context: Context, asked: Boolean = false) {
        if (Computer.load(context) == null) return
        WorkManager.getInstance(context).enqueueUniqueWork(
            "send-now", if (asked) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SendWorker>().setConstraints(wifi.build()).build(),
        )
    }

    /** Look again in a couple of minutes (for the library's details of what was just sent). */
    fun later(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "send-later", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<SendWorker>().setConstraints(wifi.build())
                .setInitialDelay(Duration.ofMinutes(2)).build(),
        )
    }

    fun stop(context: Context) {
        val wm = WorkManager.getInstance(context)
        listOf("send-every-15-min", "send-new-photos", "send-now", "send-later").forEach { wm.cancelUniqueWork(it) }
    }
}

class SendWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        if ("watch" in tags) Sending.watch(applicationContext, ExistingWorkPolicy.APPEND_OR_REPLACE)
        val ok = Sending.run(applicationContext) { isStopped }
        return if (ok || tags.contains("watch")) Result.success() else Result.retry()
    }
}

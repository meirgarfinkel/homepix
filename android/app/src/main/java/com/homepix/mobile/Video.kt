package com.homepix.mobile

import android.content.Context
import android.net.Uri
import android.view.ViewGroup
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import java.io.File
import java.io.RandomAccessFile

/** The viewer's one player, shared by every video in it; let go of when the viewer closes or the app is left. */
@Composable
fun rememberPlayer(): ExoPlayer {
    val context = LocalContext.current
    val player = remember { ExoPlayer.Builder(context).build() }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(player, lifecycle) {
        val watch = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_STOP) player.pause() }
        lifecycle.addObserver(watch)
        onDispose {
            lifecycle.removeObserver(watch)
            player.release()
        }
    }
    return player
}

/** Starts playing a file from the beginning. loop: over and over, with no sound (a motion photo). */
fun ExoPlayer.start(file: File, loop: Boolean = false, muted: Boolean = false) {
    setMediaItem(MediaItem.fromUri(Uri.fromFile(file)))
    repeatMode = if (loop) Player.REPEAT_MODE_ONE else Player.REPEAT_MODE_OFF
    volume = if (muted || loop) 0f else 1f
    prepare()
    playWhenReady = true
}

/** The picture of what's playing, fitted to the screen. */
@OptIn(UnstableApi::class)
@Composable
fun VideoSurface(player: ExoPlayer, modifier: Modifier = Modifier, degrees: Int = 0) {
    if (degrees % 360 == 0) {
        AndroidView(
            factory = { c ->
                PlayerView(c).apply {
                    useController = false
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    setShutterBackgroundColor(android.graphics.Color.TRANSPARENT)
                    keepScreenOn = true
                    layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                    this.player = player
                }
            },
            modifier = modifier,
            onRelease = { it.player = null },
        )
        return
    }
    // turned: drawn on a texture (a plain surface can't be turned), laid out in the turned space
    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        val side = degrees % 180 != 0
        AndroidView(
            factory = { c ->
                (android.view.LayoutInflater.from(c).inflate(R.layout.texture_player, null) as PlayerView).apply {
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    keepScreenOn = true
                    this.player = player
                }
            },
            modifier = Modifier.requiredSize(if (side) maxHeight else maxWidth, if (side) maxWidth else maxHeight).rotate(degrees.toFloat()),
            onRelease = { it.player = null },
        )
    }
}

internal fun icon(name: String, path: String) =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).addPath(addPathNodes(path), fill = SolidColor(Color.Black)).build()

// Material's pause, volume and volume-off icons, which the built-in icon set doesn't have
private val PauseIcon by lazy { icon("Pause", "M6,19h4L10,5L6,5v14zM14,5v14h4L18,5h-4z") }
private val SoundIcon by lazy {
    icon("Sound", "M3,9v6h4l5,5L12,4L7,9L3,9zM16.5,12c0,-1.77 -1.02,-3.29 -2.5,-4.03v8.05c1.48,-0.73 2.5,-2.25 2.5,-4.02zM14,3.23v2.06c2.89,0.86 5,3.54 5,6.71s-2.11,5.85 -5,6.71v2.06c4.01,-0.91 7,-4.49 7,-8.77s-2.99,-7.86 -7,-8.77z")
}
private val MutedIcon by lazy {
    icon("Muted", "M16.5,12c0,-1.77 -1.02,-3.29 -2.5,-4.03v2.21l2.45,2.45c0.03,-0.2 0.05,-0.41 0.05,-0.63zM19,12c0,0.94 -0.2,1.82 -0.54,2.64l1.51,1.51C20.63,14.91 21,13.5 21,12c0,-4.28 -2.99,-7.86 -7,-8.77v2.06c2.89,0.86 5,3.54 5,6.71zM4.27,3L3,4.27 7.73,9L3,9v6h4l5,5v-6.73l4.25,4.25c-0.67,0.52 -1.42,0.93 -2.25,1.18v2.06c1.38,-0.31 2.63,-0.95 3.69,-1.81L19.73,21 21,19.73l-9,-9L4.27,3zM12,4L9.91,6.09 12,8.18L12,4z")
}

/** A motion photo's badge: Material's "motion photos on" icon. */
val MotionIcon by lazy {
    icon("Motion", "M2.88,7.88l1.54,1.54C4.15,10.23 4,11.1 4,12c0,4.41 3.59,8 8,8s8,-3.59 8,-8s-3.59,-8 -8,-8c-0.9,0 -1.77,0.15 -2.58,0.42L7.89,2.89C9.15,2.32 10.54,2 12,2c5.52,0 10,4.48 10,10s-4.48,10 -10,10S2,17.52 2,12C2,10.54 2.32,9.14 2.88,7.88zM7,5.5C7,6.33 6.33,7 5.5,7S4,6.33 4,5.5S4.67,4 5.5,4S7,4.67 7,5.5zM12,6c3.31,0 6,2.69 6,6s-2.69,6 -6,6s-6,-2.69 -6,-6S8.69,6 12,6zM10,8.5v7l5,-3.5L10,8.5z")
}

private fun time(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

/**
 * A video in the viewer, playing (the page on screen) or as its still picture (the pages beside it).
 * controls: shown with the viewer's top bar. cantPlay: a file this phone can't decode, to hand to another app.
 */
@Composable
fun VideoPage(
    player: ExoPlayer,
    item: Item,
    active: Boolean,
    controls: Boolean,
    muted: Boolean,
    above: androidx.compose.ui.unit.Dp, // the viewer's bottom bar, which the controls sit above
    setMuted: (Boolean) -> Unit,
    openElsewhere: () -> Unit,
    poster: @Composable () -> Unit,
) {
    var failed by remember(item) { mutableStateOf(false) }
    var playing by remember { mutableStateOf(false) }
    var ready by remember(item, active) { mutableStateOf(false) } // this video's first picture is on screen
    var at by remember { mutableLongStateOf(0L) }
    var length by remember { mutableLongStateOf(0L) }
    var seeking by remember { mutableStateOf<Float?>(null) }

    LaunchedEffect(active, item) {
        if (active) player.start(item.file, muted = muted)
    }
    DisposableEffect(player, active, item) {
        val l = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) { playing = isPlaying }
            // (not a picture still left from the video before, on the same player)
            override fun onRenderedFirstFrame() {
                if (player.currentMediaItem?.localConfiguration?.uri?.path == item.file.path) ready = true
            }
            override fun onPlayerError(error: PlaybackException) { failed = true }
            // A picture the phone can't decode isn't an error to the player: it plays without one,
            // or finds no picture at all in a format it doesn't know.
            override fun onPlaybackStateChanged(state: Int) {
                val opened = state == Player.STATE_READY || state == Player.STATE_ENDED
                if (opened && !player.currentTracks.isTypeSupported(C.TRACK_TYPE_VIDEO)) {
                    failed = true
                    player.stop()
                }
            }
        }
        if (active) player.addListener(l)
        onDispose { player.removeListener(l) }
    }
    LaunchedEffect(active, playing) {
        while (active) {
            at = player.currentPosition
            length = player.duration.coerceAtLeast(0)
            delay(if (playing) 200 else 500)
        }
    }

    // The still picture (the video's first) stays over the video until the video has drawn its own,
    // so going from one to the other doesn't show: no black, nor a picture from the one before.
    var covered by remember(item, active) { mutableStateOf(true) }
    LaunchedEffect(ready) { if (ready) { delay(60); covered = false } }
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        if (active && !failed) VideoSurface(player, Modifier.fillMaxSize())
        if (!active || covered || failed) Box(Modifier.fillMaxSize().background(Color.Black)) { poster() }
        if (failed) {
            Column(Modifier.background(Color(0x99000000)).padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("This phone can't play this video here.", color = Color.White)
                Button(onClick = openElsewhere, Modifier.padding(top = 12.dp)) { Text("Open in another app") }
            }
        } else if (active && controls) {
            Row(
                Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = above).fillMaxWidth()
                    .background(Color(0x66000000)).padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = {
                    if (player.isPlaying) player.pause()
                    else {
                        if (player.playbackState == Player.STATE_ENDED) player.seekTo(0)
                        player.play()
                    }
                }) {
                    Icon(if (playing) PauseIcon else Icons.Filled.PlayArrow, if (playing) "Pause" else "Play", tint = Color.White)
                }
                Text(time(seeking?.let { (it * length).toLong() } ?: at), color = Color.White, style = MaterialTheme.typography.labelMedium)
                Slider(
                    value = seeking ?: if (length > 0) (at.toFloat() / length).coerceIn(0f, 1f) else 0f,
                    onValueChange = { seeking = it; player.seekTo((it * length).toLong()) },
                    onValueChangeFinished = { seeking?.let { at = (it * length).toLong() }; seeking = null },
                    modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                    colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Color(0x66FFFFFF)),
                )
                Text(time(length), color = Color.White, style = MaterialTheme.typography.labelMedium)
                IconButton(onClick = { setMuted(!muted); player.volume = if (muted) 1f else 0f }) {
                    Icon(if (muted) MutedIcon else SoundIcon, if (muted) "Turn sound on" else "Mute", tint = Color.White)
                }
            }
        } else if (active && !playing && player.playbackState == Player.STATE_ENDED) {
            Box(Modifier.size(72.dp).clip(CircleShape).background(Color(0x99000000)), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.PlayArrow, null, Modifier.size(48.dp), tint = Color.White)
            }
        }
    }
}

/**
 * A motion photo's video (a short clip a Pixel or Samsung camera keeps at the end of the photo's file),
 * saved once to the app's cache so the player can open it. Null for an ordinary photo.
 */
fun motionVideo(context: Context, photo: File): File? {
    val start = motionStart(photo) ?: return null
    val out = File(clipsDir(context), "${photo.path.hashCode().toUInt()}_${photo.lastModified()}_${photo.length()}.mp4")
    if (out.isFile) return out
    out.parentFile!!.mkdirs()
    writeWhole(out) { tmp ->
        RandomAccessFile(photo, "r").use { f ->
            f.seek(start)
            tmp.outputStream().use { o ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val n = f.read(buf)
                    if (n <= 0) break
                    o.write(buf, 0, n)
                }
            }
        }
    }
    return out
}

fun clipsDir(context: Context) = File(context.cacheDir, "motion")

/** Where the video starts in a motion photo's file, from what its XMP says, or null if there's none. */
fun motionStart(photo: File): Long? {
    if (!photo.name.endsWith(".jpg", true) && !photo.name.endsWith(".jpeg", true)) return null
    val size = photo.length()
    val head = RandomAccessFile(photo, "r").use { f ->
        val b = ByteArray(minOf(size, 256L * 1024).toInt())
        f.readFully(b)
        String(b, Charsets.ISO_8859_1)
    }
    if (!head.contains("MotionPhoto") && !head.contains("MicroVideo")) return null
    val candidates = mutableListOf<Long>()
    // older Pixels: how far from the end the video starts
    Regex("MicroVideoOffset(?:=\"|>)(\\d+)").find(head)?.let { candidates += size - it.groupValues[1].toLong() }
    // newer ones: the video is the last item in the file's list of parts, this long (plus any padding)
    val video = head.indexOf("video/mp4").takeIf { it >= 0 }
    if (video != null) {
        val near = head.substring(maxOf(0, video - 300), minOf(head.length, video + 300))
        val length = Regex("Item:Length(?:=\"|>)(\\d+)").find(near)?.groupValues?.get(1)?.toLong()
        val padding = Regex("Item:Padding(?:=\"|>)(\\d+)").find(near)?.groupValues?.get(1)?.toLong() ?: 0
        if (length != null) candidates += size - length - padding
    }
    return candidates.firstOrNull { it in 1 until size && looksLikeVideo(photo, it) } ?: findVideo(photo)
}

/** An MP4 starts with a box naming its type: 4 bytes of size, then "ftyp". */
private fun looksLikeVideo(file: File, at: Long): Boolean = runCatching {
    RandomAccessFile(file, "r").use { f ->
        f.seek(at + 4)
        val b = ByteArray(4)
        f.readFully(b)
        String(b, Charsets.ISO_8859_1) == "ftyp"
    }
}.getOrDefault(false)

/** When the XMP can't be trusted: the first "ftyp" box after the photo itself. */
private fun findVideo(file: File): Long? {
    val bytes = file.readBytes()
    var i = 0
    while (true) {
        i = indexOf(bytes, "ftyp".toByteArray(), i + 1)
        if (i < 4) return null
        val box = ((bytes[i - 4].toInt() and 0xff) shl 24) or ((bytes[i - 3].toInt() and 0xff) shl 16) or
            ((bytes[i - 2].toInt() and 0xff) shl 8) or (bytes[i - 1].toInt() and 0xff)
        if (box in 16..64 && i > 1024) return (i - 4).toLong()
    }
}

private fun indexOf(hay: ByteArray, needle: ByteArray, from: Int): Int {
    outer@ for (i in from..hay.size - needle.size) {
        for (j in needle.indices) if (hay[i + j] != needle[j]) continue@outer
        return i
    }
    return -1
}

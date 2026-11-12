package com.homepix.mobile

import android.content.Context
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.height
import android.content.Intent
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.AssistChip
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import android.provider.MediaStore
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.runtime.toMutableStateList
import coil.request.ImageRequest
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Month
import java.time.format.TextStyle
import java.util.Locale

fun monthName(key: String): String {
    val y = key.take(4).toIntOrNull() ?: 0
    val m = key.drop(5).toIntOrNull() ?: 0
    if (y == 0 || m !in 1..12) return "No date"
    return Month.of(m).getDisplayName(TextStyle.FULL, Locale.getDefault()) + " $y"
}

fun niceDate(taken: String): String = if (taken.startsWith("0000")) "No date" else
    monthName(taken.take(7)).let { mn -> "${taken.drop(8).take(2).trimStart('0')} $mn, ${taken.drop(11).take(5)}" }

private sealed interface Cell {
    data class Header(val label: String) : Cell
    data class Photo(val pos: Int) : Cell
}

/** How many photos across the grids: pinch to change it. Kept while the app is open. */
object GridZoom {
    var columns by mutableIntStateOf(4)
    const val MIN = 1
    const val MAX = 8
}

/** A grid of photos grouped by month, newest first (items must be in timeline order). title: a bar on top. */
@Composable
fun Timeline(lib: Library, all: List<Int>, title: String?, open: (List<Int>, Int) -> Unit, back: (() -> Unit)? = null) {
    val gone = Gone.files.size
    val items = remember(all, gone) { if (gone == 0) all else all.filter { lib.items[it].file !in Gone.files } }
    val cells = remember(items) {
        val out = mutableListOf<Cell>()
        var month: String? = null
        items.forEachIndexed { pos, i ->
            val m = lib.items[i].month
            if (m != month) { out += Cell.Header(monthName(m)); month = m }
            out += Cell.Photo(pos)
        }
        out
    }
    Column(Modifier.fillMaxSize()) {
        if (title != null) Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (back != null) IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            Text(title, Modifier.weight(1f).padding(horizontal = 8.dp, vertical = 12.dp),
                style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        PhotoGrid(lib, cells, items, open)
    }
}

/** The thumbnail size for a cell: the cell's width in pixels, rounded up to a few fixed sizes (so the cache reuses them). */
@Composable
private fun thumbSize(columns: Int): Int {
    val px = LocalContext.current.resources.displayMetrics.widthPixels / columns
    return listOf(192, 256, 384, 512, 768, 1080).firstOrNull { it >= px * 0.9f } ?: 1080
}

@Composable
private fun PhotoGrid(lib: Library, cells: List<Cell>, items: List<Int>, open: (List<Int>, Int) -> Unit) {
    val grid = rememberLazyGridState()
    val columns = GridZoom.columns
    val size = thumbSize(columns)
    Box(
        Modifier.fillMaxSize().pointerInput(Unit) {
            // two fingers apart: bigger photos (fewer across); together: smaller. One finger still scrolls.
            awaitEachGesture {
                awaitFirstDown(requireUnconsumed = false)
                var zoom = 1f
                do {
                    val ev = awaitPointerEvent(PointerEventPass.Initial)
                    if (ev.changes.count { it.pressed } >= 2) {
                        zoom *= ev.calculateZoom()
                        if (zoom > 1.25f && GridZoom.columns > GridZoom.MIN) { GridZoom.columns--; zoom = 1f }
                        if (zoom < 0.8f && GridZoom.columns < GridZoom.MAX) { GridZoom.columns++; zoom = 1f }
                        ev.changes.forEach { it.consume() }
                    }
                } while (ev.changes.any { it.pressed })
            }
        },
    ) {
        LazyVerticalGrid(
            columns = GridCells.Fixed(columns), state = grid, modifier = Modifier.fillMaxSize(),
            horizontalArrangement = Arrangement.spacedBy(2.dp), verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            items(cells.size, span = { if (cells[it] is Cell.Photo) GridItemSpan(1) else GridItemSpan(maxLineSpan) },
                contentType = { if (cells[it] is Cell.Photo) 1 else 0 }) { n ->
                when (val c = cells[n]) {
                    is Cell.Header -> Text(c.label, Modifier.padding(start = 12.dp, top = 16.dp, bottom = 6.dp),
                        style = MaterialTheme.typography.titleSmall)
                    is Cell.Photo -> PhotoCell(lib.items[items[c.pos]], size) { open(items, c.pos) }
                }
            }
        }
        if (cells.size > 60) Scrollbar(lib, cells, items, grid)
    }
}

/**
 * A handle on the right edge: drag it to move through the years. While dragging, the years are
 * marked along the edge and a label shows the month under the handle.
 */
@Composable
private fun BoxScope.Scrollbar(lib: Library, cells: List<Cell>, items: List<Int>, grid: LazyGridState) {
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    var height by remember { mutableIntStateOf(1) }
    var dragging by remember { mutableStateOf(false) }
    var dragAt by remember { mutableFloatStateOf(0f) } // where the handle is while dragging, 0 (top) to 1
    val handleH = with(density) { 44.dp.toPx() }
    val months = remember(cells) {
        var last = ""
        Array(cells.size) { n ->
            when (val c = cells[n]) {
                is Cell.Header -> c.label.also { last = it }
                is Cell.Photo -> monthName(lib.items[items[c.pos]].month).also { last = it }
            }
        }
    }
    val years = remember(cells) {
        cells.withIndex().filter { it.value is Cell.Header }
            .map { (it.value as Cell.Header).label.substringAfterLast(' ') to it.index }.distinctBy { it.first }
    }
    // the grid stops when its last row reaches the bottom, so the top row runs to (cells - those on screen)
    val span = (cells.size - grid.layoutInfo.visibleItemsInfo.size).coerceAtLeast(1)
    fun now() = (grid.firstVisibleItemIndex.toFloat() / span).coerceAtMost(1f)
    val shown = if (dragging) dragAt else now()
    val y = shown * (height - handleH)
    fun goTo(fraction: Float) {
        dragAt = fraction.coerceIn(0f, 1f)
        val n = if (dragAt >= 0.995f) cells.lastIndex else (dragAt * span).roundToInt()
        scope.launch { grid.scrollToItem(n) }
    }
    Box(
        Modifier.align(Alignment.CenterEnd).fillMaxHeight().width(36.dp)
            .onSizeChanged { height = it.height }
            .pointerInput(cells) {
                // only a touch on the handle itself drags it; anything else falls through to the photos
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
                    val top = now() * (height - handleH)
                    if (down.position.y < top - handleH / 2 || down.position.y > top + handleH * 1.5f) return@awaitEachGesture
                    down.consume()
                    dragging = true
                    val grab = down.position.y - top
                    do {
                        val ev = awaitPointerEvent(PointerEventPass.Initial)
                        val p = ev.changes.first()
                        goTo((p.position.y - grab) / (height - handleH))
                        p.consume()
                    } while (p.pressed)
                    dragging = false
                }
            },
    ) {
        if (dragging) {
            var lastY = -1000f
            val gap = with(density) { 22.dp.toPx() }
            for ((yr, at) in years) {
                val yy = (at.toFloat() / span).coerceAtMost(1f) * (height - handleH) + handleH / 2
                if (yy - lastY < gap) continue
                lastY = yy
                Text(yr, Modifier.align(Alignment.TopEnd).offset { IntOffset(0, (yy - gap / 2).roundToInt()) }
                    .wrapContentWidth(Alignment.End, unbounded = true).padding(end = 14.dp)
                    .clip(RoundedCornerShape(8.dp)).background(MaterialTheme.colorScheme.surface.copy(alpha = 0.85f))
                    .padding(horizontal = 6.dp, vertical = 1.dp),
                    style = MaterialTheme.typography.labelSmall, softWrap = false, maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurface)
            }
        }
        Box(
            Modifier.align(Alignment.TopEnd).offset { IntOffset(0, y.roundToInt()) }.padding(end = 2.dp)
                .size(width = if (dragging) 8.dp else 5.dp, height = 44.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(MaterialTheme.colorScheme.primary.copy(alpha = if (dragging) 1f else 0.6f)),
        )
    }
    if (dragging) {
        val label = months[(dragAt * span).roundToInt().coerceIn(0, cells.size - 1)]
        Text(label, Modifier.align(Alignment.TopEnd)
            .offset { IntOffset(-with(density) { 76.dp.roundToPx() }, y.roundToInt()) }
            .clip(RoundedCornerShape(20.dp)).background(MaterialTheme.colorScheme.primaryContainer)
            .padding(horizontal = 14.dp, vertical = 10.dp),
            color = MaterialTheme.colorScheme.onPrimaryContainer, style = MaterialTheme.typography.titleSmall)
    }
}

@Composable
private fun PhotoCell(item: Item, size: Int, onClick: () -> Unit) {
    Edits.version // (made again when a photo is turned)
    Box(Modifier.aspectRatio(1f).background(MaterialTheme.colorScheme.surfaceVariant).clickable(onClick = onClick)) {
        AsyncImage(Thumb(item.file, item.kind == "video", size, Turns.of(item.file), item.stamp), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        if (item.kind == "video") {
            val d = item.duration?.toInt()
            Text(if (d != null) "%d:%02d".format(d / 60, d % 60) else "▶",
                Modifier.align(Alignment.BottomEnd).padding(4.dp).background(Color(0x99000000)).padding(horizontal = 4.dp),
                color = Color.White, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
fun PeopleGrid(lib: Library, open: (Person) -> Unit) {
    if (lib.people.isEmpty()) { Centered { Text("No one has been named yet.") }; return }
    Column {
        Text("People", Modifier.padding(16.dp), style = MaterialTheme.typography.titleMedium)
        LazyVerticalGrid(GridCells.Adaptive(104.dp), Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            items(lib.people.size) { n -> PersonCell(lib.people[n]) { open(lib.people[n]) } }
        }
    }
}

@Composable
private fun PersonCell(p: Person, onClick: () -> Unit) {
    Edits.version
    Column(Modifier.clickable(onClick = onClick).padding(4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(88.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant)) {
            if (p.face != null) AsyncImage(FaceThumb(p.face, Turns.of(p.face.file)), p.name, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        }
        Text(p.name, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
    }
}

/** Search keeps its model and last results while switching tabs. */
class SearchState(private val context: Context) {
    var text by mutableStateOf("")
    var busy by mutableStateOf(false)
    var note by mutableStateOf("")
    var people by mutableStateOf<List<Person>>(emptyList())
    var matches by mutableStateOf<List<Int>>(emptyList())
    var related by mutableStateOf<List<Int>>(emptyList())
    private var model: Search? = null

    /** The × in the search bar: an empty bar and no results. */
    fun clear() {
        text = ""; people = emptyList(); matches = emptyList(); related = emptyList(); note = ""
    }

    /** After the library is read again: search it again (the old results point into the old one). */
    suspend fun redo(lib: Library) {
        people = emptyList(); matches = emptyList(); related = emptyList(); note = ""
        if (text.isNotBlank()) run(lib)
    }

    suspend fun run(lib: Library) {
        val q = text.trim()
        if (q.isEmpty()) return
        busy = true
        // A name narrows the search to that person's photos; the other words are searched in those.
        val (named, rest) = namesIn(q, lib.people)
        // without a full name: anyone a word of the search is part of the name of, to tap
        val words = q.lowercase().split(Regex("\\s+")).toSet()
        people = if (named.isNotEmpty()) named.flatten().distinct() else lib.people.filter { p ->
            p.name.contains(q, ignoreCase = true) || p.name.lowercase().split(' ').any { it in words }
        }
        val only = named.map { g -> g.flatMap { it.items }.toSet() }.reduceOrNull { a, b -> a intersect b }
        val who = named.joinToString(" and ") { g -> g.singleOrNull()?.name ?: g.first().name.substringBefore(' ') }
        if (only != null && rest.isEmpty()) {
            matches = only.sorted() // the library is newest first
            related = emptyList()
            note = "${only.size} ${if (only.size == 1) "photo" else "photos"} of $who"
            busy = false
            return
        }
        note = if (model == null || !lib.vectorsReady) "Getting search ready…" else "Searching…"
        val r = withContext(Dispatchers.Default) {
            val m = model ?: Search(context).also { model = it }
            m.search(lib, rest, only)
        }
        matches = r.matches
        related = r.related
        note = (if (only != null) "${r.matches.size} matches in ${r.searched} photos of $who" else "${r.matches.size} matches in ${r.searched} photos")
        busy = false
    }
}

@Composable
fun SearchScreen(lib: Library, s: SearchState, open: (List<Int>, Int) -> Unit, openPerson: (Person) -> Unit) {
    val scope = rememberCoroutineScope()
    val keyboard = LocalSoftwareKeyboardController.current
    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            s.text, { s.text = it }, Modifier.fillMaxWidth().padding(12.dp),
            placeholder = { Text("Describe a photo, or a name") }, singleLine = true,
            shape = RoundedCornerShape(16.dp),
            trailingIcon = {
                if (s.text.isNotEmpty()) IconButton(onClick = { s.clear() }) { Icon(Icons.Filled.Close, "Clear") }
            },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { keyboard?.hide(); scope.launch { s.run(lib) } }),
        )
        if (s.busy) LinearProgressIndicator(Modifier.fillMaxWidth().padding(horizontal = 12.dp))
        if (s.note.isNotEmpty()) Text(s.note, Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.outline)
        if (s.people.isNotEmpty()) LazyRow(Modifier.padding(horizontal = 8.dp)) {
            items(s.people) { p -> AssistChip(onClick = { openPerson(p) }, label = { Text(p.name) },
                modifier = Modifier.padding(4.dp)) }
        }
        val gone = Gone.files.size
        val matches = remember(s.matches, gone) { s.matches.filter { lib.items[it].file !in Gone.files } }
        val related = remember(s.related, gone) { s.related.filter { lib.items[it].file !in Gone.files } }
        val shown = remember(matches, related) { matches + related }
        val cells = remember(shown) {
            buildList {
                matches.indices.forEach { add(Cell.Photo(it)) }
                if (related.isNotEmpty()) {
                    add(Cell.Header("Possibly related"))
                    related.indices.forEach { add(Cell.Photo(matches.size + it)) }
                }
            }
        }
        PhotoGrid(lib, cells, shown, open)
    }
}

/** Material's "rotate left" icon, which the built-in icon set doesn't have. */
// Google Photos' rotate icon (Material's "rotate 90 degrees counter-clockwise")
private val RotateIcon by lazy {
    icon("Rotate", "M7.34,6.41L0.86,12.9l6.49,6.48 6.49,-6.48 -6.5,-6.49zM3.69,12.9l3.66,-3.66L11,12.9l-3.66,3.66 -3.65,-3.66zM19.36,6.64C17.61,4.88 15.3,4 13,4L13,0.76L8.76,5 13,9.24L13,6c1.79,0 3.58,0.68 4.95,2.05 2.73,2.73 2.73,7.17 0,9.9C16.58,19.32 14.79,20 13,20c-0.97,0 -1.94,-0.21 -2.84,-0.61l-1.49,1.49C10.02,21.62 11.51,22 13,22c2.3,0 4.61,-0.88 6.36,-2.64 3.52,-3.51 3.52,-9.21 0,-12.72z")
}

/** The height of the viewer's bottom bar (the video controls sit above it). */
val ACTIONS_HEIGHT = 64.dp

/** A button in the viewer's bottom bar: just its icon, as Google Photos has them. */
@Composable
private fun Action(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    IconButton(onClick = onClick, Modifier.size(56.dp)) {
        Icon(icon, label, Modifier.size(26.dp), tint = Color.White)
    }
}

/** "Nov 10", or "Nov 10, 2024" in another year, as Google Photos writes the day. */
fun dayText(taken: String): String {
    if (taken.startsWith("0000")) return "No date"
    return runCatching {
        val d = java.time.LocalDate.parse(taken.take(10))
        val f = if (d.year == java.time.LocalDate.now().year) "MMM d" else "MMM d, yyyy"
        d.format(java.time.format.DateTimeFormatter.ofPattern(f, Locale.getDefault()))
    }.getOrDefault(niceDate(taken))
}

/** "12:52" (or "12:52 PM", as the phone's clock is set), or null if there's no time. */
fun timeText(context: Context, taken: String): String? {
    if (taken.startsWith("0000") || taken.length < 16) return null
    return runCatching {
        val t = java.time.LocalTime.parse(taken.substring(11, 16))
        val f = if (android.text.format.DateFormat.is24HourFormat(context)) "HH:mm" else "h:mm a"
        t.format(java.time.format.DateTimeFormatter.ofPattern(f, Locale.getDefault()))
    }.getOrNull()
}

/** Swiping up opens the info panel. */
private fun Modifier.swipeUp(key: Any, open: () -> Unit) = pointerInput(key) {
    var total = 0f
    detectVerticalDragGestures(
        onDragStart = { total = 0f },
        onDragEnd = { if (total < -80.dp.toPx()) open() },
    ) { change, dy -> total += dy; change.consume() }
}

/**
 * One photo full screen; swipe sideways for the next, up for its details. Videos play here; one this
 * phone can't decode can be opened in another app. A motion photo's clip plays while the photo is held.
 * Turning and deleting only change this phone: the library on the computer stays as it is.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Viewer(lib: Library, items: List<Int>, start: Int, close: () -> Unit, openPerson: (Person) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val list = remember { items.toMutableStateList() }
    val pager = rememberPagerState(initialPage = start) { list.size }
    var chrome by remember { mutableStateOf(true) }
    var muted by remember { mutableStateOf(false) }
    var info by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<Item?>(null) } // deleting a file Android's trash doesn't know
    val player = rememberPlayer()
    var motion by remember { mutableStateOf<java.io.File?>(null) } // the photo on screen's clip, if it has one
    var motionTurn by remember { mutableIntStateOf(0) } // how far its file says the photo is turned (the clip turns with it)
    var holding by remember { mutableStateOf(false) }
    val current = list.getOrNull(pager.currentPage)?.let { lib.items[it] }
    val zoom = remember { ZoomState() } // the photo on screen's; back to 1x on another photo or a turn
    LaunchedEffect(current, Edits.version) { zoom.reset() }

    fun removed(item: Item) {
        Gone.files += item.file
        val i = list.indexOfFirst { lib.items[it] === item }
        if (i >= 0) list.removeAt(i)
        if (list.isEmpty()) close()
    }
    var trashing by remember { mutableStateOf<Item?>(null) }
    val trash = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { r ->
        val item = trashing
        trashing = null
        if (r.resultCode == android.app.Activity.RESULT_OK && item != null) removed(item)
    }
    fun delete(item: Item) {
        val uri = mediaUri(context, item.file, item.kind == "video")
        if (uri == null) { asking = item; return }
        trashing = item
        val ask = MediaStore.createTrashRequest(context.contentResolver, listOf(uri), true)
        trash.launch(IntentSenderRequest.Builder(ask.intentSender).build())
    }

    LaunchedEffect(current) {
        holding = false
        motion = null
        if (current != null && current.kind != "video") {
            player.stop()
            player.clearMediaItems()
            motion = withContext(Dispatchers.IO) { runCatching { motionVideo(context, current.file) }.getOrNull() }
        }
    }
    LaunchedEffect(current, Edits.version) {
        motionTurn = if (current == null) 0 else withContext(Dispatchers.IO) { fileTurn(current.file) }
    }
    LaunchedEffect(holding) {
        val m = motion
        if (holding && m != null) player.start(m, loop = true)
        else if (current?.kind != "video") player.stop()
    }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        HorizontalPager(
            pager, Modifier.fillMaxSize(), beyondViewportPageCount = 1, key = { list[it] },
            userScrollEnabled = !zoom.zoomed, // zoomed in, a swipe moves around the photo
        ) { page ->
            val item = lib.items[list[page]]
            val active = page == pager.currentPage
            if (item.kind == "video") {
                Box(Modifier.fillMaxSize().clickable { chrome = !chrome }.swipeUp(item) { info = true }) {
                    VideoPage(player, item, active, chrome, muted, ACTIONS_HEIGHT, { muted = it }, { play(context, item) }) {
                        AsyncImage(Thumb(item.file, true, 1080, stamp = item.stamp), null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                    }
                }
            } else {
                val turn = Turns.of(item.file)
                val z = if (active) zoom else null
                Box(
                    Modifier.fillMaxSize().swipeUp(item) { if (!zoom.zoomed) info = true }
                        .pointerInput(item, motion != null && active) {
                        detectTapGestures(
                            onTap = { chrome = !chrome },
                            onDoubleTap = { at -> if (active) scope.launch { zoom.toggle(at) } },
                            onLongPress = {}, // a hold plays the clip; it isn't a tap
                            onPress = {
                                if (active && motion != null) {
                                    // not while zooming or zoomed in: a pinch is a hold too
                                    val wait = scope.launch {
                                        kotlinx.coroutines.delay(250)
                                        if (!zoom.zoomed && zoom.fingers < 2) holding = true
                                    }
                                    tryAwaitRelease()
                                    wait.cancel()
                                    holding = false
                                }
                            },
                        )
                    }.zoomable(zoom, item, active),
                    contentAlignment = Alignment.Center,
                ) {
                    // zoomed in, the photo is read again in more detail (up to 4096 pixels across), shown
                    // over the screen-sized one while that loads
                    var sharp by remember(item, active) { mutableStateOf(false) }
                    var shape by remember(item, turn) { mutableStateOf(androidx.compose.ui.geometry.Size.Zero) } // the photo's size in pixels
                    if (z != null && z.scale > 1.3f) sharp = true
                    val request = remember(item, turn, Edits.version, sharp) {
                        val key = "${item.file.path}:${item.stamp}:$turn"
                        ImageRequest.Builder(context).data(item.file)
                            .apply {
                                if (sharp) size(4096).memoryCacheKey("$key:sharp").placeholderMemoryCacheKey(key)
                                else memoryCacheKey(key)
                                if (turn != 0) transformations(Turn(turn))
                            }.build()
                    }
                    AsyncImage(
                        request, null, Modifier.fillMaxSize().then(if (z != null) Modifier.zoomed(z) else Modifier),
                        contentScale = ContentScale.Fit,
                        onState = { st -> st.painter?.intrinsicSize?.let { if (it.width > 0 && !sharp) shape = it } },
                    )
                    if (z != null) SideEffect { z.photoSize(shape) }
                    if (active && holding) VideoSurface(player, Modifier.fillMaxSize(), motionTurn + turn)
                }
            }
        }
        if (chrome && current != null) {
            // as Google Photos has it: the day and the time at the top; what to do with it at the bottom
            Box(Modifier.fillMaxWidth().background(Color(0x66000000)).statusBarsPadding().height(64.dp)) {
                IconButton(onClick = close, Modifier.align(Alignment.CenterStart)) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = Color.White)
                }
                Column(
                    Modifier.align(Alignment.Center).fillMaxWidth(0.62f).clickable { info = true },
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(dayText(current.local), color = Color.White, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    // (no place name: finding one would send where the photo was taken to Google)
                    val line = timeText(context, current.local)
                    if (!line.isNullOrEmpty()) Text(line, color = Color(0xCCFFFFFF), style = MaterialTheme.typography.bodyMedium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Row(Modifier.align(Alignment.CenterEnd), verticalAlignment = Alignment.CenterVertically) {
                    if (motion != null) Icon(MotionIcon, "Motion photo: hold to play", Modifier.padding(horizontal = 8.dp).size(22.dp), tint = Color.White)
                    IconButton(onClick = { info = true }) { Icon(Icons.Outlined.Info, "Info", tint = Color.White) }
                }
            }
            Row(
                Modifier.align(Alignment.BottomCenter).fillMaxWidth().background(Color(0x66000000)).navigationBarsPadding()
                    .height(ACTIONS_HEIGHT),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Action(Icons.Outlined.Share, "Share") { share(context, current) }
                if (current.kind != "video") Action(RotateIcon, "Rotate") {
                    scope.launch {
                        val inFile = withContext(Dispatchers.IO) {
                            turnInFile(context, current.file).also { if (it) current.stamp = current.file.lastModified() }
                        }
                        if (inFile) Edits.version++ else Turns.turnLeft(current.file)
                    }
                }
                Action(Icons.Outlined.Delete, "Trash") { delete(current) }
            }
        }
    }
    if (info && current != null) InfoSheet(lib, current, { info = false }, openPerson)
    asking?.let { item ->
        AlertDialog(
            onDismissRequest = { asking = null },
            title = { Text("Delete from this phone?") },
            text = { Text("This file can't go to the phone's trash, so it will be deleted for good. It stays in your HomePix library on the computer.") },
            confirmButton = { TextButton(onClick = { asking = null; if (item.file.delete()) removed(item) }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { asking = null }) { Text("Cancel") } },
        )
    }
}

/** Android's share sheet for one photo or video, the way the gallery shares it. */
private fun share(context: Context, item: Item) {
    val video = item.kind == "video"
    // the phone's media library entry when it has one (every app can open those), else the file itself
    val uri = mediaUri(context, item.file, video) ?: FileProvider.getUriForFile(context, "com.homepix.mobile.files", item.file)
    val send = Intent(Intent.ACTION_SEND).setType(if (video) "video/*" else "image/*")
        .putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, null))
}

private fun play(context: Context, item: Item) {
    val uri = FileProvider.getUriForFile(context, "com.homepix.mobile.files", item.file)
    context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "video/*")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
}

package com.homepix.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import coil.ImageLoader
import coil.Coil
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Surface
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import coil.decode.VideoFrameDecoder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.delay
import androidx.compose.foundation.IndicationNodeFactory
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LocalRippleConfiguration
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.node.DelegatableNode
import androidx.compose.runtime.mutableIntStateOf

/** Draws nothing when touched (in place of the ripple). */
private object NoIndication : IndicationNodeFactory {
    override fun create(interactionSource: InteractionSource): DelegatableNode = object : Modifier.Node() {}
    override fun equals(other: Any?) = other === this
    override fun hashCode() = 0
}

class MainActivity : ComponentActivity() {
    private var allowed by mutableStateOf(false)
    private var resumes by mutableIntStateOf(0) // each time the app comes back to the front
    private var pairCode by mutableStateOf<PairCode?>(null) // a HomePix code opened as a link

    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            // test builds: note every time the screen's thread waits on the disk or the network
            android.os.StrictMode.setThreadPolicy(
                android.os.StrictMode.ThreadPolicy.Builder().detectAll().penaltyLog().build(),
            )
        }
        enableEdgeToEdge()
        val loader = ImageLoader.Builder(this)
            .components {
                add(ThumbFetcher.Factory()); add(ThumbKeyer())
                add(FaceFetcher.Factory()); add(FaceKeyer())
                add(VideoFrameDecoder.Factory())
            }
            .crossfade(false)
            .build()
        Coil.setImageLoader(loader)
        Thread {
            dropOldThumbs(this) // (a folder to delete: never on the screen's thread)
            deleteSharedPreferences("places") // (town names up to 0.5 kept where photos were taken)
        }.start()
        Turns.load(this)
        pairCode = intent?.dataString?.let(PairCode::parse)
        Sending.schedule(this)
        Sending.now(this)
        setContent {
            val dark = isSystemInDarkTheme()
            MaterialTheme(colorScheme = if (dark) darkColorScheme() else lightColorScheme()) {
                // no ripple when something is touched, anywhere: every tap already shows what it did
                CompositionLocalProvider(LocalIndication provides NoIndication, LocalRippleConfiguration provides null) {
                    // (the theme's background and text colours for every screen, not only the tabs': in dark
                    // mode, text outside them was left black)
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        if (allowed) App(resumes, pairCode) { pairCode = null } else AskForFiles { askForFiles() }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let(PairCode::parse)?.let { pairCode = it }
    }

    override fun onResume() {
        super.onResume()
        allowed = Environment.isExternalStorageManager()
        resumes++
    }

    private fun askForFiles() {
        startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
    }
}

@Composable
private fun AskForFiles(ask: () -> Unit) {
    Centered {
        Text("HomePix shows the library folder you copied to this phone. It needs to read your files to find it.",
            textAlign = TextAlign.Center)
        Spacer(Modifier.height(16.dp))
        Button(onClick = ask) { Text("Allow access to files") }
    }
}

@Composable
fun Centered(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) { content() }
    }
}

/** What's on screen over the tabs: a person's photos, or one photo full screen. */
sealed interface Page {
    data class PersonPage(val person: Person) : Page
    data class Viewer(val items: List<Int>, val start: Int) : Page
}

@Composable
private fun App(resumes: Int, pairCode: PairCode?, pairTaken: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var pairing by remember { mutableStateOf(false) } // (the computer screen, with no library yet)
    var tab by rememberSaveable { mutableStateOf(0) }
    LaunchedEffect(pairCode) { if (pairCode != null) tab = 3 } // (a pairing link: show how it went)
    var lib by remember { mutableStateOf<Library?>(null) }
    var status by remember { mutableStateOf("Looking for your library…") }
    var tries by remember { mutableStateOf(0) }
    LaunchedEffect(tries) {
        lib = withContext(Dispatchers.IO) {
            val t0 = System.currentTimeMillis()
            val roots = Library.find(context)
            android.util.Log.i("HomePix", "found ${roots.size} libraries in ${System.currentTimeMillis() - t0} ms")
            if (roots.isEmpty() && Computer.load(context) != null) {
                status = "No library yet. It comes from your computer the next time this phone reaches it on Wi-Fi."
                null
            } else if (roots.isEmpty()) {
                status = "No library yet. Pair this phone with HomePix on your computer (Settings, then Phones) " +
                    "and your photos show here."
                null
            } else {
                val t0 = System.currentTimeMillis()
                Library.load(context, roots) { status = it }
                    .also { android.util.Log.i("HomePix", "loaded in ${System.currentTimeMillis() - t0} ms") }
            }
        }
    }
    val l = lib
    if (l == null) {
        // Paired, and the computer's file not here yet (just paired, say, even with the pairing screen still
        // open): look again every few seconds, so the photos show as soon as it comes. A light look: only
        // whether that one file is there.
        LaunchedEffect(Unit) {
            while (true) {
                delay(3000)
                if (status.startsWith("No library") && Computer.load(context) != null) tries++
            }
        }
    }
    if (l == null && (pairing || pairCode != null)) {
        BackHandler { pairing = false }
        ComputerScreen(pairCode, pairTaken)
        return
    }
    if (l == null) {
        Centered {
            if (!status.startsWith("No library")) CircularProgressIndicator()
            Spacer(Modifier.height(16.dp))
            Text(status, textAlign = TextAlign.Center)
            if (status.startsWith("No library")) {
                Spacer(Modifier.height(16.dp))
                Button(onClick = { status = "Looking for your library…"; tries++; Sending.now(context, asked = true) }) { Text("Look again") }
                Spacer(Modifier.height(8.dp))
                androidx.compose.material3.OutlinedButton(onClick = { pairing = true }) {
                    Text(if (Computer.load(context) != null) "Computer" else "Pair with a computer")
                }
            }
        }
        return
    }
    val search = remember { SearchState(context) }
    val pages = remember { mutableStateListOf<Page>() }
    val open = { items: List<Int>, i: Int -> pages.add(Page.Viewer(items, i)); Unit }
    val all = remember(l) { l.items.indices.toList() }
    // the search vectors, read once the timeline is showing
    LaunchedEffect(l) { withContext(Dispatchers.IO) { l.vectors } }

    // Read the library again when a sync changes it: checked on coming back to the app, and every
    // 30 seconds while it's open. The new one is shown once nothing from the old one is open.
    var fresh by remember { mutableStateOf<Library?>(null) }
    LaunchedEffect(l, resumes) {
        while (fresh == null) {
            if (withContext(Dispatchers.IO) { l.changed() }) {
                // a copy caught half-synced can't be read: try again next time
                fresh = withContext(Dispatchers.IO) { runCatching { Library.load(context, Library.find(context)) {} }.getOrNull() }
            }
            if (fresh == null) delay(30_000)
        }
    }
    LaunchedEffect(fresh, pages.size) {
        val f = fresh ?: return@LaunchedEffect
        if (pages.isNotEmpty()) return@LaunchedEffect
        lib = f
        fresh = null
        search.redo(f)
    }

    BackHandler(enabled = pages.isNotEmpty()) { pages.removeAt(pages.lastIndex) }
    BackHandler(enabled = pages.isEmpty() && tab != 0) { tab = 0 }

    Box(Modifier.fillMaxSize()) {
        Scaffold(
            bottomBar = { SlimBar(tab) { tab = it } },
        ) { pad ->
            Box(Modifier.padding(pad)) {
                when (tab) {
                    0 -> Timeline(l, all, null, open)
                    1 -> PeopleGrid(l) { pages.add(Page.PersonPage(it)) }
                    2 -> SearchScreen(l, search, open) { pages.add(Page.PersonPage(it)) }
                    else -> ComputerScreen(pairCode, pairTaken)
                }
            }
        }
        for (p in pages) {
            when (p) {
                is Page.PersonPage -> Box(Modifier.fillMaxSize()) {
                    Scaffold { pad ->
                        Box(Modifier.padding(pad)) {
                            Timeline(l, p.person.items, p.person.name, open,
                                back = { pages.removeAt(pages.lastIndex) })
                        }
                    }
                }
                is Page.Viewer -> Viewer(l, p.items, p.start, close = { pages.removeAt(pages.lastIndex) },
                    openPerson = { pages.add(Page.PersonPage(it)) })
            }
        }
    }
}

/** Material's "image" icon (a picture with mountains), which the built-in icon set doesn't have. */
private val PictureIcon: ImageVector by lazy {
    ImageVector.Builder("Picture", 24.dp, 24.dp, 24f, 24f)
        .addPath(
            addPathNodes("M21,19V5c0,-1.1 -0.9,-2 -2,-2H5c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h14c1.1,0 2,-0.9 2,-2zM8.5,13.5l2.5,3.01L14.5,12l4.5,6H5l3.5,-4.5z"),
            fill = SolidColor(Color.Black),
        )
        .build()
}

/** The tabs: icons only, evenly spaced on a black bar, each on an oval. */
@Composable
private fun SlimBar(tab: Int, select: (Int) -> Unit) {
    Surface(color = Color.Black) {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().height(56.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            listOf(
                "Timeline" to PictureIcon, "People" to Icons.Filled.Face, "Search" to Icons.Filled.Search,
                "Computer" to Icons.Filled.Settings,
            )
                .forEachIndexed { i, (label, icon) ->
                    val color = if (tab == i) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                    Box(
                        Modifier.size(width = 72.dp, height = 38.dp).clip(CircleShape)
                            .background(MaterialTheme.colorScheme.surfaceContainer).clickable { select(i) },
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(icon, label, Modifier.size(24.dp), tint = color)
                    }
                }
        }
    }
}

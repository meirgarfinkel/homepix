package com.homepix.mobile

import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** How sending is going, read from what the sending job notes (off the screen's thread). */
private class Status(
    val computer: Computer?,
    val sent: Long = 0,
    val waiting: Int = 0,
    val problem: String = "",
    val away: String = "",
    val running: Boolean = false,
    val unpaired: Boolean = false,
)

private fun status(context: android.content.Context): Status {
    val c = Computer.load(context) ?: return Status(null)
    val s = SendState.prefs(context)
    val running = s.getLong("running", 0)
    return Status(
        c, s.getLong("sent", 0), s.getInt("waiting", 0), s.getString("problem", "").orEmpty(),
        s.getString("away", "").orEmpty(), System.currentTimeMillis() - running < 15 * 60_000,
        Computer.prefs(context).getBoolean("unpaired", false),
    )
}

/**
 * The computer this phone sends its photos to: pairing with one (scanning the code in HomePix's
 * Settings > Phones on the computer), how sending is going, and unpairing. unpaired(what happened):
 * this phone was just unpaired here.
 */
@Composable
fun ComputerScreen(pending: PairCode?, pendingTaken: () -> Unit, unpaired: (String) -> Unit = {}, said: String = "") {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var state by remember { mutableStateOf<Status?>(null) } // (null until first read)
    var busy by remember { mutableStateOf(false) }
    var unpairing by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") } // (what Send now did)
    var asking by remember { mutableStateOf(false) }
    var linkNote by remember { mutableStateOf("") } // how pairing from a link went, until OK

    fun start(code: PairCode) {
        busy = true
        message = ""
        scope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { pair(context, code) } }
            busy = false
            r.onSuccess { state = withContext(Dispatchers.IO) { status(context) } }
                .onFailure { message = it.message ?: "Pairing didn't work. Try again." }
        }
    }

    // A code opened as a link (from the camera app, or any app or web page) only pairs after a yes here,
    // and never over a pairing that's already there. It's let go of only once it's answered, so this
    // screen stays while the library is still loading.
    fun startLinked(code: PairCode) {
        busy = true
        scope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { pair(context, code) } }
            busy = false
            r.onSuccess { pendingTaken(); state = withContext(Dispatchers.IO) { status(context) } }
                .onFailure { linkNote = it.message ?: "Pairing didn't work. Try again." }
        }
    }
    LaunchedEffect(Unit) { // (how sending is going, kept up to date)
        while (true) {
            state = withContext(Dispatchers.IO) { status(context) }
            delay(2000)
        }
    }
    val scan = rememberLauncherForActivityResult(ScanContract()) { r ->
        val text = r.contents ?: return@rememberLauncherForActivityResult
        val code = PairCode.parse(text)
        if (code == null) message = "That isn't a HomePix code. Scan the one in HomePix's Settings on the computer."
        else start(code)
    }

    Column(
        Modifier.fillMaxSize().statusBarsPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Computer", style = MaterialTheme.typography.headlineSmall)
        val st = state
        val c = st?.computer
        if (st == null) {
            // (a moment, while it's read)
        } else if (busy || unpairing) {
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.padding(end = 12.dp))
                Text(if (unpairing) "Unpairing…" else "Pairing…")
            }
        } else if (c == null) {
            if (said.isNotEmpty()) Text(said, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                "Pair this phone with HomePix on your computer, and its new photos and videos are sent there by " +
                    "themselves whenever the phone is on the same Wi-Fi. Deleting on one never deletes on the other.",
            )
            Text(
                "On the computer, open HomePix's Settings, then Phones > Pair a phone…",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(4.dp))
            Button(onClick = {
                scan.launch(
                    ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setBeepEnabled(false)
                        .setOrientationLocked(false).setPrompt("Scan the code on the computer"),
                )
            }) { Text("Pair with a computer") }
        } else {
            Text("Paired with ${c.name}", style = MaterialTheme.typography.titleMedium)
            if (st.unpaired) {
                Text("${c.name} doesn't know this phone any more, so nothing is sent. Unpair, then pair again.",
                    color = MaterialTheme.colorScheme.error)
            } else {
                Text(if (st.sent > 0) "Last sent ${ago(st.sent)}" else "Nothing sent yet")
                if (st.running) Text("Sending now…")
                else if (st.waiting > 0) Text("${st.waiting} to check or send")
                if (st.away.isNotEmpty()) Text(st.away, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (st.problem.isNotEmpty()) Text(st.problem, color = MaterialTheme.colorScheme.error)
                if (note.isNotEmpty()) Text(note, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(
                "Photos and videos in DCIM are sent on Wi-Fi, soon after they're taken.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (!st.unpaired) Button(onClick = {
                    message = ""
                    note = if (Sending.metered(context)) "Sends once this phone is on Wi-Fi." else ""
                    Sending.now(context, asked = true)
                }) { Text("Send now") }
                OutlinedButton(onClick = { asking = true }) { Text("Unpair") }
            }
        }
        if (message.isNotEmpty()) Text(message, color = MaterialTheme.colorScheme.error)
    }

    val linked = pending?.takeIf { !busy && state != null }
    val now = state?.computer
    if (linked != null) {
        val note = when {
            linkNote.isNotEmpty() -> linkNote
            now != null -> "This phone is paired with ${now.name}. To pair with another computer, unpair first."
            linked.addresses.isEmpty() -> "That code isn't for a computer on this network."
            else -> null
        }
        if (note != null) {
            AlertDialog(
                onDismissRequest = { linkNote = ""; pendingTaken() },
                title = { Text("Not paired") },
                text = { Text(note) },
                confirmButton = { TextButton(onClick = { linkNote = ""; pendingTaken() }) { Text("OK") } },
            )
        } else {
            AlertDialog(
                onDismissRequest = { pendingTaken() },
                title = { Text("Pair with ${linked.name.ifBlank { "this computer" }}?") },
                text = {
                    Text(
                        "This phone's photos and videos will be sent to the computer at ${linked.addresses.first()}. " +
                            "Only go on if you just opened the code in HomePix's Settings on your own computer.",
                    )
                },
                confirmButton = { TextButton(onClick = { startLinked(linked) }) { Text("Pair") } },
                dismissButton = { TextButton(onClick = { pendingTaken() }) { Text("Cancel") } },
            )
        }
    }

    if (asking) {
        val name = state?.computer?.name ?: "the computer"
        AlertDialog(
            onDismissRequest = { asking = false },
            title = { Text("Unpair?") },
            text = { Text("This phone stops sending photos to $name, and $name is told to unpair it too. Nothing is deleted on either.") },
            confirmButton = {
                TextButton(onClick = {
                    asking = false
                    unpairing = true
                    scope.launch {
                        val told = withContext(Dispatchers.IO) { unpair(context) }
                        unpairing = false
                        state = Status(null)
                        unpaired(
                            if (told) "Unpaired from $name." else "Unpaired on this phone. $name couldn't be reached, " +
                                "so it still lists this phone: remove it there, in Settings > Phones.",
                        )
                    }
                }) { Text("Unpair") }
            },
            dismissButton = { TextButton(onClick = { asking = false }) { Text("Cancel") } },
        )
    }
}

private fun ago(ms: Long): String =
    if (System.currentTimeMillis() - ms < 60_000) "just now"
    else DateUtils.getRelativeTimeSpanString(ms, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS).toString()

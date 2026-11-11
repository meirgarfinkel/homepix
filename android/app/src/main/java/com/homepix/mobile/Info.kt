package com.homepix.mobile

import android.content.Intent
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Environment
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material3.AssistChip
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** What the photo's own file says: the camera, how it was set, and where it was taken. */
private class FileDetails(val camera: String?, val settings: String?, val lat: Double?, val lon: Double?)

private fun details(file: File, video: Boolean): FileDetails = runCatching {
    if (video) {
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(file.path)
            // a video's place is written like "+52.3700+004.8900/"
            val m = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_LOCATION)
                ?.let { Regex("([+-][0-9.]+)([+-][0-9.]+)").find(it) }
            FileDetails(null, null, m?.groupValues?.get(1)?.toDoubleOrNull(), m?.groupValues?.get(2)?.toDoubleOrNull())
        } finally {
            r.release()
        }
    } else {
        val e = ExifInterface(file)
        val make = e.getAttribute(ExifInterface.TAG_MAKE)?.trim()
        val model = e.getAttribute(ExifInterface.TAG_MODEL)?.trim()
        // "Google Pixel 7", not "Google Google Pixel 7"
        val camera = listOfNotNull(make?.takeUnless { model?.startsWith(it, true) == true }, model)
            .joinToString(" ").ifBlank { null }
        val settings = listOfNotNull(
            e.getAttributeDouble(ExifInterface.TAG_F_NUMBER, 0.0).takeIf { it > 0 }?.let { "f/%.1f".format(it) },
            e.getAttributeDouble(ExifInterface.TAG_EXPOSURE_TIME, 0.0).takeIf { it > 0 }?.let {
                if (it >= 1) "%.0f s".format(it) else "1/${(1 / it).toInt()} s"
            },
            e.getAttribute(ExifInterface.TAG_ISO_SPEED_RATINGS)?.let { "ISO $it" },
            e.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, 0.0).takeIf { it > 0 }?.let { "%.1f mm".format(it) },
        ).joinToString(" · ").ifBlank { null }
        val ll = FloatArray(2)
        val has = e.getLatLong(ll)
        FileDetails(camera, settings, if (has) ll[0].toDouble() else null, if (has) ll[1].toDouble() else null)
    }
}.getOrDefault(FileDetails(null, null, null, null))

private fun longDate(taken: String): String = runCatching {
    LocalDateTime.parse(taken.take(19), DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))
        .format(DateTimeFormatter.ofPattern("EEEE d MMMM yyyy, HH:mm", Locale.getDefault()))
}.getOrDefault(niceDate(taken))

private fun sizeText(bytes: Long) =
    if (bytes >= 1_000_000) "%.1f MB".format(bytes / 1e6) else "%d KB".format((bytes / 1000).coerceAtLeast(1))

/** Where the file is, without the start every path on the phone has. */
private fun folderText(file: File): String {
    val top = Environment.getExternalStorageDirectory().path
    val dir = file.parent ?: return ""
    return if (dir.startsWith(top)) dir.removePrefix(top).trimStart('/').ifEmpty { "Phone storage" } else dir
}

@Composable
private fun InfoRow(icon: ImageVector, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.Top) {
        Icon(icon, null, Modifier.padding(top = 2.dp, end = 16.dp).size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Column(Modifier.weight(1f)) { content() }
    }
}

@Composable
private fun Small(text: String) =
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

/** Everything known about one photo or video, in a panel from the bottom. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun InfoSheet(lib: Library, item: Item, close: () -> Unit, openPerson: (Person) -> Unit) {
    val context = LocalContext.current
    val video = item.kind == "video"
    val file by produceState<FileDetails?>(null, item) { value = withContext(Dispatchers.IO) { details(item.file, video) } }
    ModalBottomSheet(onDismissRequest = close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.padding(horizontal = 24.dp).navigationBarsPadding().padding(bottom = 16.dp).verticalScroll(rememberScrollState())) {
            item.description?.let { Text(it, Modifier.padding(bottom = 8.dp), style = MaterialTheme.typography.bodyLarge) }
            InfoRow(Icons.Filled.DateRange) {
                Text(if (item.taken.startsWith("0000")) "No date" else longDate(item.local), style = MaterialTheme.typography.bodyLarge)
            }
            if (item.people.isNotEmpty()) InfoRow(Icons.Filled.Face) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (name in item.people) {
                        val p = lib.people.firstOrNull { it.name == name }
                        AssistChip(onClick = { if (p != null) { close(); openPerson(p) } }, label = { Text(name) })
                    }
                }
            }
            InfoRow(Icons.Filled.Info) {
                Text(item.file.name, style = MaterialTheme.typography.bodyLarge)
                Small(folderText(item.file))
                val dims = if (item.width != null && item.height != null) {
                    val mp = item.width.toLong() * item.height / 1e6
                    "${item.width} × ${item.height}" + if (!video && mp >= 1) " · %.0f MP".format(mp) else ""
                } else null
                val length = item.duration?.toInt()?.let { "%d:%02d".format(it / 60, it % 60) }
                Small(listOfNotNull(sizeText(item.file.length()), dims, length).joinToString(" · "))
                file?.camera?.let { Small(it) }
                file?.settings?.let { Small(it) }
                if (item.keywords.isNotEmpty()) Small(item.keywords.joinToString(", "))
            }
            val lat = file?.lat
            val lon = file?.lon
            if (lat != null && lon != null && !(lat == 0.0 && lon == 0.0)) InfoRow(Icons.Filled.LocationOn) {
                Text("%.5f, %.5f".format(Locale.ROOT, lat, lon), style = MaterialTheme.typography.bodyLarge)
                TextButton(onClick = {
                    val geo = Uri.parse("geo:%f,%f?q=%f,%f".format(Locale.ROOT, lat, lon, lat, lon))
                    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, geo)) }
                }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) { Text("Open in Maps") }
            }
        }
    }
}

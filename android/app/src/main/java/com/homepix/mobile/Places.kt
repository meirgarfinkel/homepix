package com.homepix.mobile

import android.content.Context
import android.location.Address
import android.location.Geocoder
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.Locale
import kotlin.coroutines.resume

/** Where a photo or video was taken, as its own file says: (latitude, longitude), or null. */
fun whereTaken(file: File, video: Boolean): Pair<Double, Double>? = runCatching {
    if (video) {
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(file.path)
            // a video's place is written like "+52.3700+004.8900/"
            val m = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_LOCATION)
                ?.let { Regex("([+-][0-9.]+)([+-][0-9.]+)").find(it) } ?: return@runCatching null
            m.groupValues[1].toDouble() to m.groupValues[2].toDouble()
        } finally {
            r.release()
        }
    } else {
        val ll = FloatArray(2)
        if (ExifInterface(file).getLatLong(ll)) ll[0].toDouble() to ll[1].toDouble() else null
    }
}.getOrNull()?.takeUnless { it.first == 0.0 && it.second == 0.0 }

/**
 * The name of the town a place is in ("Bet Shemesh"), from the phone's own place lookup (the one the
 * gallery uses), kept so each place is looked up once. Null when the phone can't say (no network, say).
 */
object Places {
    private fun prefs(context: Context) = context.getSharedPreferences("places", Context.MODE_PRIVATE)

    suspend fun name(context: Context, lat: Double, lon: Double): String? {
        // about 100 m: the same town either way
        val key = "%.3f,%.3f".format(Locale.ROOT, lat, lon)
        prefs(context).getString(key, null)?.let { return it }
        if (!Geocoder.isPresent()) return null
        val found = withTimeoutOrNull(5000) { lookUp(Geocoder(context, Locale.getDefault()), lat, lon) } ?: return null
        val name = found.locality ?: found.subAdminArea ?: found.adminArea ?: found.countryName ?: return null
        prefs(context).edit().putString(key, name).apply()
        return name
    }

    private suspend fun lookUp(g: Geocoder, lat: Double, lon: Double): Address? =
        if (Build.VERSION.SDK_INT >= 33) {
            suspendCancellableCoroutine { k ->
                g.getFromLocation(lat, lon, 1, object : Geocoder.GeocodeListener {
                    override fun onGeocode(addresses: MutableList<Address>) { k.resume(addresses.firstOrNull()) }
                    override fun onError(errorMessage: String?) { k.resume(null) }
                })
            }
        } else {
            withContext(Dispatchers.IO) {
                @Suppress("DEPRECATION")
                runCatching { g.getFromLocation(lat, lon, 1)?.firstOrNull() }.getOrNull()
            }
        }
}

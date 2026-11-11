import java.io.File
import java.net.URI
import java.util.Properties

// The key every published HomePix.apk is signed with, so each new one installs over the last. GitHub's
// build gets it from the repo's secrets (HOMEPIX_KEYSTORE, HOMEPIX_KEYSTORE_PASSWORD); a computer from the
// file homepix.signing names in ~/.gradle/gradle.properties. Without either, the build is signed with
// that computer's debug key, which is fine for trying a change.
val releaseKey: Pair<File, String>? = run {
    System.getenv("HOMEPIX_KEYSTORE")?.takeIf { it.isNotEmpty() }?.let { path ->
        return@run File(path) to System.getenv("HOMEPIX_KEYSTORE_PASSWORD").orEmpty()
    }
    val file = providers.gradleProperty("homepix.signing").orNull?.let(::File)?.takeIf { it.isFile }
        ?: return@run null
    val p = Properties().apply { file.inputStream().use { load(it) } }
    File(p.getProperty("homepix.signing.store")) to p.getProperty("homepix.signing.password")
}

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.homepix.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.homepix.mobile"
        minSdk = 29
        targetSdk = 35
        versionCode = 4
        versionName = "0.4"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") } // the phone, and the emulator
    }

    signingConfigs {
        releaseKey?.let { (store, password) ->
            create("release") {
                storeFile = store
                storePassword = password
                keyAlias = "homepix"
                keyPassword = password
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    androidResources { noCompress += listOf("onnx") }
}

dependencies {
    val bom = platform("androidx.compose:compose-bom:2025.06.00")
    implementation(bom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("io.coil-kt:coil-video:2.7.0")
    implementation("androidx.media3:media3-exoplayer:1.6.1")
    implementation("androidx.media3:media3-ui:1.6.1")
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.31.1")
    // Sending photos to the computer in the background, and scanning its QR code (ZXing: offline, no Google services)
    implementation("androidx.work:work-runtime-ktx:2.10.1")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

// The search model (MobileCLIP's text half, the same one the desktop app uses) is too big for git: it's
// taken from HomePix's models folder on this computer when it's there, or else downloaded once.
val models = mapOf(
    "clip_text.onnx" to ("https://huggingface.co/Xenova/mobileclip_s0/resolve/main/onnx/text_model_int8.onnx" to 42_799_230L),
    "clip_tokenizer.json" to ("https://huggingface.co/Xenova/mobileclip_s0/resolve/main/tokenizer.json" to 2_224_081L),
)
val fetchModels by tasks.registering {
    val dir = layout.projectDirectory.dir("src/main/assets").asFile
    outputs.files(models.keys.map { File(dir, it) })
    doLast {
        val desktop = File(System.getProperty("user.home"), ".local/share/homepix/models")
        for ((name, source) in models) {
            val (url, size) = source
            val dst = File(dir, name)
            if (dst.length() == size) continue
            dir.mkdirs()
            val mine = File(desktop, name)
            if (mine.length() == size) {
                mine.copyTo(dst, overwrite = true)
                continue
            }
            val part = File(dst.path + ".part")
            URI(url).toURL().openStream().use { input -> part.outputStream().use { input.copyTo(it) } }
            check(part.length() == size) { "$name didn't download whole (${part.length()} of $size bytes)" }
            check(part.renameTo(dst)) { "couldn't keep $name" }
        }
    }
}
tasks.named("preBuild") { dependsOn(fetchModels) }

// For development: each release build is copied to a folder (dist/, or the one homepix.apkDir names in
// ~/.gradle/gradle.properties). Release, not debug: it runs several times faster.
val copyToDist by tasks.registering(Copy::class) {
    from(layout.buildDirectory.file("outputs/apk/release/app-release.apk"))
    into(providers.gradleProperty("homepix.apkDir").orElse(layout.projectDirectory.dir("../dist").asFile.path))
    rename { "HomePix-dev.apk" }
}
tasks.matching { it.name == "assembleRelease" }.configureEach { finalizedBy(copyToDist) }

# HomePix on Android

The HomePix phone app: the photos and videos on the phone, with who's in them and search from your
library (timeline, people, search), turning and trashing on the phone, and sending new photos to
HomePix on the computer over the home Wi-Fi. Paired, the computer sends it a small library file of
what it knows about the phone's photos, so the phone never has to find faces or work out search
vectors. Searching uses the same MobileCLIP text model as the desktop app.

## Build

    export JAVA_HOME=~/.local/jdk-17
    ./gradlew assembleRelease      # app/build/outputs/apk/release/app-release.apk, also copied to dist/HomePix-dev.apk for the phone
    ./gradlew testDebugUnitTest    # checks tokens and matches against the desktop app's

The search model (`clip_text.onnx` and `clip_tokenizer.json` in `app/src/main/assets`) isn't in git:
the build copies it from `~/.local/share/homepix/models/` when HomePix is installed, or downloads it
once (MobileCLIP-S0's text model, the same one the desktop app uses).

Each release build is also copied to `dist/HomePix-dev.apk`, or to the folder named by
`homepix.apkDir` in `~/.gradle/gradle.properties`.

## Put it on a phone

Android 11 or newer.

1. Install `HomePix-dev.apk` (`adb install`, or open the file on the phone), open it and allow "All files access".
2. On the computer: HomePix > Settings > Phones > Pair a phone... (pairing makes the phone's own import automation).
3. In the app: the **Computer** tab > scan the code.

Opening the code with the phone's camera app works too: HomePix asks before pairing.

Then, whenever the phone is on the same home network as the computer (Wi-Fi only):

- New photos and videos in DCIM are sent to the computer soon after they're taken, and every 15
  minutes. The computer is asked first, by content, so nothing it already has is sent again.
- The computer sends back what it knows about the photos on this phone only (who's in them, search
  data, dates), as a small file (`files/computer/.homepix/library.db` in the app's storage), and only
  when something changed. Photos not on the phone never show.

Deleting never travels either way. Everything is encrypted, and the app trusts only the computer
whose key was in the QR code. It only connects to private addresses (home, office or hotel Wi-Fi), never
the internet, and none of its data goes into Android's backup.

Not paired: the app falls back to a library folder copied to the phone with its hidden `.homepix`
folder (it looks up to 3 folders deep), as before.

## Updating

Open the [releases page](https://github.com/meirgarfinkel/homepix/releases) in the phone's browser, tap
**HomePix.apk** under the newest "Phone app" release, and install it. It installs over the copy you have
and stays paired. (From version 0.3; a copy older than that has to be uninstalled once first.)

## Releasing

1. Raise `versionCode` by one and set `versionName` in `app/build.gradle.kts`, in the pull request
   with the changes (the phone only installs a higher `versionCode` over the copy it has).
2. Once it's merged, tag the merge on `main` and push the tag:

       git tag android-v0.8 && git push origin android-v0.8

3. GitHub builds it, signs it with the release key and publishes "Phone app 0.8" with `HomePix.apk`,
   never marked latest (the desktop's update check reads the latest release).

Releases are signed with one permanent key, kept as the repo's `ANDROID_KEYSTORE` and
`ANDROID_KEYSTORE_PASSWORD` secrets. Losing it means everyone uninstalls once to move to a new key, so keep
a backup of it.

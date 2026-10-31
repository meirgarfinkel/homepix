# Packaging HomePix

This turns HomePix into something you can hand to anyone:

- **Linux:** `HomePix-x86_64.AppImage`, a single file of about 235 MB. Make it executable once, then double-click.
- **Ubuntu, Debian, Mint, Pop!_OS:** `HomePix.deb`, the same app as a package. Double-click it, and the software installer installs it.
- **Windows:** `HomePixSetup.exe`, a normal installer with Start menu and desktop shortcuts.

Both contain their own copy of Python and every library, so the people you give them to install nothing else. The AI models (about 130 MB) download automatically the first time the app looks through photos.

A bundle can only be built on the system it's for: the AppImage on Linux, the installer on Windows. If you don't have a Windows PC, GitHub can build both for you (last section).

---

## Linux AppImage

The AppImage runs on the Linux it was built on and on newer ones. Release builds are therefore made inside **AlmaLinux 9** (glibc 2.34), whatever Linux your own computer runs. The result runs on Debian 12+, Ubuntu 22.04+ (and Pop!_OS, Mint 21+), Fedora, Arch, and other distros of similar age or newer. It is x86_64 only.

1. **Install Docker or Podman** (one time). On Pop!_OS/Ubuntu: `sudo apt install podman`.

2. **Build**, from the `homepix` folder:
   ```bash
   bash packaging/build_in_container.sh
   ```
   The first time takes about 10 minutes, because it sets up the AlmaLinux build machine and downloads about 400 MB. Later builds take a few minutes. When it finishes it prints `Done: .../HomePix-x86_64.AppImage`. The build stops with an error if any bundled library would need a newer system than AlmaLinux 9.

3. **Check it works:**
   ```bash
   ./HomePix-x86_64.AppImage --self-test
   ```
   A window opens for a few seconds, then it should print `SELF-TEST OK`. After that, double-click the AppImage to use it normally. The first time it runs, it adds **HomePix** to your applications menu.

4. **Optional: check it on other distros.** This runs the self-test in a fresh Debian, Ubuntu, Fedora or Arch container. Add the models folder to also check that the AI runs:
   ```bash
   bash packaging/smoke_test.sh debian:12 HomePix-x86_64.AppImage ~/.local/share/homepix/models
   ```
   GitHub runs this on six distros for every pull request that changes the app (see below).

5. **Share that one file.** The person receiving it does this once:
   - Right-click the file, choose **Properties**, then **Permissions**, and turn on **Allow executing file as program** (in a terminal: `chmod +x HomePix-x86_64.AppImage`).
   - Double-click it.

**Quicker builds while trying things:** `bash packaging/build_linux.sh` builds directly on your computer, without a container (it needs `sudo apt install python3-venv libxcb-cursor0`). That AppImage may not run on systems older than yours.

**What's left to the computer it runs on:** graphics libraries (they must match its drivers), fonts, sound, and the C/C++ runtime. Every desktop Linux has these. Everything else is bundled.

**ExifTool** (which writes details into photos) is bundled with a Perl of its own by `packaging/bundle_exiftool.sh`: the version is pinned and checked against its published checksum, only the Perl modules it uses are included (found by watching it read and write every kind of file), and the build checks that it works with nothing but the bundle. To update it, change `VERSION` and `SHA256` there together, and the same two lines in `packaging/bundle_exiftool.ps1`, which bundles ExifTool's official Windows build (it comes with its own Perl) for the Windows installer.

If double-clicking does nothing on some computer, running it as `./HomePix-x86_64.AppImage --appimage-extract-and-run` works without the FUSE support AppImages normally use.

**The .deb:** `bash packaging/build_deb.sh` turns the AppImage into `HomePix.deb` (it needs `dpkg-deb`, on every Debian or Ubuntu). It's the same app, installed in `/opt/homepix`, with `homepix` to start it, a menu entry, the icon, and "Open With" for photos and videos. Its version comes from `homepix/__init__.py`. To check it installs and runs on a fresh system: `bash packaging/smoke_test.sh ubuntu:24.04 HomePix.deb`.

---

## Windows installer (on a Windows 10 or 11 PC)

1. **Install Python 3.11** from [python.org](https://www.python.org/downloads/). In the installer, tick **Add python.exe to PATH**.

2. **Install Inno Setup 6** from [jrsoftware.org](https://jrsoftware.org/isinfo.php). It's what turns the app into an installer.

3. **Copy the `homepix` folder** to the PC, open it in File Explorer, type `cmd` in the address bar and press Enter.

4. **Build:**
   ```
   packaging\build_windows.bat
   ```
   It shows five steps: setting up Python, bundling the app, adding ExifTool (downloaded and checked against its published checksum), a quick self-test where the app window opens and closes by itself, and making the installer. It finishes with `Done: HomePixSetup.exe`. The self-test uses a data folder of its own (`build\selftest`), never yours; put the AI models in `build\selftest\models` to have it check them too.

5. **Share `HomePixSetup.exe`.** Double-clicking it installs HomePix for that user (no administrator password needed) and adds it to the Start menu and, optionally, the desktop.

**The "Windows protected your PC" warning:** because the installer isn't digitally signed, Windows shows this warning the first time. People can click **More info**, then **Run anyway**. Removing the warning requires a code-signing certificate, which comes with a yearly fee.

---

## Let GitHub build both

`.github/workflows/build.yml` builds on GitHub's computers:

- **Every pull request** builds the AppImage in AlmaLinux 9. It then runs the self-test, including the AI models, on Debian 12 and 13, Ubuntu 22.04 and 24.04, Fedora and Arch. It makes `HomePix.deb` from it, installs it on Debian 12, Ubuntu 22.04 and 24.04, and runs the installed app's self-test there. It also builds the Windows installer and runs its app's self-test on Windows, with the AI models.
- **By hand** (Actions tab > **Build HomePix** > **Run workflow**): the same. After 15–25 minutes, download **HomePix-Linux** (the AppImage and the .deb) and **HomePix-Windows** under **Artifacts**.
- **A version tag** such as `v0.2.0` does everything and publishes a GitHub Release with all three files.

A pull request with auto-merge turned on (`gh pr merge <number> --auto --squash`) merges once CI and both builds have passed.

---

## Releasing an update

Change the code, build again, and share the new file. Each person's index, folders and people's names are stored separately (`~/.local/share/homepix` on Linux, `%LOCALAPPDATA%\homepix` on Windows), so updating or reinstalling keeps everything.

Raise the version in `homepix/__init__.py` and `AppVersion` in `packaging/homepix.iss` together, so the Windows installer replaces the old version cleanly. Then push a tag for it (`git tag v1.0.0 && git push origin v1.0.0`): GitHub builds and checks both downloads and publishes them as a Release.

## Why it's about 140 MB

Unpacked it's about 400 MB, and the AppImage compresses that. The biggest parts are OpenCV (about 150 MB unpacked), used for face detection and video frames; Qt (about 95 MB), which draws the window; and onnxruntime (30 MB), which runs the search model. The AI models aren't included: they download on first use. A later trim could run the two face models through onnxruntime and read video frames another way, dropping OpenCV.

## If something goes wrong

- Run the app with `--self-test`. It checks the AI libraries, HEIC support, that Qt can decode thumbnails, and that the window opens and loads the library, and says which part failed.
- The app writes a log to `~/.local/share/homepix/app.log` (Windows: `%LOCALAPPDATA%\homepix\app.log`).

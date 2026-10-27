# Packaging Photo Finder

This turns Photo Finder into something you can hand to anyone:

- **Linux:** `PhotoFinder-x86_64.AppImage`, a single file of about 235 MB. Make it executable once, then double-click.
- **Windows:** `PhotoFinderSetup.exe`, a normal installer with Start menu and desktop shortcuts.

Both contain their own copy of Python and every library, so the people you give them to install nothing else. The AI models (about 130 MB) download automatically the first time the app looks through photos.

A bundle can only be built on the system it's for: the AppImage on Linux, the installer on Windows. If you don't have a Windows PC, GitHub can build both for you (last section).

---

## Linux AppImage

The AppImage runs on the Linux it was built on and on newer ones. Release builds are therefore made inside **AlmaLinux 9** (glibc 2.34), whatever Linux your own computer runs. The result runs on Debian 12+, Ubuntu 22.04+ (and Pop!_OS, Mint 21+), Fedora, Arch, and other distros of similar age or newer. It is x86_64 only.

1. **Install Docker or Podman** (one time). On Pop!_OS/Ubuntu: `sudo apt install podman`.

2. **Build**, from the `photofind` folder:
   ```bash
   bash packaging/build_in_container.sh
   ```
   The first time takes about 10 minutes, because it sets up the AlmaLinux build machine and downloads about 400 MB. Later builds take a few minutes. When it finishes it prints `Done: .../PhotoFinder-x86_64.AppImage`. The build stops with an error if any bundled library would need a newer system than AlmaLinux 9.

3. **Check it works:**
   ```bash
   ./PhotoFinder-x86_64.AppImage --self-test
   ```
   A window opens for a few seconds, then it should print `SELF-TEST OK`. After that, double-click the AppImage to use it normally. The first time it runs, it adds **Photo Finder** to your applications menu.

4. **Optional: check it on other distros.** This runs the self-test in a fresh Debian, Ubuntu, Fedora or Arch container. Add the models folder to also check that the AI runs:
   ```bash
   bash packaging/smoke_test.sh debian:12 PhotoFinder-x86_64.AppImage ~/.local/share/photofind/models
   ```
   GitHub runs this on six distros for every pull request that changes the app (see below).

5. **Share that one file.** The person receiving it does this once:
   - Right-click the file, choose **Properties**, then **Permissions**, and turn on **Allow executing file as program** (in a terminal: `chmod +x PhotoFinder-x86_64.AppImage`).
   - Double-click it.

**Quicker builds while trying things:** `bash packaging/build_linux.sh` builds directly on your computer, without a container (it needs `sudo apt install python3-venv libxcb-cursor0`). That AppImage may not run on systems older than yours.

**What's left to the computer it runs on:** graphics libraries (they must match its drivers), fonts, sound, and the C/C++ runtime. Every desktop Linux has these. Everything else is bundled.

If double-clicking does nothing on some computer, running it as `./PhotoFinder-x86_64.AppImage --appimage-extract-and-run` works without the FUSE support AppImages normally use.

---

## Windows installer (on a Windows 10 or 11 PC)

1. **Install Python 3.12** from [python.org](https://www.python.org/downloads/). In the installer, tick **Add python.exe to PATH**.

2. **Install Inno Setup 6** from [jrsoftware.org](https://jrsoftware.org/isinfo.php). It's what turns the app into an installer.

3. **Copy the `photofind` folder** to the PC, open it in File Explorer, type `cmd` in the address bar and press Enter.

4. **Build:**
   ```
   packaging\build_windows.bat
   ```
   It shows four steps, including a quick self-test where the app window opens and closes by itself. It finishes with `Done: PhotoFinderSetup.exe`.

5. **Share `PhotoFinderSetup.exe`.** Double-clicking it installs Photo Finder for that user (no administrator password needed) and adds it to the Start menu and, optionally, the desktop.

**The "Windows protected your PC" warning:** because the installer isn't digitally signed, Windows shows this warning the first time. People can click **More info**, then **Run anyway**. Removing the warning requires a code-signing certificate, which comes with a yearly fee.

The window uses Microsoft's Edge engine, which is built into Windows 10 and 11. On the rare PC without it, Photo Finder opens in an Edge window instead.

---

## Let GitHub build both

`.github/workflows/build.yml` builds on GitHub's computers:

- **Every pull request** that changes the app or its packaging builds the AppImage in AlmaLinux 9. It then runs the self-test, including the AI models, on Debian 12 and 13, Ubuntu 22.04 and 24.04, Fedora and Arch.
- **By hand** (Actions tab > **Build Photo Finder** > **Run workflow**): the same, plus the Windows installer. After 15–25 minutes, download **PhotoFinder-Linux** and **PhotoFinder-Windows** under **Artifacts**.
- **A version tag** such as `v0.2.0` does everything and publishes a GitHub Release with both files.

---

## Releasing an update

Change the code, build again, and share the new file. Each person's index, folders and people's names are stored separately (`~/.local/share/photofind` on Linux, `%LOCALAPPDATA%\photofind` on Windows), so updating or reinstalling keeps everything.

On Windows, also raise `AppVersion` in `packaging/photofind.iss` so the installer replaces the old version cleanly.

## Why it's about 235 MB

Roughly half is the Chromium-based web engine that Qt uses to draw the window on Linux. OpenCV, used for face detection, is about a fifth. The search model is not included in that number; it downloads on first use. A possible later trim is running the two face models through onnxruntime (already included for search) instead of OpenCV, which would save roughly 50 MB.

## If something goes wrong

- Run the app with `--self-test`. It checks the window, the interface and the AI libraries, and says which part failed.
- The app writes a log to `~/.local/share/photofind/app.log` (Windows: `%LOCALAPPDATA%\photofind\app.log`).
- If the app window can't be created at all, Photo Finder opens in a browser instead, so it still works.

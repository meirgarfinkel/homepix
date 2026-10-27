# Packaging Photo Finder

This turns Photo Finder into something you can hand to anyone:

- **Linux:** `PhotoFinder-x86_64.AppImage`, a single file of about 245 MB. Make it executable once, then double-click.
- **Windows:** `PhotoFinderSetup.exe`, a normal installer with Start menu and desktop shortcuts.

Both contain their own copy of Python and every library, so the people you give them to install nothing else. The AI models (about 130 MB) download automatically the first time the app looks through photos.

A bundle can only be built on the system it's for: the AppImage on Linux, the installer on Windows. If you don't have a Windows PC, GitHub can build both for you (last section).

---

## Linux AppImage (on your Pop!_OS computer)

1. **Open a terminal in the `photofind` folder.**

2. **Install the two system packages the build needs:**
   ```bash
   sudo apt install python3-venv libxcb-cursor0
   ```

3. **Build:**
   ```bash
   bash packaging/build_linux.sh
   ```
   It takes a few minutes and shows four steps. The first time, it downloads about 400 MB of libraries and build tools. When it finishes it prints `Done: .../PhotoFinder-x86_64.AppImage`.

4. **Check it works:**
   ```bash
   ./PhotoFinder-x86_64.AppImage --self-test
   ```
   A window opens for a few seconds, then it should print `SELF-TEST OK`. After that, double-click the AppImage to use it normally. The first time it runs, it adds **Photo Finder** to your applications menu.

5. **Share that one file.** The person receiving it does this once:
   - Right-click the file, choose **Properties**, then **Permissions**, and turn on **Allow executing file as program** (in a terminal: `chmod +x PhotoFinder-x86_64.AppImage`).
   - Double-click it.

**Which Linux versions it runs on:** the one you built it on, and anything newer. Built on Pop!_OS 22.04, it runs on Pop!_OS and Ubuntu 22.04 or newer, Debian 12 or newer, Linux Mint 21 or newer. To cover older systems, build on an older system, or use GitHub (below), which builds on Ubuntu 22.04.

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

## No Windows PC? Let GitHub build both

The folder includes `.github/workflows/build.yml`, which builds both bundles on GitHub's computers and runs the self-test on each.

1. Create a repository on GitHub (it can be private) and upload the `photofind` folder to it, including the hidden `.github` folder.
2. Open the repository's **Actions** tab, choose **Build Photo Finder**, then **Run workflow**.
3. After 10–20 minutes, open the finished run. Under **Artifacts**, download **PhotoFinder-Linux** and **PhotoFinder-Windows** (each is a zip containing the file).

Pushing a tag such as `v0.2` also starts a build.

---

## Releasing an update

Change the code, build again, and share the new file. Each person's index, folders and people's names are stored separately (`~/.local/share/photofind` on Linux, `%LOCALAPPDATA%\photofind` on Windows), so updating or reinstalling keeps everything.

On Windows, also raise `AppVersion` in `packaging/photofind.iss` so the installer replaces the old version cleanly.

## Why it's about 245 MB

Roughly half is the Chromium-based web engine that Qt uses to draw the window on Linux. OpenCV, used for face detection, is about a fifth. The search model is not included in that number; it downloads on first use. A possible later trim is running the two face models through onnxruntime (already included for search) instead of OpenCV, which would save roughly 50 MB.

## If something goes wrong

- Run the app with `--self-test`. It checks the window, the interface and the AI libraries, and says which part failed.
- The app writes a log to `~/.local/share/photofind/app.log` (Windows: `%LOCALAPPDATA%\photofind\app.log`).
- If the app window can't be created at all, Photo Finder opens in a browser instead, so it still works.

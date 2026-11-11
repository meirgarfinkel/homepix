<p align="center">
  <img src="homepix-icon.png" alt="HomePix icon" width="96" height="96">
</p>

<h1 align="center">HomePix</h1>

<p align="center">
  <b>Find any photo in seconds, by what's in it or who's in it.</b><br>
  A private photo manager with AI search and face recognition that runs entirely on your own computer.
</p>

<p align="center">
  <a href="https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePixSetup.exe"><img src="docs/images/download-windows.svg" alt="Download for Windows" height="48"></a>
  &nbsp;
  <a href="https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePix.dmg"><img src="docs/images/download-mac.svg" alt="Download for Mac" height="48"></a>
  &nbsp;
  <a href="https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePix.deb"><img src="docs/images/download-linux.svg" alt="Download for Linux" height="48"></a>
</p>

<p align="center">
  Free and open source &nbsp;·&nbsp; Windows, Mac and Linux &nbsp;·&nbsp; Nothing uploaded, ever
</p>

<p align="center">
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="#what-it-does">Features</a> &nbsp;·&nbsp;
  <a href="docs/GUIDE.md">User guide</a> &nbsp;·&nbsp;
  <a href="#how-its-built">How it's built</a> &nbsp;·&nbsp;
  <a href="#for-developers">Contributing</a>
</p>

---

## The problem

Most of us have thousands of photos spread across folders, old phones and backup drives, with names like `IMG_4821.jpg`. Finding "that picture of Grandma at the beach" means scrolling for an hour.

Google Photos and iCloud solve this, but only by uploading your whole life to someone else's servers, and charging a monthly fee once you run out of space.

**HomePix gives you the same smart search and face recognition, but everything happens on your own computer.** Your photos stay exactly where they are, in your own folders. Nothing is uploaded, there's no account, and it works without the internet.

## What it does

- 🔍 **Search by describing the photo.** Type "dog on the beach", "birthday cake" or "snow at night" and the matching photos appear, even though none of them were ever tagged.
- 🙂 **Recognizes the people in your photos.** HomePix groups faces that look alike and asks "Who is this?". Name someone once, and they're found in every other photo, including new ones. If it gets someone wrong, correct it and it learns, even telling siblings apart.
- 🗓️ **Puts everything in order by date.** One timeline of every photo and video, newest first. HomePix works out when each one was really taken, even when the file's own date is wrong.
- 🧹 **Tidies up the mess.** Finds duplicate photos (including resized copies), renames files by the date they were taken, and sorts them into year folders. It always shows you the changes first.
- ✏️ **Edits the details.** Fix a wrong date, add a description or location, rotate, rename, move. Changes are written the standard way, so other photo apps see them too.
- ↩️ **Undo for everything.** Every change can be undone, even after closing the app. If the computer crashes in the middle of a change, HomePix finishes the job safely the next time it opens.
- 💼 **Takes your library along.** Copy your photo folder to a new computer and everything HomePix learned (names, faces, choices) comes with it.

See the **[user guide](docs/GUIDE.md)** for every feature and keyboard shortcut.

## Install

| System | What you need |
|---|---|
| **Windows** | Windows 10 or 11 (64-bit) |
| **Mac** | A Mac with Apple's own chip (M1 or newer) and macOS 14 Sonoma or newer |
| **Linux** | A 64-bit PC with Ubuntu 22.04, Debian 12, Fedora, Arch or newer |

Everything HomePix needs comes in the download. There's nothing else to install.

### Windows

1. Click **[Download for Windows](https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePixSetup.exe)** and open the downloaded `HomePixSetup.exe`.
2. If Windows shows a blue box saying it **protected your PC**, click **More info**, then **Run anyway**. (Windows shows this for apps from small developers without a paid certificate.)
3. Follow the installer. You don't need an administrator password.
4. Open HomePix from the Start menu.

### Mac

1. Click **[Download for Mac](https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePix.dmg)** and open the downloaded `HomePix.dmg`.
2. In the window that opens, drag the **HomePix** icon onto the **Applications** folder.
3. Open HomePix from Applications or Launchpad. The first time, macOS says it can't check the app for harmful software (it isn't registered with Apple's paid developer program). Click **Done**.
4. Open **System Settings** → **Privacy & Security**, scroll down to the message about HomePix, and click **Open Anyway**. Confirm with your password or Touch ID.

You only do steps 3 and 4 once. After that, HomePix opens like any other app, even after updates.

### Linux

**Ubuntu, Linux Mint, Pop!_OS, Debian and similar:**

1. Click **[Download for Linux](https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePix.deb)** and double-click the downloaded `HomePix.deb`.
2. Your software installer opens. Click **Install** and type your password.
3. Open HomePix from your applications menu.

**Fedora, Arch and other Linux:**

1. Download **[HomePix-x86_64.AppImage](https://github.com/meirgarfinkel/homepix/releases/latest/download/HomePix-x86_64.AppImage)**.
2. Right-click it, choose **Properties**, and turn on **Allow executing file as program**.
3. Double-click it. The first time, it adds HomePix to your applications menu. No installing needed, so it also works on computers where you can't install software.

### First time you open it

1. Choose the folder with your photos (or click **Use my Pictures folder**).
2. HomePix looks through your photos. A large library can take a few hours, so leaving it on overnight is a good idea. You can use the app while it works.
3. Once it's done, try a search, and open **Who is this?** to start naming people.

The first time, HomePix downloads its AI models (about 130 MB). Apart from that and checking for updates, it never uses the internet.

**Updates install themselves:** when a new version is out, HomePix downloads it and offers to restart into it. All versions are on the [Releases page](https://github.com/meirgarfinkel/homepix/releases).

## How it's built

HomePix is a native desktop app written in Python (about 13,000 lines) with a Qt interface, SQLite for storage and ONNX Runtime for the AI. Here are the main design decisions, what problem each one solved, and what it gained.

### All the AI runs on your computer, on everyday hardware

**Problem:** cloud photo apps send every photo to a server. Running the AI locally usually means a powerful graphics card.
**Decision:** small, efficient models instead of large ones: Apple's MobileCLIP-S0 for search (about 90 MB), and YuNet and SFace for faces, all run on the processor through ONNX Runtime.
**Result:** works on an ordinary laptop with no graphics card and no internet, and the photos never leave the computer.

### Searching tens of thousands of photos in about a second

**Problem:** running the AI on every photo each time someone searches would take minutes.
**Decision:** each photo is turned into a list of 512 numbers once, when it's first seen, and stored. A search turns the typed words into numbers in the same "language" and compares them with every photo in a single matrix multiplication, with all the vectors kept in memory.
**Result:** a search across tens of thousands of photos takes about a second, and the slow part only ever happens once per photo.

### Reading photos fast

**Problem:** a modern 24-megapixel photo takes a long time to open, and the library may hold tens of thousands of them.
**Decision:** JPEGs are decoded straight to a reduced size (the AI only needs 256×256), skipping most of the work. The full photo is read only when a small face needs a closer look.
**Result:** indexing is much faster than opening each photo in full, with no loss in what the AI sees.

### File changes that survive crashes and can always be undone

**Problem:** a photo app that renames and moves thousands of files can lose or duplicate photos if the power cuts out halfway.
**Decision:** every change follows the same three steps. First it's **planned** without touching anything. Then the whole plan is **written to a journal** in the database before any file moves (the same idea databases use to stay safe). Moves happen in two phases through hidden temporary names, and nothing is ever overwritten. On startup, HomePix checks the journal and **finishes any interrupted job** by looking at where the files actually are. Undo turns any finished job into its reverse.
**Result:** no photo is ever lost or doubled, and one mechanism handles undo, redo and crash recovery for every kind of change, including people and duplicate decisions.

### Recognizing a photo by its content, not its name

**Problem:** people rename and move photos, and drives get unplugged. Tracking photos by file path would forget everything about a photo once it moved.
**Decision:** each file gets a fingerprint from its size plus its first and last 64 KB, so only 128 KB is read instead of the whole file. Thumbnails and the portable library copy are tied to fingerprints, not paths. When many photos disappear at once, HomePix assumes a drive was unplugged, not that the photos were deleted.
**Result:** moved and renamed photos keep their names, faces and thumbnails, and a library can move to a new computer with everything intact.

### Learning from corrections

**Problem:** face recognition mixes up people who look alike, such as siblings.
**Decision:** when someone says "that's not them", HomePix trains a small per-person classifier (logistic regression, solved with a few steps of Newton's method in NumPy) to separate that person from the faces they were confused with.
**Result:** a couple of corrections are enough to tell look-alikes apart, without adding a machine-learning library.

### An interface that never freezes

**Problem:** decoding thousands of thumbnails and running AI can lock up an app's window.
**Decision:** a strict split between `core` (all the logic, no interface) and `ui` (only what to show). Slow work runs on background threads, and requests where only the newest answer matters (the photo just clicked, the search just typed) drop stale work automatically. The timeline only draws the rows on screen.
**Result:** scrolling a library of tens of thousands of photos stays smooth, even while indexing runs.

### Tested and shipped automatically on three systems

**Problem:** a desktop app has to work on Windows, Mac and many versions of Linux, each with its own quirks.
**Decision:** 260 automated tests run on Linux, Windows and Mac for every change, and every pull request is built into real installers. Each build runs the app's own `--self-test` (window, thumbnails and AI). The Linux version is built on an older Linux (AlmaLinux 9) so it runs on newer ones, and is tested on six Linux distributions. Pushing a version tag builds and publishes all four downloads, and the app updates itself from there.
**Result:** a release takes one command, and problems are caught before anyone downloads them.

## For developers

Contributions are welcome. To run HomePix from source (Python 3.10–3.12):

```bash
git clone https://github.com/meirgarfinkel/homepix.git
cd homepix
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m homepix.ui                           # run the app
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q  # run the tests
```

Set `HOMEPIX_HOME=/some/folder` to use a separate test library, so you don't touch your own. Writing details into photos from source needs ExifTool (`sudo apt install libimage-exiftool-perl`, or `brew install exiftool` on a Mac); the downloads bundle their own.

**The code in brief:**

```
homepix/
  core/   all the logic, with no interface: database, indexing, dates, people,
          search, duplicates, organizing, file changes. Fully testable without a window.
    ai/   the search and face models
  ui/     the desktop app (PySide6 / Qt): the window, its pages and menus
tests/    pytest suite, with fake AI models so it runs in seconds
packaging/  build scripts for the Windows installer, Mac disk image, AppImage and .deb
android/  the phone app (Kotlin, Jetpack Compose): sends new photos to HomePix and shows
          the phone's own photos with their people and search (see android/README.md)
```

**Where to read next:**

- **[CONTRIBUTING.md](CONTRIBUTING.md)**: setup, code checks, branches, pull requests and releases
- **[ARCHITECTURE.md](ARCHITECTURE.md)**: how the pieces fit together, step by step
- **[BUILDING.md](BUILDING.md)**: building the installers yourself
- **[REQUIREMENTS.md](REQUIREMENTS.md)**: what's in scope for the app

To contribute: fork the repo, make a branch, and open a pull request. The tests and code checks run automatically on every pull request, and changes are merged once they pass and have been reviewed.

## Credits and license

HomePix is free software under the [GNU General Public License v3.0](LICENSE). You can use, change and share it, including for money, but anything you distribute that's built on it must also be released under the GPL-3.0, with its full source code.

The AI models and font keep their own licenses:

| Part | Size | License |
|---|---|---|
| MobileCLIP-S0 image and text models ([Xenova/mobileclip_s0](https://huggingface.co/Xenova/mobileclip_s0)) | 45 + 43 MB | Apple sample code license (allows use, changes and sharing) |
| YuNet face detector (OpenCV model zoo) | 0.2 MB | MIT |
| SFace face recognizer (OpenCV model zoo) | 39 MB | Apache-2.0 |
| [Plus Jakarta Sans](https://github.com/tokotype/PlusJakartaSans) font | | SIL Open Font License |

Read the Apple license yourself before using the search model in a commercial product.

# HomePix — requirements

A complete, private photo manager for the desktop, with AI search and face recognition built in.
Agreed 2026-10-27. **v1** = first release, **v2** = next release, **Later** = not designed now but not ruled out.

## Ground rules (apply to everything)

1. **Private and local.** Nothing leaves the computer; the only network access is the one-time model download.
   (Setting up Syncthing, section 9, talks only to the Syncthing running on this computer; its Install button hands any download to the system's own installer.)
2. **Never changes files on its own.** Indexing, thumbnails and AI analysis run in the background but only read.
   Renaming, moving, deleting and editing happen only when the user starts them, after a preview. The one
   exception is an import automation the user has set up and not paused (section 9): it brings new files in from
   its "Import from" folder by itself, and every batch is journaled and undoable like any other change.
3. **A file is never lost.** Delete goes to the Trash; moves never overwrite; every change is journaled,
   undoable, and recoverable after a crash.
4. **The index never drifts from the disk.** Every change made through the app updates the database in the
   same step. Outside changes are detected safely; an unplugged drive is never treated as deleted photos.
5. **Scale.** Fast at today's ~30,000 photos + 3,560 videos; designed for 250,000. Smooth grid scrolling,
   search under 200 ms, bounded memory.
6. **Platforms.** Any mainstream Linux distribution, not just Pop!_OS: Debian, Ubuntu and derivatives,
   Fedora, Arch and others, on GNOME, KDE, Cinnamon or XFCE, with Wayland or X11. Then Windows. One download
   per platform, nothing else to install. Written in Python, with a native Qt (PySide6 widgets) interface.
   - Linux floor: glibc 2.34 (Debian 12+, Ubuntu 22.04+, Mint 21+, Fedora, RHEL/Rocky/Alma 9, Arch, openSUSE
     Tumbleweed). Debian 11 and Ubuntu 20.04 are not supported.
   - Linux packaging: AppImage only, x86_64 only (no Flatpak, .deb or ARM for now). Everything it needs is
     bundled (Qt's X11 and Wayland plugins, libxcb-cursor, exiftool with its own Perl, HEIC support).
7. **Existing data carries over.** The current index, 41,000 faces and 27 named people survive the move to
   the new app.

## 1. Library and formats

| Requirement | Priority |
|---|---|
| Several library folders, including removable drives; a disconnected drive's photos stay browsable as offline | v1 |
| Photos: JPEG, PNG, HEIC, WebP, GIF, TIFF, DNG (DNG via its embedded preview) | v1 |
| Videos (MP4, MOV): shown in the timeline and folders with a plain thumbnail, **played in the viewer** (Qt Multimedia with FFmpeg: H.264, HEVC and more; turned the way the phone held it), included in rename/organize and exact dedupe. **No AI** (no search, no faces) | v1 |
| Live Photos (HEIC + MOV) and sidecars (`.aae`, `.xmp`, `.json`) always kept together as one item | v1 |
| Import from a folder, SD card or phone: preview, copy into year folders, optional rename, skip what's already in the library. Built as import automations (section 9) | v1 |
| A library folder carries what's known about its photos (faces and names, search, duplicate choices, folders left out) in `.homepix/`, kept up to date by itself: copied to another computer (any path, any system), it's brought back in without indexing again. Photos are matched by content | v1 |
| Other RAW formats (CR2, NEF, ARW, ...) | v2 |

## 2. Browsing

| Requirement | Priority |
|---|---|
| **Timeline** (main view): everything by date, grouped by month and year, with a scrubber | v1 |
| Folders: the library's real folder tree | v1 |
| People: a page per person | v1 |
| Search results | v1 |
| Viewer: full screen, zoom, previous/next. A video plays as soon as it's shown, with controls over its bottom that fade while it plays: play/pause, time, a bar to jump with, sound on/off (kept while the app runs), open in another app. Click or Space/K/Enter plays or pauses, J and L jump 10 s, M mutes, double-click is full screen; it stops on leaving it | v1 |
| Info panel: date **and where it came from**, camera, size, dimensions, GPS, people, path | v1 |

## 3. Organizing (real folders only; no virtual albums)

| Requirement | Priority |
|---|---|
| Keywords, written into the file | v1 |
| Organizer: rename to `YYYY_MM_DD_HH_MM_SS.ext` and sort into `<library>/<year>/`; no real date goes to `Unsorted/`. Two modes: *organize a folder* and *tidy the library*. Manual, previewed, undoable. Rules from the Sep 2026 pipeline (`~/Pictures/.claude_pipeline_history/`) | v1 |

## 4. File operations

| Requirement | Priority |
|---|---|
| Selection: click, Ctrl/Shift-click, Ctrl+A, drag-box, keyboard | v1 |
| Right-click menu, toolbar, keyboard shortcuts | v1 |
| Copy / cut / paste / move into folders; rename (single and batch) | v1 |
| Ctrl+C / Ctrl+V through the **system clipboard**, so files paste into the file manager and other apps | v1 |
| Delete to Trash (the Recycle Bin on Windows); undo and redo (Ctrl+Z / Ctrl+Shift+Z) for every operation | v1 |
| Export (copy out, optionally resized or converted to JPEG); open in an external editor; show in folder | v1 |

## 5. Metadata (written into the files)

| Requirement | Priority |
|---|---|
| View all metadata | v1 |
| Edit date taken: one photo, a batch, and "shift by N hours" | v1 |
| Edit description, keywords, GPS | v1 |
| Writing: via exiftool, file modification time kept, old values journaled for undo, index fingerprint updated in the same step (so faces and names survive) | v1 |
| Faces and people stay in the app's database | v1 |

## 6. Editing

| Requirement | Priority |
|---|---|
| Lossless rotate and flip (orientation tag) | v1 |
| Open in an external editor (no in-app editing beyond rotate) | v1 |

## 7. AI (photos only)

| Requirement | Priority |
|---|---|
| Search by description | v1 |
| Faces: boxes in the viewer; name, rename, merge, split, fix wrong matches; the "who is this" flow | v1 |
| Automatic scene labels (screenshots, documents, receipts, food, beach, ...) using the existing search model, and filtering by them | Later |
| Burst grouping with best-shot suggestions | v2 |
| Search text inside photos (OCR) | Later |
| Place names from GPS, offline | Later |

## 8. Duplicates

| Requirement | Priority |
|---|---|
| Exact duplicates (byte-identical), photos and videos | v1 |
| Near duplicates (resized/recompressed): perceptual hash, cross-checked with search-model similarity; bursts never touched | v1 |
| Keeper: highest resolution, then larger file; manual override | v1 |
| Review screen: side-by-side groups; send extras to the Trash in bulk, with a preview, or skip a group for later; never automatic, and never a `Duplicates/` folder | v1 |
| Before its extras go, the keeper takes what only they have: the date taken (when it has none, or a filler at 00:00:00 and an extra has a real time; it's then renamed if named by its date), the location, the description, and their keywords (added to its own); nothing else it has is replaced; shown in the preview, and undone with the rest | v1 |

## 9. Import automations (agreed 2026-11-08)

Photos arrive in a folder (the automation's **Import from** folder) by some other app (Syncthing from an Android phone; iCloud for Windows,
osxphotos or icloudpd for an iPhone) and HomePix brings them into the library. HomePix itself never goes on
the network and never talks to the phone or the sync app.

| Requirement | Priority |
|---|---|
| **Import automations** in Settings: new, edit, pause, remove; as many as wanted (one per phone, say) | v1 |
| Each automation: **Name**; **Import from** (not a whole library folder and not containing one; no two automations on one folder; one inside the library is added to Excluded folders, after a confirmation, and its photos never count as already in the library or as duplicates); **Rename and organize into year folders** (right under Name, above Import from, off by default: rename `YYYY_MM_DD_HH_MM_SS.ext` and sort into `<library>/<year>/`, `Unsorted/` with no date, same rules as the organizer; Import to then becomes a choice of library folder); **Import to** (any folder in the library; files keep their names); **Empty the Import from folder after importing** (on by default; off: files are copied and left there); **Ask me before importing** (off by default); **Pause automation** | v1 |
| Checked when the app opens; then the watch folders are looked at **every minute** (only listed, no file read) and checked when something new has finished arriving, and fully **every hour** (no live watching); a **Check now** button in Settings | v1 |
| A file is only taken once it's been unchanged for 2 minutes; hidden files and sync apps' part-files (`.syncthing.*.tmp`, `*.part`, `.~*`) are ignored; Windows cloud-only placeholders are skipped, never downloaded | v1 |
| Already in the library (same content) → not imported again. When emptying the folder, such a file goes to the Trash; otherwise it's left and remembered | v1 |
| A file that comes back changed to the same place in Import from (turned or edited on the phone) is a new version of the photo it became: it takes that photo's place, wherever it is now, and the old version goes to the Trash. Who's in it is kept when only its orientation changed (the face boxes turn with it); an edit is indexed again. Undo puts the old version back | v1 |
| Live Photo pairs and sidecars travel with their photo. Names never overwrite (`_1`, `_2` ...). Every batch is one journaled operation: Undo puts it back as it was | v1 |
| After an automatic batch, a line in the status bar at the bottom: "Imported 37 photos from Dad's phone" (no bar across the window); Edit > Undo names it and takes it back | v1 |
| An automation set to ask first: a bar with the count of new files; **Review** shows them as thumbnails (untick to leave one for next time; hover to see where it will go); Import or Cancel | v1 |
| A missing or unplugged Import from or Import to folder: nothing happens and nothing is treated as deleted; Settings shows why it's waiting | v1 |
| The automation screen warns that emptying the folder deletes from the phone with two-way sync (Syncthing must be Receive Only on the computer) and from iCloud with iCloud for Windows; with iCloud or icloudpd, leave it off | v1 |
| Only while the app is running. **Keep running in the background** (Settings, off by default): HomePix starts hidden at each login (an autostart entry on Linux, the Startup folder on Windows, a LaunchAgent on macOS), and closing the window hides it instead of quitting; opening HomePix shows it again; File > Quit stops it until the next login. While it runs this way, an icon in the system tray (where the desktop has one) opens the window, and its menu has Open HomePix and Quit | v1 |
| Settings shows, per automation, when it last checked and last imported | v1 |
| **Set up a phone with Syncthing...** (under Import from): finds the Syncthing running on this computer (its own settings file gives the address and key; only ever this computer); shows this computer's ID as a QR code, with steps (Syncthing-Fork on Android, Möbius Sync on iPhone); **Connect** when the phone asks to; the folder the phone then shares is taken as **Receive Only** into the Import from folder (or `~/Phone photos/<phone>`), so emptying it never deletes anything on the phone. Device ID has a Copy button. Not installed: **Install Syncthing** hands it to the system's own installer (pkexec with apt/dnf/pacman; winget; Homebrew), then starts it; installed but not running: **Start Syncthing** starts it and has it start at each login (systemd user service, else an autostart entry; a Startup-folder script on Windows; brew services or the app on macOS). Either way the same commands show in a box with Copy, and a Download page button; Try again | v1 |

## Not planned

Virtual albums and smart albums; map view; crop and image adjustments; drag-and-drop out of the app;
video search and video faces; star ratings (built in 0.5.0, then dropped as not needed); cloud sync;
multiple users; mobile app; online sharing.

# HomePix — requirements

A complete, private photo manager for the desktop, with AI search and face recognition built in.
Agreed 2026-10-27, brought up to date 2026-11-11 (HomePix 1.16.4, phone app 0.5). **v1** = in the app now,
**v2** = next, **Later** = not designed now but not ruled out.

## Ground rules (apply to everything)

1. **Private and local.** No photo or anything about one leaves the computer. The only internet access is the
   one-time model download and the update check (an installed copy asks GitHub for the newest release at
   startup and downloads it quietly; a copy run from the source code never does). The AI library's own
   usage reports (ONNX Runtime's, to Microsoft) are turned off on the computer and the phone.
   The one exception is paired phones (section 10): HomePix answers them on the home network only, encrypted, and
   only phones paired with it. Nothing goes through outside servers.
2. **Never changes files on its own.** Indexing, thumbnails and AI analysis run in the background but only read.
   Renaming, moving, deleting and editing happen only when the user starts them, after a preview. The one
   exception is an import automation the user has set up and not paused (section 9): it brings new files in from
   its "Import from" folder by itself, and every batch is journaled and undoable like any other change. The
   other is the one-time rename to local time in 1.17 (section 3): the owner chose it, it runs once, and Undo
   takes it all back.
3. **A file is never lost.** Delete goes to the Trash; moves never overwrite; every change is journaled,
   undoable, and recoverable after a crash.
4. **The index never drifts from the disk.** Every change made through the app updates the database in the
   same step. Outside changes are detected safely; an unplugged drive is never treated as deleted photos.
5. **Scale.** Fast at today's ~30,000 photos + 3,560 videos; designed for 250,000. Smooth grid scrolling,
   search under 200 ms, bounded memory.
6. **Platforms.** Linux, Windows and Mac. Any mainstream Linux distribution, not just Pop!_OS: Debian, Ubuntu
   and derivatives, Fedora, Arch and others, on GNOME, KDE, Cinnamon or XFCE, with Wayland or X11. Windows 10
   and 11. A Mac with Apple's own chip on macOS 14 or newer. One download per platform, nothing else to install.
   Written in Python, with a native Qt (PySide6 widgets) interface. Android phones get their own app (section 11).
   - Linux floor: glibc 2.34 (Debian 12+, Ubuntu 22.04+, Mint 21+, Fedora, RHEL/Rocky/Alma 9, Arch, openSUSE
     Tumbleweed). Debian 11 and Ubuntu 20.04 are not supported.
   - Linux packaging: a .deb (the download the README offers) and an AppImage, x86_64 only (no Flatpak, RPM or
     ARM for now). Everything it needs is bundled (Qt's X11 and Wayland plugins, libxcb-cursor, exiftool with
     its own Perl, HEIC support).
   - Each kind of install updates itself from the matching file of the newest desktop release (the highest
     `vX.Y.Z`, whatever GitHub marks latest). The file must come from HomePix's own GitHub releases and match
     the release's SHA-256, or it isn't downloaded; one already downloaded that still matches is kept. The
     AI models are pinned to one revision each, every file with its SHA-256.
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
| No hover tips (they got in the way). Whatever the user needs to know is visible text; an icon-only control keeps a tip only with the owner's approval | v1 |

## 3. Organizing (real folders only; no virtual albums)

| Requirement | Priority |
|---|---|
| Keywords, written into the file | v1 |
| Organizer: rename to `YYYY_MM_DD_HH_MM_SS.ext` and sort into `<library>/<year>/`; no real date goes to `Unsorted/`. Two modes: *organize a folder* and *tidy the library*. Manual, previewed, undoable. Rules from the Sep 2026 pipeline (`~/Pictures/.claude_pipeline_history/`) | v1 |
| Every date is the time on the clock where it was taken (local time). A video's clock and a Pixel's `PXL_` name are UTC, so they're turned into local time; a name and the file that say the same moment a time zone apart (a quarter hour to 14 hours, to the quarter hour) are one date, the file's; a name with only a day never beats a time on that day | v1 |
| One-time rename to local time (1.17): once indexing has read every date again, the files the organizer named by UTC before (videos, Pixel photos), now a time zone away from their date, are renamed to it (into the next year's folder when that changes), as one change Undo takes back. It never runs again, even after Undo | v1 |

## 4. File operations

| Requirement | Priority |
|---|---|
| Selection: click, Ctrl/Shift-click, Ctrl+A, drag-box, keyboard | v1 |
| Right-click menu, toolbar, keyboard shortcuts | v1 |
| Copy / cut / paste / move into folders; rename (single and batch) | v1 |
| Ctrl+C / Ctrl+V through the **system clipboard**, so files paste into the file manager and other apps | v1 |
| Delete to Trash (the Recycle Bin on Windows); undo and redo (Ctrl+Z / Ctrl+Shift+Z) for every operation. Delete on more than 20 photos asks first. Ctrl+A selects only on the photos page | v1 |
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

Photos arrive in a folder (the automation's **Import from** folder) by the HomePix phone app (section 10) or some other app
(iCloud for Windows, osxphotos or icloudpd for an iPhone), and HomePix brings them into the library.

| Requirement | Priority |
|---|---|
| **Import automations** in Settings: new, edit, pause, remove; as many as wanted (one per phone, say) | v1 |
| Each automation: **Name**; **Import from** (not a whole library folder and not containing one; no two automations on one folder; one inside the library is added to Excluded folders, after a confirmation, and its photos never count as already in the library or as duplicates); **Rename and organize into year folders** (right under Name, above Import from, off by default: rename `YYYY_MM_DD_HH_MM_SS.ext` and sort into `<library>/<year>/`, `Unsorted/` with no date, same rules as the organizer; Import to then becomes a choice of library folder); **Import to** (any folder in the library; files keep their names); **Empty the Import from folder after importing** (on by default; off: files are copied and left there); **Ask me before importing** (off by default); **Pause automation** | v1 |
| Checked when the app opens; then the watch folders are looked at **every minute** (only listed, no file read) and checked when something new has finished arriving, and fully **every hour** (no live watching); a **Check now** button in Settings | v1 |
| A file is only taken once it's been unchanged for 2 minutes (a file time in the future doesn't hold it back). A paired phone's file arrives whole (section 10), so it's taken at once: a check runs 10 seconds after the last one arrives. Hidden files and sync apps' part-files (`*.tmp`, `*.part`, `.~*`) are ignored; Windows cloud-only placeholders are skipped, never downloaded | v1 |
| Already in the library (same content) → not imported again. When emptying the folder, such a file goes to the Trash; otherwise it's left and remembered | v1 |
| A file that comes back changed to the same place in Import from (turned or edited on the phone) is a new version of the photo it became, but only when it's the same picture (the same pixels: turned) or has the same date taken (edited). Anything else at that place (a reused name: WhatsApp after a reinstall, a camera after its card was formatted) comes in as a new photo, and nothing is replaced. A new version takes that photo's place, wherever it is now, and the old version goes to the Trash. Who's in it is kept when only its orientation changed (the face boxes turn with it); an edit is indexed again. Undo puts the old version back | v1 |
| Live Photo pairs and sidecars travel with their photo. Names never overwrite (`_1`, `_2` ...). Every batch is one journaled operation: Undo puts it back as it was | v1 |
| After an automatic batch, a line in the status bar at the bottom: "Imported 37 photos from Dad's phone" (no bar across the window); Edit > Undo names it and takes it back | v1 |
| An automation set to ask first: a bar with the count of new files; **Review** shows them as thumbnails (untick to leave one for next time; a line under each says where it will go); Import or Cancel | v1 |
| A missing or unplugged Import from or Import to folder: nothing happens and nothing is treated as deleted; Settings shows why it's waiting | v1 |
| The automation screen warns that emptying the folder deletes from the phone with two-way sync and from iCloud with iCloud for Windows; with iCloud or icloudpd, leave it off | v1 |
| Only while the app is running. **Keep running in the background** (Settings, off by default): HomePix starts hidden at each login (an autostart entry on Linux, the Startup folder on Windows, a LaunchAgent on macOS), and closing the window hides it instead of quitting; opening HomePix shows it again; File > Quit stops it until the next login. While it runs this way, an icon in the system tray (where the desktop has one) opens the window, and its menu has Open HomePix and Quit | v1 |
| Settings shows, per automation, when it last checked and last imported | v1 |

## 10. Phones with the HomePix app (agreed 2026-11-11, built in 1.16)

The HomePix Android app sends the phone's new photos and videos straight to HomePix over the home network,
and HomePix sends back what it knows about the photos on that phone.

| Requirement | Priority |
|---|---|
| **Phones** in Settings: the paired phones (name, the automation its photos go into, when it last sent one); **Pair a phone...**; **Unpair** (its secret stops working at once; photos already sent stay) | v1 |
| **Each phone its own automation:** pairing makes one for the phone once it has paired (named after it; its photos arrive in `Phone photos/<its name>` in the home folder, a new folder, numbered when the name's taken; renamed by date into year folders in the first library folder; that folder excluded when it's inside the library), or uses an existing one no other phone uses. Two phones never share one: their files can have the same names (WhatsApp's), and one phone's would be taken for a changed version of the other's. Unpairing removes an automation made for the phone, unless files are waiting in its folder. The automation list shows which phone uses each one ("from Pixel 7"); removing a phone's automation says so and unpairs the phone too | v1 |
| **Pairing:** a QR code holds this computer's addresses, its key's fingerprint and a one-time code (works once, for 10 minutes, only while the window is open; a new one is shown when it runs out). After a wrong code, that device waits before it may try again (1 s, then twice as long each time, up to a minute); wrong codes never close the pairing, so no one else on the network can. The phone trades the code for a secret of its own; only its SHA-256 is kept on the computer | v1 |
| **Encrypted:** TLS 1.2 or newer, with this computer's own key, made once (kept in the data folder, readable only by the user); the phone trusts only the key whose fingerprint was in the QR code. No certificates to buy or renew | v1 |
| **Private networks only:** addresses outside private, loopback and link-local ranges are never answered; nothing goes through outside servers. Only the addresses the computer announces answer (not a VPN's, a container's or a virtual machine's), and at most 8 connections from one device, 48 in all. The secret is checked before a request's body is read. Works with the computer on a cable or Wi-Fi, as long as both are on the same private network (a hotel's too, unless it keeps its devices apart). The phone, too, only ever connects to a numeric private, link-local or loopback address: never a web name or a public address, whatever a QR code or link says | v1 |
| **Found on the network:** announced as `_homepix._tcp` (mDNS), and announced again within a minute when the computer's addresses change, so a phone finds the computer when its address changes; the QR code's addresses, and the last one that worked, are tried too (for routers that block the announcements). Port 47820, or another kept for next time when that's taken | v1 |
| **Each phone has its own secret:** several phones can be paired at once, each with its own automation and its own library file; unpairing one never touches another | v1 |
| **Only what's needed is sent:** the phone asks first by each file's quick fingerprint (its size and first and last 64 KB, read in a moment), and makes the SHA-256 of the whole file only for those the library doesn't have; then it asks again by content. Content in the library, or imported before (even if since deleted, or its import undone), is never sent again; so neither a reinstall nor a lost record sends everything again. Uploads cut off and not gone on with for a week are dropped | v1 |
| **Arrives whole:** a file is written to a hidden folder in Import from (`.homepix-incoming`), its content checked, given the phone's time, and only then put at the phone's place for it (Camera/..., WhatsApp/...). An upload cut off goes on from where it stopped. A changed photo (turned on the phone) replaces the one at that place, so the automation updates the library's copy (section 9) | v1 |
| **Deletes never travel,** either way | v1 |
| Paths checked: no way out of the folder, no hidden or odd names, photos and videos only. A file isn't taken when it would leave less than 1 GB free | v1 |
| **The library's details, back to the phone:** the phone tells the quick fingerprints of its photos and videos and the places of those in DCIM; HomePix answers with a small file in the same form as a folder's portable copy, holding only the photos the phone has (same fingerprint, or imported from that place in this phone's own Import from folder), their faces, the people in them and their search data, without excluded folders or the undo history. Made again only when the library, its folders or the phone's files changed; otherwise "the same" (no file). Deleted with the phone when it's unpaired | v1 |
| Answers only while HomePix runs (Keep running in the background covers a closed window) and a phone is paired or being paired; a paused or removed automation takes nothing (the phone is told why) | v1 |

## 11. The HomePix Android app (built 2026-11, phone app 0.5)

A viewer for the photos on the phone, with what the computer knows about them, and the sender for section 10.
Android 11 or newer (minSdk 30, target 35). Kotlin and Jetpack Compose, in `android/`.

| Requirement | Priority |
|---|---|
| **Shows only what's on the phone.** Every photo and video it shows is a file on this phone; photos only on the computer never show. No previews or copies come from the computer, only the small library file (section 10), so the phone holds one copy of each photo | v1 |
| Tabs: **Timeline** (by month, newest first, pinch to change the columns, a handle on the right edge to drag through the years), **People** (a page per person, their faces cut from photos on the phone), **Search** (by description with the same MobileCLIP text model as the desktop, run on the phone; names in the search find those people), **Computer** (pairing, how sending is going, unpairing) | v1 |
| Viewer: swipe between photos, pinch to zoom, videos and motion photos play, swipe up for the details (date, camera, settings, size, people, where it was taken as coordinates with **Open in Maps**, path). No place names: the phone's lookup would send where each photo was taken to Google. Offline place names are Later (section 7) | v1 |
| **Turn:** a quarter turn written into the photo's own orientation (only the tag changes, never the picture). The changed file goes to the computer, which updates its copy and keeps who's in it (section 9). A HEIC or mirrored photo is turned in the app only | v1 |
| **Share** through Android's share sheet; **Trash** sends the file to the phone's own trash (or, for a file that can't go there, deletes it after asking). Neither ever touches the computer's copy | v1 |
| **Sending** (section 10): on Wi-Fi only (an unmetered network); soon after a photo or video is taken, every 15 minutes, when the app opens, and once more 2 minutes after a batch is sent; in small batches at background priority, so the app and the phone never stall. Away from the home network nothing is tried for long, and photos wait for the next time | v1 |
| **The library file comes first:** fetched before sending, again after a batch, and 2 minutes later, so a new photo shows its faces and search within minutes of being imported. Just after pairing it keeps looking until the library arrives, without a tap on "Look again" | v1 |
| **Pairing from a link** (a `homepix:pair?...` code opened by the camera app, another app or a web page) asks first, naming the computer and its address, and is refused while the phone is paired; the in-app scanner pairs at once | v1 |
| **Nothing leaves the phone in a backup:** Android's cloud backup and device-to-device transfer take none of the app's data (the pairing secret, the library file, what was sent). A new phone pairs again | v1 |
| Not paired: it reads a library folder copied to the phone with its hidden `.homepix` folder (up to 3 folders deep), with "All files access" | v1 |
| **Updates:** each release is a GitHub Release ("Phone app 0.x", never marked latest, so the desktop's update check isn't confused), signed with one permanent key, so it installs over the last and stays paired | v1 |
| Upload from mobile data, or from away from home (a VPN to the computer) | Later |

## Not planned

Virtual albums and smart albums; map view; crop and image adjustments; drag-and-drop out of the app;
video search and video faces; star ratings (built in 0.5.0, then dropped as not needed); cloud sync;
multiple users; online sharing.

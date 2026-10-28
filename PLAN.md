# Photo Finder — build plan

How we get from today's app to the one in `REQUIREMENTS.md`. Each milestone ends with something that works
and has been checked; the current app stays usable until the new one reaches feature parity (end of M3).

## Architecture

Two layers. The core never imports Qt, so it can be tested on its own and the CLI keeps working.

```
photofind/
  core/                 no UI code
    db.py               schema, versioned migrations (PRAGMA user_version), backup before migrating
    library.py          scanning, indexing, offline drives, outside-change detection
    media.py            formats, dates (3-tier rule, date source recorded), sidecars + Live Photo pairing
    ai/clip.py          search vectors (MobileCLIP, ONNX)
    ai/faces.py         face detection + recognition (YuNet / SFace)
    ai/labels.py        scene labels (zero-shot, reuses the search model)
    search.py           in-memory vectors (half precision, chunked scoring)
    people.py           clustering, auto-tagging, merge/split
    thumbs.py           thumbnail cache (named by content fingerprint)
    fileops.py          THE only code that changes files: journal, Trash, no-overwrite moves,
                        undo/redo, index updated in the same step, indexer paused while working
    metadata.py         exiftool (one long-running process): read all, write date/description/
                        keywords/GPS/orientation, old values journaled
    organize.py         plan / preview / apply via fileops (both modes)
    dedupe.py           exact + near duplicates, keeper rule, adopt Duplicates/ + mapping CSV
  cli.py                the command line, on top of core
  ui/                   PySide6 widgets
    app.py, main_window.py
    models/             grid/timeline list models (virtualized), people, folders
    views/              timeline, folders, people, search, viewer, info panel, organizer,
                        duplicates review, settings
    workers.py          background jobs (QThreadPool) reporting through signals
    undo.py             QUndoStack on top of the fileops journal
    desktop.py          system clipboard, show in folder (D-Bus FileManager1), open / open with
    video.py            video thumbnail frames (Qt Multimedia)
packaging/              container build (AlmaLinux 9), AppImage, Windows, smoke tests
tests/
```

## Milestones

### M0 — Make the current app safe (step 0)
Work on today's code, before any rewrite, so your data is clean when it migrates.
- #9: never delete index rows because a folder looks empty or unreadable (unmounted drive, permissions).
- #15: duplicate fingerprints no longer leave ghost rows.
- `.photofind-ignore` in the `photofind/` project folder (it's inside `~/Pictures` and currently indexed).
- Enable `pillow-heif` (175 HEIC photos are invisible today).
- #12: retry unreadable photos when the reason may have gone away; read slightly truncated JPEGs.

**Done when:** tests cover each fix, and a re-index of your library loses nothing and adds the HEIC photos.

### M1 — Core package and migrations
- Split `photofind.py` into `core/` + `cli.py`. The behavior stays the same.
- Versioned schema migrations. The first migration adds what the new app needs:
  date source, media type, file fingerprint, sidecar links, journal tables.
- A test suite on synthetic libraries.

**Done when:** the CLI works as before, and a migrated copy of your real database has the same photos,
faces and 27 named people.

### M2 — Build pipeline first (de-risk cross-distro early)
- GitHub Actions builds in an AlmaLinux 9 container (glibc 2.34) and produces the AppImage, with
  everything in the requirements bundled. At first it's a minimal Qt window plus the AI self-test.
- A smoke-test matrix: Debian 12 and 13, Ubuntu 22.04 and 24.04, Fedora, Arch (headless, `--self-test`).

**Done when:** the same AppImage passes on every distro in the matrix and opens on your machine.

*Status: built and passing on all six distros. The Qt window here is still the current pywebview one; the native Qt app comes in M3.*

### M3 — Qt app: browsing at parity
- Main window, timeline (virtualized grid, month/year headers, scrubber), folders, viewer, info panel.
- Thumbnail pipeline: background loading, disk cache, video frames.
- Background indexing with live progress, search, the people page (read-only), and settings.

**Done when:** your library (about 30,000 photos plus 3,560 videos) scrolls smoothly, search answers in
under 200 ms, memory is measured, and it does everything the current app does. Then it replaces the
current app.

Delivered in three steps; the current app keeps working until the last:
- **M3a:** window, timeline (month headers, year scrubber, selection, keyboard), thumbnail loading,
  viewer, info panel. Run it with `python -m photofind.ui`; the installer adds "Photo Finder (new,
  preview)" to the menu. *Status: done.*
- **M3b:** folders, search, background indexing with progress and the missing-photos notice, settings.
  Settings, the indexer and search moved into the core, shared by both apps; only one app indexes at a
  time. *Status: done. Search answers in about 100 ms on your library (30,000 photos).*
- **M3c:** people page (read-only), video thumbnails, memory measured; then it replaces the current
  app, in the AppImage and the installers too. *Status: done.* The old web app is gone (you chose
  not to keep it), and with it Qt's web engine. Videos are indexed without AI: date from the file
  (Apple's local date, else the movie header's UTC time made local), then the name, then the file's
  modified time. A video of 4 s or less taken within 2 s of a photo is that photo's motion (Live
  Photo) and isn't shown separately: 1,071 of your 3,677 videos. Memory on your library: about
  230 MB browsing, 480 MB at most with search and the viewer used.

  Found along the way, for later milestones:
  - **M7 (organizer):** your videos' file names are in UTC (the Sep 2026 pipeline named them by the
    raw movie-header time), 2–3 hours behind the local time the app now shows, and many Live
    Photo clips were named by the day they were copied. Organizing will want to rename most videos.
  - **640 DNG photos weren't indexed** (DNG wasn't a known photo type yet). Done in M5.

### M4 — File operations
- Selection, the right-click menu, toolbar and shortcuts.
- Copy, cut, paste and move; rename (single and batch); delete to Trash; undo and redo.
- The system clipboard, export, open in external editor, show in folder.

**Done when:**
- The collision tests pass (ported from `test_safe_move.py`).
- A "kill the app mid-operation" test recovers.
- The index matches the disk after every operation.
- Copy and paste works in Files, Dolphin, Nemo and Thunar.

*Status: done, except trying the clipboard by hand in each file manager.* Every change is journaled
before a file moves; moves go in two phases (every file to a hidden name beside itself, then to its
destination), so chains and swaps are safe in any order; nothing is ever overwritten (renameat2
with RENAME_NOREPLACE, falling back to a hard link). Killing the app at every point of an operation
(31 tests) loses nothing, doubles nothing, and leaves the index matching the disk once it reopens.
Commits are batched where recovery can tell from the disk what happened: 5,000 photos move, rename
or undo in about 0.4 s, and go to the Trash in 1 s. The Trash is the desktop's own (freedesktop.org),
so trashed photos show up in the file manager's Trash; undoing puts them back with their faces and
names. Undo history is kept in the database, so it survives closing the app. A Live Photo's clip
moves, renames and goes to the Trash with its photo. Moving and copying stay within the library
folders; Export makes copies for anywhere else. On Windows, the Recycle Bin came in M10.

### M5 — Metadata
- exiftool bundled with its own Perl.
- View all metadata.
- Edit date (one, a batch, shift by N hours), description, keywords, GPS; rotate and flip.

**Done when:**
- Round-trip tests pass on JPEG, HEIC, DNG and PNG.
- Names in a photo survive editing its metadata.
- Undo restores the original values.

*Status: done.* The AppImage bundles ExifTool 13.59 with only the Perl it uses (30 MB); its
self-test writes and reads back details on Debian 12 and 13, Ubuntu 22.04 and 24.04, Fedora and
Arch. ExifTool runs once per session (stay-open), so reading details costs about 1 ms a photo; they're read while
indexing, so keywords and descriptions written by other apps show up too. Every change is
an operation in the journal like a move: Undo writes back exactly the tags that were there, and a
crash mid-write is finished or left untouched on the next start. Writes keep the file's modified
time; the index, thumbnails and face boxes follow (boxes turn with the photo, so names stay). Each
detail goes in the standard tag and in the others the file already has (XMP, EXIF, IPTC), so
other apps agree. Rotating changes only the orientation tag, never the pixels. HEIC
photos turn with their rotation box, so they can't be flipped. Moving a batch's date moves their
Live Photo clips' dates too. DNG photos are indexed and shown from their built-in preview. Round
trips are tested on JPEG, PNG, HEIC, DNG, TIFF and GIF, including killing the app mid-write. Star ratings were
built too, then dropped in 0.5.1 as not needed.

*0.5.2:* the date everything is sorted and shown by is the library's rename rule (`core/dates.py`,
the earliest real date among the name, EXIF and the video's clock), so 31,769 of 31,828 date-named
files show the date in their name (the rest are seconds off), against about 21,000 before. Changing
a date renames a file named by it. Live Photo clips pair on the local time the files recorded.

### M6 — Faces
- Face boxes in the viewer.
- Name, rename, merge and split people; fix wrong matches; the "who is this" flow.

**Done when:** it works on your library's 41,000 faces without waiting on regrouping.

*Status: done.* Every change to who's who (naming, "not them", "not a face", rename, merge, forget,
moving faces to someone else) is a step in the same journal as file operations, so Undo, Redo and
crash recovery work the same way, from the app and the command line. Naming a group tags the faces
it makes recognizable in the same step, so one Undo takes it all back. "Who is this?" asks about one
group of look-alike unnamed faces at a time, worked out on a background thread. On a copy of your
library (912 groups, 10,966 unnamed faces) the groups take 0.25 s, someone's 7,440 faces 0.06 s
and planning a name 0.29 s; a test with 42,000 faces names and undoes in under two seconds.
Someone's faces are shown least alike first, where the wrong matches are. A face taken away from
someone is never auto-tagged again; one marked "not a face" is never asked about again. The
viewer shows face boxes with names (N), and clicking one names or corrects it.

### M7 — Organizer
- Both modes, with preview and apply through fileops.
- Videos, sidecars and Live Photos included.

**Done when:**
- A dry run on your library proposes close to zero changes.
- The old pipeline's tricky cases are tests: placeholder dates, the 1998 sRGB date, and
  filesystem-date-only files going to `Unsorted/`.

*Status: done.* Organizing goes by the one date the app shows everywhere (`core/dates.py`), so it
agrees with the timeline, and its moves are one operation in the journal: previewed, undoable, safe
from crashes, and never overwriting. On a copy of your library's index it looks at 28,191 files
(`Duplicates/` is left alone) in 1.3 s and proposes 943 changes, all of them Live Photo clips that were
named by the day they were copied: each is renamed to match its photo. Nothing else would change.
A Live Photo's clip and a photo's `.aae` / `.xmp` / `.json` sidecars are named and moved with it;
a year folder's own subfolders are kept. Also from the command line: `photofind organize [--apply]`.
Moving, renaming and trashing a photo elsewhere in the app don't take its sidecars along yet.

### M8 — Duplicates
- Exact duplicates (photos and videos), and near duplicates that never touch bursts.
- The review screen.
- Adopt the existing `Duplicates/` folder and `duplicate_mapping.csv`; known duplicates leave search
  and the timeline.

**Done when:** your 6,641 known duplicates are recognized, and a test library's planted duplicates
are all found, with no bursts flagged.

*Status: done.* Exact copies are the same fingerprint, confirmed by hashing the whole file (only
for files that might be copies). Near ones need a perceptual hash (of the thumbnail, so it's quick)
within 4 bits and the search model to see the same picture (0.95); two shots are never matched:
real dates up to an hour apart, or the same size in pixels with nearly the same file size (a series
whose dates were lost). Raw files (DNG) are only exact copies: the JPEG beside one is the same shot.
Each file joins the best match ranked above it, so bursts don't chain into one group. On a copy of
your index, of the 6,641 files in `Duplicates/` the file each duplicates is found for 6,513 (1,346
the same bytes, 5,034 look the same, 133 by name). Still in the library: 1,668 groups with 1,682
extra copies, 91% of them a smaller copy with the same camera date (the Sep 2026 pipeline hashed
photos without turning them upright first, so it missed copies saved turned). Finding them takes
2 s; working out every photo's look the first time took 15 minutes (it's done after indexing).
`Duplicates/` stays out of the timeline, person pages and search; the review screen sends extras
there (named after the copy kept) or to the Trash after a preview, and "not duplicates" is
remembered; all of it is undoable. The info panel doesn't say what a file in `Duplicates/` copies yet.

### M9 — Scene labels and filters
- Zero-shot labels: screenshots, documents, receipts, food, beach…
- Filter by label.

*Status: skipped for now (2026-10-28), moved to Later in `REQUIREMENTS.md`.*

### M10 — Windows and release
- Windows build and installer, and updated docs. (No migration path: there are no existing installs.)

**Done when:** the test suite passes on Windows, and the installer's app passes its self-test there
(AI included), built and checked by CI on every pull request.

*Status: done.* CI runs the whole test suite on Windows and builds `PhotoFinderSetup.exe` on every
pull request; its app's self-test (AI, HEIC, thumbnails, ExifTool, the window) passes there. Delete
goes to the Recycle Bin the way Explorer does it (a `$R` file and its `$I` record, so Explorer shows
and restores it too), with undo and crash recovery as on Linux; CI checks Windows lists it.
ExifTool's official Windows build (same version, its own Perl) is bundled. Clipboard cut and copy
speak Explorer's format, and a rename that only changes case isn't numbered. Found along the way,
for both systems: ExifTool 12.85 changed HEIC rotation to quarter turns, so the bundled 13.59 refused
to turn HEIC photos; fixed. Version 1.0.0; a `v1.0.0` tag publishes both downloads as a Release.

## Main risks

| Risk | Mitigation |
|---|---|
| The timeline with headers and a scrubber is custom work | Build it first in M3; fall back to a plain grid with a jump-to-year list if it fights us |
| HEVC iPhone videos may not decode in Qt's bundled FFmpeg | Check in M2's smoke tests; fallback: show a generic video icon |
| Bundling exiftool and Perl | Prove it in M2, not M5 |
| Data migration | Back up the database before every migration, and test migrations on a copy of your real one |
| Scope creep | `REQUIREMENTS.md` is the contract; new ideas go to v2 / Later there |

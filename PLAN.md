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
                        keywords/rating/GPS/orientation, old values journaled
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
  - **640 DNG photos aren't indexed** (DNG isn't a known photo type yet). Raw support belongs with
    M5's DNG work.

### M4 — File operations
- Selection, the right-click menu, toolbar and shortcuts.
- Copy, cut, paste and move; rename (single and batch); delete to Trash; undo and redo.
- The system clipboard, export, open in external editor, show in folder.

**Done when:**
- The collision tests pass (ported from `test_safe_move.py`).
- A "kill the app mid-operation" test recovers.
- The index matches the disk after every operation.
- Copy and paste works in Files, Dolphin, Nemo and Thunar.

### M5 — Metadata
- exiftool bundled with its own Perl.
- View all metadata.
- Edit date (one, a batch, shift by N hours), description, keywords, rating, GPS; rotate and flip.

**Done when:**
- Round-trip tests pass on JPEG, HEIC, DNG and PNG.
- Names in a photo survive editing its metadata.
- Undo restores the original values.

### M6 — Faces
- Face boxes in the viewer.
- Name, rename, merge and split people; fix wrong matches; the "who is this" flow.

**Done when:** it works on your library's 41,000 faces without waiting on regrouping.

### M7 — Organizer
- Both modes, with preview and apply through fileops.
- Videos, sidecars and Live Photos included.

**Done when:**
- A dry run on your library proposes close to zero changes.
- The old pipeline's tricky cases are tests: placeholder dates, the 1998 sRGB date, and
  filesystem-date-only files going to `Unsorted/`.

### M8 — Duplicates
- Exact duplicates (photos and videos), and near duplicates that never touch bursts.
- The review screen.
- Adopt the existing `Duplicates/` folder and `duplicate_mapping.csv`; known duplicates leave search
  and the timeline.

**Done when:** your 6,641 known duplicates are recognized, and a test library's planted duplicates
are all found, with no bursts flagged.

### M9 — Scene labels and filters
- Zero-shot labels: screenshots, documents, receipts, food, beach…
- Filter by label and by rating.

### M10 — Windows and release
- Windows build and installer, a migration path for existing installs, and updated docs.

## Main risks

| Risk | Mitigation |
|---|---|
| The timeline with headers and a scrubber is custom work | Build it first in M3; fall back to a plain grid with a jump-to-year list if it fights us |
| HEVC iPhone videos may not decode in Qt's bundled FFmpeg | Check in M2's smoke tests; fallback: show a generic video icon |
| Bundling exiftool and Perl | Prove it in M2, not M5 |
| Data migration | Back up the database before every migration, and test migrations on a copy of your real one |
| Scope creep | `REQUIREMENTS.md` is the contract; new ideas go to v2 / Later there |

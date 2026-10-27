# Photo Finder — requirements

A complete, private photo manager for the desktop, with AI search and face recognition built in.
Agreed 2026-10-27. **v1** = first release, **v2** = next release, **Later** = not designed now but not ruled out.

## Ground rules (apply to everything)

1. **Private and local.** Nothing leaves the computer; the only network access is the one-time model download.
2. **Never changes files on its own.** Indexing, thumbnails and AI analysis run in the background but only read.
   Renaming, moving, deleting and editing happen only when the user starts them, after a preview.
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
| Videos (MP4, MOV): shown in the timeline and folders with a plain thumbnail, opened in the system player, included in rename/organize and exact dedupe. **No AI** (no search, no faces) | v1 |
| Live Photos (HEIC + MOV) and sidecars (`.aae`, `.xmp`, `.json`) always kept together as one item | v1 |
| Import from a folder, SD card or phone: preview, copy into year folders, optional rename, skip what's already in the library | v1 |
| Other RAW formats (CR2, NEF, ARW, ...) | v2 |

## 2. Browsing

| Requirement | Priority |
|---|---|
| **Timeline** (main view): everything by date, grouped by month and year, with a scrubber | v1 |
| Folders: the library's real folder tree | v1 |
| People: a page per person | v1 |
| Search results; filter by rating | v1 |
| Viewer: full screen, zoom, previous/next; videos open in the system player | v1 |
| Info panel: date **and where it came from**, camera, size, dimensions, GPS, people, path | v1 |

## 3. Organizing (real folders only; no virtual albums)

| Requirement | Priority |
|---|---|
| Star rating 0–5 and keywords, written into the file | v1 |
| Organizer: rename to `YYYY_MM_DD_HH_MM_SS.ext` and sort into `<library>/<year>/`; no real date goes to `Unsorted/`. Two modes: *organize a folder* and *tidy the library*. Manual, previewed, undoable. Rules from the Sep 2026 pipeline (`~/Pictures/.claude_pipeline_history/`) | v1 |

## 4. File operations

| Requirement | Priority |
|---|---|
| Selection: click, Ctrl/Shift-click, Ctrl+A, drag-box, keyboard | v1 |
| Right-click menu, toolbar, keyboard shortcuts | v1 |
| Copy / cut / paste / move into folders; rename (single and batch) | v1 |
| Ctrl+C / Ctrl+V through the **system clipboard**, so files paste into the file manager and other apps | v1 |
| Delete to Trash; undo and redo (Ctrl+Z / Ctrl+Shift+Z) for every operation | v1 |
| Export (copy out, optionally resized or converted to JPEG); open in an external editor; show in folder | v1 |

## 5. Metadata (written into the files)

| Requirement | Priority |
|---|---|
| View all metadata | v1 |
| Edit date taken: one photo, a batch, and "shift by N hours" | v1 |
| Edit description, keywords, rating, GPS | v1 |
| Writing: via exiftool, file modification time kept, old values journaled for undo, index fingerprint updated in the same step (so faces and names survive) | v1 |
| Faces, people and scene labels stay in the app's database | v1 |

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
| Automatic scene labels (screenshots, documents, receipts, food, beach, ...) using the existing search model | v1 |
| Burst grouping with best-shot suggestions | v2 |
| Search text inside photos (OCR) | Later |
| Place names from GPS, offline | Later |

## 8. Duplicates

| Requirement | Priority |
|---|---|
| Exact duplicates (byte-identical), photos and videos | v1 |
| Near duplicates (resized/recompressed): perceptual hash, cross-checked with search-model similarity; bursts never touched | v1 |
| Keeper: highest resolution, then larger file; manual override | v1 |
| Review screen: side-by-side groups; send extras to `Duplicates/` or the Trash in bulk, with a preview; never automatic | v1 |
| Adopt the existing `Duplicates/` folder and `duplicate_mapping.csv`; known duplicates stay out of search and the timeline | v1 |

## Not planned

Virtual albums and smart albums; map view; crop and image adjustments; drag-and-drop out of the app;
video search and video faces; cloud sync; multiple users; mobile app; online sharing.

# How HomePix fits together

A map of the code for anyone changing it. What the app does is in [README.md](README.md), what's in
scope in [REQUIREMENTS.md](REQUIREMENTS.md), and how to work on it in [CONTRIBUTING.md](CONTRIBUTING.md).
Every file starts with a short description of what it's for; this page is how they connect.

## Two layers

```
homepix/
  core/   everything HomePix does, with no user interface: the database, indexing, dates, people,
          search, duplicates, organizing, changing files. Plain Python; never imports ui.
  ui/     the desktop app (PySide6/Qt), built on core: the window, its pages and menus.
```

`core` can be used and tested without a window, and most tests do. `ui` only decides what to show and
when; the rules (which date a photo has, who a face is, what a move does) all live in `core`.

## Where things are kept

**In the data folder** (`config.data_dir()`: `~/.local/share/homepix` on Linux,
`%LOCALAPPDATA%\homepix` on Windows, or `$HOMEPIX_HOME` if set, which the tests use):

| | |
|---|---|
| `homepix.db` | the database (SQLite): every photo, face, person, and the journal of file changes |
| `thumbs/` | thumbnails, named by each photo's fingerprint (not its id), so a moved photo keeps its own |
| `models/` | the AI models, downloaded once |
| `settings.json` | which folders make up the library, and the user's choices |
| `window.ini` | window size and layout |
| `backups/` | a copy of the database before each upgrade or "start faces over" |
| `app.log` | the app's output when it's started from the menu (no terminal) |

**In each library folder**, a hidden `.homepix/library.db`: the portable copy (`core/portable.py`),
so the folder can move to another computer and bring its people and choices along.

**The photos themselves** are only changed when the user asks (moving, renaming, details, turning),
always through `core/fileops.py`, so that it can be undone.

## The database

`core/db.py` holds the whole schema in one place (`TABLES`), with a comment on every column:

- `photos`: one row per photo or video: its path, fingerprint (`qhash`), date and where the date came from,
  search vector, details read from the file, and its look (for duplicates)
- `people`, and `faces`: each face's box, face print, who it is, and who someone said it isn't
- `ops` and `op_steps`: the journal of file changes (see below)
- `apart`: pairs of photos someone said aren't duplicates

A schema change bumps `VERSION` and adds an upgrade step to `UPGRADES`. Every upgrade backs the database
up first and runs in one transaction, so a failed one leaves it untouched.

## What happens, step by step

### Finding photos: indexing

`core/indexer.py` runs passes in a background thread, one at a time (a lock file stops two copies of the app
indexing together). Each pass calls `library.index_folder` for every library folder, which:

1. scans the folder (`scan_folder`) and compares it with the database: new, changed, moved (found again by
   fingerprint), and missing. Many photos vanishing at once is taken for an unplugged drive, not a
   deletion, so they're kept.
2. indexes each new or changed file: decodes it (`media.py`), works out its date (`dates.py`), makes its
   thumbnail, its search vector (`ai/clip.py`) and its faces (`ai/faces.py`). Videos get a date, a length and a frame.
3. afterwards: recognizes named people in the new faces (`people.auto_assign`), reads the details written in
   the files with ExifTool, works out each photo's look for duplicates, sharpens old thumbnails, and pairs
   Live Photos with their clips.

In the app, `ui/indexing.py` starts the passes (on opening, then every 30 minutes), holds them off while a
file change runs, and shows progress in the status bar.

### Showing them

`core/timeline.py` loads the library in date order, grouped by month. `ui/main_window.py` puts the right page
in front: the timeline (`views/timeline.py`, which draws only the rows on screen), the people pages, the
duplicates review, or the viewer. `ui/thumbs.py` decodes thumbnails on background threads, newest request
first, into a memory cache.

Slow work never runs on the UI thread: `ui/workers.py` runs it on a thread pool and hands the result back.
`workers.Latest` is for requests where only the newest answer matters (the photo just selected, the search
just typed).

### Changing files: the journal, undo and crash safety

Every change to the library goes the same way, whatever started it (the Edit menu, organizing,
duplicates, people, details):

1. **Plan:** a `plan_*` function works out the steps (move this file there, write these details, name these
   faces) without touching anything.
2. **Run:** `fileops.run` writes the whole plan to the journal (`ops`, `op_steps`) before touching a file, then
   does it in phases: moves in two steps (each file to a hidden name beside itself, then to where it goes),
   then copies, the Trash, restores and details. Nothing is ever overwritten (`safefs.py`); a taken name gets
   `_1`, `_2`.
3. **Recover:** if the app crashed partway, `fileops.recover` finishes the job on the next start, by looking at
   where the files actually are.
4. **Undo and redo:** `fileops.inverse` turns a finished operation into the steps that take it back.

Changes that touch only the database (who a face is, which photos aren't duplicates) are steps too, so they
undo the same way. In the app, `ui/fileops.py` runs each operation on a worker thread with a progress window,
and the menus that start them are `file_actions.py`, `photo_actions.py`, `people_actions.py`, `organizer.py`
and `dedupe.py`.

### People

`core/people.py` recognizes named people (a face is compared with the faces someone was named on), groups
unnamed look-alikes for "Who is this?", and plans every change people make (naming, "that isn't them", not a
face, renaming, merging, forgetting). "That isn't them" teaches it too: a small model per person learns from
those faces, so look-alikes are told apart. The thresholds are in `core/config.py`.

### Search

`core/search.py` keeps every photo's search vector in memory and scores them against the words typed
(`ai/clip.py`), then splits the scores into clear matches and "possibly related".

### The date

`core/dates.py` has the one rule for when a photo was taken: the earliest real date among the file's name, the
date written in it (EXIF) and a video's own clock; failing those, a stand-in date; failing that, when the file
last changed. Sorting, showing, organizing and duplicates all use it.

## The tests

`tests/` runs with `pytest` (see CONTRIBUTING.md). `tests/conftest.py` gives every test its own data folder, so
nothing touches a real library, plus the shared helpers: `pf` (core functions with fake AI models), `lib` (an
empty library folder), `home` (that folder as the library, with its own Trash), `app` and `open_window` for the
window, and `wait_until`. `test_self_test.py` runs the app's own `--self-test`, which every build also runs.

## Where to look

| To change... | Look in |
|---|---|
| which date a photo gets | `core/dates.py` |
| what's stored about a photo | `core/db.py` (`TABLES`, then an upgrade step) |
| how alike faces must be, search strictness, sizes | `core/config.py` |
| how a file change works, or a new kind of change | `core/fileops.py` (and a `plan_*` function) |
| what organizing names files | `core/organize.py` |
| what counts as a duplicate | `core/duplicates.py` |
| the details written into files | `core/metadata.py` |
| a menu or keyboard shortcut | `ui/main_window.py` (`_build_menus`), `ui/file_actions.py`, `ui/photo_actions.py` |
| colours, font, icons | `ui/theme.py` |
| a page of the window | `ui/views/` |

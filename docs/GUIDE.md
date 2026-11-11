# HomePix user guide

Everything HomePix can do, page by page. To install it, see the [README](../README.md#install).

- [Getting started](#getting-started)
- [Browsing](#browsing)
- [People and faces](#people-and-faces)
- [Search](#search)
- [Viewing a photo](#viewing-a-photo)
- [Dates](#dates)
- [Changing photos and files](#changing-photos-and-files)
- [Duplicates](#duplicates)
- [Undo, and what happens if the computer crashes](#undo-and-what-happens-if-the-computer-crashes)
- [Taking your library to another computer](#taking-your-library-to-another-computer)
- [Settings and menus](#settings-and-menus)
- [Where HomePix keeps its files](#where-homepix-keeps-its-files)
- [Known limits](#known-limits)

## Getting started

1. Open HomePix and pick the folder that holds your photos (or click **Use my Pictures folder**). You can add more folders later in Settings.
2. HomePix looks through every photo. A big library can take a few hours, so it's fine to leave it running overnight. You can use the app while it works.
3. The first time, HomePix downloads its AI models (about 130 MB). After that it never needs the internet, except to check for updates.

While it's open, HomePix looks for new photos every 30 minutes. Opening it a second time brings the open window forward.

## Browsing

- **Timeline:** every photo and video by date, newest first, in month sections, with a year scrubber down the side for jumping through the years. Ctrl + mouse wheel (or the Size slider) makes the thumbnails bigger or smaller.
- **Folders:** your library's real folder tree, with how many photos each folder inside it holds. A folder on an unplugged drive is marked *not connected*; its photos still show. Right-click a folder and choose *Exclude from library* to keep its photos out of All photos, people and search (nothing is deleted; opening the folder still shows them, and the same menu includes it again).

## People and faces

- **Who is this?** (*Name faces* in the menu, the People page, or Ctrl+Shift+N): unnamed faces that look alike, one group at a time, with the closest look-alikes side by side. None are ticked to start with: click the ones of the same person (Ctrl+A ticks them all; **Most like ticked** brings the faces most like the ones ticked to the front, most alike to least), type a name (someone new, or someone already named, offered as you type) and press Enter. Faces in other photos that clearly match the faces you named are named too (a face named that way is never used to name more, and faces too small to tell aren't named by themselves). Skip a group for now, or say it's no one you know, and it isn't asked about again.
- **Is this ...?** Right after you name a group, HomePix shows once the faces that might be them too, from every group, most alike first: tick the ones that are and press Enter, or choose *The rest aren't ...* to also say the others aren't (they're not asked about as them again, and the app learns from it). Then the groups go on, those least like anyone you've named first (someone new), and last those that look like someone you've named (with their name already typed in, as a guess).
- **People:** everyone you've named, as a page of faces. Click one to see their photos; right-click to rename them (a name someone else has makes them one person), say they're the same person as someone else, or forget them.
- **Fixing wrong matches:** on someone's photos, **Their faces** shows every face taken to be them, the least like the rest first. Click the wrong ones to tick them (Ctrl+A ticks them all; **Most like ticked** brings the faces most like them to the front), and choose *Not them*, *Someone else...* or *Not a face*. A face taken away is never put back on them by itself. It also teaches HomePix who they *aren't*: their other automatic matches that look like the faces you took away are unnamed at once, and faces like them in new photos aren't taken to be them. A couple of these is enough to tell apart look-alikes such as siblings.
- **Faces in the viewer** (N): a box on each face with its name. Click a "Who is this?" box to name it straight away (names you've used before show as you type; click one to use it). Click a named one to say it's someone else, or that it isn't a face.
- **Starting faces over:** View → **Start faces over...** forgets every face and name (a copy of the database goes to the backups folder first) and looks through the photos for faces again in the background, with the newest way of reading them. Search, dates and everything else stay.

Every change to who's who can be undone, like a file change.

## Search

Press Ctrl+F and describe what's in the photo, in plain words: "dog on the beach", "birthday cake", "red car at night". With a folder or person open, it searches just those. Clear matches come first, then a "possibly related" section.

## Viewing a photo

- **Viewer:** double-click a photo (or press Enter) to see it large: zoom with the mouse wheel or double-click, flick through with the arrow keys, F for full screen. Videos play right there: click or press Space to pause, J and L jump 10 seconds, M turns the sound off; the button at the end of the controls opens one in your usual video player. A Live Photo's moving part stays with its photo instead of showing as a video of its own.
- **Info** (I): description, keywords, when it was taken and where that date came from, camera, size, place, people, folder.

## Dates

A photo's date is the earliest real one among the file's name, the date written in the photo (EXIF) and the video's own clock; failing those, a stand-in date like 1 January at midnight; failing that, when the file was last changed. It's the same rule that *Organize by date* names files by, so a photo shows on the day its name says.

## Changing photos and files

- **Details written into the photos** (right-click a photo): description, keywords and location (Ctrl+I), for one photo or many at once; date and time, set or moved by some hours for a batch whose camera clock was wrong (Ctrl+Shift+D), and a file named by its date is renamed to its new one; rotate (Ctrl+R, Ctrl+Shift+R) and flip, without re-saving the picture; and every detail a file holds (*File info*, Alt+Enter). They're written the standard way (XMP and EXIF, using the bundled ExifTool), so other photo apps see them too, and the file's modified time is kept. A Live Photo's clip gets its photo's new date. The people found in a photo stay named.
- **Working with files** (right-click a photo): cut, copy and paste (to and from your file manager too), rename one photo or many at once with a pattern like `{date} Trip {n}` (F2), move to a folder or a new one, move to the Trash (Delete; the Recycle Bin on Windows; on a Mac, the Finder's Trash, where Put Back works), export originals or smaller JPEGs for sharing (Ctrl+E), open in your photo editor, show in folder. A Live Photo's clip and a photo's `.xmp`, `.json` and `.aae` files go along with it.
- **Organizing by date** (*Organize by date* in the menu for the whole library, or right-click a folder and *Organize this folder by date*): names each file by when it was taken (`2022_04_01_07_32_31.jpg`, then `_1`, `_2` for others in the same second) and puts it in its year's folder (`2022/`). A file with no real date (only when it was last changed) goes to `Unsorted/` with its own name. It shows every change first and does nothing until you say so. A Live Photo's clip and a photo's `.aae`, `.xmp` and `.json` files go with it. A year folder's own subfolders are left alone (`2022/Trip/`).

## Duplicates

In the sidebar, or the menu: copies of the same photo or video, side by side, the one to keep marked (the most pixels, then the biggest file; click another to keep it instead). Exact copies are the same bytes; near ones look the same (resized or saved again), checked by both a picture fingerprint and the search model. Shots of a burst are never called duplicates.

Move the extras to the Trash, for the groups you select or all of them, after a preview; say a group isn't duplicates and it's never offered again; or **Skip for now** to leave it for later (skipped groups wait at the end, under *Skipped for now*, even after closing the app).

## Undo, and what happens if the computer crashes

**Undo** (Ctrl+Z) and **Redo** (Ctrl+Shift+Z) work for every change above, even after closing the app. Nothing is ever overwritten: a name that's taken gets `_1`, `_2`, ... If the computer crashes or loses power partway through a change, HomePix finishes the job next time it opens, with no photo lost or doubled.

## Taking your library to another computer

Each library folder holds a copy of everything HomePix knows about its photos, in a hidden `.homepix` folder: the people you named and their faces, search, duplicate choices and folders left out. It's updated a few seconds after each change and when the app closes.

Copy the folder to another computer (or a new one, Linux, Windows or Mac), add it in HomePix there, and choose **Use it**: everything comes back without looking through every photo again. Photos are found by their content, so it doesn't matter where the folder ends up, or if files were renamed since.

Not carried along: the undo history, and thumbnails (made again as needed). Two computers working on the same folder at the same time don't merge: the copy saved last wins.

## Settings and menus

- **Settings** (Ctrl+,): which folders make up the library, the app to edit photos in, and the look: dark (the default), light, or matching the system.
- **Menu:** the **☰** button at the top right holds the clean-up tools, people, undo, settings and updates; the main tools have a line saying what they do. Everything you can do to photos is on their right-click menu. Shortcuts work anywhere in the window. On a Mac, shortcuts written here with Ctrl use ⌘ instead.
- **Updates:** when a new version is out, HomePix downloads it quietly and offers to restart into it. **Check for updates** in the menu checks right away.

## Where HomePix keeps its files

`~/.local/share/homepix/` on Linux, `%LOCALAPPDATA%\homepix` on Windows, `~/Library/Application Support/homepix` on a Mac (change it with the `HOMEPIX_HOME` environment variable):

- `homepix.db`: one SQLite file holding everything it learned (about 1–2 KB per photo)
- `models/`: the downloaded AI models
- `thumbs/`: small previews of every photo
- `app.log`: what the app reports, useful if it misbehaves

Each library folder also gets `.homepix/library.db`, the copy of what's known about its photos (see [Taking your library to another computer](#taking-your-library-to-another-computer)), and a note saying what it is. A copy found from another library is never overwritten; it's kept beside the new one as `library-<date>.db`.

Your photos change only when you ask (rename, move, Trash, details, organize, duplicates), and every change can be undone. Uninstalling the app keeps its folder, so reinstalling picks up where you left off; delete the folder to remove everything.

## Known limits

- Search is great for objects, scenes, colors and activities. It's weak at counting ("three dogs"), reading text in photos, and left/right relationships.
- Photos are center-cropped to a square for search, so things at the far edges of wide panoramas can be missed.
- RAW camera files other than DNG aren't supported yet; DNG photos are shown from the preview inside them.
- HEIC photos can be rotated but not flipped, and WebP files can't have details written into them with the ExifTool that comes with older Linux versions (the bundled one can).
- The Mac version runs on Apple Silicon Macs only (M1 or newer), not older Intel Macs. It's tested automatically on GitHub's Macs, but has had less use than the Windows and Linux versions.
- On Windows, files on a network folder or a USB stick can't go to the Recycle Bin (Windows keeps none there), so HomePix won't delete them.
- Very small faces in big group photos are often missed or not recognized (small faces are read from the full-size photo, which helps when it has the pixels).

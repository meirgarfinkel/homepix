# photofind

A private photo manager with AI search and face recognition that runs entirely on your own computer. Point it at a folder, let it work through your photos (overnight if the folder is big), then search with plain words like "dog on the beach" or "birthday cake", and by the people you've named.

Nothing is uploaded anywhere. The only network access is a one-time download of the AI models (about 130 MB) the first time you index.

## Desktop app (Photo Finder)

One download per system, with nothing else to install (on the project's GitHub Releases page, once a version is published):

- **Linux:** `PhotoFinder-x86_64.AppImage`, for Debian 12+, Ubuntu 22.04+, Fedora, Arch and others. Make it executable once (right-click, Properties, Permissions), then double-click it. The first time, it adds **Photo Finder** to your applications menu.
- **Windows 10 and 11:** `PhotoFinderSetup.exe`. It installs for you alone (no administrator password) and adds Photo Finder to the Start menu. Windows warns about an unknown publisher the first time, because the installer isn't signed: click **More info**, then **Run anyway**.

To build these yourself, see **[BUILDING.md](BUILDING.md)**. On Linux you can also install straight from this folder: `bash install_linux.sh`.

Photo Finder is a native desktop app (Qt). While it's open, it looks for new photos every 30 minutes; opening it a second time brings the open window forward. `--self-test` checks that the window, thumbnails and AI parts all work.

What's in it:

- **Timeline:** every photo and video by date, newest first, in month sections, with a year scrubber down the side for jumping through the years. Ctrl + mouse wheel (or the Size slider) makes the thumbnails bigger or smaller.
- **Folders:** your library's real folder tree, with how many photos each holds. A folder on an unplugged drive is marked *not connected*; its photos still show. Right-click a folder and choose *Exclude from library* to keep its photos out of All photos, people and search (nothing is deleted; opening the folder still shows them, and the same menu includes it again).
- **Who is this?** (in the sidebar, or Ctrl+Shift+N): unnamed faces that look alike, one group at a time, with the closest look-alikes side by side. None are ticked to start with: click the ones of the same person (Ctrl+A ticks them all; **Most like ticked** brings the faces most like the ones ticked to the front, most alike to least), type a name (someone new, or someone already named, offered as you type) and press Enter; faces in other photos that match are named too. Skip a group for now, or say it's no one you know, and it isn't asked about again.
- **People:** everyone you've named, as a page of faces. Click one to see their photos; right-click to rename them (a name someone else has makes them one person), say they're the same person as someone else, or forget them.
- **Fixing wrong matches:** on someone's photos, **Their faces** shows every face taken to be them, the least like the rest first. Select the wrong ones and choose *Not them*, *Someone else...* or *Not a face*. A face taken away is never put back on them by itself.
- **Faces in the viewer** (N): a box on each face with its name. Click one to name it, say it's someone else, or that it isn't a face.
- **Search** (Ctrl+F): describe what's in the photo. With a folder or person open, it searches just those.
- **Viewer:** double-click a photo (or press Enter) to see it large: zoom with the mouse wheel or double-click, flick through with the arrow keys, F for full screen. Videos open in your video player. A Live Photo's moving part stays with its photo instead of showing as a video of its own.
- **Info** (I): description, keywords, when it was taken and where that date came from, camera, size, place, people, folder.
- **Which date:** the earliest real one among the file's name, the date written in the photo (EXIF) and the video's own clock; failing those, a stand-in date like 1 January at midnight; failing that, when the file was last changed. It's the rule the library's `YYYY_MM_DD_HH_MM_SS` names were made by, so a photo shows on the day its name says.
- **Details written into the photos** (the Photo menu, or right-click): description, keywords and location (Ctrl+I), for one photo or many at once; date and time, set or moved by some hours for a batch whose camera clock was wrong (Ctrl+Shift+D), and a file named by its date is renamed to its new one; rotate (Ctrl+R, Ctrl+Shift+R) and flip, without re-saving the picture; and every detail a file holds (Alt+Enter). They're written the standard way (XMP and EXIF, using the bundled ExifTool), so other photo apps see them too, and the file's modified time is kept. A Live Photo's clip gets its photo's new date. Undo works for all of it, and the people found in a photo stay named.
- **Working with files** (the Edit menu, or right-click a photo): cut, copy and paste (to and from your file manager too), rename one photo or many at once with a pattern like `{date} Trip {n}` (F2), move to a folder or a new one, move to the Trash (Delete; the Recycle Bin on Windows), export originals or smaller JPEGs for sharing (Ctrl+E), open in your photo editor, show in folder. A Live Photo's clip goes along with its photo.
- **Organizing** (the File menu, or right-click a folder): *Tidy the library* or *Organize a folder* names each file by when it was taken (`2022_04_01_07_32_31.jpg`, then `_1`, `_2` for others in the same second) and puts it in its year's folder (`2022/`). A file with no real date (only when it was last changed) goes to `Unsorted/` with its own name. It shows every change first and does nothing until you say so. A Live Photo's clip and a photo's `.aae`, `.xmp` and `.json` files go with it. A year folder's own subfolders are left alone (`2022/Trip/`).
- **Duplicates** (in the sidebar, or the View menu): copies of the same photo or video, side by side, the one to keep marked (the most pixels, then the biggest file; click another to keep it instead). Exact copies are the same bytes; near ones look the same (resized or saved again), checked by both a picture fingerprint and the search model. Shots of a burst are never called duplicates. Move the extras to the Trash, for the groups you select or all of them, after a preview; say a group isn't duplicates and it's never offered again; or **Skip for now** to leave it for later (skipped groups wait at the end, under *Skipped for now*, even after closing the app).
- **Undo** (Ctrl+Z) and **Redo** (Ctrl+Shift+Z) for all of these, even after closing the app. Nothing is ever overwritten: a name that's taken gets `_1`, `_2`, ... And if the computer crashes or loses power partway, Photo Finder finishes the job next time it opens, with no photo lost or doubled.
- **Taking your library along:** each library folder holds a copy of everything Photo Finder knows about its photos, in a hidden `.photofind` folder: the people you named and their faces, search, duplicate choices and folders left out. It's updated a few seconds after each change and when the app closes. Copy the folder to another computer (or a new one, Linux or Windows), add it in Photo Finder there, and choose **Use it**: everything comes back without looking through every photo again. Photos are found by their content, so it doesn't matter where the folder ends up, or if files were renamed since. Not carried along: the undo history, and thumbnails (made again as needed). Two computers working on the same folder at the same time don't merge: the copy saved last wins.
- **Settings** (Ctrl+,): which folders make up the library, the app to edit photos in, and the look: dark (the default), light, or matching the system.
- **Menus:** File, Edit, Photo and View are under the **⋯** button at the top right. Their shortcuts work anywhere in the window.

Every change to who's who is undoable like a file operation, from the app or the command line.

The app and the command-line tool share the same index, so anything you indexed or named in one shows up in the other. If the app misbehaves, its log is in `~/.local/share/photofind/app.log` (Windows: `%LOCALAPPDATA%\photofind\app.log`).

## Command-line tool

### Install (Debian)

```bash
sudo apt install python3-venv
python3 -m venv ~/photofind-env
~/photofind-env/bin/pip install -r requirements.txt
```

To make the commands below shorter, add an alias to `~/.bashrc`:

```bash
alias photofind="PYTHONPATH=$HOME/photofind $HOME/photofind-env/bin/python -m photofind"
```

### Use

```bash
photofind index ~/Pictures                 # scan the folder (stop with Ctrl+C, resume any time)
photofind search dog on the beach          # opens a results page in your browser
photofind search birthday --since 2023     # with a date filter (--since / --until)

photofind people                           # groups unknown faces and shows them in your browser
photofind name 1 Mom                       # group 1 is Mom
photofind name 4 9 Dad                     # groups 4 and 9 are both Dad
photofind search --person Mom              # every photo of Mom
photofind search beach --person Mom --person Dad

photofind organize                         # what tidying the library would rename or move
photofind organize --apply ~/Pictures/new  # do it, for one folder (Undo in the app takes it back)

photofind duplicates                       # copies of the same photo found (nothing is moved)
photofind status                           # what's indexed, any unreadable files
```

Search shows only photos that score clearly above the rest of your library for that search. Photos that stand out a little are listed as "possibly related" (at most 8). If nothing stands out at all, it just says there are no matches. Add `--all` to rank every photo instead.

Fixing mistakes: `photofind unname <face id>` (the number under each face on the people page), `photofind rename Mom "Mom (Sarah)"`, `photofind forget Mom`.

Run `index` again whenever you add photos. It only processes new or changed files, notices moved or renamed photos (names stay attached), and drops deleted ones.

### Run it overnight

`index` already runs at low priority and uses half your CPU cores. To run it every night at 2 am, add this with `crontab -e`:

```
0 2 * * * cd $HOME/photofind && ionice -c3 $HOME/photofind-env/bin/python -m photofind index $HOME/Pictures --max-minutes 300 >> $HOME/.local/share/photofind/index.log 2>&1
```

`--max-minutes` stops it after that long; the next night continues where it left off.

### Windows

Same code. Install Python from python.org, then:

```
py -m venv %USERPROFILE%\photofind-env
%USERPROFILE%\photofind-env\Scripts\pip install -r requirements.txt
%USERPROFILE%\photofind-env\Scripts\python -m photofind index C:\Users\you\Pictures
```

Use Task Scheduler for nightly runs. Data is stored in `%LOCALAPPDATA%\photofind`.

## Where things are stored

`~/.local/share/photofind/` on Linux, `%LOCALAPPDATA%\photofind` on Windows (change it with the `PHOTOFIND_HOME` environment variable):

- `photofind.db`: one SQLite file holding everything it learned (about 1–2 KB per photo)
- `models/`: the downloaded AI models
- `thumbs/`: small previews of every photo
- `results.html`, `people.html`: the latest pages

Each library folder also gets `.photofind/library.db`, the copy of what's known about its photos (see *Taking your library along*), and a note saying what it is. A copy found from another library is never overwritten; it's kept beside the new one as `library-<date>.db`.

Your photos change only when you ask (rename, move, Trash, details, organize, duplicates), and every change can be undone. Uninstalling the app keeps that folder, so reinstalling picks up where you left off; delete it to remove everything.

## How it works

- **Search:** each photo is shrunk to 256×256 and turned into a list of 512 numbers by MobileCLIP-S0, a small image–text model from Apple. Your search words go through the matching text model, and photos are ranked by how close their numbers are. A search across tens of thousands of photos takes about a second.
- **Faces:** YuNet finds faces and SFace turns each into 128 numbers (both from the OpenCV model zoo). Similar faces are grouped; once you name a group, matching faces in new photos are tagged automatically. A person is never tagged twice in one photo.
- **Speed tricks:** JPEGs are decoded directly at reduced size, which is much faster than opening a full 24-megapixel photo.

## Models and licenses

| Model | File size | License |
|---|---|---|
| MobileCLIP-S0 image + text ([Xenova/mobileclip_s0](https://huggingface.co/Xenova/mobileclip_s0)) | 45 + 43 MB | Apple sample code license (allows use, modification and redistribution) |
| YuNet face detector | 0.2 MB | MIT |
| SFace face recognizer | 39 MB | Apache-2.0 |

Read the Apple license yourself before shipping a commercial product. The newer MobileCLIP2 models are more accurate but use a different, research-oriented Apple license.

The app's font is [Plus Jakarta Sans](https://github.com/tokotype/PlusJakartaSans), bundled under the SIL Open Font License (`photofind/ui/fonts/OFL.txt`).

## Tuning

Constants in `photofind/core/config.py`:

- `MATCH_STRICTNESS`: raise it if unrelated photos show up as search matches; lower it if real matches end up under "possibly related". `NEAR_STRICTNESS` and `MAX_NEAR` control the "possibly related" section.
- `CLUSTER_SIM`: raise it if different people end up in one group; lower it if one person is split across many groups.
- `ASSIGN_SIM`: how sure it must be before auto-tagging a named person.
- `MIN_FACE_PX`: ignore faces smaller than this (tiny background faces are hard to recognize).

## Known limits

- Search is great for objects, scenes, colors and activities. It's weak at counting ("three dogs"), reading text in photos, and left/right relationships.
- Photos are center-cropped to a square for search, so things at the far edges of wide panoramas can be missed.
- RAW camera files other than DNG aren't supported yet; DNG photos are shown from the preview inside them.
- HEIC photos can be rotated but not flipped, and WebP files can't have details written into them with the ExifTool that comes with older Linux versions (the bundled one can).
- From source, writing details needs ExifTool: `sudo apt install libimage-exiftool-perl` (the downloads bundle their own).
- On Windows, files on a network folder or a USB stick can't go to the Recycle Bin (Windows keeps none there), so Photo Finder won't delete them.
- Very small faces in big group photos are often missed or not recognized.

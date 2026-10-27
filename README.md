# photofind

Private photo search that runs entirely on your own computer. Point it at a folder, let it work through your photos (overnight if the folder is big), then search with plain words like "dog on the beach" or "birthday cake", and by the people you've named.

Nothing is uploaded anywhere. The only network access is a one-time download of the AI models (about 130 MB) the first time you index.

## Desktop app (Photo Finder)

To give Photo Finder to other people as a single download (an AppImage for Linux, an installer for Windows), see **[BUILDING.md](BUILDING.md)**.

To install it on your own computer straight from this folder instead:

- **Linux (Debian, Ubuntu, Pop!_OS):** open a terminal here and run `bash install_linux.sh`, then open **Photo Finder** from your applications menu.
- **Windows:** install Python from python.org, then double-click `install_windows.bat`. It adds Photo Finder to the Start menu and desktop.

Photo Finder runs in its own app window (on Linux through Qt, on Windows through the Edge engine built into Windows). If that window can't be created, it opens in a browser instead. Closing the window quits the app. While it's open, it looks for new photos every 30 minutes. `--self-test` checks that the window, interface and AI parts all work.

What's in it:

- First run asks which folder your photos are in (your Pictures folder is one click).
- A search box; results fill in while it's still working through your photos.
- A row of people across the top. When it finds faces it doesn't know, a yellow prompt asks who they are.
- Click a person to see all their photos. Each photo has a "Not (name)" button for mistakes, and you can change or remove the name.
- Click a photo to see it large, then Open it in your photo viewer or Show it in its folder.
- The gear icon manages which folders are included.

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
alias photofind="$HOME/photofind-env/bin/python $HOME/photofind/photofind.py"
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

photofind status                           # what's indexed, any unreadable files
```

Search shows only photos that score clearly above the rest of your library for that search. Photos that stand out a little are listed as "possibly related" (at most 8). If nothing stands out at all, it just says there are no matches. Add `--all` to rank every photo instead.

Fixing mistakes: `photofind unname <face id>` (the number under each face on the people page), `photofind rename Mom "Mom (Sarah)"`, `photofind forget Mom`.

Run `index` again whenever you add photos. It only processes new or changed files, notices moved or renamed photos (names stay attached), and drops deleted ones.

### Run it overnight

`index` already runs at low priority and uses half your CPU cores. To run it every night at 2 am, add this with `crontab -e`:

```
0 2 * * * ionice -c3 $HOME/photofind-env/bin/python $HOME/photofind/photofind.py index $HOME/Pictures --max-minutes 300 >> $HOME/.local/share/photofind/index.log 2>&1
```

`--max-minutes` stops it after that long; the next night continues where it left off.

### Windows

Same code. Install Python from python.org, then:

```
py -m venv %USERPROFILE%\photofind-env
%USERPROFILE%\photofind-env\Scripts\pip install -r requirements.txt
%USERPROFILE%\photofind-env\Scripts\python photofind.py index C:\Users\you\Pictures
```

Use Task Scheduler for nightly runs. Data is stored in `%LOCALAPPDATA%\photofind`.

## Where things are stored

`~/.local/share/photofind/` (change it with the `PHOTOFIND_HOME` environment variable):

- `photofind.db`: one SQLite file holding everything it learned (about 1–2 KB per photo)
- `models/`: the downloaded AI models
- `thumbs/`: small previews, only for photos that appeared in results
- `results.html`, `people.html`: the latest pages

Your photos are only ever read, never modified. To uninstall, delete that folder and `~/photofind-env`.

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

## Tuning

Constants at the top of `photofind.py`:

- `MATCH_STRICTNESS`: raise it if unrelated photos show up as search matches; lower it if real matches end up under "possibly related". `NEAR_STRICTNESS` and `MAX_NEAR` control the "possibly related" section.
- `CLUSTER_SIM`: raise it if different people end up in one group; lower it if one person is split across many groups.
- `ASSIGN_SIM`: how sure it must be before auto-tagging a named person.
- `MIN_FACE_PX`: ignore faces smaller than this (tiny background faces are hard to recognize).

## Known limits

- Search is great for objects, scenes, colors and activities. It's weak at counting ("three dogs"), reading text in photos, and left/right relationships.
- Photos are center-cropped to a square for search, so things at the far edges of wide panoramas can be missed.
- RAW camera files aren't supported yet. HEIC needs `pip install pillow-heif`.
- Very small faces in big group photos are often missed or not recognized.

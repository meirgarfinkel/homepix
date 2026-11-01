"""Moving, renaming, copying and trashing files: never overwriting one, never losing one (even if
the app is killed partway), undo and redo, and the index matching the disk after each."""

import hashlib
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import open_window, photo, recycled_from, rel, trash_records, trashed_files, wait_until

from homepix.core import fileops as fo


def digest(p) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def contents(lib) -> dict[str, str]:
    """{path relative to lib: fingerprint} for every file, hidden ones included."""
    out = {}
    for d, _, names in os.walk(lib):
        for n in names:
            p = os.path.join(d, n)
            out[rel(p, lib)] = digest(p)
    return out


def index_matches_disk(db, lib):
    """Every indexed path exists, and every photo on disk is indexed at its own path, with its own bytes."""
    indexed = {r["path"]: r["qhash"] for r in db.execute("SELECT path, qhash FROM photos")}
    on_disk = {os.path.normpath(os.path.join(lib, p)) for p in contents(lib) if not os.path.basename(p).startswith(".")}
    assert set(indexed) == on_disk
    from homepix.core.util import quick_hash

    for p, q in indexed.items():
        if q != "q":  # (a made-up row)
            assert quick_hash(p, os.path.getsize(p)) == q, p


def no_leftovers(lib):
    assert [p for p in contents(lib) if os.path.basename(p).startswith(".homepix-")] == []


def make(pf, lib, names):
    paths = {n: photo(lib / n) for n in names}
    pf.index_folder(lib, want_faces=False)
    return paths


# --- never overwriting: the cases that once lost 511 photos --------------------------------------


def test_a_name_wanted_by_one_file_and_held_by_another_that_moves_on(home, lib):
    pf, db = home
    p = make(pf, lib, ["clean-10.jpg", "clean.jpg"])
    a, b = digest(p["clean-10.jpg"]), digest(p["clean.jpg"])
    fo.run(
        db,
        "rename",
        "test",
        fo.plan_moves([(lib / "clean-10.jpg", lib / "clean.jpg"), (lib / "clean.jpg", lib / "clean_65.jpg")]),
    )
    assert contents(lib) == {"clean.jpg": a, "clean_65.jpg": b}
    index_matches_disk(db, lib)


def test_two_files_swap_names(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg"])
    before = contents(lib)
    fo.run(db, "rename", "swap", fo.plan_moves([(lib / "a.jpg", lib / "b.jpg"), (lib / "b.jpg", lib / "a.jpg")]))
    assert contents(lib) == {"a.jpg": before["b.jpg"], "b.jpg": before["a.jpg"]}
    index_matches_disk(db, lib)


def test_a_chain_of_five(home, lib):
    pf, db = home
    names = [f"{i}.jpg" for i in range(5)]
    make(pf, lib, names)
    before = contents(lib)
    fo.run(db, "rename", "shift", fo.plan_moves([(lib / f"{i}.jpg", lib / f"{i + 1}.jpg") for i in range(5)]))
    assert contents(lib) == {f"{i + 1}.jpg": before[f"{i}.jpg"] for i in range(5)}
    index_matches_disk(db, lib)


def test_a_taken_name_gets_a_number_and_nothing_is_replaced(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg", "b_1.jpg"])
    before = contents(lib)
    fo.run(db, "rename", "test", fo.plan_moves([(lib / "a.jpg", lib / "b.jpg")]))
    assert contents(lib) == {"b.jpg": before["b.jpg"], "b_1.jpg": before["b_1.jpg"], "b_2.jpg": before["a.jpg"]}
    index_matches_disk(db, lib)


def test_two_files_wanting_the_same_name(home, lib):
    pf, db = home
    make(pf, lib, ["x/a.jpg", "y/a.jpg"])
    before = contents(lib)
    fo.run(db, "move", "test", fo.plan_into([lib / "x/a.jpg", lib / "y/a.jpg"], lib / "z", "move"))
    assert contents(lib) == {"z/a.jpg": before["x/a.jpg"], "z/a_1.jpg": before["y/a.jpg"]}
    index_matches_disk(db, lib)


def test_a_name_taken_after_planning(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg"])
    steps = fo.plan_moves([(lib / "a.jpg", lib / "b.jpg")])
    (lib / "b.jpg").write_bytes(b"arrived meanwhile")
    fo.run(db, "rename", "test", steps)
    assert (lib / "b.jpg").read_bytes() == b"arrived meanwhile"
    assert (lib / "b_1.jpg").exists()


def test_safefs_never_replaces(tmp_path):
    from homepix.core.safefs import copy_file, move_file, rename_noreplace

    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    for do in (
        lambda: rename_noreplace(a, b),
        lambda: move_file(a, b, tmp_path / ".p"),
        lambda: copy_file(a, b, tmp_path / ".p"),
    ):
        with pytest.raises(FileExistsError):
            do()
    assert a.read_text() == "a" and b.read_text() == "b" and not (tmp_path / ".p").exists()


# --- copy, Trash, undo, redo -------------------------------------------------------------------------


def test_copy_keeps_the_original_and_indexes_the_copy(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg"])
    fo.run(db, "copy", "test", fo.plan_into([lib / "a.jpg"], lib / "copies", "copy"))
    assert digest(lib / "a.jpg") == digest(lib / "copies/a.jpg")
    index_matches_disk(db, lib)
    clip = db.execute("SELECT clip FROM photos WHERE path=?", (str(lib / "copies/a.jpg"),)).fetchone()[0]
    assert clip  # the copy can be searched without indexing it again


def test_trash_goes_to_the_desktop_trash_and_restores_with_everything(home, lib, tmp_path):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg"])
    db.execute("INSERT INTO people (name) VALUES ('Ann')")
    pid = db.execute("SELECT id FROM photos WHERE path=?", (str(lib / "a.jpg"),)).fetchone()[0]
    db.execute(
        "INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?,0,0,.1,.1,1,?,1)", (pid, b"\x01\x02")
    )
    db.commit()
    before = contents(lib)
    fo.run(db, "trash", "test", fo.plan_trash([lib / "a.jpg"]))
    assert [digest(f) for f in trashed_files(tmp_path)] == [before["a.jpg"]]
    assert trash_records(tmp_path) == [str(lib / "a.jpg")]
    index_matches_disk(db, lib)
    assert db.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0

    fo.undo(db)
    assert contents(lib) == before
    assert trashed_files(tmp_path) == [] and trash_records(tmp_path) == []
    index_matches_disk(db, lib)
    face = db.execute("SELECT f.emb, p.name FROM faces f JOIN people p ON p.id=f.person_id").fetchone()
    assert (face[0], face[1]) == (b"\x01\x02", "Ann")  # named faces come back too


def test_two_photos_with_the_same_name_in_the_trash(home, lib, tmp_path):
    pf, db = home
    make(pf, lib, ["x/a.jpg", "y/a.jpg"])
    fo.run(db, "trash", "test", fo.plan_trash([lib / "x/a.jpg", lib / "y/a.jpg"]))
    assert len(trashed_files(tmp_path)) == 2
    assert trash_records(tmp_path) == [str(lib / "x/a.jpg"), str(lib / "y/a.jpg")]
    fo.undo(db)
    assert sorted(contents(lib)) == ["x/a.jpg", "y/a.jpg"]


def test_recycle_bin_records_say_where_a_file_came_from(tmp_path, monkeypatch):
    """Windows' $I file: version 2, the size, when, then the path (checked on every system)."""
    import struct

    from homepix.core import trash

    monkeypatch.setenv("HOMEPIX_RECYCLE_BIN", str(tmp_path / "bin"))
    f = tmp_path / "Ünïcode photo.jpg"
    f.write_bytes(b"x" * 1234)
    dest = trash._reserve_recycled(f)
    name = os.path.basename(dest)
    assert name.startswith("$R") and len(name) == len("$RABC123.jpg") and name.endswith(".jpg")
    info = tmp_path / "bin" / ("$I" + name[2:])
    version, size = struct.unpack_from("<qq", info.read_bytes())
    assert (version, size) == (2, 1234) and recycled_from(info) == str(f)
    assert trash._reserve_recycled(f) != dest  # every reservation gets a name of its own


@pytest.mark.skipif(os.name != "nt" or not os.environ.get("CI"), reason="the real Recycle Bin, on a test machine")
def test_windows_lists_and_restores_what_goes_to_the_recycle_bin(tmp_path, monkeypatch):
    from homepix.core import trash

    monkeypatch.delenv("HOMEPIX_RECYCLE_BIN")
    f = photo(tmp_path / "homepix-recycle-test.jpg")
    data = f.read_bytes()
    dest = trash.reserve(f)
    os.rename(f, dest)
    assert not f.exists() and os.path.dirname(dest).lower().endswith(trash._sid().lower())
    listed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(New-Object -ComObject Shell.Application).NameSpace(10).Items() | ForEach-Object { $_.Name }",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "homepix-recycle-test" in listed  # Windows sees it in the Recycle Bin, under its own name
    trash.restore(dest, f)
    assert f.read_bytes() == data and not os.path.exists(dest)
    assert not os.path.exists(os.path.join(os.path.dirname(dest), "$I" + os.path.basename(dest)[2:]))


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("CI"), reason="the real Trash, on a test Mac")
def test_a_mac_moves_it_to_the_finders_trash_and_restores_it(tmp_path, monkeypatch):
    from homepix.core import trash

    monkeypatch.delenv("HOMEPIX_FREEDESKTOP_TRASH")
    f = photo(tmp_path / "homepix-trash-test.jpg")
    data = f.read_bytes()
    went = trash.send(f, trash.reserve(f))
    assert not f.exists() and os.path.exists(went) and "/.Trash" in went
    trash.restore(went, f)
    assert f.read_bytes() == data and not os.path.exists(went)


def test_undo_redo_and_new_work_clears_redo(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg"])
    start = contents(lib)
    fo.run(db, "rename", "rename a", fo.plan_rename(db, [(lib / "a.jpg", "first")]))
    renamed = contents(lib)
    fo.run(db, "move", "move b", fo.plan_into([lib / "b.jpg"], lib / "sub", "move"))
    moved = contents(lib)

    assert fo.undo(db).finished == 1
    assert contents(lib) == renamed
    assert fo.undo(db).finished == 1
    assert contents(lib) == start
    assert fo.undo(db) is None
    index_matches_disk(db, lib)

    fo.redo(db)
    assert contents(lib) == renamed
    fo.redo(db)
    assert contents(lib) == moved
    assert fo.redo(db) is None
    index_matches_disk(db, lib)

    fo.undo(db)
    fo.run(db, "copy", "new", fo.plan_into([lib / "first.jpg"], lib / "c", "copy"))
    assert fo.can_redo(db) is None  # doing something new drops what could be redone
    fo.undo(db)  # undoing a copy puts the copy in the Trash
    assert contents(lib) == renamed
    index_matches_disk(db, lib)


def test_undo_skips_a_file_moved_away_since(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg"])
    fo.run(db, "move", "test", fo.plan_into([lib / "a.jpg", lib / "b.jpg"], lib / "sub", "move"))
    os.rename(lib / "sub/a.jpg", lib / "elsewhere.jpg")
    result = fo.undo(db)
    assert result.finished == 1 and len(result.skipped) == 1
    assert (lib / "b.jpg").exists() and (lib / "elsewhere.jpg").exists()


def test_rename_keeps_the_extension_and_takes_the_live_photo_clip(home, lib):
    pf, db = home
    make(pf, lib, ["IMG_1.jpg", "IMG_2.jpg"])
    (lib / "IMG_1.mov").write_bytes(b"clip")
    db.execute(
        "INSERT INTO photos (path, qhash, kind, taken, duration) "
        "SELECT ?, 'q', 'motion', taken, 2 FROM photos WHERE path=?",
        (str(lib / "IMG_1.mov"), str(lib / "IMG_1.jpg")),
    )
    db.commit()
    fo.run(db, "rename", "test", fo.plan_rename(db, [(lib / "IMG_1.jpg", "Beach")]))
    assert sorted(os.listdir(lib)) == ["Beach.jpg", "Beach.mov", "IMG_2.jpg"]
    assert fo.with_companions(db, [lib / "Beach.jpg"]) == [str(lib / "Beach.jpg"), str(lib / "Beach.mov")]


def test_pattern_names():
    import datetime as dt

    from homepix.core.fileops import pattern_names

    items = [(f"/p/IMG_{i}.jpg", dt.datetime(2024, 7, 1)) for i in range(10)] + [("/p/x.jpg", None)]
    names = pattern_names("{date} Trip {n}", items)
    assert names[0] == "2024-07-01 Trip 01" and names[-1] == "undated Trip 11"
    assert pattern_names("{name}-edit", items[:1]) == ["IMG_0-edit"]


def test_stopping_partway_keeps_what_was_done_and_undo_takes_it_back(home, lib):
    pf, db = home
    make(pf, lib, [f"{i}.jpg" for i in range(6)])
    before = contents(lib)
    calls = []
    result = fo.run(
        db,
        "copy",
        "test",
        fo.plan_into([lib / f"{i}.jpg" for i in range(6)], lib / "c", "copy"),
        should_stop=lambda: len(calls) >= 3,
        progress=lambda d, t: calls.append(d),
    )
    assert result.stopped and result.finished == 3
    assert len(os.listdir(lib / "c")) == 3
    index_matches_disk(db, lib)
    fo.undo(db)
    assert contents(lib) == before


# --- killed partway ----------------------------------------------------------------------------------

KILLER = textwrap.dedent("""
    import os, sys
    from homepix.core import fileops, safefs
    from homepix.core.db import open_db
    lib, action, after = sys.argv[1], sys.argv[2], int(sys.argv[3])
    count = 0
    def die_soon(real):
        def wrapped(*a, **k):
            global count
            real(*a, **k)
            count += 1
            if count >= after:
                os._exit(9)  # killed, with no clean-up at all
        return wrapped
    fileops.rename_noreplace = die_soon(fileops.rename_noreplace)
    fileops.move_file = die_soon(fileops.move_file)
    fileops.copy_file = die_soon(fileops.copy_file)
    fileops.trashcan.send = die_soon(fileops.trashcan.send)
    db = open_db()
    names = sorted(n for n in os.listdir(lib) if n.endswith(".jpg"))
    paths = [os.path.join(lib, n) for n in names]
    if action == "shift":
        steps = fileops.plan_moves([(p, os.path.join(lib, names[(i + 1) % len(names)])) for i, p in enumerate(paths)])
    elif action == "move":
        steps = fileops.plan_into(paths, os.path.join(lib, "sub"), "move")
    elif action == "copy":
        steps = fileops.plan_into(paths, os.path.join(lib, "sub"), "copy")
    else:
        steps = fileops.plan_trash(paths)
    fileops.run(db, action, "test", steps)
    os._exit(0)
""")


@pytest.mark.parametrize(
    "action,after",
    [("shift", n) for n in range(1, 8)]
    + [("move", n) for n in range(1, 8)]
    + [("copy", n) for n in range(1, 4)]
    + [("trash", n) for n in range(1, 4)],
)
def test_killed_partway_loses_nothing(home, lib, tmp_path, action, after):
    pf, db = home
    make(pf, lib, [f"{i}.jpg" for i in range(4)])
    before = sorted(contents(lib).values())
    script = tmp_path / "killer.py"
    script.write_text(KILLER)
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    p = subprocess.run([sys.executable, str(script), str(lib), action, str(after)], env=env)
    assert p.returncode == 9

    notes = fo.recover(db)
    assert len(notes) == 1
    no_leftovers(lib)
    index_matches_disk(db, lib)
    trashed = [digest(f) for f in trashed_files(tmp_path)]
    now = sorted(list(contents(lib).values()) + trashed)
    if action == "copy":
        assert set(now) == set(before) and len(now) >= len(before)
    else:
        assert now == before  # every photo exactly once

    fo.undo(db)  # and what was done before the crash can be taken back
    assert sorted(contents(lib).values()) == before
    index_matches_disk(db, lib)


def test_a_move_to_another_drive_copies_checks_and_removes(home, lib, monkeypatch):
    import errno

    from homepix.core import safefs

    pf, db = home
    make(pf, lib, ["a.jpg"])
    before = contents(lib)
    real = safefs.rename_noreplace

    def across(src, dst):  # "drive" = top folder under lib
        if os.path.relpath(src, lib).split(os.sep)[0] != os.path.relpath(dst, lib).split(os.sep)[0]:
            raise OSError(errno.EXDEV, "cross-device link")
        real(src, dst)

    monkeypatch.setattr(safefs, "rename_noreplace", across)
    fo.run(db, "move", "test", fo.plan_into([lib / "a.jpg"], lib / "drive", "move"))
    assert contents(lib) == {"drive/a.jpg": before["a.jpg"]}
    assert os.stat(lib / "drive/a.jpg").st_mtime > 0
    index_matches_disk(db, lib)


# --- in the app ----------------------------------------------------------------------------------------


def test_the_clipboard_speaks_the_file_managers_formats(app):
    from PySide6.QtCore import QMimeData, QUrl

    from homepix.ui import desktop

    ab, c = os.path.abspath("/p/a b.jpg"), os.path.abspath("/p/c.jpg")
    mime = desktop.files_mime([ab, c], cut=True)
    urls = b"".join(b"\n" + QUrl.fromLocalFile(p).toEncoded() for p in (ab, c))
    assert bytes(mime.data(desktop.GNOME)) == b"cut" + urls and b"/a%20b.jpg" in urls
    assert bytes(mime.data(desktop.KDE_CUT)) == b"1"
    assert desktop.files_in(mime) == ([ab, c], True)
    assert desktop.files_in(desktop.files_mime([c]))[1] is False

    dolphin = QMimeData()
    dolphin.setUrls([QUrl.fromLocalFile(c)])
    dolphin.setData(desktop.KDE_CUT, b"1")
    assert desktop.files_in(dolphin) == ([c], True)

    for effect, cut in ((2, True), (5, False), (1, False)):  # Windows Explorer: cut, copied, copied
        explorer = QMimeData()
        explorer.setUrls([QUrl.fromLocalFile(c)])
        explorer.setData(desktop.WIN_EFFECT, effect.to_bytes(4, "little"))
        assert desktop.files_in(explorer) == ([c], cut)


def test_commands_to_open_with_keep_their_paths():
    from homepix.ui import desktop

    if os.name == "nt":
        assert desktop.split_command(r'"C:\Program Files\GIMP 3\bin\gimp.exe" --new %f') == [
            r"C:\Program Files\GIMP 3\bin\gimp.exe",
            "--new",
            "%f",
        ]
        assert desktop.split_command(r"C:\Tools\edit.exe") == [r"C:\Tools\edit.exe"]
    else:
        assert desktop.split_command("darktable --some-option '%f'") == ["darktable", "--some-option", "%f"]


@pytest.fixture
def window(home, lib, app):
    pf, db = home
    for i in range(4):
        photo(lib / "trip" / f"IMG_{i}.jpg")
    pf.index_folder(lib, want_faces=False)
    w = open_window(app, lambda w: len(w.timeline.items) == 4, size=(900, 600))
    yield w
    w.close()


def test_trash_from_the_grid_then_undo_and_redo_from_the_edit_menu(window, lib, app):
    w = window
    w.timeline.set_current(1)
    w.timeline.set_current(2, "range")
    names = {os.path.basename(it.path) for it in w.files.targets()}
    w.files.trash.trigger()
    wait_until(app, lambda: len(w.timeline.items) == 2)
    assert not any((lib / "trip" / n).exists() for n in names)
    assert w.timeline.current >= 0  # the next photo is selected
    assert "Ctrl+Z" in w.statusBar().currentMessage()
    assert w.files.undo.isEnabled() and w.files.undo.text() == "Undo Move 2 photos to Trash"
    w.files.undo.trigger()
    wait_until(app, lambda: len(w.timeline.items) == 4)
    assert all((lib / "trip" / n).exists() for n in names)
    assert w.files.redo.text() == "Redo Move 2 photos to Trash"
    w.files.redo.trigger()
    wait_until(app, lambda: len(w.timeline.items) == 2)


def test_delete_in_the_viewer_shows_the_next_photo(window, app):
    w = window
    w.open_viewer(0)
    first, second = w.timeline.items[0].path, w.timeline.items[1].path
    w.files.trash.trigger()
    wait_until(app, lambda: len(w.timeline.items) == 3)
    assert w.stack.currentWidget() is w.viewer
    assert w.viewer.items[w.viewer.index].path == second != first


def test_cut_here_and_paste_into_another_folder(window, lib, app):
    w = window
    (lib / "other").mkdir()
    photo(lib / "other" / "x.jpg")
    w.timeline.set_current(0)
    path = w.files.targets()[0].path
    w.files.cut.trigger()
    w.place = str(lib / "other")
    w.files.paste_into(str(lib / "other"))
    dst = lib / "other" / os.path.basename(path)
    wait_until(app, lambda: dst.exists() and not w.ops.busy)
    assert not os.path.exists(path)


def test_renaming_many_with_a_pattern(window, lib, app, monkeypatch):
    from homepix.ui.views import file_dialogs

    w = window
    w.timeline.select_all()

    def fake_exec(dlg):
        dlg.edit.setText("Trip {n}")
        return True

    monkeypatch.setattr(file_dialogs.BatchRenameDialog, "exec", fake_exec)
    w.files.rename.trigger()
    wait_until(app, lambda: sorted(os.listdir(lib / "trip")) == [f"Trip {i}.jpg" for i in range(1, 5)])
    wait_until(
        app,
        lambda: sorted(os.path.basename(it.path) for it in w.timeline.items) == [f"Trip {i}.jpg" for i in range(1, 5)],
    )


def test_export_smaller_jpegs(window, lib, tmp_path, app):
    from PIL import Image

    w = window
    out = tmp_path / "out"
    w.ops.export([it.path for it in w.timeline.items], str(out), 32, 80)
    wait_until(app, lambda: not w.ops.busy)
    names = sorted(os.listdir(out))
    assert len(names) == 4
    assert max(Image.open(out / names[0]).size) == 32
    assert len(os.listdir(lib / "trip")) == 4  # the originals are untouched


def test_indexing_afterwards_has_nothing_to_do(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg", "b.jpg", "c.jpg"])
    fo.run(db, "move", "m", fo.plan_into([lib / "a.jpg"], lib / "sub", "move"))
    fo.run(db, "copy", "c", fo.plan_into([lib / "b.jpg"], lib / "sub", "copy"))
    fo.run(db, "rename", "r", fo.plan_rename(db, [(lib / "c.jpg", "renamed")]))
    fo.run(db, "trash", "t", fo.plan_trash([lib / "b.jpg"]))
    info = pf.index_folder(lib, want_faces=False)
    assert (info["done"], info["moved"], info["removed"]) == (0, 0, 0)


def test_restoring_after_the_person_in_it_was_deleted(home, lib):
    pf, db = home
    make(pf, lib, ["a.jpg"])
    db.execute("INSERT INTO people (name) VALUES ('Ann')")
    pid = db.execute("SELECT id FROM photos").fetchone()[0]
    db.execute(
        "INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?,0,0,.1,.1,1,?,1)", (pid, b"\x01\x02")
    )
    db.commit()
    fo.run(db, "trash", "t", fo.plan_trash([lib / "a.jpg"]))
    db.execute("DELETE FROM people")
    db.commit()
    assert fo.undo(db).finished == 1
    assert db.execute("SELECT person_id FROM faces").fetchone()[0] is None

"""
photofind - private, local photo search, from the command line.

Everything runs on your own computer. Photos are never uploaded anywhere;
the only network access is a one-time download of the AI models (~130 MB).


Commands:
    photofind     index  ~/Pictures            scan a folder (safe to stop and resume)
    photofind     search dog on the beach       find photos by description
    photofind     search --person Mom           every photo of a named person
    photofind     people                        group unknown faces so you can name them
    photofind     name 3 Mom                    give face group 3 the name "Mom"
    photofind     organize                      preview naming files by date into year folders
    photofind     organize --apply ~/Pictures/new   do it (undo from the app's Edit menu)
    photofind     duplicates                    copies found, and those set aside in Duplicates/
    photofind     status                        what has been indexed so far

Run any command with -h for its options.
"""

import argparse
import html
import os
import sys
import time
import webbrowser
from pathlib import Path

import numpy as np

from .core import (
    HEIF,
    ClipText,
    ModelDownloadError,
    NewerDatabaseError,
    auto_assign,
    be_nice,
    cluster_unnamed,
    data_dir,
    face_thumb,
    from_blob,
    human_time,
    index_folder,
    load_faces,
    model_path,
    open_db,
    photo_thumb,
    say,
    split_matches,
)
from .core.ai.models import MODELS
from .core.config import MAX_NEAR
from .core.util import is_tty


def cmd_index(args):
    be_nice()
    tty = is_tty()
    last = {"t": 0.0}

    def progress(i):
        if i["phase"] == "scanning":
            say(f"Scanning {i['root']} ...")
        elif i["phase"] == "loading":
            say(f"{i['found']} photos found, {i['found'] - i['todo']} already indexed"
                + (f", {i['moved']} moved" if i["moved"] else "")
                + (f", {i['removed']} removed" if i["removed"] else "") + f", {i['todo']} to do.")
            say("Loading models...")
        elif i["phase"] == "indexing":
            if i["done"] == 0:
                say("Indexing. You can stop any time with Ctrl+C; run the same command later to continue.")
                return
            now = time.time()
            if tty and (now - last["t"] > 0.2 or i["done"] == i["todo"]):
                last["t"] = now
                line = (f"  {i['done']}/{i['todo']} photos | {i['rate']:.1f}/s | "
                        f"about {human_time(i['eta'] or 0)} left | {i['faces']} faces")
                print("\r" + line.ljust(78), end="", flush=True)
            elif not tty and (i["done"] % 200 == 0 or i["done"] == i["todo"]):
                say(f"  {i['done']}/{i['todo']} photos")

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    from .core import fileops
    from .core.indexer import LibraryBusy, library_lock

    try:
        with library_lock(timeout=2):  # not while the app indexes or changes files
            db = open_db()
            try:
                for note in fileops.recover(db):
                    say(f"Finished what was interrupted: {note}")
            finally:
                db.close()
            info = index_folder(args.folder, not args.no_faces, progress, deadline=deadline,
                                forget_missing=args.forget_missing)
    except LibraryBusy:
        sys.exit("The Photo Finder app is updating the library right now. Try again when it's done "
                 "(or close it).")
    except FileNotFoundError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        say("\nStopped. Progress is saved; run the same command to continue.")
        return
    if tty and info["todo"]:
        say()
    if info["unreachable"]:
        say(f"{info['unreachable']} indexed photos are in folders that couldn't be read right now; they're kept.")
    if info["missing"]:
        say(f"{info['missing']} indexed photos can't be found. They're kept in case a drive is unplugged or a folder "
            f"is out of reach.\nIf you deleted them, run:  photofind index \"{args.folder}\" --forget-missing")
    if not info["todo"]:
        say(f"{info['found']} photos found, all already indexed"
            + (f" ({info['moved']} moved, {info['removed']} removed)" if info["moved"] or info["removed"] else "") + ".")
        return
    if info["stopped"]:
        say(f"Reached the {args.max_minutes}-minute limit; run again to continue.")
    say(f"Indexed {info['done']} photos"
        + (f" ({info['errors']} could not be read; see `status`)" if info["errors"] else "") + ".")
    if not args.no_faces:
        if info["tagged"]:
            say(f"Recognized {info['tagged']} faces of people you've already named.")
        db = open_db()
        unnamed = db.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL").fetchone()[0]
        if unnamed:
            say(f"{unnamed} faces don't have a name yet. Run `photofind people` to name them.")


def _locked(work):
    """work(db) with the library to ourselves (not while the app indexes or changes files)."""
    from .core.indexer import LibraryBusy, library_lock

    try:
        with library_lock(timeout=2):
            db = open_db()
            try:
                return work(db)
            finally:
                db.close()
    except LibraryBusy:
        sys.exit("The Photo Finder app is updating the library right now. Try again when it's done "
                 "(or close it).")


def _change(db, summary: str, step) -> bool:
    """Make a change to who's who as an operation (the app's Undo takes it back). False if it
    changes nothing."""
    from .core import fileops

    if step is None:
        return False
    fileops.run(db, "people", summary, [step])
    return True


def cmd_people(args):
    _locked(lambda db: _people(db, args))


def _people(db, args):
    if db.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0:
        sys.exit("No faces found yet. Run `photofind index <folder>` first.")
    auto_assign(db)
    groups = cluster_unnamed(db)
    named = db.execute(
        "SELECT pe.id, pe.name, COUNT(f.id) AS n FROM people pe LEFT JOIN faces f ON f.person_id = pe.id "
        "GROUP BY pe.id ORDER BY n DESC"
    ).fetchall()

    shown = [(num, g) for num, g in groups if len(g) >= args.min_faces][: args.limit]
    singles = sum(1 for _, g in groups if len(g) == 1)

    if named:
        say("Named people:")
        for p in named:
            say(f"  {p['name']}: {p['n']} faces")
        say()
    if shown:
        say("Unnamed face groups (name one with: photofind name <group> <name>):")
        for num, g in shown:
            say(f"  group {num}: {len(g)} faces")
    else:
        say("No unnamed face groups left.")
    if singles:
        say(f"  (+{singles} faces seen only once)")

    # Visual page
    parts = [page_head("People")]
    for p in named:
        rows, _ = load_faces(db, "f.person_id = ?", (p["id"],))
        rows = sorted(rows, key=lambda r: -r["score"] * r["w"])[: args.per_group]
        parts.append(f"<h2>{html.escape(p['name'])} <small>{p['n']} faces</small></h2><div class=faces>")
        parts += [face_html(r) for r in rows]
        parts.append("</div>")
    for num, g in shown:
        parts.append(
            f"<h2>Group {num} <small>{len(g)} faces &middot; "
            f"<code>photofind name {num} &lt;name&gt;</code></small></h2><div class=faces>"
        )
        parts += [face_html(r) for r in g[: args.per_group]]
        parts.append("</div>")
    parts.append(
        "<p class=hint>Number under each face = face id. If a face is in the wrong place, run "
        "<code>photofind unname &lt;face id&gt;</code>.</p></body></html>"
    )
    out = data_dir() / "people.html"
    out.write_text("".join(parts), encoding="utf-8")
    say(f"\nFaces page: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


def cmd_name(args):
    from .core import people

    clusters, name_words = [], []
    for token in args.args:
        if not name_words and token.isdigit():
            clusters.append(int(token))
        else:
            name_words.append(token)
    name = " ".join(name_words).strip()
    if not clusters or not name:
        sys.exit("Usage: photofind name <group> [<group> ...] <name>    e.g.  photofind name 3 7 Grandma")

    def work(db):
        marks = ",".join("?" * len(clusters))
        faces = [r[0] for r in db.execute(
            f"SELECT id FROM faces WHERE person_id IS NULL AND hidden = 0 AND cluster IN ({marks})", clusters)]
        if not faces:
            sys.exit("Those group numbers weren't found. Run `photofind people` to see current groups.")
        step = people.plan_name(db, faces, name)
        _change(db, f"Name {len(faces)} faces {name}", step)
        return len(faces), len(step["before"]["new"]["faces"]) - len(faces) if step else 0

    n, more = _locked(work)
    say(f"Named {n} faces '{name}'" + (f", and recognized {more} more" if more > 0 else "") + ".")
    say(f"Try: photofind search --person \"{name}\"")


def cmd_unname(args):
    from .core import people

    def work(db):
        found = [r[0] for r in db.execute(
            f"SELECT id FROM faces WHERE id IN ({','.join('?' * len(args.face_ids))})", args.face_ids)]
        _change(db, f"Unname {len(found)} faces", people.plan_not(db, found) if found else None)
        return len(found)

    n = _locked(work)
    say(f"Removed the name from {n} face(s). They won't be auto-tagged again, but you can still name them.")


def cmd_rename(args):
    from .core import people

    def work(db):
        pid = people.person_id(db, args.old)
        if pid is None:
            sys.exit(f"No person called '{args.old}'.")
        _change(db, f"Rename {args.old} to {args.new}", people.plan_rename(db, pid, args.new))

    _locked(work)
    say("Renamed (if that's someone else's name, they're one person now).")


def cmd_forget(args):
    from .core import people

    def work(db):
        pid = people.person_id(db, args.name)
        if pid is None:
            sys.exit(f"No person called '{args.name}'.")
        _change(db, f"Forget {args.name}", people.plan_forget(db, pid))

    _locked(work)
    say(f"Forgot '{args.name}'. Their faces are unnamed again (Undo in the app brings them back).")


def page_head(title: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{html.escape(title)}</title><style>"
        "body{font:15px system-ui,sans-serif;margin:24px;background:#f6f5f2;color:#222}"
        "h1{font-size:20px} h2{font-size:16px;margin:28px 0 8px} small{color:#777;font-weight:normal}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}"
        ".grid a{background:#fff;border-radius:8px;overflow:hidden;text-decoration:none;color:#444;"
        "box-shadow:0 1px 3px #0002}"
        ".grid img{width:100%;height:200px;object-fit:cover;display:block}"
        ".grid div{padding:6px 8px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".faces{display:flex;flex-wrap:wrap;gap:8px}"
        ".faces figure{margin:0;text-align:center;font-size:11px;color:#888}"
        ".faces img{width:96px;height:96px;border-radius:50%;object-fit:cover;display:block}"
        "details{margin-top:28px} summary{cursor:pointer;color:#777;margin-bottom:12px}"
        "code{background:#e9e6df;padding:1px 5px;border-radius:4px} .hint{color:#666;margin-top:32px}"
        "@media(prefers-color-scheme:dark){body{background:#1c1c1e;color:#eee}"
        ".grid a{background:#2c2c2e;color:#ccc} code{background:#3a3a3c}}"
        "</style></head><body>"
    )


def face_html(r) -> str:
    try:
        src = face_thumb(r).as_uri()
    except Exception:
        return ""
    return (
        f"<figure><a href='{Path(r['path']).as_uri()}' title='{html.escape(r['path'])}'>"
        f"<img src='{src}' loading=lazy></a>{r['id']}</figure>"
    )


def grid_html(items) -> str:
    cells = []
    for r, s in items:
        try:
            thumb = photo_thumb(r["qhash"], r["path"]).as_uri()
        except Exception:
            continue
        label = (r["taken"] or "")[:10] + ("" if s is None else f" &middot; {s:.2f}")
        cells.append(
            f"<a href='{Path(r['path']).as_uri()}' title='{html.escape(r['path'])}'>"
            f"<img src='{thumb}' loading=lazy><div>{label}<br>{html.escape(Path(r['path']).name)}</div></a>"
        )
    return "<div class=grid>" + "".join(cells) + "</div>"


def cmd_search(args):
    db = open_db()
    query = " ".join(args.query).strip()
    if not query and not args.person:
        sys.exit('Tell me what to look for, e.g.  photofind search "dog on the beach"  or  --person Mom')

    where, params = ["p.clip IS NOT NULL"], []
    names = []
    for name in args.person or []:
        row = db.execute("SELECT id, name FROM people WHERE name=?", (name,)).fetchone()
        if not row:
            known = ", ".join(r[0] for r in db.execute("SELECT name FROM people ORDER BY name")) or "none yet"
            sys.exit(f"No person called '{name}'. Named people: {known}")
        where.append("EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id AND f.person_id = ?)")
        params.append(row[0])
        names.append(row[1])
    if args.since:
        where.append("p.taken >= ?")
        params.append(args.since)
    if args.until:
        where.append("p.taken < ?")
        params.append(args.until + "~")  # include the whole 'until' day/year
    rows = db.execute(f"SELECT p.id, p.path, p.taken, p.qhash, p.clip FROM photos p WHERE {' AND '.join(where)}", params).fetchall()
    if not rows:
        sys.exit("No indexed photos match those filters." if len(where) > 1 else "Nothing indexed yet. Run `photofind index <folder>` first.")

    title = query or "Photos"
    if names:
        title += " with " + " & ".join(names)

    if query:
        t0 = time.time()
        qv = ClipText().query(query)
        mat = np.stack([from_blob(r["clip"]) for r in rows])
        scores = mat @ qv
        took = time.time() - t0
        if args.all:
            order = np.argsort(-scores)
            matches, weaker = order[: args.limit], order[:0]
        else:
            matches, weaker = split_matches(scores)
            matches = matches[: args.limit]
        matches = [(rows[i], float(scores[i])) for i in matches]
        weaker = [(rows[i], float(scores[i])) for i in weaker]
    else:
        matches = [(r, None) for r in sorted(rows, key=lambda r: r["taken"] or "", reverse=True)[: args.limit]]
        weaker, took = [], 0.0

    def show(items, n):
        for r, s in items[:n]:
            say(f"  {'' if s is None else f'{s:.3f}  '}{(r['taken'] or '')[:10]}  {r['path']}")

    timing = f" ({took:.2f}s)" if query else ""
    if not query:
        say(f"{title}: {len(rows)} photos" + (f" (showing the newest {len(matches)}; use -n for more)" if len(rows) > len(matches) else ""))
        show(matches, 10)
    elif matches:
        label = "top" if args.all else "clear match" + ("es" if len(matches) != 1 else "")
        say(f"{title}: {len(matches)} {label} out of {len(rows)} photos{timing}" if not args.all
            else f"{title}: {label} {len(matches)} of {len(rows)} photos{timing}")
        show(matches, 10)
        if len(matches) > 10:
            say(f"  ... and {len(matches) - 10} more on the results page")
        if weaker:
            say(f"  (+{len(weaker)} possibly related on the results page)")
    elif weaker:
        say(f"No clear matches for '{title}' among {len(rows)} photos{timing}. Possibly related:")
        show(weaker, MAX_NEAR)
    else:
        say(f"No matches for '{title}' among {len(rows)} photos{timing}.")
        return

    count = f"{len(rows)} photos" if not query else f"{len(matches)} {'shown' if args.all else 'matches'}"
    parts = [page_head(title), f"<h1>{html.escape(title)} <small>{count}</small></h1>"]
    if matches:
        parts.append(grid_html(matches))
    if weaker:
        if matches:
            parts.append(f"<details><summary>Possibly related ({len(weaker)})</summary>{grid_html(weaker)}</details>")
        else:
            parts.append(f"<p>No clear matches. Possibly related:</p>{grid_html(weaker)}")
    parts.append("</body></html>")
    out = data_dir() / "results.html"
    out.write_text("".join(parts), encoding="utf-8")
    say(f"Results page: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


def cmd_organize(args):
    from .core import fileops, library_folders, organize

    folders = library_folders()
    if not folders:
        sys.exit("No library folders yet. Add one in the app first.")

    def work(db):
        try:
            plan = organize.plan(db, folders, args.folder)
        except ValueError as e:
            sys.exit(str(e))
        if plan.not_ready:
            say(f"{plan.not_ready:,} files are still being indexed; they're left for now.")
        if not plan.changes:
            say(f"All {plan.looked_at:,} files are named and sorted already.")
            return
        for c in plan.changes[:args.show] if not args.all else plan.changes:
            say(f"  {c.src}\n    -> {c.dst}  ({c.why})")
        if not args.all and len(plan.changes) > args.show:
            say(f"  ...and {len(plan.changes) - args.show:,} more (--all shows them)")
        n = len(plan.changes)
        if not args.apply:
            say(f"\n{n:,} of {plan.looked_at:,} files would change. Run again with --apply to do it.")
            return
        result = fileops.run(db, "organize", f"Organize {n:,} files", plan.steps())
        say(f"\nOrganized {result.finished:,} files. The app's Edit > Undo takes it back.")
        for path, why in result.skipped:
            say(f"  left as it was: {path} ({why})")

    _locked(work)


def cmd_duplicates(args):
    from collections import Counter

    from .core import duplicates, library_folders

    folders = library_folders()
    if not folders:
        sys.exit("No library folders yet. Add one in the app first.")

    def work(db):
        for f in folders:
            duplicates.fill_looks(db, os.path.join(str(Path(f)), ""))
        known = duplicates.known(db, folders)
        how = Counter(h for _, h in known.values())
        found = sum(1 for k, _ in known.values() if k is not None)
        say(f"Set aside in Duplicates/: {len(known):,}; the photo each duplicates found for {found:,} "
            f"({how['exact']:,} the same bytes, {how['near']:,} look the same, {how['mapping']:,} from "
            f"{duplicates.MAPPING}, {how['name']:,} by name)")
        groups = duplicates.find(db, folders)
        say(f"Still in the library: {duplicates.summary(groups)} (review them in the app)")
        for g in groups[:args.show]:
            say(f"  {'same bytes' if g.how == 'exact' else 'looks the same'}: keep {g.keeper.path}")
            for c in g.extras:
                say(f"      copy {c.path}")

    _locked(work)


def cmd_status(args):
    db = open_db()
    q = lambda sql: db.execute(sql).fetchone()[0]
    say(f"Data folder:  {data_dir()}")
    say(f"Photos:       {q('SELECT COUNT(*) FROM photos WHERE clip IS NOT NULL')} searchable")
    say(f"Faces:        {q('SELECT COUNT(*) FROM faces')} found, "
        f"{q('SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL')} named")
    say(f"People:       {q('SELECT COUNT(*) FROM people')}")
    dbfile = data_dir() / "photofind.db"
    say(f"Database:     {dbfile.stat().st_size / 1e6:.1f} MB")
    present = [n for n in MODELS if model_path(n).exists()]
    say(f"Models:       {len(present)}/{len(MODELS)} downloaded")
    say(f"HEIC support: {'yes' if HEIF else 'no (pip install pillow-heif to add it)'}")
    errs = db.execute("SELECT path, error FROM photos WHERE error IS NOT NULL LIMIT 20").fetchall()
    if errs:
        say(f"\nPhotos that couldn't be read ({q('SELECT COUNT(*) FROM photos WHERE error IS NOT NULL')}):")
        for r in errs:
            say(f"  {r['path']}\n    {r['error']}")


def main():
    ap = argparse.ArgumentParser(
        prog="photofind",
        description="Private photo search that runs entirely on your computer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands:")[1],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="scan a folder of photos (stop and resume any time)")
    p.add_argument("folder")
    p.add_argument("--no-faces", action="store_true", help="skip face recognition (faster)")
    p.add_argument("--max-minutes", type=float, help="stop after this many minutes (handy for overnight runs)")
    p.add_argument("--forget-missing", action="store_true",
                   help="remove photos that can't be found, even if so many are gone that it looks like an unplugged drive")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="find photos by description and/or person")
    p.add_argument("query", nargs="*", help='what to look for, e.g. "birthday cake"')
    p.add_argument("--person", action="append", help="only photos with this person (repeat for several)")
    p.add_argument("--since", help="only photos taken on/after this date, e.g. 2023 or 2023-06-01")
    p.add_argument("--until", help="only photos taken on/before this date, e.g. 2024 or 2024-12-31")
    p.add_argument("-n", "--limit", type=int, default=40, help="most matches to show (default 40)")
    p.add_argument("--all", action="store_true", help="rank every photo instead of only clear matches")
    p.add_argument("--no-open", action="store_true", help="don't open the results page in the browser")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("people", help="group unnamed faces and show them so you can name them")
    p.add_argument("--min-faces", type=int, default=2, help="smallest group to show (default 2)")
    p.add_argument("--limit", type=int, default=30, help="how many groups to show (default 30)")
    p.add_argument("--per-group", type=int, default=24, help="faces shown per group (default 24)")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("name", help="name a face group:  name 3 Mom   (several groups: name 3 7 Mom)")
    p.add_argument("args", nargs="+")
    p.set_defaults(func=cmd_name)

    p = sub.add_parser("unname", help="remove a wrong name from specific faces (by face id)")
    p.add_argument("face_ids", nargs="+", type=int)
    p.set_defaults(func=cmd_unname)

    p = sub.add_parser("rename", help="rename a person")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("forget", help="delete a person's name (their faces become unnamed)")
    p.add_argument("name")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("organize", help="name files by date and sort them into year folders")
    p.add_argument("folder", nargs="?", help="a folder in the library (default: the whole library)")
    p.add_argument("--apply", action="store_true", help="do it (without it, only shows what would change)")
    p.add_argument("--all", action="store_true", help="list every change")
    p.add_argument("--show", type=int, default=20, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_organize)

    p = sub.add_parser("duplicates", help="find copies of photos and videos (nothing is moved)")
    p.add_argument("--show", type=int, default=10, help="how many groups to list")
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("status", help="show what has been indexed")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    try:
        args.func(args)
        if args.func in (cmd_index, cmd_name, cmd_unname, cmd_rename, cmd_forget, cmd_organize):
            from .core import portable

            portable.refresh_copies()  # each library folder's copy of what's known about it
    except BrokenPipeError:  # output piped into e.g. `head`
        sys.stderr.close()
    except KeyboardInterrupt:
        sys.exit("\nStopped.")
    except (ModelDownloadError, NewerDatabaseError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

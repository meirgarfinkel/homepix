# Working on HomePix

How the code fits together: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## One-time setup

```bash
pipx install pre-commit   # or: pip install --user pre-commit
pre-commit install        # adds the commit and push hooks to this clone
```

From then on:

- **every `git commit`** runs `ruff` on what's being committed: it formats the code and fixes what it safely can.
  If it changed anything the commit stops; `git add` the changes and commit again.
- **every `git push`** checks everything (lint, formatting, line length, a few file checks) without changing
  anything. A push with problems is stopped before it leaves your computer. CI runs the same checks.

Run the push checks any time with `pre-commit run --all-files --hook-stage pre-push`.

## Running and testing

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m homepix.ui        # the desktop app (or: python homepix_app.py)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

`HOMEPIX_HOME=/some/folder` points the app (and the tests) at a separate library, so you can try things
without touching your own.

## Tuning

Constants in `homepix/core/config.py`:

- `MATCH_STRICTNESS`: raise it if unrelated photos show up as search matches; lower it if real matches end up under "possibly related". `NEAR_STRICTNESS` and `MAX_NEAR` control the "possibly related" section.
- `CLUSTER_SIM`: raise it if different people end up in one group; lower it if one person is split across many groups.
- `ASSIGN_SIM`, `ASSIGN_MARGIN`: how sure it must be before auto-tagging a named person (compared with the faces you named).
- `ASSIGN_MIN_PX`: faces smaller than this are never auto-tagged.
- `MIN_FACE_PX`: ignore faces smaller than this (tiny background faces are hard to recognize).

## Branches

- **`main`** always works. Nobody pushes to it directly; changes arrive through pull requests that
  passed CI.
- **Work happens on short-lived branches** cut from `main`, named for what they do:
  - `feature/<name>`, `fix/<name>`, `chore/<name>`, `docs/<name>` for everything else
- Open a pull request into `main` and turn on GitHub's auto-merge: `gh pr merge <number> --auto --squash`. Once
  every required check passes it's **squash-merged** (one commit per change on `main`). To hold it back, don't turn
  auto-merge on, or turn it off with `gh pr merge <number> --disable-auto`.
- `main` is protected: changes only arrive through pull requests, and `lint`, `test`, `test-windows`, `linux`
  and `windows` must all pass. Force-pushes and deleting `main` are blocked.

## CI/CD

| Workflow | Runs on | Does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push, every branch | lint (same checks as the pre-push hook) and tests on Linux, Windows and Mac; required for merging into `main` |
| `Build HomePix` (`.github/workflows/build.yml`) | every pull request; version tags; by hand from the Actions tab | builds the AppImage and .deb in AlmaLinux 9 and self-tests them on six distros, the Windows installer and the Mac disk image. A pull request's build saves no files on GitHub; a tag's publishes a GitHub Release with all of them and then deletes its copies; one started by hand keeps them, to download |

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. From an up-to-date `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Bump `__version__` in `homepix/__init__.py` and `AppVersion` in `packaging/homepix.iss` first (the app's update check compares them with the newest release). The build workflow then publishes the release with the Windows installer, Mac disk image, AppImage and .deb attached.

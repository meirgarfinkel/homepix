# Working on HomePix

## One-time setup

```bash
pipx install pre-commit   # or: pip install --user pre-commit
pre-commit install        # adds the commit and push hooks to this clone
```

From then on:

- **every `git commit`** runs `ruff` on what's being committed: it formats the code and fixes what it safely can.
  If it changed anything the commit stops; `git add` the changes and commit again.
- **every `git push`** checks everything (lint, formatting, line length, a few file checks) without changing
  anything. A push with problems is stopped before it leaves your computer.

Run the push checks any time with `pre-commit run --all-files --hook-stage pre-push`.

## Running and testing

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m homepix.ui        # the desktop app (or: python homepix_app.py)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

`HOMEPIX_HOME=/some/folder` points the app (and the tests) at a separate library, so you can try things
without touching your own.

## Branches

- **`main`** always works. Nobody pushes to it directly; changes arrive through pull requests.
- **Work happens on short-lived branches** cut from `main`, named for what they do:
  - `feature/<name>`, `fix/<name>`, `chore/<name>`, `docs/<name>` for everything else
- Open a pull request into `main`. Before merging, run the checks and tests on your computer (below), then
  **squash-merge** it by hand (one commit per change on `main`): `gh pr merge <number> --squash`.

## Checks before merging

GitHub runs nothing on pull requests; the checks run on your computer:

```bash
pre-commit run --all-files --hook-stage pre-push        # lint and formatting
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q  # tests
```

For a change to the app or its packaging, also build the AppImage and self-test it on other distros
(`packaging/build_in_container.sh`, `packaging/smoke_test.sh`; see BUILDING.md). Windows isn't checked until a
release.

`Build HomePix` (`.github/workflows/build.yml`) is the only GitHub workflow. It runs for version tags and when
started by hand from the Actions tab: it builds the AppImage in AlmaLinux 9 and self-tests it on six distros, and
builds and self-tests the Windows installer. A tag's run publishes a GitHub Release with both and then deletes its
copies; a hand-started one keeps them for 2 days, to download.

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. From an up-to-date `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The build workflow then publishes the release with the AppImage and installer attached.

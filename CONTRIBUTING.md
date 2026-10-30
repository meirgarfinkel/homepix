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

## Branches

- **`main`** always works. Nobody pushes to it directly; changes arrive through pull requests that
  passed CI.
- **Work happens on short-lived branches** cut from `main`, named for what they do:
  - `feature/<name>`, `fix/<name>`, `chore/<name>`, `docs/<name>` for everything else
- Open a pull request into `main`. When CI is green, it's **squash-merged** by itself (one commit per change on
  `main`; `.github/workflows/automerge.yml`). Open it as a draft, or add the `no-automerge` label, to hold it back.

## CI/CD

| Workflow | Runs on | Does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push, every branch | lint (same checks as the pre-push hook) and tests; required for merging into `main` |
| `Build HomePix` (`.github/workflows/build.yml`) | pull requests that change the app or packaging; version tags; by hand from the Actions tab | builds the AppImage in AlmaLinux 9 and self-tests it on six distros, and the Windows installer. A pull request's build saves no files on GitHub; a tag's publishes a GitHub Release with both and then deletes its copies; one started by hand keeps them, to download |

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. From an up-to-date `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The build workflow then publishes the release with the AppImage and installer attached.

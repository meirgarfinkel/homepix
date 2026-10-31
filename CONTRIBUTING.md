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
| `CI` (`.github/workflows/ci.yml`) | every push, every branch | lint (same checks as the pre-push hook) and tests; required for merging into `main` |
| `Build HomePix` (`.github/workflows/build.yml`) | every pull request; version tags; by hand from the Actions tab | builds the AppImage in AlmaLinux 9 and self-tests it on six distros, and the Windows installer. A pull request's build saves no files on GitHub; a tag's publishes a GitHub Release with both and then deletes its copies; one started by hand keeps them, to download |

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. From an up-to-date `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The build workflow then publishes the release with the AppImage and installer attached.

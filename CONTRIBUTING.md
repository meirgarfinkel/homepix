# Working on Photo Finder

## One-time setup

```bash
pipx install pre-commit   # or: pip install --user pre-commit
pre-commit install        # adds the pre-push hook to this clone
```

From then on, every `git push` first runs the linter (`ruff`) and a few file checks. A push with problems
is stopped before it leaves your computer. Run the same checks any time with
`pre-commit run --all-files --hook-stage pre-push`.

## Branches

- **`main`** always works. Nobody pushes to it directly; changes arrive through pull requests that
  passed CI.
- **Work happens on short-lived branches** cut from `main`, named for what they do:
  - `m0-safety-fixes`, `m1-core-split`, ... for milestones in `PLAN.md`
  - `feature/<name>`, `fix/<name>`, `chore/<name>`, `docs/<name>` for everything else
- Open a pull request into `main`. When CI is green, **squash-merge** it (one commit per change on `main`);
  the branch is deleted automatically.

## CI/CD

| Workflow | Runs on | Does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push, every branch | lint (same checks as the pre-push hook) and tests; required for merging into `main` |
| `Build Photo Finder` (`.github/workflows/build.yml`) | pull requests that change the app or packaging; version tags; by hand from the Actions tab | builds the AppImage in AlmaLinux 9 and self-tests it on six distros; the Windows installer only for tags and by hand; for a tag, publishes a GitHub Release with both |

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. From an up-to-date `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The build workflow then publishes the release with the AppImage and installer attached.

---
name: release
description: >-
  Cut a Nautobot Workflow Engine release from develop: bump version, towncrier
  notes, release PR to main, merge commit, GitHub release/tag, PyPI publish, and
  sync main back to develop. Use when the user says "release the code", "cut a
  release", "make a release", "ship it", or asks to bump/publish a version.
---

# Release the code

Execute the full release from `develop` → `main` → tag → sync back to `develop`.
Authoritative detail: `docs/dev/release_checklist.md`. Prefer that doc over improvising.

Default bump is **patch** unless the user specifies `minor` or `major`.

## Preconditions

1. `git fetch` and sync local `develop` + `main` with origin.
2. Working tree clean on `develop`.
3. `develop` CI green (`CI Tests & Linting`). If red, fix and push before releasing.
4. Ensure `PATH` includes `gh` (often `~/.local/bin`) and Poetry venv tools (`poetry env info -p`).

## Checklist

Copy and track:

```
Release Progress:
- [ ] CI green on develop
- [ ] Missing towncrier fragments created under changes/
- [ ] release-X.Y.Z branch + poetry version bump
- [ ] invoke generate-release-notes
- [ ] Commit + push release branch
- [ ] PR to main (merge commit, not squash)
- [ ] PR CI green → merge
- [ ] GitHub release tag vX.Y.Z on main
- [ ] Publish to PyPI workflow success
- [ ] release-X.Y.Z-to-develop + poetry version prepatch
- [ ] PR to develop (merge commit) → merge
```

## Steps

### 1. Determine version

```bash
poetry version          # e.g. 1.1.3a0 on develop
poetry version patch    # → 1.1.3  (or minor / major)
```

Use that version as `X.Y.Z` everywhere below.

### 2. Changelog fragments

If `changes/` lacks fragments for commits since the last tag, create them before generating notes:

- Types: `fixed`, `added`, `changed`, `documentation`, `housekeeping`, etc. (see `[tool.towncrier]` in `pyproject.toml`)
- Past-tense complete sentences
- Filename pattern: `{slug}.{type}` → renders as `#{slug}`

### 3. Release branch and notes

```bash
git switch -c release-X.Y.Z develop
poetry version patch   # if not already bumped
# Prefer venv binaries if `poetry run` hits WindowsApps stubs on WSL:
# $(poetry env info -p)/bin/invoke generate-release-notes --version X.Y.Z
invoke generate-release-notes --version X.Y.Z
git add pyproject.toml docs/admin/release_notes/
git commit -m "Release vX.Y.Z"
git push -u origin release-X.Y.Z
```

For a new major/minor series, fill in **Release Overview** in the new `version_{major}.{minor}.md`.

### 4. PR into `main`

Open PR: title `Release vX.Y.Z`, base `main`, head `release-X.Y.Z`.

Body must include What's Changed from towncrier and:

> Per the release checklist: merge with a **merge commit** (do not squash).

Wait until PR checks are green, then:

```bash
gh pr merge <N> --merge --delete-branch
```

### 5. GitHub release (triggers PyPI)

```bash
gh release create vX.Y.Z --target main --title "vX.Y.Z - YYYY-MM-DD" --notes "..."
```

Notes: towncrier sections under What's Changed + Contributors. Confirm `Publish to PyPI` workflow succeeds for the tag.

### 6. Sync `main` → `develop`

```bash
git switch main && git pull
git switch -c release-X.Y.Z-to-develop main
poetry version prepatch    # e.g. 1.1.3 → 1.1.4a0
git add pyproject.toml
git commit -m "Bump version after vX.Y.Z release."
git push -u origin release-X.Y.Z-to-develop
```

PR into `develop`, wait for green CI, merge with **merge commit** (not squash).

### 7. Done

Report: version, PR URLs, release URL, PyPI run status, develop version after sync.

## Hard rules

- Never squash release PRs (`main` or sync-to-`develop`).
- Never invent release notes without fragments or commit review.
- Fix red CI on the branch under test; do not merge failing checks.
- Push/merge/release are intentional; request approval when the harness blocks them.
- Minor releases: also follow Minor Version Bumps in `docs/dev/release_checklist.md` (lock refresh, docs, install verification) before step 1.

## Common pitfalls (WSL)

- `poetry run ruff` / `towncrier` may resolve to WindowsApps stubs → call `$(poetry env info -p)/bin/<tool>` instead.
- `invoke ruff` runs **format --check** and **lint**; both must pass.
- Django FK unit tests: call unbound model methods with `SimpleNamespace` selves; do not assign mocks onto FK fields.

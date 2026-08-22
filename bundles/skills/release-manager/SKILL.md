---
name: release-manager
description: Cut, publish, repair, or audit a Kollab release across the root Python package, workspace packages, changelogs, Git tags, PyPI, GitHub Releases, and the freshly installed CLI. Use for release work only; do not use for ordinary feature development.
---

# Kollab release manager

Own the release from version decision through installed-artifact proof. A tag, a green
workflow, or a GitHub Release by itself is not a completed release.

## Release invariant

For a target `X.Y.Z`, all of these must agree before the release is considered valid:

```text
release scope
  = root project version
  = every packages/*/pyproject.toml version
  = inter-package dependency constraints
  = root and packaged changelog entry
  = release commit
  = tag vX.Y.Z
  = built wheel/sdist metadata
  = PyPI package metadata
  = fresh-install `kollab --version`
  = GitHub Release/update-check marker
```

The source tree must already contain the correct versions before the tag is created. Do
not rely on CI to repair version files after tagging. Kollab's publish workflow currently
rewrites versions in its temporary checkout; that is a defense-in-depth check, not the
release source of truth.

## Scope and safety

- Read `AGENTS.md`, `CLAUDE.md`, `docs/release-process.md`, and this skill before acting.
- Start with `git status --short --branch`, the current branch, worktrees, remotes, and
  the latest valid release tag.
- Preserve unrelated work. Never run `git stash`, `git reset --hard`, broad `git restore`,
  `git clean`, or tag deletion/rewriting to make a release appear clean.
- A dirty checkout is a release blocker until every dirty path is classified. Do not
  assume generated files, documentation, or index artifacts are safe to ignore.
- Prefer a dedicated clean release branch/worktree when the main checkout contains user
  work. Do not move, stash, or copy another person's work without explicit authorization.
- Never reuse, delete, or force-move an existing tag. If a tag exists, inspect it and stop
  on any metadata or artifact mismatch; repair is a separate, explicitly authorized task.
- Treat commit, tag, push, PyPI upload, GitHub Release creation, and Homebrew dispatch as
  mutations. Confirm the exact target version and release commit before the first
  irreversible external action unless the user has already authorized that exact action.
- Never print credentials, API tokens, `.env` contents, or PyPI/GitHub secrets.

## Kollab release topology

Inspect these surfaces every time; do not trust this list if the repository changed:

- `pyproject.toml`: root `kollab` version, runtime dependencies, workspace sources.
- `packages/*/pyproject.toml`: workspace package versions and dependency constraints.
- `uv.lock`: locked workspace/package metadata when version constraints change.
- `CHANGELOG.md`: user-facing release history and `[Unreleased]` section.
- `kollabor/updates/CHANGELOG.md`: packaged copy used by the update UI; keep it byte-for-byte
  synchronized with the root changelog unless the source package contract has changed.
- `kollabor/version.py`: source-mode CLI version resolution; it reads the repository
  `pyproject.toml` when that file is present.
- `docs/release-process.md`: repository checklist and release-owner expectations.
- `.github/workflows/publish.yml`: tag trigger, package build/upload, GitHub Release creation,
  version-bump behavior, and Homebrew dispatch.
- `scripts/publish.py`: local build/upload path and package dependency order.
- `scripts/smoke_test.sh`: clean Docker/PyPI installation smoke test.

## State machine

Keep an evidence ledger with these fields:

```text
state: preflight | preparing | validated | committed | tagged | published | verified | blocked
version: X.Y.Z
release_commit: SHA or unset
tag: vX.Y.Z or unset
workflow_run: URL/id or unset
artifacts: local paths and published URLs
checks: command -> final result
unverified: explicit gaps
next: one highest-leverage action
```

Do not report `released` or `done` while the state is earlier than `verified`.

## 1. Preflight and version decision

1. Inspect branch, worktrees, status, staged changes, remotes, latest tags, and recent
   release commits. Record the exact base commit.
2. Read the root and workspace package versions. Count them; do not inspect only the root.
3. Inspect the current `[Unreleased]` sections and recent commit range since the last
   valid release. Classify the release as patch, minor, or major from actual user-visible
   behavior and breaking changes. Do not infer the version from the next unused tag alone.
4. Check whether `vX.Y.Z` already exists locally or remotely. If it exists, stop and report
   its target commit and version/artifact state. Never overwrite it.
5. Detect invalid prior releases. A tag whose source metadata does not match its tag name is
   not valid evidence of the current package version. Do not silently skip or repair it.
6. Inspect `.github/workflows/publish.yml` and any release-related scripts on every release;
   workflows are mutable and may have changed since the last release.
7. If the checkout is dirty, produce a path-by-path classification and stop before release
   mutation. The user must choose a clean release surface; do not decide that unrelated
   files are disposable.

## 2. Prepare the release commit

Prepare one reviewable release change on the authorized release branch:

1. Update the root version and every workspace package version to exactly `X.Y.Z`.
2. Update all inter-package minimum-version constraints that are part of the same release.
3. Update `uv.lock` using the repository's supported lock command, then inspect the diff.
4. Move the intended user-facing entries from `[Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD`
   in `CHANGELOG.md`. Keep a new empty `[Unreleased]` section at the top.
5. Synchronize `kollabor/updates/CHANGELOG.md` with the root changelog and prove the copies
   match (`cmp` or an equivalent byte comparison).
6. Add migration notes for breaking CLI, config, plugin, package, or API behavior.
7. Do not put internal debugging notes, generated indexes, credentials, raw transcripts, or
   speculative work into release notes.
8. Review the exact release diff. The only intended files should be version metadata,
   dependency locks/constraints, changelogs, and explicitly approved release documentation.

Before committing, run a parity probe equivalent to:

```bash
python - <<'PY'
from pathlib import Path
import re

expected = "X.Y.Z"
paths = [Path("pyproject.toml"), *sorted(Path("packages").glob("*/pyproject.toml"))]
found = {}
for path in paths:
    match = re.search(r'^version\s*=\s*["\']([^"\']+)', path.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"missing version: {path}")
    found[str(path)] = match.group(1)
bad = {path: version for path, version in found.items() if version != expected}
if bad:
    raise SystemExit(f"version mismatch: {bad}")
print(f"{len(found)} package versions aligned at {expected}")
PY
cmp CHANGELOG.md kollabor/updates/CHANGELOG.md
git diff --check
```

Commit only after the user has reviewed or authorized the exact release scope. Use a
release-specific message such as `chore: prepare release vX.Y.Z`; do not add attribution
footers.

## 3. Validate before tagging

Run checks proportionate to the release scope, including the negative version cases:

- `python -m pytest tests/` or the documented full suite for cross-cutting changes.
- `python -m ruff check kollabor/ packages/ plugins/` and relevant formatting/type checks.
- `python -m py_compile` for touched executable Python entry points.
- `python scripts/publish.py --build` or the current documented all-package build path.
- Inspect every generated wheel and sdist metadata. Each artifact's `Version:` must be
  exactly `X.Y.Z`; no artifact may retain `0.6.1` or another stale version.
- Confirm the built root package contains the expected bundled agents, skills, and packaged
  changelog. A successful build with missing package data is not a release pass.
- Where runtime/package behavior changed, run `scripts/smoke_test.sh X.Y.Z` after the
  artifact is available from the intended package index. A local editable import is not
  installed-artifact proof.

Before tagging, require all of the following:

```text
clean release commit
all version surfaces aligned
both changelog copies synchronized
tests/checks passed with final exit status
artifacts built and metadata inspected
target tag unused
release commit SHA recorded
```

If any gate fails, remain `blocked` and fix the producer or report the exact blocker. Do not
tag a partially validated build.

## 4. Tag and publish

Only after the pre-tag gates pass:

1. Create an annotated tag on the exact release commit:
   `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
2. Verify the tag resolves to the recorded release commit and that the tag name matches all
   metadata before pushing it.
3. Push the exact tag intentionally. Do not push `--tags` broadly.
4. Watch the tag-triggered `publish.yml` workflow to a terminal result. A queued or green
   local command is not publication proof.
5. Confirm every intended workspace package and `kollab` was built and uploaded. Check for
   partial publication; do not call the release complete if only some packages landed.
6. Confirm the GitHub Release exists for the exact tag. The update checker reads GitHub's
   latest release, so a PyPI upload without a GitHub Release is incomplete.
7. Confirm the Homebrew dispatch succeeded when the workflow requires it; report it
   separately from PyPI/GitHub success.

If publication partially fails, stop further retries, record the exact package/workflow
state, and choose a recovery plan. Never create a second tag with the same version or
silently republish a changed artifact under an existing version.

## 5. Verify the public and installed release

Run the strongest available consumer proof:

1. Inspect the public PyPI metadata for `kollab` and every released workspace package.
2. Create a fresh temporary environment outside the repository and install the exact
   `kollab==X.Y.Z` from the intended public index.
3. From outside any source checkout, verify:
   - `kollab --version` reports `X.Y.Z`;
   - `python -c 'import importlib.metadata as m; print(m.version("kollab"))'` reports
     `X.Y.Z`;
   - core imports and the documented CLI startup path work;
   - bundled agents, skills, and update/release notes are present.
4. Run `scripts/smoke_test.sh X.Y.Z` or its current replacement against the public package.
5. Verify the GitHub Release tag, release notes, source commit, and workflow artifacts all
   identify `X.Y.Z`.
6. Re-run the version parity probe against the post-release source branch. If the workflow
   committed a version bump back to `main`, verify that commit is the same intended version
   and did not overwrite unrelated work.

The release is `verified` only when source, tag, workflow, PyPI, GitHub Release, and fresh
install all agree. Otherwise report `published-partial` or `blocked`, never `done`.

## Known failure guard: tag/version drift

If the repository contains a tag such as `v0.7.0` while `pyproject.toml`, source mode, or
published metadata still reports `0.6.1`:

- state plainly that the release lineage is inconsistent;
- do not delete or move the existing tag;
- do not claim the tag is a valid installed release;
- determine whether PyPI and GitHub Release publication happened and whether any workflow
  commit landed;
- stop before selecting a repair version until the user explicitly chooses repair versus
  the next clean release.

This guard exists because a tag is an input to Kollab's publish workflow, not proof that the
release-prep commit, package metadata, and installed CLI were correct.

## Final report

Report exact evidence, not a green summary:

```text
status: verified | published-partial | blocked
version: vX.Y.Z
base_commit: ...
release_commit: ...
tag: ... -> ...
workflow: ... -> terminal result
packages: each package -> published/failed/unverified
PyPI: exact package/version observations
GitHub Release: exact tag/release observation
fresh install: exact environment and `kollab --version` output
changelog: root/package copy parity result
dirty/unrelated work: preserved paths
unverified: explicit remaining gaps
next: one action
```

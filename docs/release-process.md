---
title: "Release Process"
doc_type: release-process
created: 2026-05-04
modified: 2026-10-23
status: active
---
# Release Process

This checklist keeps Kollab releases repeatable, public-safe, and easy to
audit. It applies to the root `kollab` package and the workspace packages under
`packages/`.

## Release Owner

Each release should have one release owner. The owner is responsible for:

- confirming the release scope
- deciding the version
- running the validation gates
- checking the public repo surface
- tagging and publishing deliberately (releases are tagged whenever Marco
  decides, and agents may create a release tag ONLY when Marco explicitly
  instructs them to)
- recording follow-up issues for deferred work

## Versioning

Kollab uses SemVer-style versions:

- patch: bug fixes, documentation corrections, small compatibility improvements
- minor: new user-visible capabilities or meaningful workflow improvements
- major: breaking CLI, config, plugin, package, or API changes

Use a `vX.Y.Z` Git tag for public releases.

## Release From Main, in Place

A release's work reaches `main` through one draft release PR (`release/X.Y.Z`
into `main`). Feature work happens in git worktrees per
`SOFTWARE_DEVELOPMENT_LIFECYCLE.md` (`../kollab-wt/issue-<N>-<slug>`, based on
`origin/release/X.Y.Z`), and lanes push only their own branch: nobody pushes to
`release/X.Y.Z` or `main` directly. The shared checkout is read-only for
agents: never edit, stash, reset, clean, restore, or switch branches in it, and
never build a release from uncommitted files or a scratch clone. Steps 5 and 6
run in it and belong to the operator; an agent runs them only on Marco's
explicit instruction. Because nothing is ever committed in the shared checkout,
the merge fast-forwards it, so `main` ends clean and level by construction.

1. Land every lane's work on `release/X.Y.Z` through draft lane PRs, each merged
   with Marco's explicit approval and each committed by explicit paths
   (`git commit -- <paths>`), never `git add -A` or `git add .`. Work that is
   not part of the release stays on its own lane branch.
2. Land the release prep the same way (every version, `uv.lock`, both
   changelogs) as `chore: prepare release vX.Y.Z`.
3. Review the release PR: `git log origin/main..origin/release/X.Y.Z` is the
   full scope that ships. It stays a draft until it is marked ready.
4. Wait for the PR to merge: auto-merge (`.github/workflows/auto-merge.yml`)
   merges it with a merge commit once the required checks pass. Never squash or
   rebase it.
5. Bring `main` level and tag the merge:

   ```bash
   git pull --ff-only origin main
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

6. Once publishing is green, install it:

   ```bash
   uv sync --all-extras                   # the checkout's own venv
   KOLLAB_VERSION=X.Y.Z bash install.sh   # the release binary in ~/.local/bin
   ```

   The installer replaces a uv tool or pipx kollab at `~/.local/bin/kollab`, so
   from then on `/upgrade` swaps the binary and never touches the checkout.

Done means `main` matches `origin/main`, `git status` lists none of the
release's files, and `kollab --version` prints `Build: <branch> @ <8-character
SHA>` immediately before the unchanged final `kollab X.Y.Z` line inside and
outside the repo. Build identity comes from the exact checkout used to build the
artifact; a detached checkout reports branch `unknown` but still reports the
short SHA when HEAD is resolvable. Each unavailable field displays as `unknown`. Any other dirty file is work
outside the release: name it in the release report. Running agents
keep the code they started with; list them and ask before restarting any.

## Pre-Release Checklist

- [ ] `git status --short --branch` is reviewed.
- [ ] All machines used for this repo are checked for unpushed work.
- [ ] Release scope is summarized in `CHANGELOG.md` under the target version.
- [ ] `README.md`, `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, and docs links are current.
- [ ] No local runtime state, generated logs, raw transcripts, scratch files, or
      credentials are tracked.
- [ ] Secret and personal-data scans pass on the exact commit to be released.
- [ ] GitHub issue templates, PR template, and branch-protection settings match
      the current release gate.
- [ ] Package metadata versions match the release tag:
      `python scripts/check_version_parity.py` (engine, every workspace
      package, the web frontend bundle, and the desktop Tauri config must sit
      at one version; the publish workflow re-runs this against the tag and
      refuses to build or upload on drift).
- [ ] CLI starts locally.
- [ ] Engine starts locally when engine changes are included:
      `python -m kollabor_engine serve --port 7433`.
- [ ] Docker installed-user runtime smoke path passes when runtime/package
      behavior changed.
- [ ] tmux/raw-JSONL smoke evidence is captured for user-visible CLI/TUI/runtime
      behavior.
- [ ] CI passes on the release commit.

## Recommended Local Commands

```bash
git status --short --branch
python scripts/check_version_parity.py
gitleaks detect --source . --no-git -v
gitleaks detect --source . -v
trufflehog git file://$PWD --only-verified
python -m py_compile kollabor/cli.py kollabor_cli_main.py plugins/hub/plugin.py
python -m pytest \
  tests/unit/test_hub_project_scope.py \
  tests/unit/test_provider_models.py \
  tests/unit/test_provider_security.py \
  tests/unit/test_gemini_provider.py
```

For Docker runtime validation:

```bash
scripts/docker-runtime.sh build
scripts/docker-runtime.sh smoke
```

## Publishing

1. Confirm the release commit is clean and scanned.
2. Create the annotated release tag on the merge commit (Release From Main, in
   Place, step 5).
3. Push that exact tag: `git push origin vX.Y.Z`, never `--tags`.
4. Let the publish workflow build and check the CLI binaries on all four
   existing platforms, unsigned Tauri desktop bundles for macOS arm64 and
   x86_64, and Linux x86_64 AppImage/Debian packages. Publish the packages, then
   create the GitHub Release with every asset attached.
   The Binaries workflow compiles one isolated `frontend-dist` artifact. Every
   CLI producer passes that same output through `--frontend-dist`; desktop
   stages those binaries, and PyPI publication supplies the downloaded output
   through `KOLLAB_FRONTEND_DIST`. The `connection-service` workflow artifact
   packages the same frontend for the operator's service rollout. These two
   internal artifacts stay outside the 20-file public release manifest.
   Missing selected output or a failed frontend build stops packaging; old
   checkout output is never a successful build fallback.
5. Verify the package page, the four CLI binaries and their `.sha256` files,
   both architectures' `kollab-desktop-macos-*` `.app.zip`/`.dmg` pairs,
   both `kollab-desktop-linux-x86_64-X.Y.Z` packages and their checksums,
   and the install path after publication. `scripts/desktop_release.py` enforces
   the exact 20-file manifest and verifies every payload from the flattened
   artifact download directory. Checksums contain basenames, not build paths.
   The Binaries workflow builds and checks this set only for a `v*` tag
   (publish.yml calls it) or a manual run; pull requests and branch pushes
   build no packages. The only PR-time binary build is the `ci/binary-smoke`
   CI job (one quick Linux smoke build, no artifacts, not a required merge
   check); full four-platform binaries and all desktop builds remain tag-only. To catch a packaging failure before tagging, run it on
   the release branch: `gh workflow run binaries.yml --ref release/X.Y.Z`.
   Each platform stages a hash/version-checked native CLI from
   the same workflow; absent version inputs use `pyproject.toml`, while a
   supplied tag must match it. macOS and Linux desktop asset names therefore
   use the same source version in every workflow context.
   Unsigned macOS users must use Finder's Control-click > Open flow on first
   launch (see `desktop/README.md`). Apple signing/notarization preparation
   remains available without changing the unsigned release default.
   Native Windows assets are blocked by the CLI producer, detached-agent
   startup, Unix IPC, and Hub locking requirements documented in
   [Kollab Desktop](../desktop/README.md#native-windows-release-blocker).
   Do not add a Windows payload or count it in the manifest until the native
   backend and installed-package acceptance gates there pass.
6. Re-run the secret scan on the exact public commit.
7. Install it locally (Release From Main, in Place, step 6) and check
   `kollab --version` inside and outside the repo.

## Post-Release

- [ ] `main` matches `origin/main`, `git status` lists none of the release's
      files, and `kollab --version` reports X.Y.Z inside and outside the repo.
- [ ] Create follow-up issues for deferred work.
- [ ] Confirm installation instructions still work from a fresh environment.
- [ ] Confirm the changelog entry is visible and human-readable.
- [ ] Confirm support/security links point to public destinations.

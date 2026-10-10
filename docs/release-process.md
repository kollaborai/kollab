---
title: "Release Process"
doc_type: release-process
created: 2026-05-04
modified: 2026-10-09
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
- tagging and publishing deliberately
- recording follow-up issues for deferred work

## Versioning

Kollab uses SemVer-style versions:

- patch: bug fixes, documentation corrections, small compatibility improvements
- minor: new user-visible capabilities or meaningful workflow improvements
- major: breaking CLI, config, plugin, package, or API changes

Use a `vX.Y.Z` Git tag for public releases.

## Release From Main, in Place

Agents share one checkout on `main`. Never create a worktree, stash, reset,
clean, restore, or switch branches in it, and never build a release from
uncommitted files or a scratch clone: the checkout stays behind, dirty with
work that already shipped. Commit the release on local `main` instead; the
merge then fast-forwards it, so `main` ends clean and level by construction.

1. Commit the release's work on `main` by explicit paths
   (`git commit -- <paths>`), never `git add -A` or `git add .`. Other agents'
   uncommitted work stays uncommitted and out of the release.
2. Commit the release prep the same way (every version, `uv.lock`, both
   changelogs) as `chore: prepare release vX.Y.Z`.
3. Everything committed on `main` ships: review `git log origin/main..HEAD`,
   then push it and open the PR with
   `git push origin "HEAD:refs/heads/release/X.Y.Z"`.
4. Merge the PR with a merge commit, never squash or rebase, so local `main`
   stays a parent of the merge.
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
release's files, and `kollab --version` reports X.Y.Z inside and outside the
repo. Any file still dirty is work outside the release: name it in the release
report. Running agents keep the code they started with; list them and ask
before restarting any.

## Pre-Release Checklist

- [ ] `git status --short --branch` is reviewed.
- [ ] Release scope is summarized in `CHANGELOG.md` under the target version.
- [ ] `README.md`, `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, and docs links are current.
- [ ] No local runtime state, generated logs, raw transcripts, scratch files, or
      credentials are tracked.
- [ ] Secret and personal-data scans pass on the exact commit to be released.
- [ ] GitHub issue templates, PR template, and branch-protection settings match
      the current release gate.
- [ ] Package metadata versions match the release tag.
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
4. Let the publish workflow build and check the binaries on all four platforms,
   publish the packages, then create the GitHub Release with the binaries attached.
5. Verify the package page, the release's four binaries and their `.sha256`
   files, and the install path after publication.
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

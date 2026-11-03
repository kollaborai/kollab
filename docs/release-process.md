---
title: "Release Process"
doc_type: release-process
created: 2026-05-04
modified: 2026-11-03
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

## Prepare a release in a writer worktree; tag merged main

Release preparation is source work and follows the normal isolated-writer PR
workflow in `SOFTWARE_DEVELOPMENT_LIFECYCLE.md`. Do not commit release files
on the integration checkout. The release build and tag use the exact merged
`main` commit after the designated integrator has synchronized the
integration checkout with `origin/main`.

1. Inspect the current repository/worktree status and latest release tag. Create
   a dedicated worktree and release branch from freshly fetched `origin/main`;
   confirm both path and branch are unused. Keep the integration checkout
   source-read-only.
2. Prepare the release in that worktree, using explicit paths for commits:
   - update the root version and every workspace package version to exactly
     `X.Y.Z`;
   - update all inter-package minimum-version constraints that are part of the
     same release;
   - update `uv.lock` using the supported lock command, then inspect the diff;
   - move intended entries from `[Unreleased]` into
     `## [X.Y.Z] - YYYY-MM-DD` in both changelogs, keeping them byte-identical;
   - add migration notes for breaking CLI, config, plugin, package, or API
     behavior;
   - keep internal debugging notes, generated indexes, credentials, raw
     transcripts, and speculative work out of release notes.
3. Review the exact release diff and commit only the release scope. Push the
   branch normally and open a DRAFT PR against `main`. A different AI agent
   must review the exact final head and record the required GitHub approval as
   whichever account, `kollaborai` or `maarco`, is not the PR author. Record
   reviewer identity and exact reviewed SHA in private maintainer audit
   evidence; keep the GitHub approval plain, with no AI attribution or footer.
   GitHub prohibits author self-approval. Required
   CI, resolved conversations, and current-base gates apply. When the
   code-owner gate is enabled, one of `@maarco` or `@kollaborai` must approve;
   when one account authored the PR, the other must approve. GitHub enforces
   the owner-account approval, not independent AI-review identity, which stays
   in private exact-head audit evidence.
   If the base advances, merge `origin/main` into the writer branch and push normally;
   never rebase or force-push.
4. The designated integrator merges the release PR only when it is within
   Marco's authorized release plan and all exact-head review, approval, CI,
   resolved-conversation, and current-base gates pass. Merge as `kollaborai`
   with a merge commit. Never use `--admin` or a ruleset bypass; stop if
   identity, bypass, or gate evidence is uncertain. In the integration
   checkout, run `git pull --ff-only origin main`, then verify its HEAD equals
   `origin/main` and identifies the release merge commit. If this fast-forward
   fails or the SHAs differ, stop and report the exact state; never reset,
   rebase, or force-push.
5. Only after the release gates below pass and Marco explicitly instructs the
   agent to tag, create the annotated `vX.Y.Z` tag on that exact merged-main
   commit and push that exact tag. Never tag the writer branch or push
   `--tags` broadly.
6. Once publishing is green, run `uv sync --all-extras` and
   `KOLLAB_VERSION=X.Y.Z bash install.sh` (the release binary in
   `~/.local/bin`). Do not build from uncommitted release changes or a scratch
   clone.

The integration checkout must match `origin/main` at the intended merge commit
before build or tag actions. Classify any other dirty path instead of removing
or carrying it into release scope. Verify the installed release's `kollab --version`
prints `Build: <branch> @ <8-character SHA>` immediately before the unchanged
final `kollab X.Y.Z` line. Report the source-checkout version separately if main
advances after the tag. Build identity comes from the
exact checkout used to build the artifact; detached checkouts report branch
`unknown` while retaining a resolvable short SHA, and each unavailable field
displays as `unknown`. Running agents keep the code they started with; list them
and ask before restarting any. These proofs are required in addition to the
existing package, artifact, tag, and publication gates below.

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
2. Create the annotated release tag on the synchronized merge commit
   (Prepare a release in a writer worktree; tag merged main, step 5).
3. Push that exact tag: `git push origin vX.Y.Z`, never `--tags`. Tags do not
   start publication. From reviewed `main`, dispatch the publishing controller:
   `gh workflow run publish.yml --ref main -f tag=vX.Y.Z`.
4. Let the publish workflow build and check the CLI binaries on all four
   existing platforms, unsigned Tauri desktop bundles for macOS arm64 and
   x86_64, and Linux x86_64 AppImage/Debian packages. Publish the packages, then
   create the GitHub Release with every asset attached.
   The Binaries workflow compiles one isolated `frontend-dist` artifact. Every
   CLI producer passes that same output through `--frontend-dist`; desktop
   stages those binaries, and PyPI publication supplies the downloaded output
   through `KOLLAB_FRONTEND_DIST`. The `connection-service` workflow artifact
   packages the same frontend for the operator's service rollout. These two
   internal artifacts stay outside the 22-file public release manifest.
   Missing selected output or a failed frontend build stops packaging; old
   checkout output is never a successful build fallback.
5. Verify the package page, the four CLI binaries and their `.sha256` files,
   both architectures' `kollab-desktop-macos-*` `.app.zip`/`.dmg` pairs,
   both `kollab-desktop-linux-x86_64-X.Y.Z` packages, the Windows installer
   `kollab-desktop-windows-x86_64-X.Y.Z-setup.exe` and their checksums,
   and the install path after publication. `scripts/desktop_release.py` enforces
   the exact 22-file manifest and verifies every payload from the flattened
   artifact download directory. Checksums contain basenames, not build paths.
   The Binaries workflow builds and checks this set when the publishing
   controller calls it with the resolved release source SHA, or during a
   manual rehearsal; pull requests and branch pushes build no packages.
   The only PR-time binary build is the `ci/binary-smoke`
   CI job (one quick Linux smoke build, no artifacts, not a required merge
   check). Check the live publishing workflow state before dispatching; a
   manually disabled workflow does not execute. The controller's `head_sha`
   identifies reviewed workflow code on `main`, and its `source_sha` output
   identifies the tagged release source. These may differ when publishing an
   existing tag after a workflow repair. Every build checks out that immutable
   source SHA, while upload jobs check out the controller SHA. Record both
   SHAs, the annotated tag object, run ID/URL and artifact digests in private
   release evidence. Never move the tag to pick up publishing changes.
   A direct `binaries.yml` dispatch is a rehearsal and builds its selected
   workflow revision; it accepts no alternate source input. Use the publishing
   controller for an existing tag, without changing or creating refs.
   Each platform stages a hash/version-checked native CLI from
   the same workflow; absent version inputs use `pyproject.toml`, while a
   supplied tag must match it. macOS and Linux desktop asset names therefore
   use the same source version in every workflow context.
   Unsigned macOS users must use Finder's Control-click > Open flow on first
   launch (see `desktop/README.md`). Apple signing/notarization preparation
   remains available without changing the unsigned release default.
   Windows is built by the `desktop-windows` job for every publishing run: a
   native runtime, an NSIS installer and its `.sha256`, unsigned (SmartScreen:
   More info, then Run anyway; the release notes say so). The installer ships
   only if the job passes its agent check (`build_binary.py --agent-smoke`), and
   both the binary manifest job and the upload helper require the Windows
   installer in `desktop_release.verify_manifest(..., windows="required")`, so a
   release without the installer, or with half the pair, fails.
6. Re-run the secret scan on the exact public commit.
7. Install it locally (Prepare a release in a writer worktree; tag merged
   main, step 6) and verify the installed release version. Report the source
   checkout's version separately if main has since advanced.

### Publishing authority and first activation

Use PyPI Trusted Publishing for all ten normal distributions: `kollab` and
the nine packages in `scripts/publish.py:PACKAGES`. `kollabor-voice` is not
a separate normal distribution; its code remains bundled with `kollab`.
For each existing PyPI project, configure a GitHub publisher with owner
`kollaborai`, repository `kollab`, workflow `publish.yml` and environment
`pypi-publish`. The environment name is required in this setup.
See [PyPI's publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
and [publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

Before enabling a disabled publisher, create and read back the GitHub
`pypi-publish` and `github-release` environments. Each uses a custom
deployment branch policy allowing only the branch `main`. No additional
human reviewer is required. Preserve the protected-main review/check gates
and immutable `v*` tag rules. Remove the obsolete repository
`PYPI_API_TOKEN` and `HOMEBREW_TAP_TOKEN` secrets after the new setup is
verified; this removes their Actions copies, not the underlying account tokens.
The current workflow references neither secret.

Build jobs have only `contents: read`, do not persist checkout credentials,
and receive no PyPI credentials or OIDC permission. Release build jobs disable
automatic uv and npm action caches; manual binary runs cannot select arbitrary
source through a dispatch input. Science's GitHub metadata
lookup still receives a step-scoped read-only GitHub token. Only the PyPI
upload job has `id-token: write`; only the GitHub release job has
`contents: write`. Both run controller code from the reviewed main commit,
without executing tagged build hooks in those jobs. Repository write access
already permits GitHub release writes; default read-only workflow permissions
are a default, not a permission ceiling. Environment policies and the PyPI
publisher binding constrain the PyPI release authority.

After configuration and the reviewed workflow merge, enable with
`gh workflow enable publish.yml` if necessary, then dispatch from `main`
with the existing tag. Verify the run's resolved source matches the recorded
release commit before accepting its artifacts.

### Partial publication and Homebrew

The upload helper validates all twenty package archives before any PyPI
upload, checks existing remote digests, then stages only missing files.
Workspace publication is verified before uploading the root package. A
different digest stops the run; `skip-existing` is not a recovery policy.
GitHub assets are added only to a draft, checked against the exact 22-file
manifest, and verified remotely before the draft becomes public. Existing
assets with different bytes, unexpected names or an incomplete public release
are rejected; the publisher never clobbers them.

If a job fails after some writes, record the package/asset state and preserve
that run's artifacts. After diagnosis, rerun the failed upload job using those
same artifacts. A new full build can have different archive bytes and is not
an automatic retry. Do not delete packages, replace assets or retag to bypass
a mismatch.

The Homebrew tap already reconciles PyPI releases hourly in
`update-kollab-formula.yml`, including its own macOS installation/test gate.
No cross-repository dispatch token is needed in this publisher. After PyPI
verification, the release owner can request the existing manual path:
`gh workflow run update-kollab-formula.yml --repo kollaborai/homebrew-tap --ref main -f version=X.Y.Z`.
Check that run and the committed formula before reporting Homebrew complete.

## Post-Release

- [ ] `main` matches current `origin/main`, `git status` lists none of the release's
      files, and the installed release binary reports X.Y.Z. The tag resolves to
      the recorded release merge; any later main SHA and source version are reported.
- [ ] Create follow-up issues for deferred work.
- [ ] Confirm installation instructions still work from a fresh environment.
- [ ] Confirm the changelog entry is visible and human-readable.
- [ ] Confirm support/security links point to public destinations.

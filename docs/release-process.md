---
title: "Release Process"
doc_type: release-process
created: 2026-05-04
modified: 2026-05-04
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
2. Create the release tag: `git tag vX.Y.Z`.
3. Push the tag intentionally.
4. Let the publish workflow build and upload packages.
5. Verify the package page and install path after publication.
6. Re-run the secret scan on the exact public commit.

## Post-Release

- [ ] Create follow-up issues for deferred work.
- [ ] Confirm installation instructions still work from a fresh environment.
- [ ] Confirm the changelog entry is visible and human-readable.
- [ ] Confirm support/security links point to public destinations.

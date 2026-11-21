# Versions, releases and binaries

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

## Version Management
Version managed centrally in `pyproject.toml`:
- **Development mode** (`python main.py`): Reads from `pyproject.toml` at runtime
- **Production mode** (pip install): Uses `importlib.metadata.version("kollab")`

Files reading version: `kollabor/application.py`, `kollabor/cli.py`, `kollabor_config.loader`

To prepare a release version, update the root and workspace metadata, frontend
and desktop versions together, then run `scripts/check_version_parity.py`.

Build provenance is separate from the release version. Python wheel/binary
builds embed the exact source branch and full HEAD SHA in `kollabor._build_info`;
Vite embeds the same identity in the WebUI and signed PhoneLink bundle. The CLI
prints `Build: <branch> @ <8-character SHA>` before its final `kollab X.Y.Z`
line. A detached checkout reports branch `unknown` while retaining its SHA
when HEAD is resolvable; unavailable fields are `unknown`. Installed artifacts
do not need runtime Git access.

## Cutting a Release

`main` is branch-protected: source and version-preparation changes land through
a dedicated writer worktree and DRAFT PR, with the three required CI checks
(`ci/security-scan`, `ci/standards-check`, `ci/tests`). Keep the integration
checkout source-read-only.

1. In a worktree branch `release/X.Y.Z` from current `origin/main`, bump every
   package version and inter-package constraint to `X.Y.Z`, update `uv.lock`,
   run `python scripts/changelog.py build` to fold `changes/` into
   `[Unreleased]`, and move `[Unreleased]` entries into matching byte-identical
   changelogs.
2. Commit by explicit paths, push normally, and open a DRAFT release PR.
   A different AI agent reviews the exact final head and records the required
   GitHub approval as whichever account, `kollaborai` or `maarco`, is not the
   PR author. Record reviewer identity and exact reviewed SHA in private
   maintainer audit evidence; keep the GitHub approval plain, with no AI
   attribution or footer. GitHub prohibits author self-approval. Required CI,
   resolved conversations, and current base must
   pass. A Marco-authorized release plan covers its merge; the designated
   integrator merges as `kollaborai` with a merge commit, never with `--admin`
   or a ruleset bypass. Release tags still require Marco's separate explicit
   instruction.
3. The integrator runs `git pull --ff-only origin main` in the integration
   checkout and proves local `main` equals `origin/main` at the release merge.
   Run the pre-tag release build and validation gates from that exact commit.
   Create and push an annotated `vX.Y.Z` tag there only when Marco explicitly
   instructs it; never push `--tags` broadly.
4. After publishing, run `uv sync --all-extras` and
   `KOLLAB_VERSION=X.Y.Z bash install.sh`. Verify that the tag and published
   artifacts still identify the recorded release merge. Synchronize local
   `main` to current `origin/main`; if it advanced after tagging, report its
   SHA and source version separately from the tagged release version.

After pushing the tag, dispatch `.github/workflows/publish.yml` from reviewed
`main` with `-f tag=vX.Y.Z`. It resolves the immutable tagged source, builds
and checks all binaries/desktop assets, publishes ten packages with the
repository `PYPI_API_TOKEN` secret, then publishes the complete GitHub Release.
The token is referenced only by the two PyPI upload action steps; the protected
`pypi-publish` environment constrains the upload job but does not scope the
repository secret. Upload jobs execute controller code from main, while builds
use the tagged source SHA. Versions and changelog copies must already match
the tag. Homebrew independently reconciles PyPI hourly; verify its formula
update separately.

Canonical checklist: `docs/release-process.md`. Full agent playbook:
`bundles/skills/release-manager/SKILL.md`.

## Release Binaries

Every release also ships `kollab` as one executable per platform
(`kollab-macos-aarch64`, `kollab-macos-x86_64`, `kollab-linux-x86_64`,
`kollab-linux-aarch64`, each with a `.sha256`): a pex scie holding a portable
CPython 3.14 and every wheel, so it runs with no Python installed.
`scripts/build_binary.py` builds and smoke-tests it for the machine it runs on,
with wheels picked for macOS 11 and glibc 2.28, never for the build host;
`.github/workflows/binaries.yml` runs it on each platform only for a `v*` tag
(publish.yml calls it) or a manual run; PRs and branch pushes build no packages.
`install.sh` installs the binary first, and in a binary
`/upgrade` swaps in the latest release's binary after checking its SHA-256 and
that it starts (`kollabor/updates/auto_update.py:_upgrade_binary`).

**Code that starts kollab again** (re-exec, service units, spawned daemons) uses
`kollabor.binary.kollab_argv()`, never `sys.executable -m kollabor_cli_main`:
inside a binary `sys.executable` is its unpacked venv, so an upgraded binary
would keep restarting the old code. `tests/tmux/specs/binary_chat.json` builds
the binary from the checkout and chats through it.

With `kollabor.updates.auto_update_enabled`, an interactive launch installs a
newer release before it starts anything, then execs itself as the new version
(`kollabor/cli.py:_update_before_launch`; `KOLLAB_UPDATED_TO` stops a second
check). In-app, only the launched window installs: a `--detached` daemon just
reports the release, so the two never install it at once.

## Nightly Builds

`.github/workflows/nightly.yml` runs at 09:00 UTC and on manual dispatch, on
`main` only. When main's head has no `nightly-*` tag yet and its `ci/tests`
check passed, it stamps `X.Y.(Z+1).devYYYYMMDDHHMM` into
every pyproject, every `kollabor-*` constraint and the frontend `package.json`
(`scripts/nightly_version.py --stamp`; build only, never committed). It then
builds the four binaries with the same `build_binary.py --expect-version` smoke
test that releases run, and publishes them as a prerelease tagged
`nightly-<version>`. The release-tag ruleset protects `v*`, so nightly tags never
use that form. The workflow keeps the newest 14 nightlies. Desktop apps and PyPI
are not built nightly.

`kollabor.updates.include_prereleases` is the nightly channel, honored only by
the single-file binary (`running_binary()` set, `app_launcher()` unset). The
update cache saves `cached_include_prereleases`, and a cache from the other
channel is refetched. With the channel on,
`VersionCheckService` and `auto_update._release_to_install` read
`/releases?per_page=30` and take the newest non-draft version
(`version_comparator.newest_release`); with it off they read `/releases/latest`,
which never returns a prerelease. Tag-to-version parsing goes through
`version_comparator.release_version`, which strips both prefixes. `install.sh`
maps a `.dev` `KOLLAB_VERSION` to its `nightly-` tag.

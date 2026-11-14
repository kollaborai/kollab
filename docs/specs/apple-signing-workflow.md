# Apple Signing Workflow

Issue: #1086  
Status: Implemented  
Owner: Desktop packaging

## Problem

Release maintainers need a supported way to produce a signed and notarized
macOS desktop artifact. Before this workflow change, `.github/workflows/binaries.yml`
only built the unsigned path, and the generated disk image was not notarized or
stapled. Issue #1086 records the missing workflow input, secret wiring, and dmg
notarization.

## Goal and non-goals

Goal: let a maintainer explicitly select Developer ID signing for a macOS build
on `main`, while keeping ordinary manual builds and publish-triggered builds
unsigned by default.

Non-goals: adding Apple credentials to the repository, changing Linux or
Windows packaging, or contacting Apple services in fixture tests.

## What people see

- `workflow_dispatch` exposes `apple_signing_mode` with `unsigned` and
  `developer-id` choices; `unsigned` is the default.
- Selecting `developer-id` outside `refs/heads/main` fails the mode guard.
- Publish-triggered tag runs carry no signing input and use the unsigned path.
- A successful Developer ID run produces the signed, notarized app and a dmg
  that is also notarized and stapled.

## Contract

- The existing `desktop` job in `.github/workflows/binaries.yml` owns this
  workflow. The `build`, Linux, and Windows jobs are unchanged.
- Job environment includes `APPLE_SIGNING_MODE` and `KOLLAB_APP_RUNTIME`.
  Missing signing input resolves to `unsigned`; unknown values fail.
- The unsigned app build and its ad hoc seal run only in unsigned mode.
- The Developer ID app build and dmg notarization run only for
  `developer-id` on `refs/heads/main`. Apple credentials are passed at step
  scope. Signing state is kept under `RUNNER_TEMP`, with a cleanup retry step.
- `desktop/scripts/sign-macos.sh notarize-dmg <absolute .dmg>` uses the shared
  notarization authentication selection and Accepted-status check, staples the
  dmg, and cleans its owned signing-state directory on success or failure.
- App checks, archive creation, dmg creation, and checksums remain in both
  modes.

## Scenarios

- If a manual run leaves the signing input unset, then the macOS job builds and
  seals the unsigned app.
- If a publish-triggered tag run omits the signing input, then it resolves to
  unsigned mode.
- If a `main` manual run selects `developer-id` and the required credentials
  are valid, then the app is signed, verified, notarized, and stapled, and the
  resulting dmg is notarized and stapled.
- If a non-main run selects `developer-id`, then the guard fails before the
  signing build runs.
- If the signing mode is unknown, then the guard fails rather than silently
  selecting a build path.
- If dmg notarization fails or does not reach Accepted status, then the command
  fails, does not report success, and removes its owned temporary state.

## Failure modes

- Missing signing input: use unsigned mode.
- Unknown signing mode: fail at the mode guard.
- Developer ID selected off `main`: fail before signing.
- Incomplete or invalid notarization credentials: fail before successful
  notarization or stapling is reported.
- Notary service returns a non-Accepted result: fail the signing command.
- Dmg stapling fails: fail the signing command and clean its temporary state.
- Temporary signing-state cleanup first fails: the workflow cleanup retry runs
  for Developer ID mode.

## Decisions

- Keep the default unsigned so existing manual and reusable-workflow callers
  that omit the new input retain their behavior. This follows issue #1086's
  acceptance criteria and the PR #1090 implementation.
- Restrict Developer ID signing to `main`, as required by issue #1086.
- Keep notarization credential selection in `sign-macos.sh` and reuse it for
  app and dmg notarization, as specified by issue #1086.

## Test plan

- Run `desktop/tests/test_apple_signing.py` for credential selection,
  preflight rejection, and cleanup behavior using fixture executables.
- Parse the workflow YAML and run `shellcheck` and `bash -n` on the signing
  helper.
- Verify a clean-Mac signed release separately before shipping; fixture tests do
  not establish live Apple notarization behavior.

## Docs and changelog fragment

- Contract and operator guidance: `desktop/docs/apple-signing.md`.
- Changelog fragment: `changes/1086.added.md`.

## Open questions

None.

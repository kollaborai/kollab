# macOS signing and notarization

`desktop/scripts/sign-macos.sh` wraps a caller-supplied Tauri build, adds the
staged kollab runtime to its `Kollab.app`, signs every Mach-O in it, then
notarizes and staples the app when explicitly requested. The caller keeps
ownership of runtime staging, target/version settings, archives, checksums and
publication. This helper can be merged against main independently of the desktop
release workflow in [PR #192](https://github.com/kollaborai/kollab/pull/192).

`APPLE_SIGNING_MODE` defaults to `unsigned`, which needs no Apple account or
credentials. When the helper wraps an unsigned build it clears Apple credentials
from the build environment and uses Tauri's ad-hoc identity `-`. This is not a
Developer ID signature and does not remove Gatekeeper's unverified-developer warning.

The optional `developer-id` mode is all-or-none: all four common secrets below
plus exactly one complete notarization authentication set are required. Missing,
partial or simultaneous authentication sets fail before any build or keychain
operation. It never automatically enables signing or downgrades a requested
signed build. Explicit unsigned mode ignores inherited Apple credentials.

## The bundled kollab runtime

Kollab.app ships no scie: `codesign` cannot sign one (every scie-jump keeps a
15-byte trailer after `LC_CODE_SIGNATURE`) and notarization rejects any unsigned
Mach-O. `scripts/desktop_release.py stage --platform macos-*` unpacks the
verified CLI scie instead (`SCIE=split`): its own CPython 3.14, kollab and every
dependency installed into that Python's `site-packages` by the pex's own tools,
Tcl/Tk, headers and pip removed, all bytecode precompiled with
`unchecked-hash`. It lands in `desktop/src-tauri/binaries/app-runtime-<target>/`:

```text
Kollab.app/Contents/Resources/kollab              sh launcher: python3.14 -I -B -m kollabor_cli_main
Kollab.app/Contents/Resources/runtime/bin/python3.14
Kollab.app/Contents/Resources/runtime/lib/python3.14/...   stdlib, site-packages (about 25 .so)
Kollab.app/Contents/Resources/runtime/KOLLAB-APP           marker read by kollabor/binary.py
```

`KOLLAB_APP_RUNTIME` names that directory for the helper. After Tauri builds,
the helper copies it into `Contents/Resources`, signs each Mach-O under
`runtime/` and then the app, and runs `codesign --verify --deep --strict`.
Nothing inside the app may be written later, or its seal breaks: the launcher
passes `-B`, a `kollab-app.pth` sets `sys.dont_write_bytecode` for Python
children, and `/upgrade` refuses to update a kollab running from the app
(`kollabor/updates/auto_update.py`). Agents, daemons and restarts it starts run
the launcher, not a `kollab` on PATH (`kollabor.binary.own_launcher`).
`main.rs` still runs `Contents/Resources/kollab --web-ui`.

Ad hoc signing leaves out hardened runtime on purpose. Hardened runtime turns on
library validation, which loads an extension only when it shares the
interpreter's Team ID, and ad hoc signatures have none: `pydantic_core` fails
with "different Team IDs". A Developer ID signature gives every file the same
Team ID, so the signed path needs no entitlements. Checked locally with ad hoc
hardened runtime plus `disable-library-validation` on python3.14 standing in for
the shared Team ID: kollab's imports, HTTPS, ctypes and cffi callbacks, PyNaCl
and uvloop all run.

The **Apple signing helper checks** workflow runs fixture/static validation with
no Apple secrets or keychain access. A release owner connects the block below to
the macOS packaging job after #192 lands; no signing release job is enabled here.

## What Marco must create

1. Enroll the distributing person/organization in the Apple Developer Program.
   The account must have access to Developer ID distribution and notarization.
2. On a trusted Mac, create a certificate signing request in Keychain Access.
   In Apple Developer **Certificates, Identifiers & Profiles**, have the Account
   Holder create a **Developer ID Application** certificate from that request.
   Install the downloaded certificate beside its original private key. This is
   outside-App-Store distribution; an Apple Development, Apple Distribution or
   Developer ID Installer certificate is not the requested identity.
3. In Keychain Access, export the certificate **with its private key** as a
   password-protected `.p12`. Record the exact identity reported by
   `security find-identity -v -p codesigning`, including the team suffix.
4. Choose one notarization authentication method below. Do not configure both.
5. A repository administrator adds the secrets in GitHub **Settings → Secrets
   and variables → Actions**. Keep the `.p12`, its private key, exported base64,
   passwords and API `.p8` out of Git, issues, PRs, logs and build artifacts.

All `developer-id` builds require these repository secrets:

- `APPLE_CERTIFICATE`: base64 of the `.p12`, produced with
  `openssl base64 -A -in certificate.p12 -out certificate-base64.txt`.
- `APPLE_CERTIFICATE_PASSWORD`: the export password chosen in Keychain Access.
- `APPLE_SIGNING_IDENTITY`: the exact `Developer ID Application: <name> (<team>)`
  identity. Do not put an example identity into the workflow.
- `APPLE_TEAM_ID`: the distributing team's ID from Apple Developer membership.

Then configure exactly one authentication set:

- Apple ID: `APPLE_ID` is the account email; `APPLE_PASSWORD` is a newly created
  **app-specific password**, not the account's normal login password. Create it
  at the Apple Account's Sign-In and Security → App-Specific Passwords page.
- App Store Connect team API key: create a key with Developer access under
  Users and Access → Integrations. Set `APPLE_API_ISSUER` to its issuer ID,
  `APPLE_API_KEY` to its key ID, and `APPLE_API_PRIVATE_KEY` to the complete
  downloaded `.p8` contents. Apple permits that private-key download only once.
  The helper creates `APPLE_SIGNING_STATE_DIR/notary-key.p8` privately and passes
  it through `notarytool --key`; do not configure a path from Marco's Mac as a
  repository secret. An issuer-less individual API key is not this helper's
  supported authentication set.

No certificate, key, identity, account, team or credential is supplied by this PR.

## Helper contract

Run from the same working directory as the existing Tauri build.
The repository examples below retain the generated release overlay from
`scripts/desktop_release.py stage`; stage the matching native CLI first, as the
macOS job does. Tauri 2.12 merges repeated `--config` arguments in order, keeping
the version overlay and the additional hardened-runtime setting together.

```bash
# Optional early gate; omitted mode uses APPLE_SIGNING_MODE, then unsigned.
bash scripts/sign-macos.sh preflight

# APP_PATH must be absolute, end in /Kollab.app, and not already exist.
# The helper runs the command after -- with its arguments and directory intact.
bash scripts/sign-macos.sh build "$APP_PATH" -- \
  cargo tauri build --target "$TARGET" --bundles app \
  --config '{"bundle":{"macOS":{"hardenedRuntime":true}}}' \
  --config src-tauri/tauri.release.json

# Unsigned: add the runtime to an existing Tauri build and seal it ad hoc.
bash scripts/sign-macos.sh seal "$APP_PATH"

# Safe, idempotent retry after the signed build's own EXIT/INT/TERM cleanup.
bash scripts/sign-macos.sh cleanup
```

`build` and `seal` both read `KOLLAB_APP_RUNTIME` and fail before any build when
it does not name a staged runtime. `seal` refuses an app that already holds one.

The caller sets `APP_PATH` to its actual build output, including the target/profile
layout. Use a fresh output directory: rejecting an existing app prevents a successful
command from accidentally validating stale output. The helper accepts the caller's
Apple Silicon or Intel target and version/config flags without choosing its own.
For signed builds enable `bundle.macOS.hardenedRuntime` in that same build config;
verification rejects signatures missing it. Preserve existing version overrides
when adding that setting instead of replacing the caller's complete config.

Signed builds additionally need `RUNNER_TEMP` and `APPLE_SIGNING_STATE_DIR`. The
latter must be a new direct child named `kollab-apple-signing-*` under the former.
Python 3.9+, Bash, OpenSSL and the macOS/Xcode command-line tools must be available.

## Connect the existing macOS packaging job

The macOS packaging job is in `binaries.yml`. To opt in, add an explicit manual
input to that workflow. Tag, push and reusable-workflow runs with no input
continue to resolve to unsigned. No secret
should enable signing merely by existing in repository settings.

```yaml
"on":
  workflow_dispatch:
    inputs:
      apple_signing_mode:
        description: Optional Developer ID signing and notarization
        type: choice
        options: [unsigned, developer-id]
        default: unsigned
```

The following is a job fragment, not a second packaging workflow. Keep the existing
sidecar preparation, Rust tools, matrix and asset names. The caller exports `TARGET`
and an absolute, fresh `CARGO_TARGET_DIR` and checks out main before selecting
Developer ID mode. Retain all existing build/version/config arguments in **both**
build commands below. The generated `src-tauri/tauri.release.json` from shared
sidecar staging supplies the release version; keep that overlay in both paths.
The signed path additionally enables hardened runtime. Give the
job enough time for the build plus the 30-minute notary wait.

```yaml
env:
  APPLE_SIGNING_MODE: ${{ inputs.apple_signing_mode || 'unsigned' }}
  KOLLAB_APP_RUNTIME: ${{ github.workspace }}/desktop/src-tauri/binaries/app-runtime-${{ matrix.target }}
steps:
  - name: Check the explicitly selected mode and trusted ref
    shell: bash
    run: |
      case "$APPLE_SIGNING_MODE" in
        unsigned) ;;
        developer-id)
          [ "$GITHUB_REF" = refs/heads/main ] || { echo 'Developer ID builds require main.' >&2; exit 1; }
          ;;
        *) echo 'Unknown Apple signing mode.' >&2; exit 1 ;;
      esac
  - name: Existing unsigned Tauri build
    if: env.APPLE_SIGNING_MODE == 'unsigned'
    working-directory: desktop
    run: |
      cargo tauri build --target "$TARGET" --bundles app --config src-tauri/tauri.release.json
      bash scripts/sign-macos.sh seal "$CARGO_TARGET_DIR/$TARGET/release/bundle/macos/Kollab.app"
  - name: Optional Developer ID Tauri build
    if: env.APPLE_SIGNING_MODE == 'developer-id' && github.ref == 'refs/heads/main'
    working-directory: desktop
    shell: bash
    env:
      APPLE_CERTIFICATE: ${{ secrets.APPLE_CERTIFICATE }}
      APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
      APPLE_SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}
      APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
      APPLE_ID: ${{ secrets.APPLE_ID }}
      APPLE_PASSWORD: ${{ secrets.APPLE_PASSWORD }}
      APPLE_API_KEY: ${{ secrets.APPLE_API_KEY }}
      APPLE_API_ISSUER: ${{ secrets.APPLE_API_ISSUER }}
      APPLE_API_PRIVATE_KEY: ${{ secrets.APPLE_API_PRIVATE_KEY }}
    run: |
      export APPLE_SIGNING_STATE_DIR="$RUNNER_TEMP/kollab-apple-signing-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
      printf 'APPLE_SIGNING_STATE_DIR=%s\n' "$APPLE_SIGNING_STATE_DIR" >> "$GITHUB_ENV"
      bash scripts/sign-macos.sh build "$CARGO_TARGET_DIR/$TARGET/release/bundle/macos/Kollab.app" -- \
        cargo tauri build --target "$TARGET" --bundles app \
        --config '{"bundle":{"macOS":{"hardenedRuntime":true}}}' \
        --config src-tauri/tauri.release.json
  - name: Retry cleanup of only this job's temporary keychain
    if: always() && env.APPLE_SIGNING_MODE == 'developer-id'
    run: bash desktop/scripts/sign-macos.sh cleanup
  # Keep existing archive/checksum/upload steps after successful builds.
```

Use step-level secret injection only for the explicitly selected, main-only signed
step. Keep checkout credentials unpersisted and workflow token permissions at
`contents: read` unless an existing publication step requires its own write scope.
Do not give secrets to PR jobs or use `pull_request_target` for this integration.
The helper deliberately clears Tauri's automatic certificate/notary variables from
the build subprocess: the temporary keychain provides signing and the helper owns
the single notarization submission. Do not pass secrets in build arguments.

Keep archive, checksum and upload steps success-only: no `always()` or
`continue-on-error` on the build or publication path. Archive the **post-stapling**
app at the same path only after the helper succeeds, then retain the existing
release workflow's validation. Tauri updater signatures are a separate concern.

## Validation and cleanup

Developer ID packaging uses a new password-protected temporary keychain, verifies
the imported identity, and preserves/restores the user's existing keychain search
list. It never makes its keychain the default and never imports into login.
An EXIT/INT/TERM cleanup trap removes its import/key files and temporary keychain;
an `always()` workflow step retries cleanup after errors or cancellation. The
helper refuses cleanup of unowned directories or paths outside this job's
`RUNNER_TEMP`. Use GitHub-hosted ephemeral macOS runners: a machine crash or hard
kill can prevent any cleanup handler, so this is not a persistent self-hosted
runner secret-management design. Configure the consuming workflow to upload only
its final archive/checksum and exclude all temporary signing state.

The signed path verifies the expected team, hardened runtime and secure
timestamp on the app and on every Mach-O under `Contents/Resources/runtime`,
since `--deep` never reaches code in Resources and notarization checks all of
it. It submits a ZIP with `notarytool --wait`
(30-minute wait limit), requires JSON status **Accepted**, staples and validates
the ticket, rechecks the signature, and runs Gatekeeper assessment. It returns
success only after all of those checks and cleanup succeed. Rejection, timeout,
bad signatures, a missing runtime, staple or Gatekeeper errors return nonzero. A timeout
does not cancel Apple's server-side submission; use its submission ID in Apple's
notary history before deciding whether to retry.

Before publishing a signed download, on a clean Mac extract it, set `ARCHIVE` to
the existing workflow's archive filename, and run:

```bash
shasum -a 256 -c "$ARCHIVE.sha256"
codesign --verify --deep --strict --verbose=2 Kollab.app
xcrun stapler validate Kollab.app
spctl --assess --type execute --verbose=2 Kollab.app
```

Open it through Finder with download quarantine intact, check the window/tray,
launch the bundled runtime and exercise its normal agent/tool flow. Check both
architectures before claiming signed release qualification. No entitlements or
hardened-runtime exceptions are introduced here; the signed Python runtime still
needs that real execution check. Do not weaken signing checks to
hide a failed smoke test. Certificate expiry/revocation and Apple service delays
require operator attention; fixture tests do not prove Apple's acceptance.

## What notarization still needs

The layout and the signing order are in place and proven ad hoc; these steps
need a Developer ID and have not run yet:

1. Apple Developer Program enrollment, a **Developer ID Application**
   certificate and the repository secrets above (see "What Marco must create").
2. Wire the `developer-id` step from the fragment above into the macOS job of
   `binaries.yml`, with `KOLLAB_APP_RUNTIME` and the hardened-runtime config.
   Only the unsigned `seal` path is connected today.
3. A first real submission for each architecture. If Apple rejects a file,
   `xcrun notarytool log <id>` names it; every Mach-O under
   `Contents/Resources/runtime` (about 25 `.so` plus `python3.14`, some of
   them universal) must carry the team, hardened runtime and a timestamp.
4. Run the notarized app with no entitlements first. ctypes and cffi callbacks,
   PyNaCl, uvloop and TLS worked under hardened runtime locally; add
   `com.apple.security.cs.allow-unsigned-executable-memory` only if a signed
   run proves it is needed.
5. Notarize and staple the `.dmg` as well as the app inside it, then run the
   clean-Mac checks below on both downloads.
6. Expect one Keychain prompt per stored secret after the switch: macOS knows
   `python3.14` by its signature, and Developer ID replaces the ad hoc one.
   From then on the requirement (team + identifier) stays the same across
   updates, so later releases do not prompt again.

## Local validation without Apple credentials

Needs Python 3.9+, Ruby's standard YAML library, Bash and ShellCheck. Tests replace
Apple/Rust tools with fixture executables and never access the real keychain.
They execute the documented unsigned build, both signing authentication methods,
and the cleanup retry after loading the example's `GITHUB_ENV` state path.

```bash
python3 -m unittest discover -s desktop/tests -p 'test_apple_signing.py' -v
bash -n desktop/scripts/sign-macos.sh
shellcheck desktop/scripts/sign-macos.sh
python3 -m json.tool desktop/src-tauri/tauri.conf.json > /dev/null
git diff --check
```

References: [Tauri macOS signing](https://v2.tauri.app/distribute/sign/macos/),
[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
and [GitHub runner certificates](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).

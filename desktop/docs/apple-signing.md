# macOS signing and notarization

The **Desktop macOS packaging** workflow builds the existing Tauri app for Apple
Silicon and Intel. Its default mode is `unsigned`: no Apple secrets or account
are needed. Tauri uses the ad-hoc identity `-`, which is not a Developer ID
signature and does not remove Gatekeeper's unverified-developer warning.
The existing CLI assets, release workflow and local desktop build stay unchanged.

The optional `developer-id` mode signs **and** notarizes the app. It fails before
building if required secrets are missing or authentication is ambiguous. It never
silently falls back to an unsigned download. This mode can run only by manually
dispatching the workflow from `main`; PRs run fixture/static validation without
Apple secrets, keychain access or notarization.

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
  `openssl base64 -A -in <certificate.p12> -out <certificate-base64.txt>`.
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
  repository secret. An issuer-less individual API key is not this workflow's
  supported authentication set.

No certificate, key, identity, account, team or credential is supplied by this PR.

## Run and validate

After this workflow is merged to main:

1. Open Actions → Desktop macOS packaging → Run workflow. Leave mode `unsigned`
   for the default path, or explicitly choose `developer-id` after configuring
   the secrets above. Select `main` for Developer ID builds.
2. Set `runtime_tag` to an existing published Kollab release, initially
   `v0.16.1`. Each architecture downloads the existing `kollab-macos-<arch>` and
   `.sha256` assets, verifies the checksum, and supplies the corresponding Tauri
   `binaries/kollab-<target>` sidecar. No CLI release asset is replaced.
3. Download `Kollab-macos-<arch>-<mode>` from that workflow run. It contains a
   ZIP holding `Kollab.app` and the ZIP's checksum. This workflow does not create
   a GitHub Release, upload public release assets, or configure Tauri updater
   signatures. Those are separate release-owner actions.

Developer ID packaging uses a new password-protected temporary keychain, verifies
the imported identity, and preserves/restores the user's existing keychain search
list. It never makes its keychain the default and never imports into login.
An EXIT/INT/TERM cleanup trap removes its import/key files and temporary keychain;
an `always()` workflow step retries cleanup after errors or cancellation. The
helper refuses cleanup of unowned directories or paths outside this job's
`RUNNER_TEMP`. Use GitHub-hosted ephemeral macOS runners: a machine crash or hard
kill can prevent any cleanup handler, so this is not a persistent self-hosted
runner secret-management design. Only the final ZIP/checksum are uploaded.

The signed path verifies the app and bundled `kollab` sidecar, expected team,
hardened runtime and secure timestamp. It submits a ZIP with `notarytool --wait`
(30-minute wait limit), requires JSON status **Accepted**, staples and validates
the ticket, rechecks the signature, and runs Gatekeeper assessment. It creates
the distributable ZIP only **after** stapling. Rejection, timeout, bad signatures,
missing sidecars, staple or Gatekeeper errors prevent artifact upload. A timeout
does not cancel Apple's server-side submission; use its submission ID in Apple's
notary history before deciding whether to retry.

Before publishing a signed download, on a clean Mac extract it and run:

```bash
shasum -a 256 -c Kollab-macos-<arch>-developer-id.zip.sha256
codesign --verify --deep --strict --verbose=2 Kollab.app
xcrun stapler validate Kollab.app
spctl --assess --type execute --verbose=2 Kollab.app
```

Open it through Finder with download quarantine intact, check the window/tray,
launch the bundled runtime and exercise its normal agent/tool flow. Check both
architectures before claiming signed release qualification. No extra entitlements
or hardened-runtime exceptions are introduced here; the packaged Python/scie
runtime still needs that real execution check. Do not weaken signing checks to
hide a failed smoke test. Certificate expiry/revocation and Apple service delays
require operator attention; fixture tests do not prove Apple's acceptance.

## Local validation without Apple credentials

Needs Python 3.9+, Ruby's standard YAML library, Bash and ShellCheck. Tests replace
Apple/Rust tools with fixture executables and never access the real keychain.

```bash
python3 -m unittest discover -s desktop/tests -p 'test_apple_signing.py' -v
bash -n desktop/scripts/package-macos.sh
shellcheck desktop/scripts/package-macos.sh
python3 -m json.tool desktop/src-tauri/tauri.conf.json > /dev/null
git diff --check
```

References: [Tauri macOS signing](https://v2.tauri.app/distribute/sign/macos/),
[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
and [GitHub runner certificates](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).

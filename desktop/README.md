# Kollab Desktop

A desktop tray app that runs Kollab locally, like Ollama or Docker Desktop.
It ships its own `kollab` and opens the Kollab web UI in a native window. The
app bundles no frontend: the window loads `http://127.0.0.1:<port>/`, served by
`kollab --web-ui`. The frontend itself stays in `packages/kollabor-webui`.
Built with Tauri 2.

macOS uses the existing menu bar app. Linux x86_64 builds produce an AppImage
and a Debian package from the same source; closing the window keeps its tray
icon available. Windows x86_64 builds an NSIS installer in a provisional job and
releases none yet (see [Windows](#windows)). The four existing CLI release
binaries are unchanged.

## Build

Needs Rust and the Tauri 2 CLI (`cargo install tauri-cli --version "^2"`, or
use `npx @tauri-apps/cli@2` in place of `cargo tauri`).

First unpack a kollab release binary into the app runtime. `stage` checks the
binary against its `.sha256`, so use a release asset pair or build one from this
checkout; the runtime lands in the gitignored `src-tauri/binaries/`.

```bash
python scripts/build_binary.py --out dist/bin    # or download kollab-macos-* + .sha256
python scripts/desktop_release.py stage --platform macos-aarch64 --source dist/bin/kollab-macos-aarch64
cd desktop
cargo tauri build --config src-tauri/tauri.release.json --bundles app
KOLLAB_APP_RUNTIME="$PWD/src-tauri/binaries/app-runtime-aarch64-apple-darwin" \
  bash scripts/sign-macos.sh seal "$PWD/src-tauri/target/release/bundle/macos/Kollab.app"
cargo test --manifest-path src-tauri/Cargo.toml
```

The bundle is `src-tauri/target/release/bundle/macos/Kollab.app` (ad-hoc
signed, about 175 MB on disk). To watch its log, run the binary from a terminal:
`.../Kollab.app/Contents/MacOS/kollab-desktop`.

The CLI is not a scie inside the app: `codesign` cannot sign a scie, and
notarization rejects unsigned code. `stage` unpacks it into the scie's own
CPython with kollab installed in it; `seal` copies that to
`Kollab.app/Contents/Resources/` (`kollab` launcher, `runtime/`), signs every
Mach-O in it, then the app, and `codesign --verify --deep --strict` must pass.
The app updates whole: a kollab running from it never runs `/upgrade` in place.
Details in [macOS signing and notarization](docs/apple-signing.md).

Release builds publish one unsigned `.app.zip` and one `.dmg` for each supported
macOS architecture (`aarch64` and `x86_64`), targeting macOS 11 or newer to
match the bundled CLI, with a sibling `.sha256` file for
each archive. The archives are attached to the GitHub Release alongside the
CLI binaries and are named `kollab-desktop-macos-<arch>-<version>.*`.

macOS and Linux CI use the same verified sidecar staging helper. It checks the
downloaded CLI's SHA-256 and reported version, restores executable permissions,
and generates the ignored `tauri.release.json` overlay. CI builds the apps only
for a `v*` release tag or a manual run. A manual build derives the release
version from `pyproject.toml`; a release tag must match it. The committed base
Tauri config is not rewritten during builds.
The packaged macOS CLI and app metadata must report that version before the
`.app.zip` and `.dmg` archives are collected.

The app is not signed with an Apple Developer ID, so Gatekeeper blocks its
first launch with "Apple could not verify Kollab is free of malware".
Control-click **Open** no longer gets past this on macOS 15 and newer. To allow
it once:

1. Drag `Kollab.app` to Applications and open it. Click **Done** on the warning.
2. Open **System Settings > Privacy & Security**, scroll to the Security
   section, and click **Open Anyway** next to the Kollab message (it shows for
   about an hour after the blocked launch). Confirm with your password.
3. Open Kollab again and click **Open Anyway** in the dialog.

Or, from a terminal, clear the download's quarantine flag and open it normally:

```bash
xattr -dr com.apple.quarantine /Applications/Kollab.app
```

## Signing and notarization

The reusable `scripts/sign-macos.sh` helper adds the staged runtime to a Tauri
build and signs it: ad hoc by default, or with Developer ID, notarization and
stapling from explicitly configured Apple Developer secrets. It does not
download sidecars, archive or publish assets.
See [macOS signing and notarization](docs/apple-signing.md) for operator setup,
cleanup guarantees, and validation limits.

## Linux packages

CI builds Linux desktop packages on Ubuntu 22.04 with WebKitGTK 4.1, GTK 3,
Ayatana AppIndicator, librsvg, libxdo and patchelf. The desktop system-library
baseline is separate from the unchanged CLI's glibc 2.28 baseline. Debian
packages declare their runtime dependencies; AppImages still require a Linux
desktop with compatible system libraries. External links use `xdg-open`.

For a source build, first build the native CLI with
`python scripts/build_binary.py --out dist/bin` from the repository root, then:

```bash
python scripts/desktop_release.py stage --platform linux-x86_64 --source dist/bin/kollab-linux-x86_64
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
cd desktop
NO_STRIP=1 cargo tauri build --config src-tauri/tauri.release.json --bundles appimage,deb
cd ..
python scripts/desktop_release.py collect --platform linux-x86_64
```

The verified CLI becomes `src-tauri/binaries/kollab-x86_64-unknown-linux-gnu`;
both Linux packages install it as `usr/share/kollab/runtime/kollab`, which the
app resolves relative to its `usr/bin/kollab-desktop`. Staging restores
its executable bit after an Actions artifact download and checks both its
SHA-256 and reported version. `NO_STRIP=1` preserves the scie's appended Python
archive during AppImage bundling. The data-directory placement also keeps
linuxdeploy from rewriting its ELF library paths and corrupting the scie lift
manifest. CI extracts both packages, compares the bundled
CLI byte for byte, and runs its `--version`. The generated, ignored `tauri.release.json`
uses the source version; a supplied release tag must agree with that source.
The Debian package also declares `xdg-utils` for external links; AppImage hosts
need `xdg-open` available. CI uses the pinned prebuilt npm Tauri 2.12.0 CLI;
the equivalent Cargo CLI invocation above works for source builds.

Public Linux assets are:

- `kollab-desktop-linux-x86_64-X.Y.Z.AppImage` and `.AppImage.sha256`
- `kollab-desktop-linux-x86_64-X.Y.Z.deb` and `.deb.sha256`

Download a payload and its checksum into one directory and run
`sha256sum -c <asset>.sha256`. AppImages downloaded through a browser need
their executable bit enabled before opening. These packages are unsigned;
SHA-256 detects corruption and does not replace publisher authentication.

The release job checks the exact flattened asset set and every checksum before
uploading. It rejects missing files, unexpected names, symlinks, and changed
payloads. The combined manifest has 20 release files: four CLI/checksum pairs,
four macOS desktop/checksum pairs, and two Linux desktop/checksum pairs.
The merged macOS packaging and Apple signing preparation are retained.
CI downloads and flattens the outputs of all seven native build jobs, then
checks these 20 files and all ten SHA-256 pairs before a release can publish.

CI also opens the packaged AppImage under Xvfb against an isolated test Web UI,
checks rendered content, closes/reopens the window through a second launch,
and quits while confirming the attached test runtime remains reachable. This
checks the native package/window lifecycle; it does not prove a live model turn.

## Windows

Windows is built by the provisional `desktop-windows` job in
`.github/workflows/binaries.yml`, on a manual run only until the native port
lands (a release tag never runs it, and it never runs on a pull request): it builds the native runtime (`kollab-windows-x86_64.exe`, a pex scie
like the other platforms), stages it as `runtime\kollab.exe` and builds an NSIS
installer, `Kollab_X.Y.Z_x64-setup.exe`, per user with no administrator prompt.

```bash
python scripts/build_binary.py --out dist/bin          # on Windows: kollab-windows-x86_64.exe
python scripts/build_binary.py --agent-smoke dist/bin/kollab-windows-x86_64.exe
python scripts/desktop_release.py stage --platform windows-x86_64 --source dist/bin/kollab-windows-x86_64.exe
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
cd desktop && npx @tauri-apps/cli@2.12.0 build --config src-tauri/tauri.release.json --bundles nsis
cd .. && python scripts/desktop_release.py collect --platform windows-x86_64
```

**It is not released yet.** The native runtime cannot start an agent on Windows
(`kollab --detached` forks, agents talk over Unix sockets, the hub locks with
`fcntl`), so `--agent-smoke` fails and the job publishes no installer: the
release artifact `kollab-desktop-windows-x86_64` is uploaded only when every
step passed, and the installer built meanwhile goes to the non-release artifact
`windows-desktop-unverified`. The blockers, the Windows shell changes and the
ordered port work are in [docs/specs/windows-desktop.md](../docs/specs/windows-desktop.md).
Until the port lands, attach: if a Kollab web UI already answers on
`KOLLAB_WEBUI_PORT` (a runtime in WSL or on another machine reached on
`127.0.0.1`), the installed app's window opens it.

Once released the assets are `kollab-desktop-windows-x86_64-X.Y.Z-setup.exe` and
`.sha256`, unsigned (SHA-256 detects corruption; it does not authenticate the
publisher). Windows SmartScreen shows "Windows protected your PC" on first run:
choose **More info**, then **Run anyway**. Authenticode signing can follow, as
Apple signing did.

On Windows the app resolves `runtime\kollab.exe` beside `kollab-desktop.exe`,
then an installed `kollab.exe` (`where.exe`, then `~\.local\bin`), and starts
`kollab.exe --web-ui` without a console window. Quit closes the runtime's
stdin, and the runtime stops its UI server and engine and exits; if it still
runs after 10 s, the app ends the tree it started with `taskkill /T /F`.
`kollab-desktop.exe --quit` quits a running app.

## Phone (iOS and Android)

The same crate builds the phone app (#369). `src/lib.rs` is the entry point for
both; the tray, single-instance, window-state and autostart plugins are
desktop-only. The phone app opens its own link screen first (`mobile/`, set
in `src-tauri/tauri.ios.conf.json` and `src-tauri/tauri.android.conf.json`):
Scan QR Code reads the code from Settings → PhoneLink with the camera, and
Paste Link Instead takes the copied link. It opens only a fresh PhoneLink
invite (`mobile/invite.js`), then hands the app to the PhoneLink loader on that
directory, which links it the same way the QR code does in Safari. Later
launches go straight to the saved directory. Inside the app, the loader's
error screen adds Scan QR Code, which comes back to the link screen. Only
the link screen may use the camera scanner (`src-tauri/capabilities/phone.json`);
pages a directory serves get no app permissions. There are no store builds.

Build and run it in the iPhone simulator (Xcode with an iOS simulator runtime,
and `rustup target add aarch64-apple-ios aarch64-apple-ios-sim`):

```bash
cargo install tauri-cli --version 2.12.0 --locked   # the release workflow's version
cd desktop/src-tauri
cargo tauri ios build --target aarch64-sim --debug
xcrun simctl boot "iPhone 18 Pro"
xcrun simctl install booted gen/apple/build/arm64-sim/Kollab.app
xcrun simctl launch booted ai.kollabor.kollab
```

`gen/apple/` is the generated Xcode project and is committed. To regenerate it,
move it away and run `cargo tauri ios init --ci`: the iOS minimum (15.0) comes
from `bundle.iOS.minimumSystemVersion` in `tauri.conf.json`, the camera and
microphone texts from `src-tauri/Info.ios.plist`.

Build and run it in the Android emulator (JDK 17; the Android SDK with
`platform-tools`, `ndk;29.0.14206865`, `emulator` and
`system-images;android-36;google_apis;arm64-v8a`, its license accepted so
Gradle can fetch the SDK platform and build tools it needs; and
`rustup target add aarch64-linux-android`, with rustup's `cargo` first in
`PATH`, since Homebrew's `rust` has no Android targets):

```bash
export ANDROID_HOME=<sdk> NDK_HOME="<sdk>/ndk/29.0.14206865" JAVA_HOME=<jdk 17>
cd desktop/src-tauri
cargo tauri android build --apk --target aarch64 --debug
avdmanager create avd -n kollab-phone -k "system-images;android-36;google_apis;arm64-v8a" -d pixel_7
emulator -avd kollab-phone &
adb install gen/android/app/build/outputs/apk/universal/debug/app-universal-debug.apk
adb shell am start -n ai.kollabor.kollab/.MainActivity
```

`gen/android/` is the generated Gradle project and is committed. To regenerate
it, move it away, run `cargo tauri android init --ci`, and put the microphone
permissions (`RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`) back in
`gen/android/app/src/main/AndroidManifest.xml` and the light status bar icons
(`enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT))`)
back in `MainActivity.kt`, and `gen/android/app/src/debug/` (debug builds
trust user-installed CAs, for the emulator's scratch https relay). The camera
permission comes from the barcode scanner plugin's own manifest.

`tests/live/phonelink_emulator.sh` links the debug app in a headless
emulator to a scratch computer (the PhoneLink harness, its relay on
`https://relay.localhost:<port>` with a scratch CA installed as a user
CA): it pastes the link, approves the phone, waits for the web UI, and
answers Android's microphone prompt, then checks the microphone track is
live. Speech itself is only checked on a real phone.

## Startup

The port is `KOLLAB_WEBUI_PORT`, or 8080 if that is unset.

1. **Attach:** if a Kollab web UI already answers on the port, the window
   opens it. The app checks `/openapi.json` the same way `kollab --web-ui`
   does. The app never stops a runtime it attached to.
2. **Spawn:** otherwise the app runs `kollab --web-ui` with `KOLLAB_WEBUI_NO_BROWSER=1` in
   its own session (setsid), and shows a loading page until the UI answers.
   It uses the newer of:
   - the bundled kollab (`Kollab.app/Contents/Resources/kollab`);
   - an installed one: your login shell's `command -v kollab`, then
     `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`.

   It compares `--version`. A tie goes to the bundled one. The child's output
   goes to Tauri's platform app log directory as `webui.log`
   (`~/Library/Logs/ai.kollabor.kollab/webui.log` on macOS).
3. **Error:** if the UI doesn't answer within 30 s, or the child exits first,
   the app shows the last 40 lines of that output and a Retry button.
4. **Install page:** this shows only if there is no bundled kollab and none
   installed. It suggests `uv tool install kollab` or the `install.sh`
   one-liner.

## Menu bar and quitting

- **Tray menu:** Open Kollab, Launch at Login (off by default; when on, the app
  starts at login, hidden in the menu bar), Check for Updates…, Quit Kollab.
- **Closing the window:** the window hides and the app keeps running in the
  menu bar. While the window is hidden the Dock icon goes away too (an
  accessory app, as Docker Desktop does); Open Kollab or a second launch
  brings both back.
- **Quit:** Quit Kollab in the tray or the menu bar, Cmd+Q, or Dock > Quit. If the app
  started the runtime, it first asks "Quit Kollab and stop the agents it
  started?". On Quit it sends SIGINT to the runtime's process group (CLI,
  engine, UI server), then SIGTERM after 5 s. An attached runtime is never
  stopped.
- **Quit requests from outside:** the app handles the quit Apple Event itself.
  Logout, restart and shutdown quit without asking. Any other quit request
  (Dock > Quit, AppleScript, a utility that quits apps whose last window
  closed) asks like Quit Kollab while the window shows, and is refused while
  the app sits in the menu bar. The log names the sender.
- **Signals:** SIGINT, SIGTERM and SIGHUP to the app quit without asking but
  clean up the same way.
- **Engine reuse:** a spawned `kollab --web-ui` reuses a running engine of the
  same version, just as it does in a terminal. The app didn't start that
  engine, so it is left running.

## Updates

The app updates itself on macOS (`src-tauri/src/updater.rs`). 30 s after launch
and every 6 hours it reads the latest GitHub release; when it is newer than the
app and carries `kollab-desktop-macos-<arch>-<version>.app.zip` plus its
`.sha256`, it asks "Kollab X.Y.Z is available" (Install / Later). Later quiets
the background check for that version until the next launch. Check for
Updates… (tray and app menu) always asks, and says when Kollab is up to date.

Install downloads beside the running app (a hidden `.Kollab-update-<pid>`
folder on the same volume), and checks the zip against the release's
SHA-256, `codesign --verify --deep --strict`, and that the bundle identifier
matches and `CFBundleShortVersionString` is the release's version. Only then
does it stop the runtime it started (as Quit does), swap the bundles in one
`renamex_np(RENAME_SWAP)` (two renames with rollback off APFS), verify the
installed app again (rolling back on failure) and reopen the new app once this
one has exited. Any failure leaves the old app running and names the release
page. A folder Kollab cannot write to (or a translocated app) gets the same
message. The app is signed ad hoc, so the release checksum is the integrity
check, as for the CLI binary's `/upgrade`.

The web UI's update banner asks the running kollab to update; inside Kollab.app
that answers method `app`, and in the app's window the banner then opens
`kollab-desktop://update`, which runs Check for Updates. Linux packages are not
updated in place: the prompt offers the release page instead.

`KOLLAB_DESKTOP_RELEASE_URL` points the updater at another release JSON
(`file://` works), for testing.

## Window behavior

- Links to other origins and `target="_blank"` links open in the default
  browser.
- A second launch focuses the existing window (single instance). Clicking the
  Dock icon reopens the window.
- The window opens at 1280x800 (minimum 390x600) and remembers its size and
  position. It never restores as hidden.
- Voice input: macOS asks for microphone access with "Kollab uses the
  microphone for voice input."
- Test hook: with `KOLLAB_DESKTOP_SELFTEST_CLOSE=1`, the app presses its own
  red close button (`performClose:`) a second after the UI has rendered.

## Files

| Path | What it is |
| --- | --- |
| `src-tauri/src/main.rs` | Desktop binary: calls `kollab_desktop_lib::run()` |
| `src-tauri/src/lib.rs` | Entry point for the desktop and phone apps; the phone app's window |
| `src-tauri/src/desktop.rs` | Desktop app logic: runtime, window, menus, quitting |
| `src-tauri/src/updater.rs` | Self-update from the latest GitHub release |
| `src-tauri/tauri.conf.json` | Identifier `ai.kollabor.kollab`, the macOS `binaries/kollab` sidecar (bundled as `Contents/Resources/kollab`) |
| `src-tauri/tauri.release.json` | Generated release version, formats and opaque sidecar file mappings (Linux packages, Windows `runtime/kollab.exe`) |
| `../scripts/check_desktop_package_windows.py` | Installs the NSIS installer silently and checks launch, render, close and relaunch |
| `src-tauri/Info.plist` | Microphone usage text; local networking allowed |
| `src-tauri/tauri.ios.conf.json` | iOS phone app start page: the link screen in `mobile/` |
| `src-tauri/tauri.android.conf.json` | Android phone app start page: the link screen in `mobile/` |
| `src-tauri/capabilities/phone.json` | Lets only the phone app's link screen use the camera scanner |
| `src-tauri/Info.ios.plist` | Phone app camera and microphone usage texts |
| `mobile/` | Phone app link screen: scan or paste the PhoneLink link (`invite.js` decides which links open) |
| `src-tauri/gen/apple/` | Generated Xcode project for the iOS phone app |
| `src-tauri/gen/android/` | Generated Gradle project for the Android phone app; microphone permissions in `app/src/main/AndroidManifest.xml` |
| `ui/index.html` | Bundled loading, install and error page |
| `app-icon.png` | 1024 px icon source (the Kollab mark) |
| `src-tauri/icons/` | App icons from `cargo tauri icon`, plus `tray.png` (template image) |

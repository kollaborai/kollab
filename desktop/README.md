# Kollab Desktop

A desktop tray app that runs Kollab locally, like Ollama or Docker Desktop.
It ships its own `kollab` and opens the Kollab web UI in a native window. The
app bundles no frontend: the window loads `http://127.0.0.1:<port>/`, served by
`kollab --web-ui`. The frontend itself stays in `packages/kollabor-webui`.
Built with Tauri 2.

macOS uses the existing menu bar app. Linux x86_64 builds produce an AppImage
and a Debian package from the same source; closing the window keeps its tray
icon available. The four existing CLI release binaries are unchanged.

## Build

Needs Rust and the Tauri 2 CLI (`cargo install tauri-cli --version "^2"`, or
use `npx @tauri-apps/cli@2` in place of `cargo tauri`).

First put the kollab release binary where the bundler expects the sidecar.
That folder is gitignored, because the binary is about 47 MB.

```bash
cd desktop
mkdir -p src-tauri/binaries
cp ~/.local/bin/kollab src-tauri/binaries/kollab
# or download the matching kollab-macos-* release asset from GitHub Releases
cargo tauri build
codesign --force --sign - src-tauri/target/release/bundle/macos/Kollab.app
cargo test --manifest-path src-tauri/Cargo.toml
```

The bundle is `src-tauri/target/release/bundle/macos/Kollab.app` (ad-hoc
signed, about 58 MB). To watch its log, run the binary from a terminal:
`.../Kollab.app/Contents/MacOS/kollab-desktop`.

The CLI goes in `Kollab.app/Contents/Resources/kollab`, not next to the app's
executable. It is a pex scie: a launcher with its Python payload appended after
the launcher's code signature, which `codesign` cannot sign. Beside the
executable it counts as nested code, so the bundle cannot be sealed and a
downloaded copy opens as "Kollab is damaged". As bundle data it is sealed with
the rest of the app; `codesign --verify --deep --strict` must pass.

Release builds publish one unsigned `.app.zip` and one `.dmg` for each supported
macOS architecture (`aarch64` and `x86_64`), targeting macOS 11 or newer to
match the bundled CLI, with a sibling `.sha256` file for
each archive. The archives are attached to the GitHub Release alongside the
CLI binaries and are named `kollab-desktop-macos-<arch>-<version>.*`.

macOS and Linux CI use the same verified sidecar staging helper. It checks the
downloaded CLI's SHA-256 and reported version, restores executable permissions,
and generates the ignored `tauri.release.json` overlay. Pull requests and manual
builds derive the release version from `pyproject.toml`; a supplied release tag
must match it. The committed base Tauri config is not rewritten during builds.
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

The reusable `scripts/sign-macos.sh` helper can wrap a CI Tauri build to sign,
notarize and staple with explicitly configured Apple Developer secrets. Its
default is unsigned; it does not download sidecars, archive or publish assets.
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

Windows packages are blocked by the native backend requirements below. The
release manifest contains Linux desktop packages and the four existing CLI
platforms; it contains no Windows executable, installer, or WSL sidecar.

## Native Windows release blocker

The current backend cannot run the desktop app's agent workflow on native
Windows. Producing a Tauri `.exe` alone would leave these failures:

- **CLI producer:** `scripts/build_binary.py:platform_name()` accepts only
  Darwin and Linux. A Windows/AMD64 platform probe exits with
  `no kollab binary for Windows AMD64`. A native producer must package Python,
  workspace packages, plugin/bundle data, and the built Web UI from the same
  commit. It must also preserve interpreter re-execution: the `--web-ui`
  handler in `kollabor/cli.py` starts the engine with `sys.executable -m
  kollabor_engine` and the UI with `sys.executable -c ...`. Freezing only the
  CLI entry point does not establish that contract.
- **Agent startup:** `DaemonPool.spawn()` in
  `packages/kollabor-engine/src/kollabor_engine/daemon_pool.py` launches
  `kollab --detached --as <identity>`. The ordinary detached branch in
  `kollabor/cli.py:cli_main()` calls `os.fork()` and `os.setsid()` without a
  Windows path. Removing `os.fork` in a controlled local probe produced
  `AttributeError` at that call, before an agent could start. Python documents
  [`os.fork` as POSIX-only](https://docs.python.org/3.12/library/os.html#os.fork).
- **Agent IPC:** `DaemonHandle.connect()` uses
  `kollabor_rpc.open_unix_connection_with_large_buffer()`, whose transport
  calls `loop.create_unix_connection()`. The other end is
  `plugins/hub/messenger.py:AgentSocketServer.start()` using
  `asyncio.start_unix_server()`. Python's Windows event loops
  [do not implement these Unix connection/server methods](https://docs.python.org/3.12/library/asyncio-platforms.html#windows).
  Switching event-loop policy does not supply them. Both ends need a native
  transport with the current attach/RPC access controls and framing preserved.
- **Hub locking:** importing `plugins.hub.plugin` imports
  `plugins/hub/coordinator.py`, which unconditionally imports `fcntl`.
  `messaging_bridge.py` and `task_ledger.py` also require it. Denying only
  `fcntl` in a fresh local import probe reproduced `ModuleNotFoundError` in
  `coordinator.py`. Python documents
  [`fcntl` as Unix-only](https://docs.python.org/3.12/library/fcntl.html).
  Native file locking must preserve election, poll ownership, and ledger
  serialization; skipping the Hub plugin would not produce a working app.

The local probes test individual missing-API conditions on macOS, not execution
on a Windows runner. The TUI's `terminal_state.py` already guards its Windows
console imports; its Unix `termios` branch is not evidence of a Windows startup
failure. The blockers above remain even with those console guards.

After the backend port, the desktop Rust owner still needs native Windows
implementations for `spawn_webui`, child/tree ownership and graceful shutdown,
and `quit_on_signals`. They currently depend on Unix `pre_exec`/`setsid`,
`waitid(WNOWAIT)`, `killpg`, and `tokio::signal::unix`. The port must retain
ownership of the whole spawned runtime and leave an attached runtime running.
It must also resolve the real bundled `.exe`, use Windows CLI discovery, and
make the bundled status-page URL/navigation policy agree with the Windows
WebView origin. These changes have not been replaced by no-op cfg branches.

Windows assets may enter the release manifest only after a native Windows x64
CI job builds the CLI from this commit and proves all of the following:

1. Outside the checkout and without a system Python, the CLI reports the
   release version, imports the installed packages/Hub plugin, verifies HTTPS,
   and runs its embedded interpreter's `-m` and `-c` child paths.
2. `--web-ui` starts the engine/UI; creating a real agent session completes a
   provider turn and tool call through native daemon startup and RPC.
3. Native desktop tests and the installed unsigned package prove load/error/
   Retry, render, close/reopen, second launch, owned-runtime shutdown, and
   attached-runtime preservation.
4. The selected `.exe`/`.msi` or archive and basename `.sha256` files are
   downloaded from that job, flattened with the other release artifacts, and
   verified against an explicitly updated exact manifest/count.

No native Windows package, Windows runner smoke, or Windows model turn has
been validated by this Linux packaging change. A compiler-only result or an
HTTP fixture would not satisfy these gates.

## Startup

The port is `KOLLAB_WEBUI_PORT`, or 8080 if that is unset.

1. **Attach:** if a Kollab web UI already answers on the port, the window
   opens it. The app checks `/openapi.json` the same way `kollab --web-ui`
   does. The app never stops a runtime it attached to.
2. **Spawn:** otherwise the app runs `kollab --web-ui` with `BROWSER=true` in
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
  starts at login, hidden in the menu bar), Quit Kollab.
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
| `src-tauri/src/main.rs` | All app logic |
| `src-tauri/tauri.conf.json` | Identifier `ai.kollabor.kollab`, the macOS `binaries/kollab` sidecar (bundled as `Contents/Resources/kollab`) |
| `src-tauri/tauri.release.json` | Generated release version, formats and opaque sidecar file mappings |
| `src-tauri/Info.plist` | Microphone usage text; local networking allowed |
| `ui/index.html` | Bundled loading, install and error page |
| `app-icon.png` | 1024 px icon source (the Kollab mark) |
| `src-tauri/icons/` | App icons from `cargo tauri icon`, plus `tray.png` (template image) |

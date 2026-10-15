# Kollab Desktop

A macOS menu bar app that runs Kollab locally, like Ollama or Docker Desktop.
It ships its own `kollab` and opens the Kollab web UI in a native window. The
app bundles no frontend: the window loads `http://127.0.0.1:<port>/`, served by
`kollab --web-ui`. The frontend itself stays in `packages/kollabor-webui`.
Built with Tauri 2.

## Build

Needs Rust and the Tauri 2 CLI (`cargo install tauri-cli --version "^2"`, or
use `npx @tauri-apps/cli@2` in place of `cargo tauri`).

First put the kollab release binary where the bundler expects the sidecar.
That folder is gitignored, because the binary is about 47 MB.

```bash
cd desktop
mkdir -p src-tauri/binaries
cp ~/.local/bin/kollab src-tauri/binaries/kollab-aarch64-apple-darwin
# Intel Macs use kollab-x86_64-apple-darwin instead.
# or download the matching kollab-macos-* release asset from GitHub Releases
cargo tauri build
cargo test --manifest-path src-tauri/Cargo.toml
```

The bundle is `src-tauri/target/release/bundle/macos/Kollab.app` (unsigned,
about 58 MB). To watch its log, run the binary from a terminal:
`.../Kollab.app/Contents/MacOS/kollab-desktop`.

Release builds publish one unsigned `.app.zip` and one `.dmg` for each supported
macOS architecture (`aarch64` and `x86_64`), targeting macOS 11 or newer to
match the bundled CLI, with a sibling `.sha256` file for
each archive. The archives are attached to the GitHub Release alongside the
CLI binaries and are named `kollab-desktop-macos-<arch>-<version>.*`.

Because the app is unsigned, macOS Gatekeeper may block the first launch. Open
the downloaded archive, then Control-click `Kollab.app`, choose **Open**, and
confirm **Open** in the dialog. If macOS still refuses it, run
`xattr -dr com.apple.quarantine /path/to/Kollab.app` once and open it again.

## Signing and notarization

The reusable `scripts/sign-macos.sh` helper can wrap a CI Tauri build to sign,
notarize and staple with explicitly configured Apple Developer secrets. Its
default is unsigned; it does not download sidecars, archive or publish assets.
See [macOS signing and notarization](docs/apple-signing.md) for operator setup,
cleanup guarantees, and validation limits.

## Startup

The port is `KOLLAB_WEBUI_PORT`, or 8080 if that is unset.

1. **Attach:** if a Kollab web UI already answers on the port, the window
   opens it. The app checks `/openapi.json` the same way `kollab --web-ui`
   does. The app never stops a runtime it attached to.
2. **Spawn:** otherwise the app runs `kollab --web-ui` with `BROWSER=true` in
   its own session (setsid), and shows a loading page until the UI answers.
   It uses the newer of:
   - the bundled kollab (`Kollab.app/Contents/MacOS/kollab`);
   - an installed one: your login shell's `command -v kollab`, then
     `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`.

   It compares `--version`. A tie goes to the bundled one. The child's output
   goes to `~/Library/Logs/ai.kollabor.kollab/webui.log`.
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
| `src-tauri/tauri.conf.json` | Identifier `ai.kollabor.kollab`, the `binaries/kollab` sidecar |
| `src-tauri/Info.plist` | Microphone usage text; local networking allowed |
| `ui/index.html` | Bundled loading, install and error page |
| `app-icon.png` | 1024 px icon source (the Kollab mark) |
| `src-tauri/icons/` | App icons from `cargo tauri icon`, plus `tray.png` (template image) |

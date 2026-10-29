# Windows desktop (issue #268)

Status: packaging path built, runtime port open. Nothing here has run on a
Windows machine yet; the Windows CI job (`desktop-windows` in
`.github/workflows/binaries.yml`) is the first place any of it will.

## What blocks Windows

Found by reading the code at `release/0.18.0`, not by running it on Windows: an
AST scan of the 566 Python files under `kollabor/`, `plugins/` and
`packages/*/src` for Unix-only stdlib modules and calls; a walk of each smoke
import's module-level import closure; the 72 runtime packages of `uv.lock`
checked for Windows wheels; and `pex==2.103.4`'s scie code. Line numbers are at
that commit; the function names are the stable handle.

### Runtime binary (the scie): feasible

| Question | Finding |
| --- | --- |
| Can pex build a Windows scie? | Yes. `pex --scie-platform` lists `windows-x86_64`; with `--scie-name-style platform-file-suffix` the file is `kollab-windows-x86_64.exe` (`pex/scie/science.py`, `qualified_binary_name`), and `--scie-hash sha256` writes `<file>.sha256` beside it. |
| Did our script allow it? | No. `scripts/build_binary.py:platform_name()` accepted only Darwin and Linux (`no kollab binary for Windows AMD64`). Fixed here, with `binary_filename()` for the `.exe`. |
| Do the dependencies resolve? | Yes. All 72 runtime packages in `uv.lock` ship a `win_amd64` or `none-any` wheel; none is sdist-only. `colorama`, `pywin32-ctypes` and `pywin32` arrive by `sys_platform == 'win32'` markers. |
| Does the scie launch? | Only when pex 2.98.2 builds it. pex 2.98.3 and later carry scie-jump 1.13.0, whose first launch on Windows fails reading back its `configure` env file (`invalid type: string "C:\\...", expected a borrowed string`, Binaries run 40969272150). `scripts/build_binary.py` and `scripts/desktop_release.py` pin 2.98.2 on Windows only; elsewhere they keep 2.103.4, since 2.98.3 made `--venv` scies survive a deleted `PEX_ROOT` cache (pex#3226). |
| Does the smoke import? | Not yet. On the runner (Binaries run 40971899270) `kollab --version` stops in `kollabor_cli_main.cli_main`, which imports `plugins/hub/halt_runtime.py` before anything else, and that module imports `fcntl` when it loads. The scan below missed it because that import sits inside `cli_main`. Otherwise: no Unix-only module is reachable by module-level imports from `kollabor_cli_main`, `kollabor.application`, `kollabor_engine`, `kollabor_webui`, `kollabor_voice` or `plugins`, the imports `build_binary.smoke` checks. The hub plugin is not among them (below). |
| `--web-ui` start-up | `kollabor/cli.py` (`--web-ui` handler, about lines 2312-2330) starts the engine as `sys.executable -m kollabor_engine serve` and the UI as `sys.executable -c "from kollabor_webui import main; main()"`. In a scie `sys.executable` is the pex venv's interpreter, so the re-exec contract holds. `kollabor/binary.py:running_binary()` (`SCIE` plus `PEX-INFO` in `sys.prefix`) is platform neutral; `app_launcher()` is only the macOS unpacked-runtime marker. No change needed. |
| Desktop browser tab | `kollabor_webui.main()` (`packages/kollabor-webui/src/kollabor_webui/__init__.py`, `open_browser_delayed`) always calls `webbrowser.open`. The desktop set `BROWSER=true` to silence it; Windows has no `true`, so Python fell through to the default browser. Done: the desktop sets `KOLLAB_WEBUI_NO_BROWSER=1` (port item 7). |

### Agent runtime: blocked, needs a real port

The web UI starts an agent by running `kollab --detached` and talking to it over
a Unix socket (`DaemonPool.spawn`, `packages/kollabor-engine/.../daemon_pool.py`
about lines 550-640). Every step below fails or does not exist on native
Windows.

| # | Blocker | Where | Why it fails |
| --- | --- | --- | --- |
| 1 | Detached start forks | `kollabor/cli.py:cli_main` (`--detached` branch, `os.fork` about 2604, `os.setsid` 2612); `kollabor/daemon.py:fork_daemon` (58, 71, 82), `stop_daemon` (192-198, `os.WNOHANG`, `signal.SIGKILL`), `_wait_for_ready` (246). `_should_use_daemon()` (2408) sends every interactive launch down this path. | [`os.fork` and `os.setsid` are POSIX-only](https://docs.python.org/3.14/library/os.html#os.fork): `AttributeError` before the agent exists. `Popen(start_new_session=True)` in `DaemonPool.spawn` is ignored on Windows. |
| 2 | Local IPC is AF_UNIX | Servers: `plugins/hub/messenger.py:AgentSocketServer.start` (450, `asyncio.start_unix_server`), `plugins/hub/network_attach.py` (210), `packages/kollabor-voice/.../service.py:VoiceService.start` (121), `plugins/deep_thought/socket_server.py` (70). Clients: `packages/kollabor-rpc/.../transport.py:open_unix_connection_with_large_buffer` (41, `create_unix_connection`), `plugins/hub/messenger.py:_open` (2372), `kollabor/application.py:_initialize_attach_proxy` (2150), `kollabor/attach_client.py:AttachClient.run` (105), `kollabor/mcp_hub.py` (39, 60), `plugins/hub/presence.py:_socket_responds_async` (352), `packages/kollabor-engine/.../hub_bridge.py:_query_socket_path` (307), `plugins/hub/relay_owner.py:local_relay_rpc` (363), voice `client.py` (29). | [Windows event loops do not implement `create_unix_connection` or `create_unix_server`](https://docs.python.org/3.14/library/asyncio-platforms.html#windows); `AF_UNIX` sockets are a Unix family. `presence.py:19` and `local_directory.py:173` hard-code `/tmp/kollabor-hub`, `hub_bridge.py:242` globs it. |
| 3 | `fcntl` imported at module level | Hub: `plugins/hub/coordinator.py:3`, `messaging_bridge.py:29`, `task_ledger.py:22` (all reached from `plugins/hub/plugin.py` by plain imports), `dns/discovery_publish.py`, `dns/discovery_store.py`, `dns/service_locator.py`, `dns/storage.py`, `relay_owner.py`, `relay_runtime.py`, `relay_selfhost.py`. Voice: `packages/kollabor-voice/.../bootstrap.py:6`, `service.py:11`. Call time: `kollabor_voice/classifiers.py:101`. | [`fcntl` is Unix-only](https://docs.python.org/3.14/library/fcntl.html): the hub plugin fails to import. Election (`coordinator.py`), poll ownership (`messaging_bridge.py`) and ledger serialisation (`task_ledger.py`) all rest on `flock`. Modules that already guard the import (`token_storage.py`, `provisioned_state.py`, `sidebar_order.py`, `dns/private_directory.py`, `enrollment_*.py`) run without their lock on Windows, a silent weakening to review. |
| 4 | `os.getuid()` | 40 calls in 15 files, among them `plugins/hub/messenger.py` (3), `local_directory.py` (3), `relay_runtime.py` (6), `relay_owner.py` (4), `relay_conversations.py` (4), `secure_session.py`, `device_record.py`, `enrollment_recovery.py`, `enrollment_delegations.py`, `presence.py`, `kollabor_config/provisioned_state.py`, `kollabor_ai/conversation_logger.py`. | Missing on Windows: `AttributeError` at call time, mostly in the socket owner checks of blocker 2. |
| 5 | Signals and process groups | `os.killpg` / `os.getpgid` / `signal.SIGKILL` in `kollabor_agent/process_manager.py:SubprocessStrategy.kill` (365-376), `shell_executor.py:_kill_unix_process_group` (375-383), `plugins/terminal_plugin.py:_kill_process` (583-594), `plugins/hub/plugin.py:_hard_kill_agent` (12804), `DaemonHandle.close` (`daemon_pool.py:446`), `plugins/hub/presence.py:pid_alive` (110, `os.WNOHANG`). `loop.add_signal_handler` in `kollabor_voice/service.py:serve` (942), `plugins/hub/relay_runtime.py` (810, 908), `relay_selfhost.py` (470). | No process groups or `SIGKILL` on Windows, and `ProactorEventLoop` has no `add_signal_handler`. |
| 6 | POSIX shell for agent tools | `kollabor_agent/shell_executor.py` (284, `SHELL` or `/bin/sh`), `shell_utils.py:55` (`/bin/bash`), `kollabor_ai/prompt_renderer.py` (944, `["/bin/sh", "-c"]`), `kollabor/llm/acp_bridge.py` (121, 220), `plugins/terminal_plugin.py` (280-290, 773; tmux). | A product decision first: Git Bash, PowerShell or WSL for agent tools. tmux has no Windows build. |
| 7 | Desktop browser tab | See the table above. | Done: `KOLLAB_WEBUI_NO_BROWSER=1` skips the tab on every platform. |

Not blockers for the desktop: the TUI's `termios`/`tty` imports are guarded in
`terminal_state.py`, `inline_editor_service.py`, `fullscreen/renderer.py` and
`commands/mcp_command.py`. `kollabor/attach_client.py` imports them unguarded,
but no module-level import chain from the smoke imports reaches it. The
self-host relay (`plugins/hub/relay_service.py`: `grp`, `resource`, `chown`) is a
Linux and macOS server and stays out of scope.

### Desktop shell (`desktop/src-tauri/src/main.rs`): done here, unverified

Before this change the file did not compile for Windows (`pre_exec`/`setsid`,
`libc::waitid`, `killpg`, `tokio::signal::unix`; `libc` is a Unix-only
dependency). Now:

| Piece | Windows behaviour |
| --- | --- |
| `bundled_kollab` / `windows_bundled_kollab` | `runtime\kollab.exe` beside `kollab-desktop.exe`, where NSIS puts the staged resource. |
| `installed_kollab` | `where.exe kollab.exe` (a GUI app gets the user's PATH from Explorer), then `~\.local\bin\kollab.exe`. |
| `spawn_webui` | `CREATE_NO_WINDOW`; working directory `%USERPROFILE%`. |
| `child_exited`, `stop_child` | `Child::try_wait` (the open handle keeps the id from naming another process); `taskkill /PID n /T /F`, then wait up to 5 s. Forceful, unlike SIGINT then SIGTERM: a GUI parent has no console for Ctrl+C. |
| `quit_on_signals` | `tokio::signal::ctrl_c`. |
| `STATUS_PAGE`, `allowed_in_window` | WebView2 serves the app's files from `http://tauri.localhost/`, not `tauri://localhost/`. |
| `windows_subsystem = "windows"` | A release build opens no console window. |
| Tests | The `kernel_*` process-group tests, the `/proc` parser test and `kollab_version_reads_the_last_word` are `cfg(unix)`; the Windows lookup test is portable and runs on every host. |

## What ships

- **Installer**: NSIS (`Kollab_X.Y.Z_x64-setup.exe`), per-user (`installMode: currentUser`, no administrator prompt), not MSI: one file, no WiX download, no elevation. Released as `kollab-desktop-windows-x86_64-X.Y.Z-setup.exe` plus `.sha256`. The WebView2 bootstrapper is Tauri's default.
- **Runtime**: the verified scie stays opaque, as on Linux. `desktop_release.py stage --platform windows-x86_64` copies it to `binaries/kollab-x86_64-pc-windows-msvc.exe` and maps it to `runtime/kollab.exe` in the generated `tauri.release.json`. Nothing on Windows rewrites or signs it. It is not a release asset: `install.sh` and `/upgrade` know no Windows binary.
- **Unsigned**: SmartScreen says "Windows protected your PC"; More info, then Run anyway. Documented in `desktop/README.md`. Authenticode signing follows later, as the Apple helper did in #197.
- **Triggers**: `binaries.yml` runs only from `publish.yml` on a `v*` tag or by hand (`workflow_dispatch`), never on a pull request. `desktop-windows` adds no PR check.

### The gate: no installer for a runtime that cannot run agents

`desktop-windows` builds the scie, then runs `python scripts/build_binary.py
--agent-smoke dist/bin/kollab-windows-x86_64.exe`: with a throwaway home and
folder, a detached agent must start, show online in `kollab --hub status`, and
stop. That is native daemon start plus local IPC (blockers 1 to 5), no provider
and no model turn. It fails today, with the runtime's own error, which the job
log shows.

The packaging steps still run after a failed gate, so the Tauri Windows build,
the NSIS installer and the installed-app smoke (`scripts/check_desktop_package_windows.py`:
silent install, launch against an isolated fixture web UI, render, close to the
tray, second launch) are checked meanwhile. The installer then goes only to the
non-release artifact `windows-desktop-unverified`; the job fails with "the
installer is not released", and the release artifact `kollab-desktop-windows-x86_64`
is uploaded only when every step, the gate included, passed.

`desktop-windows` runs only on a manual run (`workflow_dispatch`) until the
port lands: a release tag never runs it or waits on it, and `release-manifest`
does not need it. `continue-on-error: true` also keeps a red manual run from
failing the whole workflow. `desktop_release.py verify` treats the installer pair as optional but never
half there (a changed installer or a missing checksum still fails).

### Promotion

After the port, on a manual run (`gh workflow run binaries.yml --ref release/X.Y.Z`)
where `desktop-windows` is green: remove its `if: github.event_name ==
'workflow_dispatch'` and its `continue-on-error`, add it back to
`release-manifest`'s `needs`, and pass `--windows required` to `desktop_release.py verify` in `binaries.yml` and
`publish.yml`. The manifest becomes 22 files and eleven checksum pairs; update
the counts in `docs/release-process.md` and `desktop/README.md`.

## Port work (follow-up, in order)

1. **Detached start without fork.** A Windows branch in `cli_main` and `kollabor/daemon.py`: start the agent as a `subprocess` with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, own readiness through `_wait_for_ready`, and make `stop_daemon` and `DaemonHandle.close` use `TerminateProcess`.
2. **A local IPC transport.** One abstraction under `kollabor_rpc` and `plugins/hub/messenger.py` for the sites in blocker 2: named pipes (`\\.\pipe\kollabor-hub-<hash>-<name>`, `ProactorEventLoop` pipe server and client) or loopback TCP with a per-agent token. Keep framing (`LOCAL_MAX_LINE_BYTES`, `LARGE_BUFFER_LIMIT`) and the owner-only access the Unix sockets get from the filesystem. Replace the `/tmp/kollabor-hub` constants with a per-user directory.
3. **One file-lock helper.** Mostly done. `kollabor_config.file_lock` (`lock`, `try_lock`, `unlock`, `locked_file`) keeps `flock` semantics on both platforms and its tests run on a Windows runner (`.github/workflows/windows-locks.yml`, #414).
   - Every lock in the hub plugin's coordinator, Telegram bridge, task ledger, service locator, private directory and enrollment, and in the engine's handover, MCP settings, sidebar order and PhoneLink, now goes through it.
   - Still on `fcntl`, in two groups:
     - The relay and kill-switch paths: `plugins/hub/relay_owner.py`, `relay_runtime.py`, `relay_selfhost.py`, `halt_runtime.py`, `dns/storage.py`, `dns/discovery_store.py` and `dns/discovery_publish.py`.
       - Several of these ship in the headless relay (`scripts/relay/build_service.py` `FILES`), which must not import a workspace package (`tests/unit/test_relay_build_allowlist.py`). They need a lock the relay can ship.
       - After the swap, rerun the Docker harness in `tests/docker/kill_switch/`.
       - `halt_runtime.py` comes first: `cli_main` imports it at start, so until it moves the Windows binary cannot print its version.
     - `kollabor_voice` (`bootstrap.py`, `service.py`, `classifiers.py`, `speech_settings.py`) and `kollabor_ai/oauth/token_storage.py`. Their packages do not depend on `kollabor-config`.
4. **`os.getuid()`.** One `current_user_id()` helper (Windows: the user SID) for the 40 calls.
5. **Signals and trees.** Job Objects for agent and shell trees in place of `killpg`; `SIGKILL` becomes `TerminateProcess`; replace `add_signal_handler` with `signal.signal` or console control handlers.
6. **Agent shell.** Decide the Windows shell for agent tools (Git Bash, PowerShell, WSL) and wire `shell_executor`, `shell_utils`, `prompt_renderer` and `acp_bridge`; tmux features stay Unix-only.
7. **No browser tab.** Done: `kollabor_webui.main()` opens no tab when `KOLLAB_WEBUI_NO_BROWSER` is set, and the desktop sets it.
8. **Desktop.** Graceful stop (an engine shutdown request, or Job Object `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus a console-free Ctrl+Break) instead of `taskkill /F`; confirm that WebView2 raises `on_navigation` for the `kollab-desktop://` report and update links; a Windows updater (`updater.rs` is macOS only: the prompt offers the release page elsewhere); GUI automation for clean Quit in `check_desktop_package_windows.py`, which today ends the app with `taskkill`.
9. When 1 to 5 land, `--agent-smoke` passes unchanged; then promote, as above.

## Issue #268 acceptance, as of this change

| # | Item | State |
| --- | --- | --- |
| 1 | Document the blockers | Done (this file). |
| 2 | Packaged CLI and engine run on Windows x86_64 | The CLI binary builds and smoke-tests in the job (unverified on a runner). The agent runtime does not run natively: port work 1 to 7. |
| 3 | NSIS/MSI in the tag-triggered workflow, checksums, manifest | Built; released only once the gate passes; manifest pair optional until promotion. |
| 4 | CI smoke: install, launch, render, close and relaunch, clean quit | Install, launch, render, close, relaunch: written, unverified. Clean quit: not done (port work 8). |
| 5 | Unsigned, SmartScreen notes | Done (`desktop/README.md`). |
| 6 | Docs list Windows | Done (`desktop/README.md`, `docs/release-process.md`). |

## Not verified

Nothing in this change ran on Windows. The Windows-gated Rust has not been
compiled (the build host is macOS with no Windows target). The Python scripts
are covered by unit tests with a fake runtime; `agent_smoke` has never run
against a real daemon on any platform.

# Code, Git and Terminal on Windows: design (#987, #268)

Status: design only. Rows marked "built" were checked in the code. Everything else is "not built".
Seed: the "Windows" section of `docs/specs/code-shell-settings.md`. Shell order and the notice text in that seed are
replaced by this spec (see section 8, Q4, and the seed fix list in section 2).

## 1. Goal

A developer on Windows, or a Windows server, gets files, git and a shell in the web UI, served by the daemon on that
machine. The shell runs on ConPTY through the optional `pywinpty` package; files and git need no change except path
checks and one junction rule.

## 2. Unix-only calls and their Windows replacement

`terminal.py` line numbers are from this worktree. "Built" means the Windows branch exists in the code. Nothing here is
built except the `proc_ctl` helper it will call.

| Unix-only call | where | Windows replacement | status |
|---|---|---|---|
| `os.openpty()` | terminal.py:401 | ConPTY created by the pywinpty spawn, size given at spawn | not built |
| `fcntl.ioctl(TIOCSWINSZ)` in `_set_winsize` | terminal.py:383-388 (used :403, :498) | `setwinsize(rows, cols)` on the ConPTY. Keep (rows, cols) order: pywinpty and the Unix struct differ | not built |
| `os.login_tty(0)` launcher and `os.execv` | terminal.py:371-380, :406 | argv passed straight to the spawn. No launcher | not built |
| `subprocess.Popen` with PTY fds as stdio | terminal.py:405-413 | pywinpty spawn with argv, cwd, env, size | not built |
| `os.read(fd)` | terminal.py:139 | reader thread calls `read()`, text goes to the loop with `call_soon_threadsafe` | not built |
| `os.write(fd)` | terminal.py:122 | writer thread calls `write()` | not built |
| `loop.add_reader` / `add_writer` and the `remove_` pair | terminal.py:84, :129, :133, :145, :196, :210-211 | none. The threads replace them. The Windows default loop is the Proactor loop, which lacks these: the first shell fails at :84 | not built |
| `os.waitpid(pid, os.WNOHANG)`; `os.WNOHANG` does not exist on Windows | terminal.py:181 | `isalive()` each 50 ms; exit code from the exit status | not built |
| `signal.SIGKILL`; `signal.SIGHUP` (not on Windows) | terminal.py:194, :240 | `proc_ctl.kill_tree(pid, proc_ctl.HARD)` (proc_ctl.py:19, :356) | not built (helper built) |
| `os.killpg` in `_signal_group` | terminal.py:391-393 | `proc_ctl.kill_tree`: walks the Toolhelp tree, so the shell's children end too | not built (helper built) |
| `os.set_inheritable`, `os.set_blocking` on the master fd | terminal.py:419-420 | not needed: no fd | not built |
| `os.close` of master and slave fds | terminal.py:212, :415, :418 | pywinpty close | not built |
| `/bin/zsh`, `/bin/sh` fallbacks in `_login_shell` | terminal.py:324, :327 | Windows resolver, section 4.2 | not built. Present bug: on Windows this returns `/bin/sh`, so `env_report` reports the shell as `sh` (terminal.py:366) |
| `hasattr(os, "openpty")` gate | terminal.py:442-448 | pywinpty import check plus Windows build check, section 5 | not built. The gate exists; its message is an `error` with other text |
| `_Session` typed on `subprocess.Popen`, uses `returncode` | terminal.py:59, :68, :183, :188 | a small backend object with pid, `isalive()`, exit status | not built |

Portable, no change: `import signal` (:18), `os.access(..., X_OK)` (:325), `os.waitstatus_to_exitcode` (:187),
`_shell_env`, `_env_verdict`, `env_report` names, the ring, idle reap, link and sweep code, the 8-shell count.

Files and git rows:

| Unix-only call | where | Windows replacement | status |
|---|---|---|---|
| `os.O_NOFOLLOW` is absent on Windows; code uses `getattr(os, "O_NOFOLLOW", 0)` | files.py:171 | no open-time refusal on Windows. The junction rule in `_kind` and here does the job (section 3) | not built |
| `DirEntry.is_dir(follow_symlinks=False)` returns True for a junction | files.py:107-114 | add `os.path.isjunction(entry.path)` (Python 3.12+; the floor is 3.14) | not built |
| `startswith(base)` containment, 10 sites | files.py:261, :305, :333, :366, :394, :404, :432; git.py:196, :212, :408 | one helper that applies `os.path.normcase` to both sides | not built |
| git path `HEAD:./{rel}` needs "/"; `_rel_path` passes "\" through | git.py:132-139, :199 | convert "\" to "/" in `_rel_path` | not built. Check what the web UI sends first |
| `os.replace` over a file another program holds open | files.py:201 | PermissionError on Windows. The temp file is removed (:202-205), so the result is an error, not a torn file | no change. Test in section 7 |
| `os.chmod(temp, mode)` | files.py:200 | Windows sets or clears only the read-only bit. Harmless | no change |

Found while reading (fix in the lane that owns the file, not in this spec):
- `env_report` reports the shell as `sh` on Windows (terminal.py:366, :327).
- `MAX_SESSIONS = 8` (terminal.py:37) is never read. The live cap is `settings.max_shells` (terminal.py:449-455).
- `docs/specs/code-shell-settings.md:66` says `powershell.exe` then `cmd.exe`. This spec replaces that order (Q4).
- `docs/specs/windows-desktop.md` has no row for the Code, Git or Terminal views.

## 3. Files and git: what works with no change

- Files: tree, read, search, write, create, make folder, rename, delete. The code uses `os.scandir`, `os.path`,
  `os.open`, `os.fsync` and `os.replace`, all of which exist on Windows. The containment check is the security
  boundary. It still holds after the normcase change below.
- Git: every call is `subprocess.run(["git", "-C", root, ...])` with no shell, so it works when `git.exe` is on PATH.
  A timeout kills only `git.exe`, not its helpers (for example a credential helper). Minor; note only.

Why `os.path.normcase`: Windows file names are case-insensitive and accept both "\" and "/". Both sides of each check
come from `os.path.realpath`, so they usually match. normcase makes the check hold when they differ in case or in the
separator. On POSIX it changes nothing. Keep the trailing-separator trick (`os.path.join(base, "")`): it stops
`C:\Repo` from matching `C:\Repo2`. One helper, used at the 10 sites in section 2, not ten copies.

Junctions and symlinks: `DirEntry.is_symlink()` is False for a junction. So `_kind` (files.py:107-114) returns "dir",
and `_tree` (:130-146) and `_search_files` (:149-165) walk into it. `_search_text` (:168-171) opens through it, because
`O_NOFOLLOW` does not exist on Windows. Result today, by reading the code (not run on Windows): search can read a file behind a
junction that points outside the folder. Fix: `_kind` returns "" for `os.path.isjunction(entry.path)`, and the same check runs before `_search_text`
opens a file. Creating a junction needs no admin rights, so this is the realistic Windows case; a git checkout does not
create one.

## 4. Terminal design: ConPTY through `pywinpty`

### 4.1 Where the code goes

- One new module, `kollabor/code_service/conpty.py`. It is the only file that imports `winpty`, and it does so inside
  the spawn function, never at import time. The module imports on Linux and macOS without the package.
- `terminal.py` picks the Windows path by reading `sys.platform` inside the function, not at import time, so tests can
  patch it.
- The shell object for `_Session` gets the same small surface on both platforms: `pid`, `isalive()`, exit status,
  `read()`, `write()`, `setwinsize()`, `close()`. Unix wraps the fd and the Popen; Windows wraps the pywinpty process.
  `_Session` keeps one code path for the ring, the reap, the links and the cap.

### 4.2 Shell choice when `terminal.shell` is empty

Order (first that exists wins):
1. `%ProgramFiles%\PowerShell\7\pwsh.exe`, then `pwsh.exe` found on PATH.
2. `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`.
3. `%COMSPEC%` (normally `cmd.exe`).

Rules:
- Absolute paths only. A bare name is never resolved from the working folder. The daemon's folder is a user's
  repository, which can hold any `pwsh.exe`. PATH entries go through `windows_path_entries` (shell_utils.py:304-317),
  which drops the working folder and anything inside it. Do not call bare `shutil.which("pwsh.exe")`.
- `terminal.shell` set: used when it is an absolute path to an existing file. Otherwise the order above applies, as the
  Unix code falls back silently today (terminal.py:322-327).
- Arguments: PowerShell gets `-NoLogo` only. Do not reuse the agent-command argv (shell_utils.py:286-296), which adds
  `-NoProfile -NonInteractive -ExecutionPolicy Bypass`. The user's profile loads as usual. cmd gets no arguments.
- `terminal.login` is ignored on Windows (today it adds `-l`, terminal.py:406).
- Never `/bin/sh` on Windows.

### 4.3 Environment

`_shell_env` and `_env_verdict` are the same code. By reading the marker list, none of the usual Windows variables
(`SystemRoot`, `COMSPEC`, `PATH`, `PATHEXT`, `USERPROFILE`) contains TOKEN, SECRET, API_KEY or PASSWORD. Verify on a
Windows run. Windows variable names are case-insensitive; `_env_verdict` already uppercases.
`env_report` must call the Windows resolver (section 4.2) so the header says `pwsh.exe`, not `sh`.

### 4.4 Threads, because the Proactor loop has no `add_reader`

- Reader: one daemon thread per shell. It loops on `read()`. Each text chunk goes to the loop with
  `loop.call_soon_threadsafe(self._publish, text)`. On EOF or an exception it stops. If the loop is closed, the
  `RuntimeError` from `call_soon_threadsafe` ends it.
- Writer: one thread per shell, fed by a queue of whole input messages. It calls `write()`. The 1 MB backlog cap
  (`MAX_PENDING_INPUT_BYTES`, terminal.py:42) counts bytes not yet written. The count changes on the loop thread, and
  the writer reports written bytes back with `call_soon_threadsafe`.
- Exit: `_watch` keeps its 50 ms loop. `isalive()` replaces `waitpid`; the exit status becomes the exit code. `None`
  when unknown, as the `ChildProcessError` branch does today.
- Decoding: pywinpty's `read()` returns text (to verify on the pinned version). On Windows, skip the incremental UTF-8
  decoder.

### 4.5 How each operation maps

| operation | today (Unix) | ConPTY design |
|---|---|---|
| open | openpty, launcher, Popen (terminal.py:396-427) | pywinpty spawn with argv, cwd, env, size. The cap check (:449-455) is unchanged |
| resize | TIOCSWINSZ (:383-388, :498) | `setwinsize(rows, cols)`. `_resize_message` checks "is there a shell", not "is there an fd" (:495) |
| input | `os.write` from the pending buffer (:119-133), 1 MB cap (:113) | writer thread; whole messages in a queue; the cap counts unwritten bytes |
| output and polling | `add_reader` and `os.read` (:135-153) | reader thread; text into `_publish` (:155-163) on the loop. The ring and the long poll (:94-104, :277-304) are unchanged |
| exit | `waitpid` in `_watch` (:173-197) | the same loop with `isalive()` and the exit status |
| close | SIGHUP, then SIGKILL after 1 s (:231-243) | close the ConPTY (the shell gets a close event), then `kill_tree(HARD)` after 1 s if still alive |
| idle reap, links, sweep | asyncio timers (:216-229, :553-562) | unchanged |
| 8 shells | count at :449, setting `terminal.max_shells` | unchanged |
| env | `_shell_env` (:344-353) | unchanged; same function |
| `start_command` | written at open (:465-466) | unchanged |
| `terminal.login` | adds `-l` (:406) | ignored on Windows |

### 4.6 Close and tree kill

- `destroy()` closes the ConPTY, then waits `KILL_AFTER_SECONDS` (1 s, same as Unix). If the shell is still alive, it
  calls `proc_ctl.kill_tree(pid, proc_ctl.HARD, created=...)`. The `created` value is `proc_ctl.process_created(pid)`
  taken at spawn; it stops a reused pid from being killed. `kill_tree` ends the shell and its children (proc_ctl.py:356).
- `shutdown_terminals` (terminal.py:599-613) keeps its shape: destroy each shell, await each task.
- Closing the ConPTY must end the blocked `read()` in the reader thread. Test it (section 7, W3).

## 5. Without `pywinpty`: the notice

Current state: not built. The code answers `{"type": "error", "message": "This computer cannot run a web terminal yet:
it needs a Unix PTY. Setting: kollabor.code.terminal.enabled turns the view off."}` (terminal.py:442-448). A grep for
`terminal_unsupported` finds it only in the seed (`docs/specs/code-shell-settings.md:64`), not in code.

Proposed form: keep `type: "error"` and add `"code": "terminal_unsupported"`. The web UI parser has no case for any
other type and returns null for it (`packages/kollabor-webui/frontend/src/components/terminal/use-terminal-ws-connection.ts:93-109`).
A new type would be dropped silently and the view would stay blank. The `error` path already shows `message`
(use-terminal-ws-connection.ts:208-217). Q2 covers this choice.

Exact message text, one sentence:

> A web terminal on Windows needs Windows 10 version 1809 or later and the pywinpty package: run `pip install "kollab[windows-terminal]"`, or set kollabor.code.terminal.enabled to false to turn the view off.

Trigger: a new shell is opened (not a reattach), on Windows, and either the pywinpty import fails or
`sys.getwindowsversion().build` is below 17763 (Windows 10 1809). The `terminal.enabled` check stays first
(terminal.py:440-441), so the off switch still gives its own message. No stack trace is ever returned; the error is
logged with `logger.exception` only, as the spawn error is today (terminal.py:460-462).

## 6. Packaging

- Declare: `pyproject.toml` `[project.optional-dependencies]` gets `windows-terminal = ["pywinpty>=<pin>; sys_platform == 'win32'"]`.
  The marker keeps Linux and macOS installs unchanged. The pin is chosen when the code lands, after checking that
  release's API names. pywinpty is not declared in `pyproject.toml` and not in `uv.lock` today (grep).
- `uv.lock` changes with the extra; regenerate it with `uv lock` in the same change.
- Single-file binary: `scripts/build_binary.py` builds the wheels and hands them to pex (build_binary.py:401-415). It does
  not select extras. So the Windows binary, which is the desktop installer's runtime (build_binary.py:17-18), would not
  contain pywinpty. Options are in Q1.
- Lazy import: `winpty` is imported only inside `conpty.py`'s spawn path. A missing package is then the notice, not an
  import crash.

## 7. Test plan

### 7.1 Linux and macOS (runs in `ci/tests`)

- Shell order with injected `environ` and `is_file` (the pattern `agent_shell` already uses): pwsh, then powershell,
  then COMSPEC; an absolute `terminal.shell` wins; never `/bin/sh`.
- The notice: patch the pywinpty import to fail. `_open_session` returns the exact text in section 5, the `code`, and
  no "Traceback".
- Fake ConPTY object driving `_Session`: output order, exit code from the fake's exit status, resize argument order
  (rows, cols), the 1 MB input cap, the reader ending on EOF, and the 9th shell refused with the cap message.
- Containment helper: `ntpath.normcase` cases. `C:/Repo` against `c:\repo\a.txt` is accepted. `C:\Repo2\x` against
  base `C:\Repo` is refused (the prefix trap).
- Junction rule: a DirEntry-like fake, with `os.path.isjunction` patched to True, makes `_kind` return "".
- Env: a mixed-case `my_token` is dropped. `env_report` on a Windows-patched environ names `pwsh.exe`, not `sh`.

Limit: patching `sys.platform` does not remove `signal.SIGHUP`, `signal.SIGKILL` or `os.WNOHANG`, which all exist on
Linux. These tests prove branch selection and the logic. They cannot prove that the Windows path avoids Unix-only
attributes. Only a Windows runner can.

### 7.2 Windows runner

Follow `.github/workflows/windows-locks.yml` (`runs-on: windows-latest`, pytest on named files). New file:
`tests/unit/code_service/test_terminal_windows.py`, plus the containment and junction tests in `test_files.py` run on Windows.

- W1: open a shell (pwsh, or powershell if pwsh is absent). Send `Write-Output kollab-ok`. Poll until the text is in the
  output. Send `exit`. Exit code recorded.
- W2: shell runs `ping -t 127.0.0.1`. Close the session. `ping.exe` is gone within 6 s.
- W3: closing the ConPTY ends the reader thread. Pytest exits without hanging.
- W4: `mklink /J` inside a temp folder pointing outside. Tree does not list it. Search does not read a file behind it.
- W5: atomic write while another handle holds the target open. PermissionError, temp file removed, original intact.
- W6: temp repo. Status, stage, commit. Files with CRLF line endings.
- W7: output with non-ASCII text (`é`, an emoji) round-trips intact.
- W8: clean venv without the extra. The notice text is returned.

### 7.3 Manual, on a real Windows 10 (1809+) and a Windows 11 machine

- M1: `pip install "kollab[windows-terminal]"`, start the daemon in a folder, open the Terminal view. PowerShell prompt
  appears. `Get-Location` shows the folder.
- M2: resize the browser window. Lines rewrap. `$Host.UI.RawUI.WindowSize` changes.
- M3: `ping -t 127.0.0.1`, then Ctrl+C in the web terminal. It stops.
- M4: close the Terminal view. `Get-Process ping` shows nothing left over.
- M5: reload the page while `ping -t` runs. Output replays; the shell is still alive.
- M6: open 8 shells. The 9th shows the cap text with `kollabor.code.terminal.max_shells`.
- M7: start the daemon with `MY_TOKEN=1` and `KOLLABOR_TEST=1` set. In a shell, `Get-ChildItem Env:` shows neither.
  Turn on `kollabor.code.terminal.pass_secret_env`: `MY_TOKEN` appears.
- M8: set `terminal.shell` to the absolute path of `cmd.exe`. A cmd prompt appears.
- M9: save a file that Notepad has open. An error shows; the file is intact.
- M10: a junction (`mklink /J`) to another drive. It is not listed, and search finds nothing behind it.
- M11: Git view on a repo with CRLF files: status, diff, commit.
- M12: `pip uninstall pywinpty`. Terminal view shows the notice. Files and Git still work.
- M13: stop the daemon. No `pwsh` or `conhost` process from the terminal is left behind.

## 8. Open questions (max 6)

Q1. Packaging: extra only, or base dependency too?
- Plan above uses the extra `windows-terminal` (section 6). `build_binary.py` ships wheels, not extras, so the Windows
  binary would lack pywinpty.
- Recommended: also add `pywinpty; sys_platform == "win32"` to the base dependencies. Then the Windows binary and every
  Windows pip install have it, and the notice only shows for a broken install. The extra name stays as an alias.

Q2. Notice form: `error` with a `code`, or a new `terminal_unsupported` type?
- Recommended: `error` with `"code": "terminal_unsupported"`. The UI already shows `error` text. A new type needs a
  case in `use-terminal-ws-connection.ts` (lane U) first, or it is dropped silently.

Q3. Windows builds older than 1809: keep the notice, or try the old winpty backend?
- Recommended: keep the notice. ConPTY is the only backend in this design. The notice names 1809.

Q4. Shell order and flags.
- Recommended: `pwsh.exe`, then `powershell.exe`, then `%COMSPEC%`. `-NoLogo` only. The user profile loads. This
  replaces the seed's "powershell.exe then cmd.exe" (`docs/specs/code-shell-settings.md:66`); lane S or T updates that line.

Q5. Git Bash in the web terminal?
- Recommended: no, for v1. Agent commands already resolve Git Bash (shell_utils.py:355-400). The web terminal is a
  person's interactive shell, so PowerShell or cmd. Adding Git Bash later is one more entry in the order.

Q6. Junctions: same rule as symlinks?
- Recommended: yes. A junction is not listed, not searched and not read unless `files.follow_symlinks` is true
  (`docs/specs/code-shell-settings.md:43`), the same rule as a symlink. This needs the `isjunction` check in `_kind`
  and in `_search_text`.

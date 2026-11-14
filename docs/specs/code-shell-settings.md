# Code, Git and Terminal settings: every limit has a switch (#987)

Rule: if the code editor, Git or Terminal views refuse, hide, drop or cap something, a setting turns it off or
changes it. A developer who runs agents on other computers must be able to work around any guard. Defaults stay safe;
nothing is locked.

## Where a setting lives (resolution order, first hit wins)

1. The agent's own `agent.json`, key `"code"` (an object with the keys below, without the `kollabor.code.` prefix).
2. The project's `.kollab/config.json`.
3. The global `~/.kollab/config.json`.
4. The default in the table.

Project and global config are merged before they are read (project keys win), so tiers 2 and 3 act as one. A value of the wrong type is ignored and the next tier down in this list is used; because the merge already picked the project value, a wrong-typed project value falls to the default, not to the global value. Known limitation: a typo in a switch such as `read_only` therefore falls to its default.

The daemon of the session resolves these (`kollabor/code_service/settings.py`, `resolve(config, agent_code)`), so a
session on another computer follows that computer's config and agent, not this one's. The web UI shows and edits them
in Settings (System Configuration, section "Code And Terminal" for global/project) and, per agent, in the agent's
`agent.json`. A per-agent editor in the web UI is new UI: it needs a mockup first (see Not built yet).

A setting is read when a request or a shell starts. A running shell keeps the environment it started with.

## Settings (all under `kollabor.code.`)

| key | default | what it changes |
|---|---|---|
| `enabled` | true | false: the daemon refuses every code, git and terminal call (404 `code_disabled`). Lets a machine owner close the door |
| `read_only` | false | true: writes, creates, renames, deletes, git writes and terminal input are refused (403 `read_only`). Opt-in only |
| `terminal.enabled` | true | false: no shells |
| `terminal.pass_secret_env` | false | true: secret-looking names reach the shell (containing TOKEN, SECRET, API_KEY, APIKEY, PASSWORD, PASSWD, CREDENTIAL, PRIVATE_KEY, ACCESS_KEY or DATABASE_URL, or a `_`-separated word PAT) |
| `terminal.pass_kollab_env` | false | true: `KOLLAB_*` and `KOLLABOR_*` reach the shell |
| `terminal.env_allow` | [] | exact names that always pass, even if a filter above would drop them |
| `terminal.env_deny` | [] | exact names that never pass |
| `terminal.env_set` | {} | extra `NAME: value` pairs set in the shell |
| `terminal.shell` | "" | shell program; empty = `$SHELL`, else zsh, else sh (Windows: see the Windows section) |
| `terminal.login` | true | start the shell with `-l` |
| `terminal.start_command` | "" | one line typed into a new shell once it is ready |
| `terminal.max_shells` | 8 | most live shells per daemon |
| `terminal.idle_reap_minutes` | 10 | minutes a shell with no viewer lives; 0 = never |
| `terminal.scrollback_kb` | 256 | output kept for a reconnecting viewer |
| `files.max_file_kb` | 2048 | largest file the editor opens or saves |
| `files.show_hidden` | false | list dot-files and the skipped folders (`node_modules`, `dist`, ...) in the tree; `.git` itself is never listed |
| `files.search_max_files` | 20000 | files one search scans |
| `files.extra_roots` | [] | extra absolute folders the editor may reach besides the daemon's folder (`~` is expanded; a relative entry is dropped) |
| `files.follow_symlinks` | false | true: a symlink inside the folder is read and listed even if it points outside |
| `git.allow_outside_workspace` | false | true: commit even when files outside the folder are staged |
| `git.read_only` | false | true: git writes refused, reads work |

Names are looked up exactly; the env filters compare case-insensitively on the name only. Values are never logged.

## What the UI must show

- Terminal header: how many variables were hidden ("Env: 5 Hidden") with the names on hover, from the daemon op
  `terminal.env_report` (names only, never values), plus the shell name and the machine (`device:folder`).
- A refusal says which setting caused it ("Read only is on for this agent. Setting: kollabor.code.read_only").
- A daemon older than this feature (`RpcMethodNotFound` -> 502): a plain notice, "This agent runs older code. Restart it
  to use Code, Git and Terminal."
- The terminal view reconnects by itself and says "Reconnecting" while it does.

## Windows

The daemon can run on Windows (#268). Today the terminal needs `os.openpty`, `termios`, `fcntl` and `os.login_tty`,
which do not exist there. Plan:

1. Importing `kollabor.code_service` never fails on Windows (all Unix-only imports are lazy).
2. Without a PTY, a terminal request answers a plain `terminal_unsupported` message naming the setting that would
   change it, instead of a stack trace.
3. Later: ConPTY through the optional `pywinpty` package, `terminal.shell` defaults to `powershell.exe` then `cmd.exe`,
   and `terminal.login` is ignored. Not built in this change; the spec lane writes the design and the open questions.
4. Files and git work on Windows as is: paths are compared after `os.path.realpath` and `os.path.normcase`.

## Lane map

Shared contract first (me): `settings.py`. Then lanes that touch disjoint files run together.

| lane | files it may touch | needs |
|---|---|---|
| S settings core (me) | `kollabor/code_service/{settings,terminal,__init__}.py`, `kollabor/state/local.py`, `packages/kollabor-config/.../loader.py`, `packages/kollabor-tui/.../config_widgets.py`, `tests/unit/code_service/test_settings.py`, `test_terminal.py`, `tests/unit/test_code_rpc.py` | nothing |
| F files and git | `kollabor/code_service/{files,git}.py`, `tests/unit/code_service/test_files.py`, `test_git*.py` | S |
| U terminal and notices UI | `packages/kollabor-webui/frontend/src/components/terminal/*`, `src/lib/code-fetch.ts`, `src/components/code/DaemonNotice.tsx`, their tests | S (op names) |
| W Windows design | `docs/specs/code-shell-windows.md` only | S |
| T tmux spec and docs | `tests/tmux/specs/code-shell.json`, `docs/features/*` | F, U |
| R reviewers | read-only | all |

Every lane: at most 250k tokens; grep first, read at most 150 lines at a time, pipe test output through `tail`.

## Not built yet

- A per-agent editor for these settings in the web UI (mockup first; until then edit `agent.json` or Settings).
- Windows ConPTY.
- Multiple terminal tabs with rename, terminal search, font-size shortcuts (the Terminal view already has a sessions
  sidebar).

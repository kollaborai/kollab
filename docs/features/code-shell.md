# Code, Git and Terminal views

The web UI's session panel has three views beside Chat and Trajectory. They show for a chat that has a folder.

- **Code Editor**: file tree, Monaco editor with tabs and split panes, quick open, search.
- **Git**: status, stage, commit, branches, stash, log, diff. It sits beside the same editor panes.
- **Terminal**: a real shell in the session's folder.

The daemon of the session serves all three. A session opened from another computer shows that computer's files, its git
repository and its shell, the same ones its user gets. Design and wire contract: `docs/specs/floating-code-shell.md`.

## Settings

The views have on and off switches and limits. Defaults are safe and nothing is locked. Full rules:
`docs/specs/code-shell-settings.md`.

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

### Where to set them

- **Web UI**: Settings → System Configuration, section "Code And Terminal". It saves to the project or the global config file.
- **Terminal**: `/config`, then Tab until the "Code And Terminal" section shows.
- **Config files**: `.kollab/config.json` (this project) or `~/.kollab/config.json` (all projects), under `kollabor.code`:

  ```json
  {
    "kollabor": {
      "code": {
        "read_only": true
      }
    }
  }
  ```

- **Per agent**: the `"code"` object in the active agent's `agent.json`, next to its `system_prompt.md`, with the keys
  above without the `kollabor.code.` prefix. Nested or dotted keys both work:

  ```json
  {
    "code": {
      "read_only": true,
      "terminal": { "enabled": false, "max_shells": 2 }
    }
  }
  ```

The order is first hit wins: the agent's `agent.json`, then the project config, then the global config, then the default.
A setting is read when a request or a shell starts. A shell that is already running keeps the environment it started with.
The daemon resolves these, so a session on another computer follows that computer's config and agent.

## What a refusal looks like

A refusal names the setting that caused it. Examples:

- `Read only is on for this agent. Setting: kollabor.code.read_only` (403 `read_only`)
- `Terminals are turned off for this agent. Setting: kollabor.code.terminal.enabled`

## The terminal's hidden variables

A new shell gets only the environment the filters allow. The Terminal view's header shows "Env: N Hidden". Hover it to
see the names, one line per reason: "Secret names", "Kollab names" or "Denied by setting". Only names are shown, never
values. The same list comes from `GET /sessions/{id}/code/terminal/env`.

## An older daemon

A daemon that started before this change has no code call. The engine answers 502 `daemon_outdated` with the text
"This agent runs older code. Restart it to use Code, Git and Terminal." The Terminal view shows that text as a notice.
Restart the agent to fix it. The Code Editor and Git views do not show the notice yet.

## Behaviors worth knowing

- Delete and rename act on a symlink itself, never on the file or folder it points to.
- A file that is not UTF-8 opens as Latin-1 and saves back byte for byte. If the new text holds a character Latin-1
  cannot hold, the save is refused (`encoding_unsafe`) instead of rewriting every other byte; save under another name.
- Guarded commits from a subfolder are unavailable because another Git process can change the index after the scope check. Open the repository root to commit, or explicitly enable `git.allow_outside_workspace`.
- Stash deletion from the UI is unavailable: Git selects a positional entry, which can change during a concurrent stash operation. List, create, and apply remain available.
- The git stash button stashes this folder only. Applying a stash that includes paths outside the folder is refused unless `git.allow_outside_workspace` is enabled.
- A save checks for a file changed or deleted since it was opened and offers an explicit overwrite when it detects a conflict. An external writer can still race the final replacement. Unsaved tabs kept after switching folders or devices can only be saved from their original workspace.
- A commit that would carry staged files from outside this folder is refused unless `git.allow_outside_workspace` is on.
- `terminal.start_command` does not run when `read_only` is on.

## Not built yet

- A per-agent editor for these settings in the web UI (mockup first; until then edit `agent.json` or Settings).
- Windows ConPTY.
- Multiple terminal tabs with rename, terminal search, font-size shortcuts (the Terminal view already has a sessions
  sidebar).

## Related

- `docs/specs/floating-code-shell.md`: the views, the daemon, the wire contract.
- `docs/specs/code-shell-settings.md`: the settings, their resolution and the rules.
- `docs/specs/code-shell-windows.md`: the Windows design.
- `tests/tmux/specs/code-shell.json`: the `/config` check for the Code And Terminal section.

## Worktrees

The Code and Git views list the agent computer's linked Git worktrees in the folder selector, which can also browse any readable folder on that computer. A single delivered assignment that names an available linked worktree opens it initially. When no unique assignment is available, the daemon's folder opens first. Selecting a folder changes the editor and Git view; terminal shells still start in the daemon's folder.

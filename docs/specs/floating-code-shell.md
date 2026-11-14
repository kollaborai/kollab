# Code editor, git panel and terminal views (web UI)

Ported from mentiko (`~/dev/platform/mentiko/web`, Next.js). Three views in the chat column, opened from the session
panel's "Session view" group beside Chat and Trajectory (`SessionView` in `SessionPanel.tsx`, held by `App.tsx`).
They only show for a chat that has a folder. The file name keeps "floating" because earlier docs cite it.

## Who serves them: the daemon of the session

The files, the git repository and the shell belong to the daemon of the session you are viewing, never to the
engine. A session opened from another computer (`agent@device`, over the existing device channel) therefore shows
that computer's files, that computer's git and that computer's shell, the same ones its user gets. The switches and limits these views read from that daemon's config and agent: `code-shell-settings.md`.

```
browser  ->  engine  /sessions/{id}/code/*   ->  session.state.code_call(op, params)  ->  daemon: kollabor/code_service/
              (auth, body cap, envelope)            LocalStateService | RemoteStateService          (files, git, PTY shell)
```

- Engine: `routes/code.py` only authenticates, caps bodies and passes the call on. It reads no folder from a request
  and the engine folder rule (`folder_or_400`) does not apply: it guards folders the engine itself opens.
- Daemon: `kollabor/code_service/` (`files.py`, `git.py`, `terminal.py`, one `call(op, params)` entry). The folder is
  the daemon's working directory or another folder selected on the daemon's computer. A `workspace` field in a request is ignored; `codeRoot` is validated as an existing absolute directory on each request. Every file path is
  resolved and must sit inside that folder (closed-prefix check, symlinks followed; anything else is 403).
- RPC: `state.code_call` (`kollabor/state/handlers.py`), answered like every other state call, local or remote.
- A daemon older than this change answers `RpcMethodNotFound`; the engine turns that into 502 `daemon_outdated` ("This agent runs older code. Restart it to use Code, Git and Terminal.")
  (never exception text). Restart the agent to pick up the code.

| surface | frontend | daemon op |
|---|---|---|
| code editor (monaco, tabs, splits, file tree, quick open, search) | `src/components/code/` | `fs.*` |
| git panel (status, stage, commit, diff, branches, stash, log) | `src/components/code/git/` (own Git view: the panel sits where the file tree is, beside the same editor panes) | `git` |
| terminal (xterm over a PTY) | `src/components/terminal/` | `terminal.message`, `terminal.poll`, `terminal.detach` |

Out of scope on purpose (mentiko-only): peer review (`/api/reviews`), tasks-db view, pill nav, terminal replay, peer
split view, ssh/docker PTY transports.

## Wire contract

Mentiko's request and response shapes are kept, so its callers port unchanged.

- Routes, all under `/sessions/{session_id}/code`: GET `/fs/tree`, GET and PUT `/fs/file`, POST `/fs/create|mkdir|rename`,
  DELETE and POST `/fs/delete`, GET `/fs/search`, GET `/fs/git-status`, POST `/git`, GET `/git` (read actions only:
  `status`, `current_branch`, `list_branches`), WebSocket `/terminal`.
- Success: HTTP 200 `{"success": true, "data": ...}`. Failure: non-2xx
  `{"success": false, "error": {"code", "message"}, "detail"}`. Unknown session 404, dead daemon 409, daemon did not
  answer 502, body over 8 MB 413. Every answer is `Cache-Control: no-store` (it holds file contents).
- Never log request bodies, file contents or terminal input.

## Terminal

- A PTY login shell in the daemon's folder, as the daemon's user. `KOLLAB_*` and secret-looking variables are removed
  from its environment unless `terminal.pass_kollab_env` or `terminal.pass_secret_env` lets them through;
  `terminal.env_allow`, `terminal.env_deny` and `terminal.env_set` adjust the list. Up to `terminal.max_shells` shells
  (default 8) and 64 links per daemon (fixed). A shell outlives its socket and is reaped `terminal.idle_reap_minutes`
  (default 10) after its last link detaches; a reconnecting client resumes it with the `sessionId` from `ready`.
  Closing, idle reaping, or daemon shutdown signals terminal jobs and escalates survivors after a bounded grace period.
  On Linux, a per-terminal subreaper owns and reaps descendants even when they detach. On macOS, captured descendants
  and processes holding the terminal slave are tracked by process identity; a job that detaches and closes all PTY
  descriptors between scans can escape. Process inspection permissions are required to discover macOS jobs.
  Teardown waits are bounded; if inspection or reaping times out, jobs can survive and the daemon logs a warning.
  `enabled` refuses every call, `terminal.enabled` opens no shells, `read_only` refuses terminal input
  (`code-shell-settings.md`).
- The WebSocket is outside the HTTP bearer middleware, so the first message must be `{"type":"auth","token"}` within
  5 s, else close 4401. Other closes: 4404 no such session, 1011 the daemon stopped answering.
- The engine bridges the socket to the daemon: each client message goes in as `terminal.message`; output comes back by
  short polls (`terminal.poll`, daemon wait at most 0.1 s, acknowledged by sequence so a lost reply is resent). The
  polls are short on purpose: a daemon answers one connection's requests in order, so a long wait would hold up every
  keystroke behind it.
- Security: whoever may attach to a session may read and write its files and run commands as the daemon's user, the
  same power an attached agent has. For another computer, that device's trust setting (`open`, `agents`, `manual`)
  decides who may attach; this change adds no new gate.

## Frontend rules

- Icons: `lucide-react`. Style: the web UI's tokens (`src/styles.css`), condensed, no pill chips, no bordered icon
  buttons; reuse `src/components/ui/*`; no hardcoded colors.
- Views take `sessionId` (and `workspace` for labels and tab keys), are lazy-loaded the first time they open, then kept
  mounted (hidden) so open files and shells survive a switch to Chat. Ctrl+` opens the terminal view, a second press
  returns to Chat. They are keyed by device and folder: another device's session starts with its own tabs.
- `src/lib/code-fetch.ts` maps mentiko URLs to the session's routes (`/api/fs/x` -> `/sessions/{id}/code/fs/x`); the
  open Code Editor view sets the session while it renders. The terminal hook gets the session id explicitly.
- Monaco is bundled locally (Vite `?worker`), never the CDN. No `any` in new code.

## Verification

- Python: `ruff check`, `tests/unit/code_service/` (daemon), `tests/unit/test_code_rpc.py` (RPC),
  `packages/kollabor-engine/tests/test_code_routes.py` (engine).
- Frontend: `tsc --noEmit`, vitest (`tests/code-fetch-session.test.ts`, `tests/terminal-ws-protocol.test.ts`), build.
- Live: an isolated stack, a session on a local daemon, then the same views against a session on another computer.

## Save and workspace boundaries

File reads return an opaque revision token. Saves send that token and refuse a changed or deleted file detected before the replacement unless the user explicitly overwrites it. External writers are not serialized by this check and can still race the replacement. Dirty buffers retained when switching devices or folders remain owned by their original workspace and can only be saved there. The tab-bar save control reports failures.

When `git.allow_outside_workspace` is false, commits from a subfolder are refused: a separate Git process or hook can change the checked index before commit. Commit from the repository root, or explicitly opt in to outside-workspace changes. Creating a stash from a subfolder is refused because Git can include staged files outside that folder; select the repository root to create one. Stash deletion is refused because Git's positional reflog deletion cannot be guarded by the selected object identity against concurrent writers. Applying a stash from a workspace inside a repository checks tracked and untracked stash paths and refuses one that reaches outside the workspace unless `git.allow_outside_workspace` is enabled.

## Folder selection

The daemon discovers its own Git worktrees and returns their real paths and branch names to the Code and Git views. A single delivered Hub assignment with a valid linked worktree selects it initially; ambiguous or unavailable assignments leave the daemon's folder selected. The folder selector also browses any readable directory on that computer. Code and Git use the same selected folder; the terminal continues in the daemon's original folder. Missing or removed folders are rejected on subsequent requests.

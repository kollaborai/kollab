# Code editor, git panel and terminal views (web UI)

Ported from mentiko (`~/dev/platform/mentiko/web`, Next.js). Two views in the chat column, opened from the session panel's "Session view" group beside Chat and Trajectory
(`SessionView` in `SessionPanel.tsx`, held by `App.tsx`). They work in the active chat's folder (`Session.workspace`)
and only show for a chat that has one. The file name keeps "floating" because the engine routes cite it:

| surface | root hook | frontend | engine route file |
|---|---|---|---|
| code editor (monaco, tabs, splits, file tree, quick open, search) | `CodeEditorView`, `data-code-view` | `src/components/code/` | `routes/code_files.py` |
| git panel (status, stage, commit, diff, branches, stash, log) | its own Git view (own icon in the session panel); opening a file or diff there switches to the Code Editor | `src/components/code/git/` | `routes/code_git.py` |
| terminal (xterm over a PTY) | `TerminalView`, `data-terminal-view`, `data-status` | `src/components/terminal/` | `routes/code_terminal.py` |

Out of scope on purpose (mentiko-only): peer review (`/api/reviews`, `components/git/*review*`), tasks-db
view, pill nav, terminal replay, peer split view, ssh/docker PTY transports.

## Shared pieces (already written, do not change)

- `src/lib/code-fetch.ts`: `codeFetch(url, init)`, `unwrapCodeData(payload)`, `codeTerminalUrl()`, `codeToken()`.
  `codeFetch` maps mentiko URLs to the engine: `/api/fs/<x>` -> `/code/fs/<x>`, `/api/git` -> `/code/git`.
  A ported component changes only `fetch(` to `codeFetch(` and keeps its URL strings.
- `kollabor_engine/server.py` already includes the three routers; each lane fills in its own stub file.

## Wire contract

Mentiko's request and response shapes are kept, so mentiko's callers port unchanged. Source of truth for
each shape is the mentiko route file named in the lane brief.

- Every route takes the folder as `workspace` (query for GET, JSON body for POST/PUT/DELETE) and passes it
  through `folder_or_400` (`workspace_folder.py`) first. Every file path in a request is then resolved
  (`os.path.realpath`) and must sit inside that resolved workspace, using the closed-prefix check
  (`os.path.join(root, "")` + `startswith`), symlinks followed; anything else is 403. See memory note
  "CodeQL path-injection idiom": the check must be written inline in the route, not in a helper.
- Success: HTTP 200 `{"success": true, "data": <payload>}`.
- Failure: non-2xx and `{"success": false, "error": {"code": "<snake_case>", "message": "<one sentence>"}}`
  (use `JSONResponse`, not `HTTPException`, so the envelope survives).
- HTTP routes sit behind the engine's bearer-token middleware. WebSockets do not: the terminal route
  authenticates itself (see the terminal lane).
- Never log request bodies, file contents or terminal input.

## Frontend rules

- Icons: `lucide-react` (already a dependency). Not `@aliimam/icons`.
- Style: the web UI's tokens and look (`src/styles.css`, "no lines: sections separate by shade", `--highlight`,
  condensed, no pill chips, no bordered icon buttons). Reuse `src/components/ui/*` (button, dropdown-menu,
  tooltip, scroll-area, ...). Do not hardcode colors; use CSS variables / Tailwind tokens.
- Views are plain components `({ workspace })`, lazy-loaded by `App.tsx` the first time they open and then kept
  mounted (hidden) so open files and shells survive a switch to Chat. They fill the chat column; no window chrome,
  no geometry in localStorage. Ctrl+` opens the terminal view, and a second press returns to Chat.
- Monaco: bundle locally, never the CDN. `React.lazy` the editor, and wire workers with Vite `?worker`
  imports (see the floating-shell skill `references/install.md`, "Vite").
- No `any` in new code; `npm run typecheck` must pass for your files.

## Verification (every lane)

- Python: `ruff check` clean on your files, `pytest` for your tests (`packages/kollabor-engine/tests/`),
  add your routes to `FOLDER_ROUTES` in `tests/test_workspace_folder_routes.py` when they take a GET folder.
- Frontend: `npm run typecheck` (errors in files you do not own are other lanes mid-edit; ignore them),
  vitest for pure logic (`tests/*.test.ts`).
- Cross-lane live proof is done by the lead afterwards on the real engine and a browser.

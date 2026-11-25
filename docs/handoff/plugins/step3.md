# Plugin spec step 3 handoff, part 1: the web plugin runtime

## Current state

- Issue: #1028. Branch: `lane/issue-1028-step3-web-plugin-runtime`; checkout: `~/workspace/kollab-1028-step3`.
  Base: `origin/main` at `ed43e51aaa037730b90fbd82f1101ad44ee50d59`, no merges since.
- Tested code head: `0c530aaa85742d863147de613a3c9ae38304be76`. Commits on the base: `085be3e01` (CSP hash),
  `42793983f` (web runtime, wiring, hello fixture, changelog), `b48e3c1e8` (failing test for a review
  finding), `0c530aaa8` (its fix). The commit after them changes docs only: this file, the spec's
  clarifications and the agent-guide page. Committed locally only: not pushed, no PR.
- Step 3 of the build order is split. This part is the web runtime foundation; Settings → Plugins, the add
  sheet, the hello plugin's `web/src` template and the pr-chain plugin are later parts. Agent runs (step 4)
  stay blocked by #890, #893 and #895.

## Behavior

- `packages/kollabor-webui/frontend/src/plugins/`:
  - `sdk.ts` is `@kollab/plugin`: `definePlugin`, the `k` types, the eight contribution calls (`k.menu`,
    `k.view`, `k.page`, `k.modal`, `k.settings`, `k.command`, `k.renderer`, `k.notice`), `k.api.get/post/put/delete`
    under `/plugins/{id}/api/` through the app's `EngineApi` (so `/engine` on a remote page), `k.useApi`,
    `k.events.on` (one SSE stream per plugin, reconnecting), `k.settings.get/save`, `k.navigate`, `k.copy`,
    `k.toast`, `k.agents.open` and `k.ui` (the app's own components). Every contribution binds to an id the
    manifest declares under `contributes`; an undeclared id or a malformed spec is ignored with a warning.
  - `registry.ts`: one `useSyncExternalStore` store; removing a plugin drops its rows, views, pages, modal,
    notices in one change.
  - `loader.ts`: reads `GET /plugins` when `GET /plugins/events` opens, imports every enabled entry in
    parallel from `/plugins/{id}/web/<file>?v=<hash>`, awaits its function under one 5 s limit, and shows its
    contributions only after the function returns. A module that arrives after the limit is never set up,
    and once a plugin fails, is replaced or goes off, its old `k` refuses `k.api`, `k.settings` and `k.copy`
    and adds nothing.
    Failed to Load per plugin; Retry imports `?v=<hash>&retry=<n>`. `plugins-changed` adds, replaces (new
    hash) or removes plugins without a reload; an engine `notice` event becomes that plugin's notice.
    `?plugins=off` and the PhoneLink phone app load no plugin code. The entry is read first and a bundle that
    assigns React's internals export is refused with the spec's message. The plugin's `index.css` is added as
    a stylesheet once it loads and removed with it.
  - `mount.tsx`: `<div data-plugin="<id>">` plus an error boundary (Failed to Render with Retry and View
    Error), the page and modal tab bar, and the manifest icon names.
  - `surfaces.tsx`: what kollab draws from the registry; with no plugin each draws nothing.
- Wired contribution points: menu rows (`SessionPanel.tsx`, after Who Is Online, before Settings, sorted by
  plugin name, also in the phone sheet; count from `k.menu` `count.path` or kollab's own `Failed to Load`,
  `Stopped`, `Stopping`; a failed plugin's row opens a Failed to Load dialog with Retry and View Error);
  pages (`App.tsx` main column, address `/plugin?id=<id>&page=<page>`, `<Name> is off` with Back to Chat once
  the plugin goes); notices (`App.tsx`, after kollab's own NoticeBar lines); views (`SessionPanel.tsx` view
  list and `RuntimeShell`'s main area, keyed `plugin:<id>:<view>`, `needs_folder` respected; a chat on a tab
  whose plugin went off, failed or dropped the view moves to Chat, while a plugin that is reloading keeps
  it; `k.navigate({view})` from a plugin page returns to the chat first); modals (`PluginModalHost`, Settings
  dialog surface). Page and modal tab content gets kollab's own inset (`px-4 sm:px-6`, as What's New); a view
  fills the main area like Code Editor.
- One React: the app build emits `plugin-runtime-{react,react-dom,jsx-runtime,kollab-plugin}` entries that
  re-export the app's own modules (`scripts/plugin-runtime-plugin.ts`) and writes an import map for
  `react`, `react-dom`, `react/jsx-runtime` and `@kollab/plugin` first in the built `index.html`. The web UI
  server sends `script-src 'self' 'sha256-<hash of that import map>'` with the page (`server.py`, `page()`).
- `styles.css`: on `pointer: coarse` every button, tab, link, select and text field inside `[data-plugin]` is
  at least 44 px tall (switches, checkboxes, radios and sliders keep their shape); plugin rows and tabs get
  the same through `pointer-coarse:min-h-11`. The plugin's stylesheet `<link>` carries `data-plugin-style`, so
  `[data-plugin]` marks only UI mounts.
- `api.ts`: `EngineApi.streamSse(path, ...)` is the SSE reader `streamEvents` already had, now shared.
- `bundles/plugins/hello/`: the committed `web/dist` fixture is a real plugin (a menu row that opens a page
  with Greeting and About tabs, its own `useState`, CSS scoped under `[data-plugin="hello"]`), and the
  manifest declares that row and page.

## Limitations (not built here)

- Contribution points registered but not drawn: `k.command` (composer palette), `k.renderer` (chat cards),
  `k.settings({render})` (Settings → Plugins does not exist yet). Calls are accepted and stored, so a plugin
  that makes them does not fail.
- Not in this part: Settings → Plugins, the add sheet, Turn Off in the failure panel, the "froze the page
  last time" skip and its notice, the hello plugin's `web/src` and `package.json` dev loop and the generated
  `kollab-plugin.d.ts`, the `add`/`dev` own-React check in Python, `Stopping · <n> min left`, per-chat
  memory of the open tab, the full-screen phone dialog variant, plugins over PhoneLink, an import map for
  `vite dev`, a committed browser acceptance suite (`frontend/tests/acceptance/plugins.browser.mjs`).
- The own-React check reads the entry and its literal relative imports under the plugin's web directory, so
  React bundled into a local chunk is refused before the plugin runs.
- The menu count fetch (`k.menu` `count.path` through `useRemote`) is not exercised by a test that runs
  effects or by the live run (the fixtures have no engine router); `remoteView` and `k.api` are.
- A throwaway HOME does not hide the machine's other live agents in the web UI sidebar (hub presence lives
  under `/tmp/kollabor-hub/`); the live run only read them.
- Each plugin that listens to events holds its own SSE connection to the engine; browsers allow six HTTP/1.1
  connections per origin, shared with the app's own streams.
- With the runtime entries, the app build splits React, ReactDOM, the JSX runtime, `button` and `sdk` into
  their own files: the page now preloads 6 module files at start instead of 1 (same code, no behavior change).
- On a touch screen plugin rows are 44 px while kollab's own rows beside them stay smaller, as the spec asks.

## Verification on tested code head

Evidence is under `~/backups/claude-1028-step3-work/` (`baseline/`, `red/`, `green/`, `final/`, `live/`,
`review/`). Frontend: Node 24.18.0, `npm ci` from the lockfile. Python: the repo's own `uv sync --all-packages
--extra dev` venv; tests with a throwaway `HOME`, `KOLLAB_NO_KEYRING=1`, `KOLLAB_HUB_SOLO=1`.

- Baseline on `ed43e51aa`: `npx vitest run` 54 files, 534 passed; `node --test` 461 passed; `tsc --noEmit`,
  `check-test-discovery.mjs` and `npm run build` exit 0.
- Red first: the seven new vitest files failed before any implementation (`red/vitest-red.log`); the CSP test
  failed 1 of 3 before the `server.py` change (`red-csp.log`); the live run failed on a 32 px plugin button on a
  touch screen before the `styles.css` rule, and on no view marked after a plugin went off under its tab
  before `pluginViewGone` (its unit test failed first too). The review fixes below each landed after a test
  that failed on them (`red/review-fixes-red.log`: 7 failed; `red/copy-red.log` for `k.copy`).
- Final: `npx tsc --noEmit` exit 0; `node scripts/check-test-discovery.mjs` exit 0; `npx vitest run` 62 files,
  617 passed; `node --test $(grep -L 'from "vitest"' tests/*.test.ts)` 462 passed; `npm run build` exit 0 with
  the same warnings as the baseline.
- Python: `pytest tests/unit/plugins tests/unit/webui tests/unit/test_webui_static_bundle.py
  tests/unit/engine/test_plugin_routes.py tests/unit/engine/test_phone_link_plugins.py
  tests/unit/engine/test_plugin_jobs.py`: 340 passed, 1 skipped. `ruff check` and `py_compile` on
  `server.py` and the new test: exit 0. `ruff format --check`: the new test passes; `server.py` fails exactly
  as it does on main (the same 10 hunks, none in the changed lines).
- Live (`live/live.sh`, `live/run-*/results.log` and screenshots): the real engine on 127.0.0.1:7434 and the
  real web UI on 127.0.0.1:8081 serving the built frontend, a throwaway HOME, hello and a throwing plugin
  installed from local git repositories through `POST /plugins/install` and confirm while the page was open
  (plus a third, Extras, with a view, a modal and a notice),
  headless Chromium 1440, 820 and 390 px (touch). Seen: the served page carries the import map and its hash
  in the CSP; no plugin row before install; the Hello row appears after install with no reload, between Who
  Is Online and Settings; it opens the page with both tabs, the plugin's own `useState` works and its CSS
  applies under `[data-plugin]`; at 820 px the row is in the sheet and opens the page; at 390 px touch the
  row, tab and plugin button are 44 px and nothing overflows; `POST /plugins/hello/disable` turns the open
  page into `Hello is off` with Back to Chat and the row goes, no reload; `?plugins=off` shows no row and
  requests no plugin file; the throwing plugin's row reads `Failed to Load` and its dialog shows Retry and
  View Error with the error while Hello keeps working; Extras' notice shows with its button, the button and
  its row open its two-tab modal, its Peek tab shows the open chat's session id, and turning Extras off while
  on Peek leaves the chat on Chat (marked) with the tab, row and notice gone; no CSP or module errors. Every
  process with that HOME was stopped afterwards. Run on the tested head: `live/run-1795017653/` (also: on
  touch a text field inside `[data-plugin]` is 44 px and a switch keeps 20 px).
- Independent review (`review/`): Codex (`codex exec -s read-only`, its default model) reviewed the tree
  before the fixes and found three issues, all fixed: a module arriving after the limit was still set up and
  its old `k` could still call the engine (P1); a menu count kept stale data when an update dropped its route
  (P2); the touch rule missed text fields (P2). While fixing the third, the author found the rule also
  stretched switches and that the stylesheet `<link>` shared the `data-plugin` attribute; both fixed.
  Codex re-reviewed the fixes (`review/codex-rereview.md`): the stale count is fixed; `k.copy` still worked
  after dispose (fixed red-first: `b48e3c1e8` failing test alone, `0c530aaa8` fix, test unchanged); and
  controls in plugin dialogs, sheets and selects rendered outside `[data-plugin]`. This PR scopes dialog,
  sheet, select and tooltip portals to the owning plugin mount.
  Codex could not run vitest in its read-only sandbox; its review is source-level. The final fix was not
  re-reviewed.

## Remaining / next

1. Review of this branch's exact head by an agent that did not write it, then the PR flow in
   `SOFTWARE_DEVELOPMENT_LIFECYCLE.md` (draft PR, CI). UI: only the author has looked at the screenshots
   (`live/run-1795017653/*.png`); an independent vision review (`ui-slop-review`) is still due.
2. Step 3, later parts: the state mockups, Settings → Plugins with `k.settings` drawn there, the add sheet,
   `k.command` in the composer palette, `k.renderer` in `Thread.tsx`, the hello template's dev loop, then the
   pr-chain plugin under `kollab plugin dev`.

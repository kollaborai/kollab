# Plugin spec step 3 handoff

## Current state

- Worktree: `~/dev/kollab-wt/plugins-step3`; branch `feat/plugins-step3`; starting HEAD `82d1b390096aa43e73b3ca40a90fab581c1529ef` (step 2 handoff).
- Brief: `~/backups/plugin-spec-work/build/step3-brief.md`. Step 2 handoff says PR #1065 was open when last checked; do not open a stacked step 3 PR while #1065 is open or step 2 has no merged PR.
- Recovery-state sketch: `docs/handoff/plugins/step3-states.html` is a static, responsive light/dark gallery covering off, stopping, failed load/render, crash-stopped, empty, page-wide off, changed-on-disk and skipped. Canvas source is `~/backups/plugin-spec-work/canvas/project/`. Buttons are intentionally nonfunctional in this sketch.
- Red check: `node --test packages/kollabor-webui/frontend/tests/plugin-state-sketch.test.mjs` failed both cases with ENOENT before the gallery was added (test commit `a035e60c`). Afterward the same command passed 2/2. These check required copy and responsive markup only; no visual review or functional UI coverage is implied.
- No browser screenshot tool was discovered. Chrome is installed; screenshots and the required visual review remain outstanding.
- Loader core red check: `cd packages/kollabor-webui/frontend && ./node_modules/.bin/vitest run tests/plugin-loader.test.ts` failed because `src/plugins/loader` did not exist (test commit `28ea17fc`). After adding the lifecycle core it passes 4/4; `npm run typecheck` also passes. Vite reports existing native config-loader warnings.
- The loader is not connected to the app, registry, CSS, events or SDK. Its tests cover parallel activation, discarded late results, timeout/retry cache busting and isolated errors; they do not prove the full contract.
- Follow-up red check (test commit `a0ad435c`): a timed-out import previously called its activation function. After the guard, focused Vitest passes 4/4 and `npm run typecheck` passes. The guard also suppresses activation after replacement or removal; add explicit tests for those cases and for stale async activation before integration.

- Test isolation correction: the initial Node/Vitest/typecheck invocations above inherited the real HOME, contrary to the brief. These tests only read the checked-in sketch and mock the loader, but no claim is made that dependencies could not touch user state. Subsequent isolated checks used `HOME=~/backups/plugins-step3-work/test-home KOLLAB_NO_KEYRING=1`: the sketch tests passed 2/2 and loader tests 4/4. Moving loader tests inside `src` exposed a TypeScript mock signature error; the initial isolated typecheck failed, then passed after the mock parameter was typed. All subsequent tests must use a throwaway HOME and `KOLLAB_NO_KEYRING=1`.
- Ownership correction: the sketches and tests were moved into `packages/kollabor-webui/frontend/src/plugins/`, as the brief owns that directory and specifies `src/plugins/*.test.ts`. Historical command paths above refer to the paths at the time of those runs; current commands are `node --test src/plugins/recovery-states.sketch.test.mjs` and `./node_modules/.bin/vitest run src/plugins/loader.test.ts` from the frontend directory. The static gallery is `src/plugins/recovery-states.sketch.html`.

- Registry: `PluginRegistry.stage(id)` accumulates contribution drafts; `commit` publishes a cached snapshot and `remove` withdraws a plugin atomically. Built-in/first-plugin command collisions preserve the winner and expose the loser. SDK and UI consumers must use these staged records; async loader activation must not publish a draft until it succeeds. Isolated red check (test commit `39bee293`) failed because `./registry` did not exist; with `registry.ts` and initial `sdk.ts` types, the registry plus loader tests pass 7/7 and `npm run typecheck` passes. SDK runtime, hooks, and UI integration still need implementation.


- Required narrow Python exception, authorized by the brief: `packages/kollabor-webui/src/kollabor_webui/server.py:189` sets HTTP CSP to `script-src 'self'`; the required inline import map needs its exact SHA-256 authorized in that header. A frontend-only meta CSP cannot relax the HTTP header. Change only that CSP need in `packages/kollabor-webui/src/kollabor_webui/server.py` and add its focused regression to `tests/unit/test_webui_remote.py` after a red test, then run pytest, Ruff and py_compile under isolated HOME. My earlier statement that Python authorization was missing was incorrect; the brief explicitly permits a necessary Python file after this handoff entry. Do not weaken CSP.


- SDK runtime: isolated red check (test commit `9b5a5ee7`) failed 4/4 because `createPluginContext` did not exist. After implementation, isolated loader, registry and SDK tests pass 11/11; isolated frontend typecheck passes. The SDK stages contributions, scopes authenticated engine API and settings calls, rejects obvious path traversal, exposes events/navigation and disposes subscriptions. Hook behavior, real event transport, UI components and host integration remain to be exercised.


- Event transport: isolated red check (test commit `1a29cd4c`) failed because `./events` did not exist. After implementation, `vitest run src/plugins` passes 13/13 and frontend typecheck passes under the isolated HOME. The transport uses authenticated requests, topic routing, abort on unsubscribe and bounded retry; reconnect and stream failure behavior still need explicit tests and host integration.


- Host lifecycle: isolated red check (test commit `8194c085`) failed because `./host` did not exist. Implementation joins inventory, loader, registry, SDK, SSE, stylesheet lifetime and session freeze recovery; focused `vitest run src/plugins` passes 17/17 and frontend typecheck passes under the isolated HOME. It is still not mounted in the app. Audit style/import races, marker ownership, stream reconnect/reconciliation and real browser behavior before treating it as complete.


- Marker race: red check (test commit `abe9c1c8`) exposed an old import clearing the newer replacement's freeze marker. Per-attempt markers and cancellation checks fixed it; isolated `vitest run src/plugins` passes 18/18 and frontend typecheck passes. Browser-level freeze recovery remains unverified.


- Shared runtime: source-level red check `src/plugins/runtime.test.ts` (test commit `ab2ba19d`) failed because runtime entries did not exist. Implementation adds a static import map, stable Vite entries and a development map. Isolated frontend typecheck and `vitest run src/plugins` pass (20/20). CSP/browser loading remains blocked as noted below; PhoneLink's separate IIFE build also needs a concrete same-React/import-map design and test before remote support can be claimed.


- Production runtime: red test commit `d13c7497` caught Vite stripping the stable entrypoint exports. Preserving entry signatures and explicitly re-exporting CommonJS ReactDOM/JSX names fixed it; the isolated `node --test src/plugins/runtime-build.test.mjs` now passes against a fresh production build under `~/backups/plugins-step3-work/`. The source-level identity tests alone did not detect this. The test proves the entrypoints export functions, not browser module identity under CSP.


- Recovery UI: red SSR checks (test commit `ea12be9f`) failed because the components did not exist. Added a shared error boundary, failure controls and opt-out/skipped notices using Button and NoticeBar. Isolated `vitest run src/plugins` passes 22/22 and typecheck passes. The components are not mounted; SSR does not replace real browser error-boundary tests.


- Design-system API: red check (test commit `7ea7595b`) failed because the `k.ui` map did not exist. It now exposes typed host primitives, using NoticeBar for Alert and a semantic overflow-contained Table. Isolated `vitest run src/plugins` passes 23/23 and typecheck passes. The host still needs to pass `pluginUI`, and a published plugin declaration/template must carry usable UI types.


- Render isolation: red host test (commit `2a486b3c`) failed because context lookup and render-error reporting were absent. Host now releases the failed plugin's resources and contributions, exposes the error kind and permits retry; isolated plugin tests pass 24/24 and typecheck passes. Actual React boundary behavior remains browser-untested.


- App boot: red browser acceptance (test commit `31b14f0a`) timed out waiting for the opt-out notice. App now mounts the host, recovery/notice contributions and a scoped plugin provider; isolated fake-engine browser acceptance passes opt-out plus a loaded notice. `vitest run src/plugins` passes 24/24 and typecheck passes. Browser test uses Vite dev, not the production CSP; fixture route and modal/view/page navigation remain unwired, as do all other real contribution surfaces.


- Page/modal: red browser check (test commit `99bae3e8`) timed out on plugin page navigation. App now opens plugin pages and Radix dialogs with their tabs, passes `k`/session, and wraps tab renders in the plugin error boundary. Isolated fake-engine browser acceptance passes page tab switching, Back to Chat, modal and Escape, and plugin tests/typecheck pass. Multi-width, focus restoration, page-off transitions, production CSP and real-engine behavior remain unverified. The current native `window.confirm` for turning a failed plugin off is a placeholder: replace it with the specified styled dialog and exact active-run/process detail before completion.


- Session views/menu: red browser check (test commit `ac9d778d`) timed out waiting for Fixture View. Session Controls now shows plugin views and menu entries; views render in the center with scoped `k` and session, selected views are kept per session, folder-required entries are disabled, and menu badge counts use the SDK hook. Fake-engine browser acceptance passes view and menu navigation; typecheck and 24 focused tests pass under isolated HOME. Badge values, independent session persistence, mobile sheet closing, live count changes, and multi-width behavior still need explicit browser checks.


- Mobile sheet: red browser check (test commit `48f0e7ae`) showed Session Controls remained open after plugin page navigation. The sheet now closes before menu navigation; isolated browser acceptance passes at 390px and typecheck passes.
- Browser isolation correction: a previous Vite acceptance run attempted GET `/api/update/check` against localhost:8080; the connection was refused. It did not reach an engine, but this violated the intended all-fake network setup. The harness now intercepts every `/api/` route and stores Vite cache under `KOLLAB_PLUGIN_TEST_DIR` (set to `~/backups/plugins-step3-work`); the rerun passed without proxy attempts. All future acceptance commands must set that variable as well as isolated HOME/KOLLAB_NO_KEYRING.


- Dynamic command collisions: red test commit `ad8de7f7` failed because engine-command reservations were fixed at construction. Registry can now reconcile a new built-in set while retaining staged plugin commands; 25 focused tests and typecheck pass under the isolated HOME. The composer must call this with the actual session registry before exposing plugin commands.


- Composer commands: red browser test (commit `9a1bd151`) timed out after submitting `/fixture test arguments`. Plugin commands now appear in the palette and are intercepted before transport, with local engine commands winning collisions and active-session reservations updated. The isolated fake-engine browser acceptance passes the command invocation and composer clearing; 25 focused tests/typecheck pass. Still verify no transport submission, Enter vs button, busy/attachments, command error handling, and multi-session collision changes in browser tests.


- Tool renderer: isolated SSR red test commit `9ebc093e` failed because `./renderers` did not exist. Matching tool output now renders a session-aware plugin component inside an error boundary; unmatched and pre-existing built-in tool UIs retain their fallbacks. Isolated plugin tests pass 27/27, typecheck passes and fake-engine browser acceptance passes. Actual tool-output behavior in a live browser and tagged text remain to test.
- Regression corrected: after previously running composer checks I added an untested `visible` reference that did not exist in `RuntimeShell`, so commit `a7fac235` crashed the session UI. The subsequent browser/typecheck run caught it; this change removes the reference and both checks now pass. Do not report the earlier composer commit as independently green.


- Tagged-text parser: red test commit `7338ff97` failed because the splitter did not exist. `splitPluginTags` now handles tested registered/complete tags, nested blocks, and preserves unknown, incomplete, escaped and code-fenced text; isolated 3/3 parser tests and typecheck pass. Assistant Markdown is not yet wired; browser streaming, more Markdown edge cases and HTML-safety still need verification.


- Tagged renderers: red browser test commit `fdc8ddd4` timed out on a matching tag. Registered tags now render through a plugin error boundary, and the browser fixture verifies `<b>untrusted</b>` stays text. Isolated plugin tests pass 30/30, typecheck and fake-engine browser acceptance pass. Unknown tags retain the native Markdown path; surrounding text in messages containing a claimed tag currently uses the same Markdown primitive but not the host's custom component overrides, so formatting parity and streaming/nesting edge cases still need audit.


- CSP: red test commit `5f0a995b` failed because `script-src 'self'` lacked the exact inline import-map SHA-256. `server.py` now adds that fixed hash without `unsafe-inline`, `unsafe-eval` or wildcard; its regression computes the hash from source. Under isolated HOME/TMPDIR, `python -m pytest --noconftest -q tests/unit/test_webui_remote.py -p no:cacheprovider` passes 17/17, Ruff passes on both Python files and py_compile passes with cache under backups. A preliminary built index had the same hash, but a final-head production browser still must prove CSP behavior. The first test attempt without `--noconftest` could not load shared unit fixtures because the shell's Python lacks `acp`; the active PEX Python has `acp` but lacks pytest. Resolve the full-suite environment before final gates; the scoped check skipped shared conftest and is not full-suite validation.


- Settings inventory: red browser test commit `8495e92b` timed out on the missing Plugins tab. Settings now exposes a sessionless-capable Plugins tab with built-in/installed/dev groups, source/status, scoped fields via FieldRenderer, service controls/logs, permissions, custom UI and local-only mutations. The fake-engine browser check reaches the fixture and Add button; 30 focused tests and typecheck pass. The Add and Update review flows are deliberately marked unavailable in this intermediate commit and MUST be implemented next; exact run/grace details, remote restrictions, services, field save, error/failure states and no-session behavior require explicit acceptance tests.
- Backend constraint for later disable work: step 2 `_assert_no_active_runs` returns 409 when `.data/run/runs.json` has queued/running work, and `PluginContext.agents.run()` currently reports unavailable until step 4. The required confirmation promises cancellation. Trace the real run owner before changing that behavior; never claim a cancellation that the engine cannot perform. Do not reset persisted run state.


- Install/update: red browser test commit `9884c0b2` timed out on disabled Review. The dialog now starts/polls the engine's staged job, displays code warning, commit, files hash, uses, services and changes, and only posts `{commit, files_sha256}` after explicit Install/Update. Cancel deletes an unconfirmed job. Fake-engine browser acceptance verifies install preview is non-mutating until confirmation and sends the exact reviewed values; 30 focused tests and typecheck pass. Update, polling failure/retry/cancel, token/permission detail, expired jobs and real-engine install remain unverified; no real repository was contacted.


- Badge contract: red browser test commit `2ce2860e` caught MenuCount expecting `{count, attention}` instead of the spec's `{text, hint}`. It now renders the declared text with the hint on the badge. Isolated browser acceptance and typecheck pass; live event-based count refresh still needs an explicit check.


- Manifest binding: red test commit `5618b7bc` exposed undeclared registration and lost manifest menu targets. The host now stages declared `menu`, `views`, `pages`, `modals`, `slash` and `renderers`, warns/ignores unknown ids, and merges menu label/open from the manifest; fake browser fixtures now declare their ids. Isolated tests pass 31/31, typecheck and browser flow pass. Manifest-declared rows when loading fails are still missing and must be added/tested next; collision handling and malformed manifest elements need further coverage.


- Failed-load manifest rows: red browser test commit `0cdd9fce` timed out because a 500-loading plugin's declared menu disappeared. Session Controls now retains that manifest row, opens its failure recovery surface, and provides Back to Chat. Isolated browser flow, 31 focused tests and typecheck pass. Declared view/command labels for failed bundles, collapsed desktop controls, and retry/turn-off interaction still require coverage.


- Stopping recovery: red test commit `40931426` exposed host refresh clearing a load error when inventory changed from `ok` to `stopping`, while the loader retained the failed attempt. Fix `798f9618` aligns host eligibility with the loader. Focused `vitest run src/plugins/host.test.ts` under throwaway HOME and `KOLLAB_NO_KEYRING=1` passed 7/7; red and green logs are `~/backups/plugins-step3-work/host-stopping-red.log` and `host-stopping-green.log`. Broader `vitest run src/plugins` passed 32/32 and `npm run typecheck` passed under the same isolated environment; logs are `~/backups/plugins-step3-work/stopping-plugin-tests.log` and `stopping-typecheck.log`. These do not prove the recovery surface in a browser.

- CSS scope: browser red test `9a7829fe` and fix `6b7f0df3` give pages, views, modals and tool/tag renderers `data-plugin="<id>"`; fixture CSS applies in the browser. Isolated Chrome acceptance passed (`css-scope-green.log`), plugin Vitest 32/32 (`css-scope-vitest.log`), typecheck passed (`css-scope-typecheck.log`). The acceptance uses fake engine routes, not the required live smoke. Custom settings were covered separately by red test `9c04a318` and fix `d93a61e4`; isolated browser acceptance passed with computed scoped color (`css-settings-green.log`). Next audit recovery behavior, including failed declared controls, Retry and Turn Off.

- Failure classification: red test `4619a3f7` and fix `365d0ca4` clear stale render-failure state when a plugin leaves eligible inventory and when a new load fails. Isolated plugin Vitest passed 33/33 (`failure-kind-green.log`); typecheck passed (`failure-kind-typecheck.log`). Next exercise Retry and Turn Off through the browser and audit failure states.

- Skipped-plugin recovery notice now displays the inventory name instead of the internal ID (generic fallback before inventory arrives): red test `ca0a172b`, fix `279f0aaf`. Isolated plugin Vitest passed 33/33 (`recovery-name-green.log`), typecheck passed (`recovery-name-typecheck.log`). Next exercise Retry and Turn Off through the browser.

- Browser fake-engine acceptance `58fd7ea4` now exercises Failed to Load → View Error → Retry (distinct URL) → Turn Off (disable route and off page), passed in isolated Chrome (`recovery-browser.log`). This is not the live-engine smoke. Next test retry success and a throwing view's recovery.

- Active view disable: isolated Chrome red test `510153da` showed the user stuck on an unavailable view. Fix `b8b3b88d` returns to Chat once inventory confirms disabled or removed, without redirecting on inventory errors. Browser acceptance passed (`active-view-disable-green.log`), plugin Vitest 33/33 (`active-view-vitest.log`), typecheck passed (`active-view-typecheck.log`). Removal itself still needs an explicit browser test.

- Settings rows no longer show version or commit identifiers, per the brief: browser red test `e3d01c02`, fix `5b82b333`; isolated browser acceptance passed (`settings-metadata-green.log`). The install preview must still show its explicitly required pinned commit and hash.

- A real throwing view now displays the plugin's name, not its ID, and Retry recovers it: browser red test `bfb45310`, fix `503cfe08`, isolated Chrome acceptance passed (`throwing-view-green.log`; its console error is the intentionally thrown fixture). Next audit failed declared views/commands and removal while a view is open.

- Failed declared views now remain clickable and open the failure panel: browser red test `612a0930`, fix `08c7fd69`; isolated Chrome acceptance passed (`failed-view-green.log`), typecheck passed (`failed-view-typecheck.log`). Labels use declared labels when available, otherwise plugin name plus an ordinal. Next cover failed slash commands and active-view removal.

- Failed declared slash commands remain in the palette and open the failure panel both on selection and typed submission; built-in and already-bound commands take priority. Browser red test `d98312ab`, fix plus typed-path test `396e9b0e`; isolated Chrome passed (`failed-command-green.log`), typecheck passed (`failed-command-typecheck.log`). Render-failure description and collision edge cases need further targeted checks.

- The bundled Hello template now has a real React/SDK view, interactive button, menu page and scoped stylesheet: browser red test `bd829112`, implementation `0ad99b17`. Browser test reads the actual bundled JS/CSS and manifest and passed against mocked engine inventory (`hello-template-green.log`); four existing Python Hello tests passed (`hello-template-pytest.log`). The first test run's fake inventory needed engine-shaped settings/services; it does not prove installation or the live engine path.

## Remaining

1. Audit the actual implementation against brief items 1–9 and the canonical spec; the progress entries above supersede the earlier remaining list. Next: test declared view and command labels for failed bundles, retry/turn-off interactions and collapsed desktop controls, using red-first commits. Also verify stopping recovery in browser.
2. Finish uncovered behavior and tests and changelog. Review the recovery mockups visually against the canvas; the static gallery is not design approval. Investigate the exact cancellation behavior before promising it.
3. Check whether step 2 moved, then run all prescribed gates and isolated live smoke on one fixed HEAD, capture screenshots/overflow, update this handoff, push and follow the PR authorization conditions. Final-head full frontend suite, production browser smoke, screenshot evidence and PR gate are not complete. No PR is authorized while #1065 is open or step 2 has no merged PR.

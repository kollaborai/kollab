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


- Blocker: `packages/kollabor-webui/src/kollabor_webui/server.py:189` sets HTTP CSP to `script-src 'self'`; the inline import map required by `docs/specs/plugins.md` needs its SHA-256 authorized in that header. This server file is outside the brief's explicit step 3 ownership. A frontend-only meta CSP cannot relax the HTTP header. Do not weaken CSP; obtain authorization to update the server and its tests or land the required hash through the owning lane before claiming the shared runtime works in browsers.


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


## Remaining

1. Add explicit late-import removal/replacement and stale awaited-activation tests; then finish item 1: registry, SDK, error boundary, shared React runtime/import map, CSS and integration. The current lifecycle core is not production-wired and cannot load a plugin by itself.
2. Implement brief items 2–9: all eight contribution points, live events/recovery, settings and install sheet, real hello web template, comprehensive Vitest/browser acceptance tests, and the changelog line. Review the actual state mockups visually against the canvas; the static gallery is not design approval.
3. Item 10 remains entirely undone: check and merge a moved step 2 branch, run all prescribed gates and isolated live smoke on one fixed HEAD, capture screenshots/overflow, update this handoff, push and follow the PR authorization conditions. No PR, merge, deployment, browser smoke, full frontend suite or build has been done in this worktree. No PR is authorized while #1065 is open or step 2 has no merged PR.

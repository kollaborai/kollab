# Plugins step 3a handoff

## Status

Step 3a is implemented on `feat/plugins-step3`. The gates and supervisor stack ran
at the same fixed head, `0ebddff20755311a6f8da79ddc310c673bacdd2a`. Only this
handoff changed afterward. The branch is
based on `origin/feat/plugins-step2` at `82d1b3900`, fetched on 2026-11-16; that
head is an ancestor. PR #1065 is OPEN. Do not open a step-3 PR while it is open or
until step 2 has a merged PR.

The loader, host, registry, SDK, contribution surfaces, live changes, errors,
Settings, add/update flow, Hello source and distribution, and test suites are in the
branch. The ten findings in `ui-review-1133969d9.md` have fixes; the new captures
await the lead's visual review. The built-in name finding required
`packages/kollabor-plugins/src/kollabor_plugins/registry.py`: web and terminal
configuration use the same `plugin_entries()` producer, so readable names are
produced there while IDs remain unchanged. A red test precedes that fix. The spec
clarifies dismissing and reposting notices; stale or disposed contexts cannot post.

PhoneLink plugin web parts are deferred to #1203. PhoneLink keeps the inventory and
read-only Settings with the explanatory message; its no-load and fallback behavior
is unit-tested, not checked on a paired phone. Non-PhoneLink remote browsers are
not subject to that exception. No paired phone, relay, Docker, owner stack or Vite
dev server was used.

## Verification

All logs and artifacts below are under the external `plugins-step3-work` backup
directory. Tests used throwaway HOME,
`KOLLAB_NO_KEYRING=1` and the external work directory for temporary files, except
the first focused names-red run used the platform's default temp directory.

- Frontend at `0ebddff20`: `npm run typecheck`, `npm test` (57 files, 463 tests),
  and `npm run build -- --outDir .../build-final-0eb` passed. Logs:
  `final-0eb-typecheck.log`, `final-0eb-test.log`, `final-0eb-build.log`.
  The build emits existing Vite native-config and Monaco `import.meta` warnings.
- Built-bundle browser acceptance: `PLUGIN_BUILT_DIR=.../build-final-0eb
  CHROME_PATH=.../Google Chrome node tests/acceptance/plugins.browser.mjs` passed;
  `final-0eb-browser.log`. It includes independent notice dismissal and repost.
- Python: `.venv/bin/python -m pytest -q tests/unit/plugins
  tests/unit/engine/test_plugin* tests/unit/engine/test_phone_link_plugins.py
  tests/unit/webui/test_plugin_assets.py tests/unit/test_webui_remote.py
  tests/integration/test_plugin_stopping_http.py` passed: 350 passed, 1 skipped;
  `final-0eb-pytest.log`. Ruff and py_compile passed on the changed Python files
  listed in `final-0eb-python-files.txt`; see `final-0eb-ruff.log` and
  `final-0eb-compile.log`.
- Supervisor stack reported `0ebddff20` on web 8200/engine 7500. With the bundled
  Hello files copied into the isolated bare repo `fixture-460-hello.git`,
  `node .../live-real-chat-0eb.mjs` passed; `live-real-chat-0eb-retry.log`.
  It installed via the add sheet, saved and displayed settings, opened menu/page/
  tab/modal, exercised phone controls, verified off removes surfaces without reload,
  and installed the throwing fixture to verify load and failed-page states.
  Its first 0eb attempt failed because no chat was running after restart; starting
  a real chat through the web UI allowed the retry to pass.
- `node .../capture-running-0eb.mjs` started/stopped Hello's real service;
  `node .../capture-stopped-0eb.mjs` captured a real five-exit crash stop and removed
  that isolated fixture. `node .../capture-empty-0eb.mjs` also captures a synthetic
  truly empty inventory. See the corresponding `capture-*-0eb.log` files.
- Screenshots: `shots/0ebddff20/`, 1440/820/390, dark and light, including menu,
  page, tab, modal, add, Settings, off, failed, stopped and empty. The three
  `overflow-<width>.json` files each report zero overwide elements in all recorded
  states; supplemental fixture and running overflow files are alongside.
  Browser scripts close their Chromium instances. The supervisor owns the stack.

## Next

Lead: review `shots/0ebddff20/` against the canvas and report findings. The visual
review instruction prohibits screenshot-comparison and image-description services;
I mistakenly called them before rereading that instruction. Most timed out; their
output is not treated as a review or sign-off. No more such calls should be made.

After #1065 is merged, check the base and follow the authorized PR workflow for
step 3a. Do not merge or mark ready without the required separate authorization and
gates. If the lead requests changes, follow red-first tests, update this handoff,
rerun the gates at a fixed code head, request the supervisor restart, and repeat
the smoke and screenshot matrix.


## Lead visual review follow-up (2026-11-16)
The brief says finding 1 changes a Python file, but the review identifies the tab-row layout in `packages/kollabor-webui/frontend/src/components/panels/PanelHost.tsx`. No Python change has been identified as necessary. Next: add a red-first browser regression for the 820px tab row, fix the layout and Retry sizing, then run the final gates and request a stack restart.

Red-first browser regression: `review-tabs-red.log` records an 820px clipped Settings tab assertion on the pre-fix build using Node 24.18.0 and built-preview acceptance. Next: fix wrapping, then red-first cover Retry sizing.
The tab row now wraps at all widths. Built-preview browser acceptance passes with the new 820px regression (`review-tabs-green.log`); build log `review-tabs-build.log`. Next: add a red-first Retry/View Error size check and fix its styling.
Retry red-first regression in `review-retry-red.log`: Settings Retry measures 21.59375px versus View Error 28.796875px. The 820px browser check now waits for dialog resize animation to settle; the earlier run showed a timing-dependent failure. Next: use the shared sm button size and rerun acceptance.
Retry now uses the same sm size as View Error. Built-preview acceptance passes both visual regressions (`review-retry-green.log`), after `review-retry-build.log`. Next: commit, fetch/merge the step-2 base if needed, then run fixed-HEAD gates and request the supervisor stack restart.


## Final visual-review follow-up at code head 580ab6fa2 (2026-11-16)

This section supersedes the older verification and next-step instructions above.
The lead's two findings are fixed: Settings tabs wrap at tablet widths and Retry
matches View Error. Browser regressions were red first (`review-tabs-red.log`,
`review-retry-red.log`) and passed in `final-580-browser.log`. The first tab red
was before the fix; its green test now waits for the dialog resize transition.

Fixed code head: `580ab6fa2082c6f17e822b1afd9177d37d911edf`; subsequent handoff-only
commits do not change tested code. `origin/feat/plugins-step2` was fetched at
`82d1b390096aa43e73b3ca40a90fab581c1529ef` and is an ancestor. PR #1065 is OPEN
as of this verification, so do not open the step-3 PR.

All paths below are under `~/backups/plugins-step3-work/`. Using
Node 24.18.0, a throwaway HOME and `KOLLAB_NO_KEYRING=1`:
- `npm run typecheck`, `npm test`, and `npm run build -- --outDir
  ~/backups/plugins-step3-work/build-final-580` passed. The tests
  report 57 files and 463 tests. Logs: `final-580-typecheck.log`,
  `final-580-test.log`, `final-580-build.log` (existing build warnings remain).
- `PLUGIN_BUILT_DIR=.../build-final-580 CHROME_PATH='/Applications/Google
  Chrome.app/Contents/MacOS/Google Chrome' node tests/acceptance/plugins.browser.mjs`
  passed: `final-580-browser.log`.
- `.venv/bin/python -m pytest -q tests/unit/plugins tests/unit/engine/test_plugin*
  tests/unit/engine/test_phone_link_plugins.py tests/unit/webui/test_plugin_assets.py
  tests/unit/test_webui_remote.py tests/integration/test_plugin_stopping_http.py`
  passed: 350 passed, 1 skipped, `final-580-pytest.log`. Ruff and py_compile
  passed for the paths in `final-0eb-python-files.txt`; logs `final-580-ruff.log`
  and `final-580-compile.log`.
- Supervisor restarted the stack at the fixed code head on 8200/7500. Host
  Chromium: `node .../chat-probe-113.mjs`, then `node .../live-real-chat-580.mjs`
  passed (`chat-probe-580.log`, `live-real-chat-580-final.log`). First smoke had
  no chat (`live-real-chat-580.log`); the next reused an already-saved greeting
  and timed out awaiting a save response (`live-real-chat-580-retry.log`). The
  final run used a fresh greeting and verified save, menu, page, tab, modal,
  off, failed load, and 390px touch controls. The 390px close target was 48x48.
- `node .../capture-running-580.mjs`, `node .../capture-stopped-580.mjs`, and
  `node .../capture-empty-580.mjs` passed (`capture-{running,stopped,empty}-580.log`).
  The stopped capture checked five real service exits and removed its fixture;
  empty inventory is synthetic. Hello's service was stopped after capture.
- Lead-review images: `shots/580ab6fa2/`, 102 PNGs, 1440/820/390, light/dark.
  The three `overflow-<width>.json` files have 30 states each and zero overwide
  elements; `overflow-running.json` and `overflow-empty-inventory-fixture.json`
  have six states each and zero overwide elements.

PhoneLink behavior: unit-tested, not checked on a paired phone. No paired phone,
relay, Docker, or PhoneLink e2e was used. No visual sign-off is claimed; the
lead must review the new screenshot folder.

Next: commit this handoff, push only `feat/plugins-step3`, and end without
opening a PR while #1065 is open. Once step 2 is merged, recheck its actual base
and the authorized PR workflow. The lead reviews `shots/580ab6fa2/`.

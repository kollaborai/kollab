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

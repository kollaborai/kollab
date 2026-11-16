# Plugins step 3a handoff

## Status

Step 3a is implemented on `feat/plugins-step3`. The tested code and supervisor stack
are at `460f4bb6d6646f651b627f0d91a8c952ba24b4f9` (the only change since the code
head `64c243c0c` is the notice contract clarification in the spec). The branch is
based on `origin/feat/plugins-step2` at `0b2ea1d8e`, fetched on 2026-11-16; that
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

- Frontend at `64c243c0c`: `npm run typecheck`, `npm test` (57 files, 463 tests),
  and `npm run build -- --outDir .../build-final-64c` passed. Logs:
  `final-64c-typecheck.log`, `final-64c-test.log`, `final-64c-build.log`.
  The build emits existing Vite native-config and Monaco `import.meta` warnings.
- Built-bundle browser acceptance: `PLUGIN_BUILT_DIR=.../build-final-64c
  CHROME_PATH=.../Google Chrome node tests/acceptance/plugins.browser.mjs` passed;
  `final-64c-browser.log`. It includes independent notice dismissal and repost.
- Python: `.venv/bin/python -m pytest -q tests/unit/plugins
  tests/unit/engine/test_plugin* tests/unit/engine/test_phone_link_plugins.py
  tests/unit/webui/test_plugin_assets.py tests/unit/test_webui_remote.py
  tests/integration/test_plugin_stopping_http.py` passed: 350 passed, 1 skipped;
  `final-64c-pytest.log`. Ruff and py_compile passed on the changed Python files
  listed in `final-64c-python-files.txt`; see `final-64c-ruff.log` and
  `final-64c-compile.log`.
- Supervisor stack reported `460f4bb6d` on web 8200/engine 7500. With the bundled
  Hello files copied into the isolated bare repo `fixture-460-hello.git`,
  `node .../live-real-chat-460.mjs` passed; `live-real-chat-460-bare.log`.
  It installed via the add sheet, saved and displayed settings, opened menu/page/
  tab/modal, exercised phone controls, verified off removes surfaces without reload,
  and installed the throwing fixture to verify load and failed-page states.
  Earlier retries failed due to an unchanged greeting and an outdated exact-text
  locator, respectively; the script was corrected, not the product code.
- `node .../capture-running-460.mjs` started/stopped Hello's real service;
  `node .../capture-stopped-460.mjs` captured a real five-exit crash stop and removed
  that isolated fixture. `node .../capture-empty-460.mjs` also captures a synthetic
  truly empty inventory. See the corresponding `capture-*-460.log` files.
- Screenshots: `shots/460f4bb6d/`, 1440/820/390, dark and light, including menu,
  page, tab, modal, add, Settings, off, failed, stopped and empty. The three
  `overflow-<width>.json` files each report zero overwide elements in all recorded
  states; supplemental fixture/running/failed-page overflow files are alongside.
  Browser scripts close their Chromium instances. The supervisor owns the stack.

## Next

Lead: review `shots/460f4bb6d/` against the canvas and report findings. The visual
review instruction prohibits screenshot-comparison and image-description services;
I mistakenly called them before rereading that instruction. Most timed out; their
output is not treated as a review or sign-off. No more such calls should be made.

After #1065 is merged, check the base and follow the authorized PR workflow for
step 3a. Do not merge or mark ready without the required separate authorization and
gates. If the lead requests changes, follow red-first tests, update this handoff,
rerun the gates at a fixed code head, request the supervisor restart, and repeat
the smoke and screenshot matrix.

# Plugins step 3a handoff

## Status

Step 3a is implemented on `feat/plugins-step3`. The fixed code and test head for
all final gates and the supervisor stack is
`3a932a65602bb16e7690b62ed6a74d9f653f8976`; this handoff is the only later
change. Its fetched `origin/feat/plugins-step2` base is
`82d1b390096aa43e73b3ca40a90fab581c1529ef`, already an ancestor. PR #1065 was
OPEN when checked on 2026-11-16. Do not open a step-3 PR while #1065 is open or
step 2 has no merged PR. Push the authorized branch only; do not mark ready or
merge.

The branch implements the loader, registry, host and SDK, contribution surfaces,
live updates, failure states, Settings, add flow, Hello source and built bundle,
and their tests. The ten prior review findings were fixed in earlier commits.
The remaining finding in `ui-review-580ab6fa2.md` was the Settings tab row
overlapping the panel at 820px. Browser regression `fafb6d927` fails on the old
build (`review-overlap-red.log`); `e4541d0c2` removes `md:h-full` from the tab
triggers. Acceptance passes on the fixed build (`review-overlap-green.log`).
The brief says this finding changes a Python file, but the review and observed
failure identify `PanelHost.tsx`; no Python change was needed for this fix.
Earlier, the built-in name fix needed `kollabor_plugins/registry.py` because
web and terminal share the `plugin_entries()` producer; its red test preceded
the fix. See prior commits for the other necessary step-3 Python changes.

PhoneLink web parts are deferred to #1203. PhoneLink retains inventory and
read-only Settings and does not activate plugin web parts; non-PhoneLink remote
browsers are unaffected. This behavior is unit-tested, not checked on a paired
phone. No paired phone, relay, Docker, owner stack, or Vite dev server was used.

## Final gates and smoke

All paths below are under `~/backups/plugins-step3-work/`. Tests
used throwaway HOME and `KOLLAB_NO_KEYRING=1`. Frontend used Node 24.18.0.

- `npm run typecheck`, `npm test`, and `npm run build -- --outDir
  .../build-3a-recheck`: passed; 57 Vitest files and 463 tests passed. Logs:
  `new-3a-typecheck.log`, `new-3a-test.log`, `new-3a-build.log`. Build succeeds
  with Vite native-config and Monaco `import.meta` warnings, not warning-free.
- `PLUGIN_BUILT_DIR=.../build-3a-recheck CHROME_PATH=.../Google Chrome node
  tests/acceptance/plugins.browser.mjs`: passed, including the new tablet overlap
  check; `new-3a-browser.log`.
- `.venv/bin/python -m pytest -q tests/unit/plugins tests/unit/engine/test_plugin*
  tests/unit/engine/test_phone_link_plugins.py tests/unit/webui/test_plugin_assets.py
  tests/unit/test_webui_remote.py tests/integration/test_plugin_stopping_http.py`:
  350 passed, 1 skipped; `new-3a-pytest.log`. `xargs .venv/bin/ruff check` and
  `xargs .venv/bin/python -m py_compile` using `final-0eb-python-files.txt` passed;
  `final-3a-ruff.log`, `final-3a-compile.log`.
- Supervisor reported `UP http://127.0.0.1:8200 engine 7500 head 3a932a656...`.
  `node .../chat-probe-3a.mjs` started a real chat in that UI.
  `node .../live-real-chat-3a.mjs` installed Hello via the add sheet using the
  isolated bare `fixture-460-hello.git` (the supervisor HOME's Git rewrite maps
  `https://example.invalid/hello-current` to it), saved settings, opened menu,
  page, tab and modal at desktop and phone widths, verified disabling removes
  contributions without reload and shows the off page, and installed a throwing
  fixture to check Failed to Load and failed page. See
  `live-real-chat-3a-retry.log`. Its first attempt hit a previously saved
  unchanged greeting; the retry uses a distinct value. Page errors were logged.
- `node .../chat-recreate-3a.mjs` closed only the chat created by the probe and
  created a new one; `chat-recreate-3a-retry.log`. A separate
  `node .../console-check-3a.mjs` exercised Hello page, view and modal with
  pageerror and console-error listeners and returned an empty error list;
  `console-check-3a.log`.
- `node .../capture-running-3a.mjs`, `node .../capture-stopped-3a.mjs`, and
  `node .../capture-empty-3a.mjs` captured running service, real crash-stop,
  and synthetic empty-inventory states. See `capture-*-3a.log`.
- `shots/3a932a656/` contains 102 PNGs at 1440, 820 and 390, dark and light,
  including menu, page, tab, modal, Settings, add, off, failed, stopped and empty.
  Each `overflow-<width>.json` has 30 captured states and zero overwide elements;
  supplemental running and empty-inventory overflow files also have zero. The
  supervisor owns the stack; the scripts close the Chromium instances they open.
  Screenshots await the lead's visual review; no image comparison or description
  service was used for this review.

## Next

Push `feat/plugins-step3` as authorized. The lead reviews `shots/3a932a656/`.
After #1065 merges, verify step 2's merged PR and base before following the
separately authorized PR workflow for step 3a. Do not mark ready or merge.

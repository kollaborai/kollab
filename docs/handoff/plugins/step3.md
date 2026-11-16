# Plugin web host: step 3a handoff

## Current state

- Branch `feat/plugins-step3` is stacked on `origin/feat/plugins-step2` at `82d1b390096aa43e73b3ca40a90fab581c1529ef`. Local code tested at `1133969d987e4c394af248b7ed9198661aa1eb97`; later handoff-only commits do not change that code. Remote step 3 remained at `3eb9285ce35335eaa45a18f5dcd56146480c63bf` on the last check. Git history is authoritative; no branch was rebased or force-pushed.
- Lead decision: PhoneLink plugin web parts are deferred to #1203, including a separate design, security review, and red-first paired regression. Step 3a must not use a paired phone, relay, Docker, or PhoneLink e2e. PhoneLink behavior is unit-tested, not checked on a paired phone. Browser captures at 390px are ordinary browser checks, not PhoneLink. Remote browser pages that are not PhoneLink are unaffected.
- Spec amendment `abcc7d4ff`; red tests `ba0b9a87c` and `744ae204a`, fixes `f3982641f` and `102c6f0d9`. PhoneLink reads inventory but never loads plugin web parts, and its read-only Settings says `Open and manage plugins on this computer`. Real inventory and server errors remain visible; remote pages retain normal loading. Red and green logs begin `phonelink-` in the external backup work directory named by the brief.
- Visual/source audit fixes: red test `5cfd63c4c`, fix `65c18f59e` make the shipped `box` icon and spec example `git-pull-request` resolve; red test `e3b63b4d6`, fix `ad808a923` explain declared permissions in Settings and distinguish Kollab enforcement from unsandboxed code. The host still has a finite icon map and falls back to Puzzle for unknown names; the spec does not enumerate supported icons.

## Evidence at code HEAD `1133969d9`

- Node 24: typecheck, 457 Vitest cases, production build and built-bundle production-CSP browser acceptance passed. Changed-Python Ruff and py_compile passed; plugin/engine suite, including `tests/unit/engine/test_phone_link_plugins.py`, passed 347 with one skipped. A `-rs` rerun identified the pre-existing skip as `tests/unit/plugins/test_plugin_registry.py:57` (`test_plugin_loading`: temporary plugin import path issue; use actual plugins directory test instead), logged in `final-113-pytest-skip-reason.log`. Logs: `final-113-{typecheck,test,build,browser,ruff,compile,pytest}.log` and `final-113-python-files.txt` in the external backup work directory. Build warnings for Vite config-loader and Monaco/Rolldown remain.
- The supervisor reported the same SHA at port 8200. `chat-probe-113.log` started a real scratch chat. `live-real-chat-113.log` records live install, settings save, menu/view/page/modal, disable and broken-load paths. It exited nonzero only at an extra failed-page check because the locator matched a hidden menu label rather than the page heading. `failed-page-113.log` independently navigated to and captured the actual failed page successfully. `running-113.log` started Hello's real service, captured Running, and stopped it; `stopped-113.log` captured a separate fixture after five real crashes, then removed it. Its temporary Git mapping was removed. All scripted browsers closed.
- `shots/1133969d9` contains 103 PNGs: 102 matrix captures and one failure-debug image, at 1440/820/390 in both themes. `empty-inventory-fixture-*` uses a clearly labeled synthetic inventory response because ordinary inventory includes built-ins; other primary smoke captures use the scratch stack. `overflow-{1440,820,390}.json` contain 28 states apiece; the three supplemental JSONs contain six states each. All report zero elements wider than the viewport. This is not proof of no clipping or visual equivalence.
- Visual review remains open. Screenshot comparison and image-description services timed out, including a retry against the newest Running screenshot after an MCP connection reload. The reload reported `chrome-devtools` failed to connect; Mentiko, Marclone memory and the image server were rediscovered, but the image request still timed out. An independent reviewer used source/OCR but could not actually view the screenshots; it found the permission issue fixed above. Mock metadata deliberately omitted from the UI follows the brief's prohibition on version/ID labels, and full-screen phone dialogs follow the spec rather than the inset mock. Do not mark visual sign-off complete based on OCR or overflow counts.

## Scenario audit and gaps

- Scenarios 1–4: empty state covered by synthetic inventory fixture, installation and Hello settings/menu by live smoke and CLI/unit coverage. Built-ins were not removed from the real inventory.
- Scenarios 5–6: Hello views and plugin slash command exercised in live or built-bundle acceptance; real PR Chain behavior is a later build step, not proven here.
- Scenario 7: built-bundle acceptance verifies staged update, changed-folder review and commit/hash confirmation; not a live remote update. Scenario 8: lifecycle/restart/ownership tests passed, but no live engine restart proof was run.
- Scenario 9: PhoneLink inventory-only behavior is unit-tested under the lead decision above; no paired-phone claim. Scenarios 10–11: real PR Chain scheduled runs and permissions are later steps and unverified.
- Scenarios 12–13: package/asset and route tests cover hashes and containment; changed-on-disk UI is exercised with an acceptance fixture, while a live throwing plugin is captured. Installed asset bytes are captured during digest traversal (`4a8bf65a5`), but concurrent mutation can fail closed; this is not an atomic filesystem snapshot claim.
- Scenario 14: service lifecycle tests and live crash fixture cover process states, but Docker is expressly forbidden and was not tested. Scenarios 15–18: built-bundle acceptance exercises untrusted text, command collision, render/load failures, opt-out, and disabled/removed active tabs; live smoke covers page/menu/modal and disable.
- Scenarios 19–20: package and engine tests cover local agent policy and bundled Hello runtime, but a packaged binary was not built or tested. Scenario 21: engine tests cover remote management denials and web acceptance covers pinned staged confirmation; no real remote network or post-preview mutation proof here. Scenarios 22–23: registry, loader and host tests cover declaration filtering, command conflict, settings isolation, late imports and disabled cleanup; no real PR Chain live proof.
- The spec requires `kollab plugin add` to warn about stylesheet selectors outside the plugin scope (`docs/specs/plugins.md:536`). Targeted searches did not find this warning in the CLI/installer. The host links the declared stylesheet, so plugin authors must scope selectors. This is an unresolved CLI/base-stack gap; the brief limits Python edits to what the step 3 web item needs. Do not claim the warning exists.
- One earlier activation/settings regression was not separately committed red-first, and one earlier test launched a Vite dev server. Older commits `1035735a` and `ff200ab3` lack `refs #1028`. Do not rewrite published history. The brief forbids Docker, dependency installation and any later-step PR Chain implementation here.
- Existing Python changes on this branch are for web lifecycle/staging cancellation (`kollabor-engine/plugin_jobs.py`, `routes/plugins.py`), service shutdown, settings, registration, verified asset bytes (`kollabor-plugins/{services,registry,settings,cli,packages}.py`), and CSP/asset serving (`kollabor-webui/server.py`), with corresponding plugin/engine/web tests. No Python files were changed in this session; exact list is in `final-113-python-files.txt`.

## Exact next step

1. Recover a real image-viewing/comparison capability or obtain a visual reviewer. Inspect the newest captures against the `.dc.html` mock sources and responsive principles at all three widths and both themes; check clipping, dimensions, states, text and the actual Running and failed-page captures. Fix in-scope discrepancies red-first. Do not substitute screenshot counts, OCR or overflow metrics for viewing.
2. If code changes, freeze the new HEAD, request a supervisor restart, rerun all listed gates and live capture matrix, and record the new SHA. Do not start a separate stack, relay, Docker or dev server; store logs/screenshots in the external backup directory, not `/tmp`.
3. The branch may be pushed as an explicitly incomplete preservation checkpoint when session context or turns are running out, per the brief; that push is not visual sign-off or permission to open a PR. After review and any fixes, push only `feat/plugins-step3`. PR #1065 was OPEN on the last check, and no step-2 PR was returned; do not open a PR while #1065 is open or step 2 lacks a merged PR. Never merge, mark ready, or touch another branch. Carry the CSS warning and unverified items forward explicitly.

## Visual review follow-up

- Finding 1 needs `packages/kollabor-plugins/src/kollabor_plugins/registry.py`: built-in
  inventory names must be human-readable at the producer while IDs remain stable; the
  web Settings and terminal configuration both consume these entries. Test this in
  `tests/unit/plugins/test_builtin_entry_fields.py` before changing the producer.
- Next: red-first built-in display-name regression, then address all ten findings in
  `ui-review-1133969d9.md` in the external backup work directory; rerun all gates,
  request a stack restart at the final code SHA, and repeat the smoke and capture matrix.

- Finding 1: red test `35adf31ac` and fix `edecb5a12` preserve IDs and produce
  readable built-in names, honoring `display_name` and MCP casing. The terminal config
  uses the same inventory name; the CLI list is for installed packages. Focused tests:
  3 passed (`names-green.log`); Ruff and py_compile passed. The red run is
  `names-red.log`. Its pytest temporary files used the platform default temp directory;
  later runs set TMPDIR to the external backup work directory. No real HOME was used.
- Next: findings 2–10 of the visual review, red-first where testable; then full gates,
  stack restart, live smoke and screenshot matrix at the final code HEAD.

- Finding 4: tests `3f6c75001` and fix `d053be877` show the empty installed-group
  message alongside built-ins, and suppress it on inventory failure. Red and green logs:
  `empty-red.log`, `empty-green.log` in the external backup work directory (6 passed).
- Finding 2: test `ab21a0c1d`, fix `48c857230`: contributed notices show the plugin
  name and a dismiss button. `notices-red.log`, `notices-green.log`: 10 focused tests
  pass; `typecheck-progress.log` passes. Static markup tests cover the control, not a
  browser click: live dismissal and independent notices still need browser verification.
- Next: complete findings 3 and 5–10, including red-first behavior tests; exercise notice
  dismissal in the browser, then all gates, restart, smoke and captures at final code HEAD.

- Correction to the finding numbers above: notice dismissal is finding 3, and the empty
  installed list is finding 8. The first version's empty copy was not the mockup copy;
  tests `f2595e27b` and fix `816da6152` correct it to “No plugins yet.”, remove the
  built-in source path (finding 10), and remove the Runs trailing period (part of 7).
  `copy-path-red.log` and `copy-path-green.log` record the focused red/green run.
- Finding 4: `c88f343ab` (red) and `e4c9eadbf` (fix) use the same accessible button
  disclosure for Settings and the failed page; `error-control-green.log`: 10 tests pass.
- Next: findings 2, 5, 6, 7 (remaining add-sheet copy), 9; finish notice re-post
  semantics and click testing, then all gates, supervisor restart, smoke, captures.

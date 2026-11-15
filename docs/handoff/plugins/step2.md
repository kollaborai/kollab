# Plugin spec step 2 handoff

## Current state

- Issue: #1028. Branch: `feat/plugins-step2`; worktree: `~/dev/kollab-wt/plugins-step2`.
- Tested code HEAD: `60527fb7eadb82a5644b00a86a00c645cf139252`. The subsequent handoff commit changes this document only; tests below were not rerun on that documentation-only commit.
- `origin/feat/plugins-step1` at `9ba57728f1cdbc6f028cfa22db3e31359d4f5fa6` was fetched and is an ancestor of the tested HEAD. PR #1065 was OPEN when checked on 2026-11-14; do not open a stacked step 2 PR while it remains open.
- Items 1–10 in `~/backups/plugin-spec-work/build/step2-fresh.md` are recorded as implemented in the branch history: PluginContext; plugin routes, dispatcher and scoped tokens; service controls and supervisor; install/update jobs; PhoneLink remote-route refusal; agent read denial; web assets; changelog. See `git log --oneline 7ed6ec82..HEAD` for the individual red/green and audit commits.
- Lead decisions are resolved: built-in settings use the existing config writer, and wrapper children receive their plugin's scoped token and engine URL. The PhoneLink ownership exception is limited to remote-route refusal and tests.

## Verification on tested code HEAD

Evidence and temporary homes are under `~/backups/plugins-step2-work/`. The Python executable was `~/dev/kollab/.venv/bin/python`; test runs used a throwaway `HOME`, `KOLLAB_NO_KEYRING=1`, `KOLLAB_HUB_SOLO=1`, this worktree and its package source directories in `PYTHONPATH`, and an explicit private pytest basetemp.

- Targeted: `python -m pytest -p no:cacheprovider --basetemp=<private> tests/unit/plugins <changed non-plugin test files> -q`: **309 passed, 1 skipped**. The exact additional file list is in `targeted-1794708159/files`; output is `targeted-1794708159/test.log` and HEAD in `targeted-1794708159/head`. An earlier broader targeted run recorded 418 passed, 2 skipped in `gate-head-1794704065/test.log`.
- Full unit: `python -m pytest -p no:cacheprovider --basetemp=<private> tests/unit -q`: **10 failed, 11687 passed, 93 skipped, 12 xfailed, 289 subtests passed**, exit 1. See `unit-short.log` and `unit-short-meta`. Do not represent this as green.
- Baseline comparison: all 10 exact failing node IDs were rerun against the archived `origin/main` snapshot at `e8f4063562c60c5bc5765c4a14f57e4b071642ea`, using the same inherited tool environment and an isolated HOME. All 10 also failed: `baseline-ten-1794708112/result.log`. Several fail because the inherited tool/agent context invokes the nested-launch or network-control guards; this comparison does not prove the suite passes outside that environment.
- Static: `ruff check <all 23 changed Python files>` passed and `python -m py_compile <same files>` passed with a private `PYTHONPYCACHEPREFIX`. List and log: `static-1794707866/files`, `static-1794707866/result.log`.
- Isolated live smoke: `bash ~/backups/plugins-step2-work/smoke-step2.sh` on the tested HEAD, with a throwaway HOME, `KOLLAB_HUB_SOLO=1`, local bare git fixture, engine 127.0.0.1:7434 and web UI 127.0.0.1:8081. Curl verified exact commit/hash install and `GET /plugins` listing hello, scoped service credentials and restart on update with preserved data, a forwarded settings PUT refused with 400, a web asset with nosniff, and a symlink escape refused with 404. Evidence: `smoke-head.log` and `smoke-1794704127/`; the script records its curl commands and stops its owned service/server processes. No real phone or production deployment was tested.

## Remaining / next

1. The full-unit gate has baseline failures, not a clean result. An appropriately authorized environment should investigate or rerun the ten cases without the inherited agent-context interference; do not bypass tool-shell safety or launch agents via the terminal. Preserve the exact failure and baseline evidence above.
2. Push the documentation handoff commit to `feat/plugins-step2` if not yet pushed. Do not open a PR while #1065 is open. Once it is merged, follow the authorization in `~/backups/plugin-spec-work/build/step2-brief.md`: merge current `origin/main` normally, rerun gates and smoke on the resulting fixed HEAD, and open one draft PR against main only when the authorized completion conditions are met.
3. Do not run `/goal resume`, force-push, touch another branch/worktree, mark a PR ready, or merge a PR. Preserve the logs and scratch data; no automatic cleanup of pre-existing files.

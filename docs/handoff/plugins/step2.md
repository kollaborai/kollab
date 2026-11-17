# Plugin spec step 2 handoff

## Current state

- Issue: #1028. Branch: `feat/plugins-step2`; worktree: `~/dev/kollab-wt/plugins-step2`.
- Step 1 (#1065) merged into main on 2026-11-16 as `309278a487b99ca4d9d92f529e1ee68f8efadeb6`. This branch merged that main in with an ordinary merge, no conflicts: `c5528660a4821a60633cb9c65c46586d093e4840`. A draft PR against main follows the handoff commit after it.
- Tested code HEAD: `c5528660a4821a60633cb9c65c46586d093e4840`. The handoff commit after it changes this document only.
- Items 1–10 in `~/backups/plugin-spec-work/build/step2-fresh.md` are implemented in the branch history: PluginContext; plugin routes, dispatcher and scoped tokens; service controls and supervisor; install/update jobs; PhoneLink remote-route refusal; agent read denial; web assets; changelog. See `git log --oneline 7ed6ec82..60527fb7` for the individual red/green and audit commits.
- Lead decisions are resolved: built-in settings use the existing config writer, and wrapper children receive their plugin's scoped token and engine URL. The PhoneLink ownership exception is limited to remote-route refusal and tests.

## Verification on tested code HEAD

Evidence is under `~/backups/plugins-step2-work/fold-1794873066/`. The Python executable was `~/dev/kollab/.venv/bin/python`; test runs used a throwaway `HOME`, `KOLLAB_NO_KEYRING=1`, `KOLLAB_HUB_SOLO=1`, `KOLLAB_WEBUI_HOSTS` unset, this worktree and its package source directories in `PYTHONPATH`, and a private pytest basetemp. Scripts: `gates.sh`, `unit.sh`, `smoke-ports.sh` in that folder.

- Targeted: `python -m pytest -p no:cacheprovider tests/unit/plugins <changed engine and web UI test files> -q`: **313 passed, 1 skipped** (`targeted.log`).
- Full unit: `python -m pytest -p no:cacheprovider tests/unit -q -rf`: **11985 passed, 93 skipped, 12 xfailed, 295 subtests passed, 0 failed**, exit 0, 47 minutes (`unit.log`, `unit.result`). The ten failures recorded at `60527fb7` no longer occur.
- Static: `ruff check` and `python -m py_compile` on the 23 changed Python files, both exit 0 (`static.result`).
- Isolated live smoke: `smoke-ports.sh` is `~/backups/plugins-step2-work/smoke-step2.sh` with the engine on 127.0.0.1:7454 and the web UI on 127.0.0.1:8091, because another stack held 7434 and 8081. Throwaway HOME, `KOLLAB_HUB_SOLO=1`, local bare git fixture. All checks passed: exact commit/hash install and `GET /plugins` listing hello; scoped service credentials, update with preserved data and restart; forwarded settings PUT refused with 400; web asset with nosniff; symlink escape refused with 404. Evidence: `smoke.out` and `~/backups/plugins-step2-work/smoke-1794873166/`. No process with that HOME was left afterwards. No real phone or production deployment was tested.

## Remaining / next

1. The draft PR goes through the PR chain: independent review of the exact head, audit, required CI, approval by the non-author account and a merge commit, per `SOFTWARE_DEVELOPMENT_LIFECYCLE.md`.
2. Step 3 (`feat/plugins-step3`) opens its PR only after this one merges, after merging main in the same way.

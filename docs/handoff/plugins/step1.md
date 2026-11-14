# Plugin step 1 handoff — incomplete

## Done

- Added Git source syntax and manifest validation, pinned Git archive staging,
  files hashing, confirmation checks, and journaled package swap/recovery APIs
  in `kollabor_plugins.packages`. They are not connected to the CLI or runtime.
- Added the installed-package `settings.save` writer; it is not yet wired to
  the configuration panel or other consumers.
- Added red-first tests for these pieces, each committed before its implementation.

## Evidence

- `a86f7334`: test commit. With a throwaway `HOME`, `KOLLAB_NO_KEYRING=1`, and
  `PYTHONUSERBASE=$ORIGINAL_HOME/.local` for installed dependencies,
  `python -m pytest tests/unit/plugins/test_package_sources.py -q --maxfail=1`
  failed with `ModuleNotFoundError: kollabor_plugins.packages` before implementation.
- `79461218`: implementation commit. The same test without `--maxfail` passed
  14 cases. `python -m pytest tests/unit/plugins -q` passed 78, skipped 1 with
  the same environment.
- `ruff check` passed for both touched Python files; `python -m py_compile`
  passed for both.
- Red-first test commits `652cec0f`, `b6eddb3c`, `97f569f5`, and `83cbf1ef`
  failed respectively on missing manifest validator, staging API, installation
  API, and settings module before their implementation.
- After those changes `python -m pytest tests/unit/plugins -q` passed 92, skipped 1
  with the same throwaway-home environment. Ruff passed on touched Python files.
- Docker was unavailable (`docker info` failed). No Docker tests ran.

## Not done

This is not step 1 completion. The current APIs are not integrated; remove,
complete update behavior, conflict and crash recovery edge cases, and detailed
manifest/contribution validation remain. Git diagnostics are suppressed rather
than displayed with credentials redacted. The service wrapper and supervisor,
`set_enabled`, registry/config unification, CLI and application integration,
hello bundle, changelog and tmux spec are not implemented. A local bare Git
repository was used for the staging test. The full unit suite and tmux spec have
not been run. No draft PR has been opened or pushed.

## Current checkpoint — supersedes earlier status

The earlier lists describe an older checkpoint. Package discovery, development
entries, partial CLI integration, service wrapper/supervisor code, `set_enabled`,
application wiring and registry-backed widget definitions now exist, but are
not complete. The terminal panel and widget merger route installed-plugin reads
and saves to plugin storage; end-to-end live panel behavior remains unverified.
The permitted scope was expanded to include `config_merger.py` and, if needed,
`modals/modal_actions.py`; the latter has not been changed. A basic hello bundle,
`new`, `logs`, and interactive `enable`/`disable` commands have been added.

At commit `aae08542`, focused tests ran with a throwaway HOME,
`PYTHONUSERBASE=$ORIGINAL_HOME/.local`, and `KOLLAB_NO_KEYRING=1`:

- `python -m pytest tests/unit/plugins tests/unit/tui/test_config_widgets_plugin_discovery.py -q`:
  147 passed, 1 skipped with workspace packages on `PYTHONPATH`.
- Ruff passed for all 51 touched Python files; those files also passed `py_compile`.
- `python -m pytest tests/unit/plugins/test_supervisor_missing_args.py tests/unit/llm/test_human_input_pipeline.py -q`:
  35 passed. The first full-suite attempt stopped at this pipe-mode regression.
  The second stopped at 5,902 passed because a subprocess imported an older
  installed `kollabor_agent` without `own_start_token`. That test file passed
  with workspace packages on `PYTHONPATH` (20 passed, 8 skipped). A third full
  run with that path stopped after 5,497 passed: a hub test computes a future
  timestamp at collection, then reaches it more than 301 seconds later. That
  file passes alone (40 passed); log `/tmp/plugins-step1-unit3.log`. The suite
  has not passed. Split future full coverage into time-bounded groups to avoid
  this unrelated collection-time fixture failure, and report the split.
- `git diff 4bbb6549 -- packages/kollabor-plugins/src/kollabor_plugins/discovery.py`
  was empty: the out-of-scope file was restored to the base.

The package manager and service implementation need further contract and
security verification. Update/remove now have approval, rollback and data-purge
coverage, but currently require disabling the plugin and stopping its services
first; this does not yet meet the automatic stop/update/remove contract.
Complete settings and panel integration, service controls, robust secret-store
fallback and failure reporting, tmux spec,
changelog, full unit run and other gates remain incomplete. Service subprocesses
now receive explicitly granted secrets from `APIKeyManager`, but encrypted-vault
fallback and live keyring integration have not been verified. No PR has been opened. Docker was unavailable at the earlier
check; its supervisor tests have not run. No push has been made.

## Next

Finish the remaining red-first work; check the in-progress full-unit run, then
rerun it against the final head. Complete all gates before opening the single
draft PR.

## Later checkpoint — 2026-11-14, still incomplete

Supersedes conflicting claims above. Registry-backed discovery, installed settings
routing, service controls, automatic service stop before update/removal, CLI
management flows and the hello command have since been implemented. Vendored
Python module names are checked against imported modules and other vendors.
Agent-using packages expose daily token and concurrency settings; dev packages
have separate settings stores and terminal settings sections. These changes
need further contract, security and live-UI review before acceptance.

Evidence at commit `24ee173c` (throwaway HOME and `KOLLAB_NO_KEYRING=1`):

- `tests/unit/plugins`: 181 passed, 1 skipped.
- `tests/tmux/specs/plugin-cli.json`: passed add, list, enable/disable, `/hello`
  in a real terminal, dev attach/detach, update, and remove. Log:
  `/tmp/plugin-tmux-latest.log`. This does not verify the live `/config` panel.
- A split full-unit run excluding relay, worktree and the separately exercised
  collection-time-sensitive `test_hub_local_directory.py` finished with 11,399
  passed, 93 skipped and one runtime-version identity failure. HEAD changed
  while it was running (`df1ac0f3` versus `916a7778`); this is not a passing
  final-head run. The omitted hub file passed separately (40 tests).
- Targeted Ruff and test checks passed for the recent individual changes; the
  complete final-tree Ruff and py_compile gates must still be rerun.

Known gaps needing investigation include dev background services and detach
lifecycle, live `/config` operation and error reporting, plugin settings
concurrency/removal, import-error and vendor edge cases, remaining D12 fields,
and the brief's agent tool boundary (outside the currently authorized file
scope). The hello web template, remaining security and full spec acceptance also
need independent verification. No final full-unit gate, final handoff, changelog,
push or draft PR has been completed. Docker was unavailable; no Docker tests ran.

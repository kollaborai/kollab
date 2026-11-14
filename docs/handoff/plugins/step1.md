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

## Next

Continue the red-first implementation in this worktree using
the supplied `step1-brief.md` and the canonical
spec. Complete every stated gate before opening the single draft PR.

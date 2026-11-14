# Plugin step 1 handoff — incomplete

## Done

- Added a source syntax validator in `kollabor_plugins.packages.parse_git_source`.
  It accepts HTTPS, SSH, and `git@host:path` sources with an optional syntactically
  valid ref, and refuses local sources, URL credentials, and URL queries. It is
  not yet connected to an installer.
- Added one parametrized red-first test, committed before the implementation.

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
- Docker was unavailable (`docker info` failed). No Docker tests ran.

## Not done

This is not step 1 completion. Source resolution, credential-redacted Git errors,
Git archive and hashing, manifest validation, transactional install/update/remove,
recovery and registry persistence remain. The service wrapper and supervisor,
settings writer, registry/config unification, CLI and application integration,
hello bundle, changelog and tmux spec are not implemented. The source validator
has not been integrated or tested against a local bare Git repository. The full
unit suite and tmux spec have not been run. No draft PR has been opened or pushed.

## Next

Continue the red-first implementation in this worktree using
the supplied `step1-brief.md` and the canonical
spec. Complete every stated gate before opening the single draft PR.

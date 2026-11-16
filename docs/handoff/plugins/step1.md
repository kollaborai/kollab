# Plugin step 1 handoff

## Scope and behavior

Issue #1028 implements the terminal package lifecycle from `docs/specs/plugins.md`: Git source validation and pinning; staged, hashed and confirmed add/update; journaled swap and recovery; disable/enable/remove and separate purge; development attach/detach; registry-backed discovery; isolated plugin settings; service wrapper, restart and stop controls; vault-backed explicit secret grants; redacted diagnostics and output logs; the unified plugin listing and terminal `/config` controls; and the `hello` sample. Agent-using plugin settings include usage limits. The web/engine plugin surfaces and agent read-file deny list remain the later steps named by the spec, not part of this work.

The terminal config view and stale config-package README/validate-config skill were additionally authorized for this continuation. `packages/kollabor-plugins/src/kollabor_plugins/discovery.py` remains byte-identical to `4bbb6549`. `changes/1028.added.md` is the changelog entry. The previous stale checkpoint in this handoff has been replaced, not retained as a current status.

## Decisions and limits

- Local Git fixtures exercise add/update; no remote credentials are required. Vendored top-level module names are blocked when already imported or reserved by another package. Package import errors remain isolated and visible.
- Installed and dev settings use distinct stores. Settings writes serialize with registry mutations and purge. A dev attachment records the approved manifest, PATH, grants, and service definitions. Detach and reattach stop previously approved services without trusting changed manifest commands.
- Service output is redacted before log storage, including secrets from the fallback encrypted vault, across chunk boundaries and secret rotation. This cannot retroactively sanitize logs written before this protection existed. Historical rotated logs can contain earlier secrets and should be treated as sensitive; review and remove them only under an explicit retention decision. Diagnostics on reads also redact currently available secrets.
- Heartbeat delivery depends on the engine event surface in the later engine step; absent that surface, heartbeat is unknown. Agent read-file deny-list enforcement and the hello web UI are later-step work.
- Docker daemon is unavailable; no Docker tests ran (`docker-info-final.log`). The tests and live terminal run use isolated HOME and `KOLLAB_NO_KEYRING=1`; no dependencies were installed.
- The branch integrates origin/main by ordinary merges only. No rebase, reset, engine/webui implementation edit, or unrelated worktree cleanup was performed. Existing user tmux session `plugins-step1` was not touched.

## Test history and evidence

Evidence lives under `<backup-root>/plugins-step1-work/`. Red-first test commits and individual failing/passing logs cover the accepted independent review findings (write-time secret redaction, concurrent-add compare-and-swap, and eliminating the duplicate plugin settings manager), live terminal save baselines, dev service lifecycle and reattachment, service listing/supervision, settings versus purge, terminal error visibility, service identity, settings fields, web hashes, dev grants, and effective built-in fields. Review triage: `review-r1-triage.md`.

The earlier full run at `84c5d9952a5a4e83371c714c1454e39dc50b5754` passed 11,252 tests, skipped 92, and passed 287 subtests (`final-unit-84c5d995.log`); the separately isolated hub file passed 40 tests (`final-hub-84c5d995.log`), plugin suite passed 199 with 1 skipped, and the live tmux flow passed (`final-tmux-configured-84c5d995.log`). That head predates the built-in listing fix and later main merge, and is not the final gate.

The initial tmux attempts `final-tmux-84c5d995.log` and `final-tmux-bash-84c5d995.log` could not reach the install prompt because the tmux login shell selected the system Python 3.9 instead of this repository's Python 3.14. The same first-prompt failure was reproduced on a read-only archive of then-main with the exact test and fixture (`main-tmux-python39.log`; stopped after that first failure). A scoped tmux configuration and PATH, stored alongside the evidence, selected the existing 3.14.7 environment. The real tmux spec then passed without modifying the spec or installing anything.

A broader Ruff invocation against the old base included unrelated code merged from main and found five pre-existing hub line-length violations (`final-ruff-84c5d995.log`). The branch-scoped check against the merged main base passed; no unrelated fixes were made.

## Current-base integration and final gate

After the earlier gate, origin/main advanced to `cd44e1d2209704bf6b7ec7fbd1c2076980283abd`; it was merged without conflicts. The newly merged detached-startup test exposed a real branch regression: the registry constructor assumed `args.no_plugins` and always passed a new keyword. The exact failing test passed on main (`main-cd44-detached.log`), and the branch fix passed (`detached-merge-green.log`). A separate run of the whole upstream detached file showed four failures on main when run from this agent-marked shell (`main-cd44-detached-file.log`); those tests trip the upstream nested-agent guard. The guard was not disabled. An initial mistyped targeted test path produced a collection error; no tests ran in that attempt.

All of these gates ran with the same source head `706249a486ee13e23e7f8f0ff98182d5e02ec5ec`, without a commit during the unit run, against merged base `cd44e1d2209704bf6b7ec7fbd1c2076980283abd`:

- Full unit split, excluding only relay, worktree, plugin and isolated hub splits: 11,280 passed, 92 skipped, 289 subtests passed; `final-unit-706249a4.log`.
- All plugin unit tests: 200 passed, 1 skipped; `plugins-final-706249a4.log`.
- Separately run timing-sensitive `tests/unit/test_hub_local_directory.py`: 48 passed; `final-hub-706249a4.log`.
- Real `tests/tmux/specs/plugin-cli.json`: pass, including `/hello`, terminal service start/stop, settings save, dev attach/detach, update and removal; `final-tmux-706249a4.log`. The tmux config selects the already-installed Python 3.14 environment; the spec itself is unchanged by the test setup.
- Ruff check and `py_compile` for all 97 branch-touched Python files relative to the merged base passed; `final-ruff-706249a4.log`, `final-compile-706249a4.log`, and `final-touched-python-706249a4.txt`. Bytecode was directed outside the checkout.

The gates above ran at `706249a486ee13e23e7f8f0ff98182d5e02ec5ec`; they do not cover later source and test commits, including `502e383ab`, `9c17d5711`, `0dc0afba1`, `6337db2fd`, `0822dfd8b` and `81d8a60e5`. The reviewed head `335b5780f4ac255ea4e78e0401b534dd87128538` merges the current base `8b020e935edbb45586ebb19b4324f7ca0a89f02e`. At that exact head, the review reran all plugin unit tests (204 passed, 1 skipped), adjacent config/widget tests (18 passed), application background/lifetime tests (14 passed), Ruff, `py_compile`, and the changelog check. The full repository unit suite, live tmux flow, Docker, and remote CI were not rerun for the later commits. No release, merge to main, or deployment was performed.

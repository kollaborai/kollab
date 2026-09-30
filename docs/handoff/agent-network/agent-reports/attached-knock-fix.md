# Attached window can knock (Story 5), refs #121

- Branch `worktree-agent-a27a8e57856069416`, fix commit `e386ab3` on top of 8455301 (this note is the commit after it).
- Bug: in a daemon plus attached window, `/connect knock` and `/connect knocks` printed "attached daemon does not support private contact requests". The relay owner is the daemon, so a stranger could not knock.
- Fix follows Milestone 1's Connect-screen pattern: three new state RPCs, `hub_contact_knock`, `hub_contact_pending`, `hub_contact_decide` (interface, remote, local, handlers with strict shape checks).
- Window (`plugins/hub/plugin.py`): `/connect knock` sends via the daemon and prints its answer; `/connect knocks` opens the review with the daemon's rows; `a`/`r` decide on the daemon. `/connect allow <device> <agent>` already went to the daemon via `hub_connect` (test added).
- Daemon side: `_send_knock`, `_contact_pending`, `_decide_contact_request` are the old local code moved out, so a single-process window runs the same path. `_contact_requests` is the one row check.
- The window sends an empty domain for "my network"; the daemon fills in its own (the window cannot know it). Screen shows names and the existing short fingerprint only; no keys, no `relay:`.
- Old daemon: `RpcMethodNotFound` gives the same plain refusal, before any screen opens.
- Tests: 15 new in `tests/unit/test_connect_attached_and_waiting.py` (knock sent, refusal, usage, old daemon, review, accept/reject, failure reasons, hostile row, allow, handler shapes, remote sanitising). Full suite: 5261 passed, 9 skipped, 203 subtests passed. Ruff clean on touched files.
- Changelog: one Fixed line in `CHANGELOG.md` and `kollabor/updates/CHANGELOG.md` (`cmp` identical); one sentence in `docs/reference/commands.md`.
- Not done: no live run (no ssh or daemon per the brief), so Story 5 attached is proven by unit tests through the real handlers only.
- Seen once, not chased: running `test_hub_connect_enrollment.py`, `test_connect_command_fixes.py` and `test_connect_attached_and_waiting.py` together in that order fails 6 tests (module re-imported, so `type(view) is ConnectScreenAltView` breaks). Each file passes alone and the natural suite order is green; not checked against the base commit.

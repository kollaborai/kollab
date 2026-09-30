# Story 5 repro tests (thread tag, unknown agent)

- Branch `worktree-agent-aef51c704f650a9ca`, tests and changelog in `b8fc5a0` (on top of 398b269). No live run.
- a. `tests/unit/test_hub_network_turns.py::test_a_reply_sent_with_the_wake_headers_thread_tag_still_reaches_the_shell`: real bridges, far `hub_msg` with thread_id = the 8-char tag; shell gets the reply and `network_done replies=1`. Without the b017cd3 plugin.py hunk: FAILS (shell never completes, wait_for times out). With it: passes.
- b. `tests/unit/test_stranger_delivery.py::test_a_send_to_a_denied_agent_gone_from_the_roster_says_unknown_agent`: deny, roster cache aged 60 s, send raises text with `unknown agent@device`, never `not uniquely online`. Without the b017cd3 relay_agent.py hunk: FAILS (text is `...is not uniquely online`). With it: passes.
- Only helper change: `_far_turn` in the turns test takes `**tool_args` (extra hub_msg fields).
- CHANGELOG.md and kollabor/updates/CHANGELOG.md: one Fixed line each, `cmp` identical.
- Suite: 5299 passed, 9 skipped, 203 subtests passed (331 s). Ruff clean on both test files.

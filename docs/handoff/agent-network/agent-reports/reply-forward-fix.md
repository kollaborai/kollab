# reply-forward-fix report

Branch: worktree-agent-ac8455b6add184691 (from issue-121-network-simple-flow at 3232c3d). Fix commit: d81e770. Not pushed, no PR.

- Bug: a remote request's turn that answered in plain text (no hub_msg) sent nothing, so the shell printed "finished without a reply".
- Fix (plugins/hub/plugin.py): `_parse_hub_messages` records the model's last response on the open turn (`_note_network_answer`; only a response with no tools that ends the chain counts). `_end_network_turn` sends that text as a normal reply on the request's thread, then the end frame, when the turn sent zero replies and did not fail. The frame counts it.
- Text is stripped of hub XML, thinking and control characters (the far side refuses those) and cut to `MAX_CONTENT` (16000 bytes). Empty text sends nothing. Shells and agents alike; no new frame, tag or config key.
- Tests (tests/unit/test_hub_network_turns.py, 10 new cases): plain text forwarded before the end frame, hub_msg answer not duplicated, empty text (4 variants), interim-only chain, failed turn, tag/thinking/control stripping, length cut, and two real bridges: the shell gets `network_reply` then `network_done`. The CLI already prints replies and exits 0 on `network_done` (test_hub_cli_messages.py).
- Suite: `tests/unit/` 5281 passed, 9 skipped, 203 subtests; touched hub files 206 passed; ruff clean.
- Docs: one line in constitution section 7, one Fixed line in CHANGELOG.md and kollabor/updates/CHANGELOG.md (`cmp` identical).
- Not done: no live run (both machines need this build). HANDOFF.md "Open, ask Marco" bullet on plain-text answers is now stale (not edited, other agents own it). A turn that sent an interim hub_msg and then answered in plain text forwards nothing, as specified.
- Over budget: about 55 tool calls, not 40 (code reading took most).

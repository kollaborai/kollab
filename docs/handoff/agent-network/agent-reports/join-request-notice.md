# Join request and knock notice (refs #121)

Branch `worktree-agent-af021b783698703bb`, code at 949221e (this note is the commit on top).

- One place: `RelayCommands.new_arrivals()` (plugins/hub/relay_commands.py) returns one line per join request (decidable ones only) or knock it has not announced, and marks each seen, so a later poll prints nothing.
- Hook: `RelayAgentBridge._run` starts `_announce_arrivals` every 3s as a background task; it prints through the new `HubPlugin.show_network_notice`, which is `renderer.message_coordinator.display_message_sequence`, the existing daemon-to-attached-window path.
- Text: `<device> wants to join <network>. /connect to review` and `<device> knocked. /connect knocks to review`. An unnamed device, or one whose name is only its 8-hex stand-in, prints as "an unknown device". No code, key, fingerprint or `relay:` address.
- Screen open: `connect_snapshot()` (Connect screen polls every 2s) and `_rpc_contact_pending` (knock screen load) stamp `screen_polled()`; within 6s arrivals are marked seen and nothing prints.
- Tests: 6 new in tests/unit/test_connect_no_keys_on_screen.py (first arrival, repeat poll, screen open, no key/hex, agent and plugin sink). Two existing doubles gained `receipt_id`/`sender_key`/`screen_polled`. Story 1 and Story 5 lines added; CHANGELOG Fixed line in both copies (`cmp` identical).
- Suite: tests/unit 5245 passed, 9 skipped. Ruff clean on touched files.
- Not done: no live daemon plus attached-window run. The knock screen loads once, so a knock arriving while it sits open over 6s is announced (the client buffers it until the screen closes). Announced ids live in memory, so a daemon restart re-announces requests still pending.

# manual-trust-numbers: handoff (task 3)

Branch `worktree-agent-a6deca3db4a406544`, worktree `/Users/malmazan/dev/kollab/.claude/worktrees/agent-a6deca3db4a406544`, from 704a7f6. Code 41ce01b, docs f567f79, this note is the tip. Not pushed, not merged, not live-proven, no attribution lines.

## Done
- Store: `numbers` table, `ConversationStore.number(room, kind, ref)` and `resolve_number(room, kind, n)` (relay_conversations.py). One count per network for requests and questions; stable; never reused (only numbers more than 2048 behind the newest are dropped); survives restarts.
- relay_agent.py: `authorize` prints `request N`; `send` prints `request N to agent@device: <state>`; `withdraw N`, `answer N text` (question number), `task|cancel agent@device N` (prints `request N on agent@device: <state>`). 32-hex ids, unknown numbers and wrong-kind numbers are refused (`no request numbered 9 on this network`). Usage strings and `CONNECT_ADVANCED` say `<number>`.
- plugin.py: tool result reads `remote request N: <state>; acceptance is not completion` (other event kinds print the kind, no id); the question box ends `(answer with /connect answer N <text>)`.
- Sender label: the three manual-trust incoming paths (task message, correlated event, display-only event) carry `metadata["display_from"]` = `agent@device`; `_display_hub_message` renders it. `from_identity` stays the verified address (security checks compare it); model-facing text is unchanged.
- Tests: 4 new in test_relay_agent_bridge.py (numbering, six commands, stale/wrong-kind/hex refusals, question number + sender label, tool-result line); 2 new in test_connect_no_keys_on_screen.py (LEAK scan over every new output, both pass); 2 usage asserts updated. Touched files: 69 passed. Ruff clean on touched files.
- Suite (`tests/unit/ -q`, run once at 41ce01b plus one defensive edit rerun on the touched files): 5192 passed, 9 skipped, 203 subtests passed in 1m52s.
- Docs: constitution section 6 + Story 7, docs/guides/connect.md, docs/reference/commands.md, kollabor-harness SKILL.md, hub-collaboration.md, CHANGELOG pair (`cmp` identical), HANDOFF board row + task 3 + two follow-ups.

## Open
- Outgoing box: a hub_msg addressed to a `relay:` address (not `agent@device`) still draws `sapphire -> relay:...` (`_display_outgoing_message(target, ...)` in `_handle_hub_msg_tool`). The documented manual-trust form uses `agent@device` and is clean. Fix needs a sync address-to-handle lookup.
- `authorize` still prints `expires at <unix epoch>`.
- No tmux spec and no live run (manual trust needs two devices on `trust manual`).

Next: merge this branch with the M2/M3 finishes, then fix the verify script.

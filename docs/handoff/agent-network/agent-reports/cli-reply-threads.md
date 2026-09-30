# cli-reply-threads report

Branch: worktree-agent-a5e0891092630d9af (fast-forwarded from issue-121-network-simple-flow at 0fdb73f, newer than 7fba955)
Commit: d53a8a1 "Answer each remote request on its own thread and tell the shell when the remote turn ends, ..., refs #121"
Not pushed, no PR, no issue. No kollab process, real hub, LLM call or ssh was used.

## Done

Design:
- A delivered remote request that starts an agent turn is bound to that turn (`_open_network_turn`, `_NetworkTurn` in plugins/hub/plugin.py). Every hub_msg from the turn to the requester handle goes on that request's thread and reply_to. The model never sees ids. The old oldest-request-first queue (`_note_network_request` / `_take_network_request`) is gone.
- The request stays open until its turn ends: the model has been called since it arrived (`_set_working`, LLM_REQUEST_PRE) and has been idle for 1 s (`settle_network_turn`, called every relay tick). A request that finds the model busy waits for that chain to end first; an acknowledgement or no-model request ends at once; one that no turn handles is dropped at 600 s.
- The runtime then sends the requester one end-of-turn frame on the thread, through the same hub_msg router: optional wire field `turn_end {replies, failed}` (strict in `validate_message`), `RelayAgentBridge.send(turn_end=)`. `_deliver_open_message` intercepts it on the requester: it goes to the waiting shell only, never displayed, never given to the model. A failed turn (`qp.last_turn_error`) sends a fixed text, no error detail.
- Requests are handed to the model one at a time in arrival order: `_tick` holds the next open-trust delivery while `network_turn_open()`.
- Dedup of identical hub_msg text now includes the request thread (same words on two requests are both sent); a refused or deduped reply is not counted.
- Shell side: `network_send` streams frames (`network_reply` per message, then `network_done` / `network_timeout` / `network_sent` / `error`). `request_network_send(..., on_reply=)` returns the terminal frame. `kollab --hub msg` prints each reply as it arrives (flush), exits 0 on `network_done` (prints "<handle> finished without a reply" if none), 1 on a failed turn (fixed error text), 1 on the existing timeout message ("did not finish within" if replies had come). The waiter finishes only when the end frame AND every reply it counts have arrived (an end frame can overtake a retried reply).
- Related change (small, flagged): a reply on a thread a shell is waiting for no longer wakes the asking agent's model (`_decide_hub_wake`, "answer to a shell request"). Without it each reply started a model turn on the Mac and the next frame (and the shell's exit) waited behind it, because the relay delivers only while the model is idle. It is still shown on screen and queued for the model's next turn. One `if` to revert.

Files changed:
- plugins/hub/plugin.py, plugins/hub/relay_agent.py, plugins/hub/relay_conversations.py, plugins/hub/messenger.py, kollabor/cli.py
- tests: tests/unit/test_hub_network_turns.py (new, 31 tests: responder unit tests, wire validation, 5 tests over the two real bridges), tests/unit/test_hub_network_cli_requests.py (rewritten for streaming), tests/unit/test_hub_msg_remote_target.py, tests/unit/test_hub_cli_messages.py
- tests/live/m1/proof.sh: new `s3-overlap` step (two `kollab --hub msg` at once, hostname ask + disk ask, each must print only its own answer, both exit 0, server shell ran twice more); tests/live/m1/README.md line. NOT run live. The block was run offline against a fake `kollab` (good / crossed / exit-1 modes: PASS / FAIL / FAIL as intended) and `bash -n` passes.
- docs: docs/specs/agent-network-simple-flow.md section 7 (replaces "oldest unanswered request first"), docs/reference/commands.md, docs/guides/connect.md
- CHANGELOG.md + kollabor/updates/CHANGELOG.md: one Fixed line each, `cmp` byte-identical.

Tests (KOLLAB_NO_KEYRING=1 /Users/malmazan/dev/kollab/.venv/bin/python -m pytest, from the worktree root):
- Full `tests/unit/ -q` (run once, after all Python changes, before the docs/proof/changelog edits): 4944 passed, 7 skipped, 203 subtests passed in 83.6 s.
- After the changelog edit: touched modules + test_bundle_packaging_metadata: 95 passed.
- ruff (venv ruff) on all touched .py files and tests: All checks passed.
- Mutation check (14 one-line mutations of the new logic: binding, gate, no-wake rule, end-frame intercept, settle conditions, dedup key, reply count, announced-reply wait, failed detection, deferred, skipped, max-age, debounce): all 14 caught by the new tests.
- Not re-run after the final commit: the full suite (no Python changed since the full run).

## Not done / unverified

- No live proof (main session runs it). Both machines must run this build: an old responder never sends the end frame, so a new shell would wait its 600 s and exit 1.
- The overlap step in proof.sh has only been checked against a fake kollab, not the real two-machine run.
- The end-of-turn signal is a 1 s idle debounce on `llm.is_processing`, not an event from the queue processor (which has no turn-end event on the hub-continue path). Live check: a slow tool call or a human-input hand-off must not end the turn early.
- A model that answers in plain assistant text instead of hub_msg sends nothing, so the shell prints "<handle> finished without a reply" and exits 0 (before: hung to the timeout). Whether the runtime should forward such a final text is a product call for Marco; not changed.
- Follow-up worth a look (not done): each reply to a shell request is still shown in the asking agent's window as a normal hub message; the relay's `MAX_AGENT_ACTIVE = 8` queue now sees up to 3 rows per request (interim, answer, end frame) if the asker is busy.

## Exact next step

Main session: build wheels from d53a8a1 (or the merged branch), run `bash m1/install_both.sh <wheeldir>` then `bash m1/proof.sh` and read the `s2-*`, `s3-*` (including new `s3-overlap`) and `z*` rows. If `s3-overlap` fails with "waits out its 300s limit", the end frame did not arrive or was refused. There is no success log line for it; only a debug line on the server when the end frame is refused or cannot be sent ("network turn end for <handle> ..."). Look there first, then at `conversations.sqlite3` rows for the thread.

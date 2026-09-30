# fix-hub-msg report

Branch: `worktree-agent-a7e3ce79f64a05c1b` (worktree `/Users/malmazan/dev/kollab/.claude/worktrees/agent-a7e3ce79f64a05c1b`)
Base: `a4fcab8` (fast-forward confirmed). Commit: `d881b51` (one commit, `refs #121`, no attribution). Not pushed. `.venv` link removed before commit.

Tests: full unit suite `4654 passed, 7 skipped, 203 subtests passed` (was 4616 passing). 0 failed.
New/changed test files: `tests/unit/test_hub_msg_remote_target.py` (16, new), `tests/unit/test_altview_registration.py` (21, new), `tests/unit/test_hub_network_cli_requests.py` (20, reworked), `tests/unit/test_hub_network_surface.py` (2 lines).
The new tests were run against the pre-fix `plugin.py` / `contact_altview.py` and 18 of them failed there, as they should.
`ruff check` clean on every changed file. `black` run only on the two new test files and `test_hub_network_cli_requests.py`; `plugin.py`, `cli.py`, `messenger.py`, `contact_altview.py` and `test_hub_network_surface.py` were already not black-clean at HEAD, so I hand-formatted my hunks instead of reformatting them.

## Bug 1: false "not online" warning (fixed)

Producer: `_handle_hub_msg_tool` in `plugins/hub/plugin.py`. The `elif self._presence:` result branch asked local presence about a remote handle.
Fix:
- Success on a handle target: `sent to <agent@device>`. Set only after `_route_message` returned no rejection.
- Handle not on the roster: `_route_message` calls `relay.resolve_handle` (the existing producer), which raises `unknown agent@device: run /connect status to see who is online`. The tool result carries that wording verbatim. The misleading `send with force="true"` suffix is dropped for handle targets.
- Found in the same path and fixed: the router ignored the send receipt, so a peer that refused (`rejected`/`failed`/...) still gave "sent". New `_receipt_refusal()` turns a refusal into a rejection using the receiver's fixed reasons (`CONVERSATION_REJECTION_DETAILS`), never raw receipt fields. `hub_broadcast scope="network"` counts only accepted sends.
- `_route_message` no longer falls through to local presence when a handle arrives with no bridge; it returns `the network is not connected on this device: run /connect status`.
- Dedup: a failed handle send was cached before routing, so a retry after the peer appeared was swallowed as silent success. Handle sends are now cached only after the network accepted them. A repeated identical send inside 120 s now says `not sent again: this exact message to <handle> was already sent` instead of returning empty success (local targets keep the silent behaviour).
- The published tool result no longer carries `metadata["network"]` (a `relay:<key>:<workspace>:<agent>` address that went out on the `tool_result` semantic event). Found while adding the key-leak scan; the message keeps it internally.
- `[warn] requesting peer went offline before delivery` is not produced by any code: grep finds no such string. It is the receiving model's own paraphrase of the false tool warning (visible in `evidence-run3/s3-04-srv-pane.txt`). It goes away with the producer fix.
- Prompt: `bundles/agents/system/hub-collaboration.md` said nothing about offline or resend. Added two bullets: what `sent to`, the unknown wording, and the not-sent-again result mean; do not resend or run `hub_status` to confirm; answer a remote agent once.

Presence-check callers (`scan_all_presence`) and verdicts:
| # | Line (before edit) | Caller | Can `agent@device` reach it? | Verdict |
|---|---|---|---|---|
| 1 | 2876 | `_handle_hub_msg_tool` result | yes | Fixed (the bug) |
| 2 | 6219 | `_sender_has_active_task` | sender may be a handle | Internal bool, returns False when absent. No visible text. No change |
| 3 | 9615 | `_get_peer_by_identity` (capture, orchestrator lookup) | callers are `_handle_capture_command` and `_resolve_identity_to_agent_name`, both after `_remote_target_refusal` | Not reachable. No change |
| 4 | 9745 | spawn, online identity exclusion | `_handle_spawn_command` refuses handles first | Not reachable |
| 5 | 9799 | spawn, "already online" | same | Not reachable |
| 6 | 10013 | `/hub wake` | yes: printed `agent x@y not found` | Fixed: now refuses with `not allowed on a remote device; ask <handle> to do it` |
| 7 | 10138 | `/hub stop all` | "all" is local by definition; handles refused earlier | No change |
| 8 | 10195 | `/hub stop <name>` | `_handle_stop_command` refuses handles first | Not reachable |

(The brief said 7; there are 8 calls in `plugin.py` counting the `hub_msg` one.)
Other paths swept:
- `/hub msg` (`_handle_msg_command`, also the state-service path): same router. Fixed the `use force="true"` hint for handle rejections. Success text `sent to <target>` was already truthful.
- `@agent@device` mention (`send_user_message`): a handle fell to `error: agent '...' is not online and has no runnable pool identity`. Fixed: a handle goes through the router.
- `kollab --hub msg agent@device` (`_handle_network_send_request`): resolves and routes through the same router; rejection text is the fixed reason. Unchanged apart from Bug 2.
- `hub_broadcast` (`_handle_broadcast_command`, `scope="network"`): no warning text; count now excludes refused sends.
- Reply path on the receiving machine: same `_handle_hub_msg_tool`, fixed by the same change (test: answer goes out as `sent to`, carrying the request's thread).
- `hub_capture` / `hub_spawn` / `hub_stop` / `hub_restart`: already refuse handles.

## Bug 2: CLI prints the wrong reply (fixed)

Root cause: `_on_message_received` resolved a CLI waiter by thread id, else by falling back to "first message from that handle" (`_cli_waiters_by_handle`). The relay send path dropped the thread id, so the fallback was always the one used. Run 5 sent a duplicate Story 2 answer after the false warning; the next message from that agent resolved the Story 3 waiter. Overlapping requests also overwrote each other's by-handle entry.
Fix (both ends):
- `_route_message` handle branch now passes `thread_id` and `reply_to` to `relay.send`. The relay payload, owner RPC and store already carry both fields for open messages (validated as 32-hex), and `_deliver_open_message` already puts them on the receiver's `HubMessage`. No relay change was needed.
- The receiver records each inbound network request (`_note_network_request`, per handle, FIFO, 32 max, 600 s expiry). A `hub_msg` to that handle with no explicit thread takes the oldest owed request (`_take_network_request`) and sends its thread id as `thread_id` and its wire id as `reply_to`. The model never sees thread ids, so the runtime supplies them. Inbound messages that are themselves answers (`reply_to` set) are not recorded as owed.
- The waiter matches only on the request's own thread. `_cli_waiters_by_handle` is removed with no fallback (dev policy: no compat layers).
Verified by unit tests only: overlapping requests answered in either order; a late duplicate answer to request 1 arriving while request 2 waits does not resolve request 2; a full loop across two plugins (CLI request, answering agent's `hub_msg`, back to the waiter). Not verified on real relay hops or two machines.
Limits: a peer on a build without this change does not echo the thread, so the CLI waits to timeout rather than printing a wrong answer. If the answering model sends two messages for one request, the first takes the request's thread (an "on it" ack is what the CLI prints), the second starts a new thread. FIFO means an extra message from the model can be attributed to the next owed request.
Docs: `docs/reference/commands.md`, CLI/messenger comments, spec section 7 ("What the sender is told"), and both CHANGELOGs (byte-identical, checked with `cmp`) updated.

## Bug 3: ContactReviewAltView launch ERROR (fixed)

`plugins/altview/contact_altview.py`: `ContactReviewAltView(domain="kollabor.ai", on_load=None, on_decide=None)`, the ConnectScreenAltView pattern. With no `on_load` it is an empty inbox (`no pending knocks`); with requests but no `on_decide`, `a`/`r` show `knock review is not connected` and decide nothing. The class is `category="internal"`, so the integrator only reads its metadata and registers no command.
Swept: instantiated every `AltView` subclass in all 15 files under `plugins/altview/` with no arguments; `ContactReviewAltView` was the only failure. Test: parametrized over every class, plus real `discover_and_register_plugins` with a mock registry asserting zero ERROR records (restores `sys.modules` afterwards, since discovery re-executes plugin files under their real module names).

## Bug 4: status widget `+0` (not fixed here, by rule)

The visible `◈ koordinator* +0` is `render_hub` in `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py`, which reads `len(hub._roster)` (local peers). That file is under the off-limits `status/` directory. `get_status_line` in `plugins/hub/plugin.py` is a legacy text path the row does not use, so I left it. Two-line patch for whoever owns `status/`:
```python
peers = len(getattr(hub, "_roster", [])) + sum(
    1 for row in hub._remote_agent_rows() if row.get("online")
)
```
`_remote_agent_rows()` is a sync snapshot refreshed each turn (`_refresh_remote_agent_rows`) and never raises. Open: whether `+N` should mean "reachable agents" (local plus remote) or stay local-only with a separate remote count.

## Not done / caveats
- No live two-machine run and no tmux JSON spec; everything above is unit-level. The live proof still has to confirm: no warning in either pane, no resend, the second `kollab --hub msg` printing its own answer, and no ERROR line in the launch logs.
- The receipt state `queued` is identical for "queued locally because the transport failed" and "accepted by the peer", so `sent to <handle>` can mean "accepted for delivery". Fixing that needs a new receipt field in `relay_agent.py`; not attempted.
- `Done Gate` items 2 and 3 (live proof, 390/820 px) do not apply or are not met: no UI changed, no live host driven.

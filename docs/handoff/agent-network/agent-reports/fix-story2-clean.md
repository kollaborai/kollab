# fix-story2-clean report

Branch: worktree-agent-af17a66014ec41836 (fast-forwarded to 55cca30 first, confirmed)
Commit: afa383d  "Run one hub XML tag once, tell the asker a remote request's reply comes by itself, refs #121"
Not pushed. No PR, no issue. No remote host touched. `.venv` symlink removed before commit.

## Bug 1: one `<hub_msg>` tag ran twice

Root cause. `LLMService._register_registry_tool_tags` (kollabor/llm/llm_coordinator.py) bridges every
ToolRegistry tool into `ResponseParser._plugin_tags`. The app runs it in `_initialize_llm_core`
(kollabor/application.py) BEFORE `_initialize_plugins`. The hub plugin's
`_register_pipeline_tools` (plugins/hub/plugin.py) then called `register_plugin_tag("hub_msg", ...)`,
which only appended. The coordinator's "skip tags a plugin already registered" guard only works
when the plugin registers first, so in the real order both entries stayed. Both patterns match the
same text (the registry's generic `mixed` pattern and the hub's own), `_extract_plugin_tools` yields
two tools, and `get_all_tools`' semantic dedup does not merge them because the two extractors return
different dicts (`{to: 'to="..."', message}` vs `{target, content, ...}`). The queue processor ran
both. The dedup cache `_recent_hub_msgs` absorbed the second one, which is why the screen showed
`sent to ...` then `not sent again: ...`. Native tool calls never touch the tag parser, so they were
fine. Reproduced with a script against real parser + real coordinator method + real hub plugin
registration: core-first gave 2 tools, plugins-first gave 1.

Change. packages/kollabor-ai/src/kollabor_ai/response_parser.py, `register_plugin_tag`: registering a
name again replaces the earlier entry (`self._plugin_tags = [t for t in self._plugin_tags if
t["name"] != tag_name]` before the append). Keyed on name, not tool_type, because
`notifications` / `notifications_clear` legitimately share one tool_type. The dedup cache is untouched.
After the fix both start orders give 57 tags and no duplicate names or tool_types.

Test. tests/unit/test_hub_tag_single_execution.py (new, 23 cases). Real `ResponseParser`, real
`ToolExecutor.execute_tool`, real `HubPlugin` handler with a fake relay, real
`LLMService._register_registry_tool_tags`, in both start orders. Counts handler calls (1) and network
sends (1) for one tag; two different tags give two calls, not four; no name or tool_type registered
twice; sibling samples; the replace rule; the shared-tool_type rule. Before the fix, with the parser
change reverted, 6 cases failed (all `core-first`, plus the replace-rule test); after, all pass.

Siblings (41 hub tag names were registered twice in the real start order; all fixed by the same change):
- hub_msg: was double-executed (proved). Fixed.
- hub_broadcast: was double-executed (extractors differ: `{message}` vs `{content, force_attr}`). Fixed. Test.
- vault_write: was double-executed (same reason). Fixed. Test.
- hub_reply: not in the registry, never doubled. Test confirms one tool.
- hub_status, hub_stop, scratchpad, scratchpad_append: registered twice but both extractors returned
  the same dict, so `_deduplicate_tools` hid it; they passed even before the fix. Now registered once. Test.
- Remaining doubled-registration names (claims, context_query, crystal_*, curate, evict, feed_*, file_*,
  hub_agents, hub_capture, hub_claim, hub_cron_*, hub_queue, hub_spawn, hub_vault(s), hub_work, lane_*,
  scratchpad_get/clear, state_update, task_approve/checkpoint/complete/reject): not driven one by one.
  Whether each one actually ran twice depends on whether the two extractors' dicts matched. They are
  fixed structurally: the whole-parser uniqueness test asserts no repeated name or tool_type.
- Other registrants (agent_orchestrator, goals, env_queue): none of their names are in the registry, no doubling.

## Bug 2: the asker did not wait

Root cause. The `hub_msg` result for a new agent@device request was just `sent to <agent@device>`
(`_handle_hub_msg_tool`, plugins/hub/plugin.py). Every tool result triggers another model turn, and
nothing at that moment says the reply comes by itself, so the model polled `hub_status`, tried
`hub_capture`, then sent a second message. The two prompt bullets from d881b51 were not enough.

Change. plugins/hub/plugin.py, `elif handle:` branch. When no thread_id / reply_to is set after
`_take_network_request` (so it is a new request, not an answer on a received thread), the result is:

`sent to <agent@device>; its reply arrives by itself as a hub message. end your turn unless you have other local work, and do not check status, capture, or send again.`

An answer on a received request thread stays exactly `sent to <agent@device>`. The slash command,
CLI and @mention paths are unchanged. The same handler serves native and XML calls. The refused-capture
wording `not allowed on a remote device; ask <agent@device> to do it` is untouched. The prompt bullet
in bundles/agents/system/hub-collaboration.md was aligned (end your turn, no `hub_capture`).
docs/specs/agent-network-simple-flow.md and both changelogs (byte-identical, `cmp` clean) updated.

Tests. tests/unit/test_hub_msg_remote_target.py: four new tests (exact new-request text and no
key/receipt/relay leak; answer on a received thread is plain `sent to` and carries thread_id/reply_to;
the answer is owed once so the next message is a new request; local target unchanged). Three old
assertions that pinned `== "sent to PEER"` for new requests now use `startswith`.

## Decision on stopping the turn in the runtime: NOT implemented

- `wait="true"` does not stop the sender's turn today. The handler computes `any_wait` and writes
  `metadata["wait"]` on the outbound message, and nothing in the repo reads it (grep of plugins,
  packages, kollabor). The queue processor sets `turn_completed = False` after any tool result
  (`_tool_results_requiring_followup` returns every result). So "stop like wait=true" would mean new
  plumbing, not reusing something.
- A runtime stop cannot tell whether the agent has other work. It would cut off "ask the server, and
  meanwhile do Y locally", and "ask A then ask B" in successive turns (B would wait for A's reply).
  A batch check (only stop when every tool in the response is a new remote request) does not fix the
  cross-turn cases. Per the brief, that is unsafe, so the runtime is unchanged and the result text
  carries the instruction.

## Test counts (exact)
- Baseline stated in the brief: 4708 passed.
- After the change: `pytest tests/unit -q` -> 4731 passed, 7 skipped, 203 subtests passed (70.5s).
- Targeted: test_hub_tag_single_execution + test_hub_msg_remote_target + test_tool_auto_enrollment +
  test_connect_no_keys_on_screen -> 58 passed, 134 subtests passed.
- `ruff check` on the four changed py files: my files clean. plugins/hub/plugin.py and response_parser.py
  reported no new violations.

## Not done / caveats
- No live two-machine proof: no real model or remote host here. Both fixes are proven at the parser,
  executor and handler level only. A live rerun of the uname/uptime scenario is still owed.
- Whether the model obeys the result text is not proven; it is a strong nudge, not a guarantee. The
  `wait` attribute being a no-op in the runtime is a separate finding, not fixed here (the prompt still
  documents `wait="true"` as "send, then STOP" in CLAUDE.md and the hub_msg tool definition).
- The registry's generic hub_msg entry used to act as an accidental fallback for attribute orders the
  hub pattern does not accept (e.g. `kind` before `wait`). With one entry per name only the hub pattern
  matches. That fallback produced a garbled target anyway, so no working behavior is lost, but a hub
  pattern that accepts any attribute order would be a fair follow-up.
- The venv is an editable install of the main checkout: pytest picks up the worktree via pyproject's
  relative `pythonpath`, but a bare `python script.py` needs PYTHONPATH set to the worktree's packages.
  I used a wrapper in the scratchpad for the repro scripts.

## Files changed
- packages/kollabor-ai/src/kollabor_ai/response_parser.py
- plugins/hub/plugin.py
- tests/unit/test_hub_msg_remote_target.py
- tests/unit/test_hub_tag_single_execution.py (new)
- bundles/agents/system/hub-collaboration.md
- docs/specs/agent-network-simple-flow.md
- CHANGELOG.md, kollabor/updates/CHANGELOG.md

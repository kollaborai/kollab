# fix-hub-tags report

Branch: worktree-agent-ad091aec23fd222a9  (worktree /Users/malmazan/dev/kollab/.claude/worktrees/agent-ad091aec23fd222a9)
Commit: ec9d300 on top of 564a63d (one commit, refs #121, no attribution). Not pushed.
Base note: built on 564a63d as you approved (afa383d was stale).

## Task 1: hub tag attributes in any order

Before the fix (checked with the real ResponseParser + the hub's own registration):
- `<hub_msg wait="true" to="lapis">hi</hub_msg>` was NOT run, NOT stripped, and shown raw in the reply. Same for single quotes and `kind` before `thread`. Same for 8 more tags when shuffled: hub_reply, task_snooze, hub_spawn, hub_capture, crystal_search, crystal_list, curate, hub_ask_ctx. hub_broadcast's `scope` never matched at all. crystal_edit/crystal_delete were order-free but double-quote only.
- Why: the one registered regex both extracts and strips (`ResponseParser._clean_content` re-uses it). `_parse_hub_messages` only strips `<wait_for_user>`, so there is no second strip path. No match = no run and no hiding.
- `_HUB_MSG_EMBEDDED_ATTRS_RE` (native call with attribute text jammed into `to`) had the same fixed order to>wait>force. `to='wait="true" to="lapis"'` fell through as a literal identity: misrouted, `wait` lost. Deleted; it now uses the same helper.

Fix:
- New `plugins/hub/xml_tags.py`: `tag_pattern(name, required, valid, end)`, `tag_attrs(text)`, `embedded_attrs(name, text)`. Any order, "double" or 'single' quotes.
- Converted 12 of the 46 tags `_register_pipeline_tools` registers (brief said ~41, log line says 43): hub_msg (6 attrs), hub_reply, hub_broadcast (force + scope), task_snooze, hub_spawn, hub_capture, crystal_search, crystal_list, crystal_edit, crystal_delete, curate, hub_ask_ctx.
- Left alone, 34 tags with 0 or 1 attribute: hub_stop hub_status hub_restart scratchpad scratchpad_append scratchpad_clear scratchpad_get state_update task_checkpoint task_complete task_approve task_reject lane_claim lane_release file_changed file_watch file_unwatch feed_recent feed_file claims hub_agents hub_queue hub_claim hub_work hub_vault hub_vaults hub_cron_add hub_cron_list hub_cron_delete vault_write global_vault_write crystal_read context_query evict. A test fails if a new tag is not put in one of the two lists.
- Extractors return the same keys and values. Required attributes stay required (no `to` = no match = raw, as now). Bad numeric/enum values still do not match (`limit="abc"`, `decision="drop"`). Empty `query=""` still matches.
- Proof of "same as today": old-vs-new differential fuzz, 150,000 well-formed inputs, 0 differences (only widenings); 200,000 embedded-`to` cases, 0 differences; garbage input never raises, worst 0.5 ms. The fuzz leaves out cases where the old code was wrong: `>` inside a crystal_edit value, single-quoted crystal attributes, an unclosed tag followed by another tag. Found and fixed one O(n^2) case in the embedded reader on the way.
- Deliberate widenings (all were raw text before): unknown attributes are ignored, unquoted values (`to=lapis`) work, `>` inside a quoted value works, and `<hub_broadcast scope="network">` now parses (spec + prompt document it; the old pattern never matched it). crystal_edit/crystal_delete now read single quotes.
- Safety: a stray `<hub_msg` in prose is not swallowed with the real tag after it (`<` is not allowed outside quotes).
- Embedded recovery still only accepts to/wait/force (kind/thread_id in `to` stays a literal target and fails, as before).

## Task 2: wait="true" ends the turn

Root cause:
- `wait` was only copied into `metadata["wait"]` (message and tool result). Nothing read it.
- The seam that decides "call the model again" is `_tool_results_requiring_followup` + Step 10 in `QueueProcessor._execute_llm_turn_inner`. It returned every result and forced `turn_completed = False` after any tool, so LOOP 2 / `_hub_continue` called the model again after every send.

Fix (no new layer):
- hub handler sets `metadata["end_turn"] = True` on a successful send with wait (explicit, auto-wait phrases "standing by", "waiting for", "going quiet", "staying quiet", a JSON `true` from a native call, and a deduplicated repeat). The flag is on the tool result only, not on the message sent to the peer.
- `_tool_results_requiring_followup` returns [] when a result has `end_turn is True` and nothing in the batch failed; Step 10 then sets `turn_completed = True`. One seam covers XML tags, native tool calls and hub_reply.
- Other tools in the reply still run. Kept going (model sees it): rejected send, "not online" warning (message went nowhere), any failed tool in the same batch.
- Judgement calls to check: the "not online" warning and "any failed sibling" rules go beyond the brief's "rejected send continues". Say if you want them off.

## Files (10)
- plugins/hub/xml_tags.py (new), plugins/hub/plugin.py
- packages/kollabor-agent/src/kollabor_agent/queue_processor.py, .../tool_definitions/hub.py (wait wording only)
- tests/unit/test_hub_xml_tags.py (new, 100 tests), tests/unit/test_hub_wait_ends_turn.py (new, 39 tests), tests/unit/llm/test_queue_processor.py (+3)
- Docs, only where wait/attributes text was now incomplete: CLAUDE.md, bundles/agents/system/hub-collaboration.md, docs/plugins/development.md (one tip on `end_turn`). The spec was already right; not touched.
- Untouched: core_widgets.py, layout_manager.py, test_voice_plugin_lifecycle.py, CHANGELOGs.

## Tests
- `KOLLAB_NO_KEYRING=1 /Users/malmazan/dev/kollab/.venv/bin/python -m pytest tests/unit/ -q` : 4873 passed, 7 skipped, 0 failed (203 subtests), 75 s.
- New tests fail on the old code: 39 of 98 tag tests against the old plugin.py; 19 of 34 wait tests against the old queue_processor.py (21 of 36 against the old plugin.py). Swapped in, run, restored byte-for-byte.
- Wait tests drive the real ResponseParser + ToolExecutor + hub handler + QueueProcessor: explicit wait stops, auto-wait stops, no wait continues, rejected send continues (XML and native, hub_reply, agent@device, other tools still run).
- ruff clean on every touched file; black clean on the new files.

## Not verified / flags
- No live run (no kollab process, no LLM, per brief). "The reply that arrives later starts the next turn" is the existing `_hub_continue` path, unchanged and not re-proven here. This needs the two-machine proof.
- No tmux spec (CLAUDE.md asks for one): it needs a live model + hub.
- Pre-existing, not touched: `tests/test_hub_roster_inject.py::test_malformed_roster_update_does_not_poison_roster` fails when run on its own (AttributeError: the file never imports `unittest.mock`). It is outside tests/unit. One-line fix `import unittest.mock`.
- Found, not fixed: hub_cron_add's handler reads `target`, but the XML tag and the native tool never pass it, so a reminder always targets self; spec section 7 says it may target agent@device. Marco's call.
- Single-attribute tags (task_complete etc.) still take double quotes only.
- CHANGELOG untouched to avoid merge conflicts. Suggested Fixed lines (both files, byte-identical):
  - "Hub XML tags take their attributes in any order and either quote style: `<hub_msg wait="true" to="lapis">` is run and hidden instead of left on screen as raw text, and `<hub_broadcast scope="network">` parses."
  - "`wait="true"` on `hub_msg` / `hub_reply` (and the idle phrases that set it) now ends the sender's turn once the send succeeds; a rejected send, a send to nobody, or a failed tool in the same reply keeps the turn going."

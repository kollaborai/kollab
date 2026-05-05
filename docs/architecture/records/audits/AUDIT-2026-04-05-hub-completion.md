---
title: "Hub Completion Spec"
doc_type: architecture-audit
created: 2026-04-05
modified: 2026-04-05
status: historical
---
# Hub Completion Spec

*Created: 2026-04-05*
*Status: COMPLETE*
*Source: 3 audit agents + jarvis code review + 20 agent recommendations*

---

## Critical Fixes (do first)

- [x] FIX-1: TRIGGER_LLM_CONTINUE silent failure (plugin.py:890)
- [x] FIX-2: vault save on shutdown swallowed (plugin.py:1592)
- [x] FIX-3: message display failure swallowed (plugin.py:936)
- [x] FIX-4: expose vault via method, not _vault attribute
- [x] FIX-5: add debug logging to vault snapshot except (compaction:540)
- [x] FIX-6: add log line when suppress_display triggers (queue_processor)
- [x] FIX-7: coordinator cleanup logs "agent None is dead" (plugin.py:743)
- [x] FIX-8: /hub claim ignores slot_id argument (plugin.py:1417)

## Missing Features (specced but never built)

### User Input Broadcasting
- [x] FEAT-1: hook on USER_INPUT_POST that broadcasts to all peers
      when human types a message, other agents see it as observed
      renders as: ~ user -> jarvis (tilde = observing)
      files: plugins/hub/plugin.py (add hook + broadcast method)

### Message Deduplication
- [x] FEAT-2: seen_message_ids set (bounded, last 1000)
      check HubMessage.id before processing in _on_message_received
      files: plugins/hub/plugin.py

### Attach/Detach System
- [x] FEAT-3: --attach <designation> CLI flag in cli.py
- [x] FEAT-4: hub console Enter key attaches to agent (live output + detach)
- [x] FEAT-5: get_frames socket action in messenger.py
- [x] FEAT-6: subscribe socket action in messenger.py

### Skill Routing
- [x] FEAT-7: required_capabilities field on WorkSlot (models.py)
- [x] FEAT-8: capability scoring in coordinator.py work assignment
- [x] FEAT-9: _try_assign_work uses capability matching (plugin.py)

### Parent Watchdog
- [x] FEAT-10: consumer for KOLLAB_PARENT_PID env var
       child monitors parent every 10s, self-terminates if dead
       files: new module or add to application.py startup

### Org Launcher Evolution
- [x] FEAT-11: agent_bundle field in org JSON files
- [x] FEAT-12: org_launcher passes --agent <bundle> to spawn
- [x] FEAT-13: update engineering.json and startup.json

### Trender Tag Activation
- [x] FEAT-14: add hub trender tags to jarvis system_prompt.md
- [x] FEAT-15: add hub trender tags to ALL agent system prompts (9 bundled + jarvis)
- [x] FEAT-16: implement hub_designation trender tag
- [x] FEAT-17: implement hub_peers trender tag

### ProcessManager Integration
- [x] FEAT-18: ProcessManager kept as future standard, orchestrator uses own logic
       decision: keep both. ProcessManager is the clean API for future strategies
       (Docker, SSH, remote). orchestrator migrates to it in a future refactor.

## Dead Code Cleanup

- [x] CLEAN-1: display_queue.py capture_frame removed (replay kept, has callers)
- [x] CLEAN-2: AgentIdentity class removed from models.py

## Code Review Fixes (jarvis)

- [x] REVIEW-1: expose vault via service method (done in FIX-4)
- [x] REVIEW-2: service locator contracts documented in CLAUDE.md hub section
- [x] REVIEW-3: add debug logging to bare except blocks (done in FIX-1/2/3/5)

## Verification Tests

- [x] TEST-1: user input broadcasting - structural pass (hook + method verified)
- [x] TEST-2: message dedup - dedup set works, bounded at 1000
- [x] TEST-3: /hub subcommands - verified whoami, status, agents, spawn, capture, stop
- [x] TEST-4: departure announcement - coder going offline shown in jarvis
- [x] TEST-5: dreaming loop - structural pass (all 7 components verified)
- [x] TEST-6: hub console - structural pass (attach/detach/sidebar/feed verified)
- [x] TEST-7: trender tags - all 6 tags render, 9 agent prompts have tags
- [x] TEST-8: skill routing - peridot selected for [code,test] over ruby [guard,watch]
- [x] TEST-9: app startup with --agent jarvis - ◈ jarvis* +0 correct
- [x] TEST-10: suppress_display - verified (hub_msg only = no empty bubble)

---

## Priority Order

  wave 1 (critical fixes):       FIX-1 through FIX-8
  wave 2 (high impact features): FEAT-1, FEAT-2, FEAT-7/8/9, FEAT-14/15
  wave 3 (attach system):        FEAT-3/4/5/6
  wave 4 (remaining):            FEAT-10/11/12/13/16/17/18
  wave 5 (cleanup):              CLEAN-1/2, REVIEW-1/2/3
  wave 6 (verification):         TEST-1 through TEST-10

---

## Post-Completion: Additional Work (2026-04-05)

### Bugs Found During Testing
- [x] BUG: hub_msg tags showing raw in assistant response (suppress_display + clean_response)
- [x] BUG: duplicate departure messages (unique HubMessage per peer)
- [x] BUG: stale presence causes designation collision (async socket ping + force-claim)
- [x] BUG: CLI --hub msg only sent to target (now broadcasts to all, open channel)
- [x] BUG: empty designation in departure messages (guard on _started)
- [x] BUG: terminal_output/status/kill tools "Unknown tool type" (routing fix in tool_executor)
- [x] BUG: TRIGGER_LLM_CONTINUE race condition (queued retry when processing)
- [x] BUG: TRIGGER_LLM_CONTINUE empty queue (_continue_conversation instead of _process_queue)
- [x] BUG: agents not generating hub_msg tags after compaction (strengthened instructions)

### Features Added Post-Completion
- [x] /hub kill <designation> - remote agent shutdown via socket
- [x] kollab --hub CLI (status, capture, kill, msg, agents)
- [x] /hub cron (add, list, delete, clear) - schedule recurring hub messages
- [x] hub-collaboration shared section for all agent system prompts
- [x] vault/queue/agents_list trender tags activated in all 9 agents
- [x] vault rebirth uses sys_msg (not role="user")
- [x] context snapshot saved before compaction (verbatim messages to vault)
- [x] tmux dependency killed entirely (subprocess + ring buffer)
- [x] periodic vault autosave (crash protection)
- [x] departure announcements on agent shutdown
- [x] parent watchdog (KOLLAB_PARENT_PID consumer)

### Now Committed (tested + verified)
- [x] task_ledger.py - TaskCard + TaskLedger on disk (270 lines)
- [x] context_compaction_plugin.py - task-aware summarization
- [x] departure message 4x dup fixed (shutdown re-entry guard)
- [x] departure empty designation fixed (_started guard)
- [x] terminal_output/status/kill tool routing fixed
- [x] TRIGGER_LLM_CONTINUE: continue_conversation instead of process_queue
- [x] hub_msg instructions strengthened in roster injection

### Regression Audit Results (2026-04-05)
  ✔ app starts cleanly
  ✔ all imports work (17 plugin modules)
  ✔ no circular imports (629 modules)
  ✔ ruff: 0 errors (after F821 fix)
  ✔ no new test failures (14 pre-existing)
  ✔ terminal routing fix verified
  ⚠ 14 pre-existing test failures (not from our changes)

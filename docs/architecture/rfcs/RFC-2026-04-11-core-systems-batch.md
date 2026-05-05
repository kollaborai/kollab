---
title: "RFC Batch: 2026-04-11 Core Systems"
doc_type: architecture-rfc
created: 2026-04-11
modified: 2026-04-11
status: historical-index
---
# RFC Batch: 2026-04-11 Core Systems

This index covers five RFCs authored during the 2026-04-11 core-systems
design session. They describe interconnected changes that were intended
to be implemented together, or at least in a coordinated order, because
they share mechanisms and conventions.

## Reading order

Read the specs in this order. Each one references earlier ones.

1. **[RFC-2026-04-11-hub-loop-prevention.md](RFC-2026-04-11-hub-loop-prevention.md)** —
   Adds the `<wait_for_user/>` tag, loop detection, and the
   coordinator-breakthrough cooldown. This is the smallest spec
   and the one that ships the fastest. Start here because it
   defines vocabulary (presence states, loop detection, cooldown)
   that the other specs reference.

2. **[RFC-2026-04-11-unified-tool-loading.md](RFC-2026-04-11-unified-tool-loading.md)** —
   Rewrites how tool definitions are stored and how they flow into
   agent system prompts. One source of truth generates native JSON,
   XML documentation, and the response parser regexes. Agent bundles
   declare which tools they have, and mid-session tool grants inject
   the relevant system prompt section as a user message.

3. **[RFC-2026-04-11-agent-notification-system.md](RFC-2026-04-11-agent-notification-system.md)** —
   A queue of pending notifications (permission changes, new files
   in context, peer agent events, etc.) that gets rendered as a
   mission-control dashboard at the top of every user message. Uses
   the user-message injection convention established by the hub-loop
   spec.

4. **[RFC-2026-04-11-context-service.md](RFC-2026-04-11-context-service.md)** —
   The largest spec. A content-addressable context ledger that
   dedupes file reads, tracks heavy tool results, and lets agents
   curate what stays verbatim vs what gets replaced by an
   agent-written summary at compaction time. Uses XML tags
   (`<curate>`, `<context/>`, `<evict>`) consistent with kollabor's
   default XML tool-calling protocol.

5. **[RFC-2026-04-13-context-service-phase-d-hub-bridge.md](RFC-2026-04-13-context-service-phase-d-hub-bridge.md)** —
   Extracted from the context service spec's deferred phase D.
   Multi-agent hub bridge that shares ledger metadata across peers,
   detects divergent file versions, and supports cross-agent context
   queries via `<hub_ask_ctx>`. Depends on phases A-C, the
   notification system, and hub-loop-prevention being implemented
   first.

## What's shared across these specs

All four specs share these conventions:

### XML-first tool calling

kollabor's default mode is XML tags in assistant content, not
native openai `tool_calls`. All examples show XML unless explicitly
labeled "native mode." See `docs/architecture/tool-calling-architecture.md` for
the full protocol reference.

File reads use `<read><file>path</file></read>`, not
`<file_read path="..."/>`. Terminal commands use
`<terminal>cmd</terminal>`. Tool results come back as user-role
messages with a literal `Tool result: [tag_name] <content>` prefix.

### The injection envelope

Several specs inject messages into the conversation as user-role
messages with bracketed prefixes:

- `[system: hub command results]` — hub plugin's result injection
- `[context service]` — context service ledger queries
- `[context service: curator]` — context service curator prompt
- `[notification queue]` — agent notification system dashboard

The bracketed prefix is a convention the static system prompt teaches
the model: messages beginning with `[` followed by a known source
name are runtime machinery, not human input. Agents treat them as
system reminders regardless of the role being `user`.

Injections are **ephemeral unless noted** — they appear in the next
request body, then get discarded from conversation history after
the request completes. This preserves prefix cache.

### Presence state

The hub-loop spec introduces a new presence state:

- `active` — agent is working, can be re-invoked by any mechanism
- `waiting` — agent emitted `<wait_for_user/>` and refuses all
  non-coordinator re-invocations until the cooldown expires or a
  coordinator breakthrough arrives

Later specs reference this state machine. The context service respects
it (doesn't inject curator prompts to waiting agents). The notification
system respects it (queues notifications for waiting agents to see
when they wake up). The unified tool loading respects it (tool grants
don't wake a waiting agent unless flagged urgent).

### Haiku-implementable detail

All four specs are written at the detail level needed for a small
model (haiku or a junior coder) to implement without ambiguity.
That means:

- Absolute file paths for every new or modified file
- Function signatures with type hints
- Regex literals copied verbatim
- Before/after code snippets for non-obvious transformations
- Explicit phase ordering with dependencies called out
- "Do not modify" lists for files the implementer should leave alone
- Open questions with **explicit recommendations + fallbacks** so
  the implementer can proceed even without additional input

Each spec is a full walkthrough including end-to-end examples
showing agent input, tool result output, state transitions, and
the effect on conversation history.

## Session context

The specs in this folder were written after a long debugging session
that uncovered several production issues:

- **Runaway token usage** on a chronos-crown session (2026-04-11
  raw jsonl showed a single 254 KB dead-code scan tool result being
  replayed through ~20 subsequent requests, costing ~$0.76 on
  grok-4.1-fast)
- **Compaction never firing** because openrouter streaming responses
  return token_usage=0, which means the compaction plugin's
  prompt_tokens gate always fails
- **Agent deadlock** when emitting `<hub_msg>` as the only action in
  a turn — the handler was silent on success, so no `cmd_results`
  entry was appended, no injection fired, no force_continue, and
  the agent's turn ended cold without actually starting the
  promised work. Fixed in commit 3fb8dc1 on 2026-04-11.
- **Agent-to-agent loops** where two agents would ping-pong "are you
  done?" / "yes i'm done" with no terminal state primitive to stop
  them. Agents have explicitly complained that there's no way for
  them to end their own session.

These four specs together address all of these issues. The
context-service spec addresses the token usage and compaction gate
problems. The hub-loop spec addresses the agent-to-agent looping.
The unified-tool-loading and agent-notification specs don't address
immediate production issues, but they clean up architectural debt
that will make the other two specs easier to implement correctly.

## Implementation order recommendation

Suggested sequence for bringing these to production:

1. **Phase A (critical, must-ship fixes):**
   - openrouter streaming usage capture (one-line fix in
     openrouter_provider.py — unblocks compaction gate 1)
   - tool result hard cap (config: `tool_result_cap_kb`, default 32)
   - the silent-tag hub fix (already committed: 3fb8dc1)

2. **Phase B (hub loop prevention):**
   - Implement RFC-2026-04-11-hub-loop-prevention.md in full
   - This is self-contained and unblocks agent development

3. **Phase C (notification system):**
   - Implement RFC-2026-04-11-agent-notification-system.md
   - The hub loop spec will have already established the injection
     envelope convention, so the notification system can build on it

4. **Phase D (unified tool loading):**
   - Implement RFC-2026-04-11-unified-tool-loading.md
   - Requires touching more files but doesn't block anything urgent

5. **Phase E (context service):**
   - Implement RFC-2026-04-11-context-service.md
   - The biggest piece of work. Depends on the notification system
     being in place (context events go through the notification queue).

6. **Phase F (context service hub bridge):**
   - Implement RFC-2026-04-13-context-service-phase-d-hub-bridge.md
   - Multi-agent context sharing. Depends on phases A-E being fully
     operational (context service + notification system + hub-loop).

Phases A and B are the only ones that address active production
bugs. C, D, E are architectural improvements that make future work
easier but are not time-critical.

## Do not modify list (for implementers)

These files are out of scope for ALL four specs and must not be
touched by any implementer working from these specs:

- `kollabor/llm/llm_coordinator.py` — only add methods, do not
  refactor existing ones
- `kollabor/application.py` — core lifecycle, no changes
- `packages/kollabor-events/src/kollabor_events/` — event system
  is stable, no new events unless a spec explicitly requires one
- `packages/kollabor-tui/src/kollabor_tui/` — terminal rendering
  is not part of these specs (except config widgets which have
  a well-defined contract)

If a spec requires changes outside its scope, it calls them out
explicitly in an "Impact on other systems" section.

## Related existing docs

- `docs/architecture/tool-calling-architecture.md` — reference doc for kollabor's
  XML + native tool calling protocols. Read this first if you've
  never worked on kollabor's tool system.
- `docs/features/context-service.md` — the original (pre-XML-fix)
  version of the context service spec. Superseded by the version
  in this folder. Kept in place for backward compatibility with
  existing cross-references.
- `docs/architecture/rfcs/RFC-2026-04-13-context-service-phase-d-hub-bridge.md` —
  extracted phase D from the context service spec. Multi-agent hub
  bridge for cross-agent context metadata sharing.
- `CLAUDE.md` — project-wide conventions
- `plugins/hub/plugin.py` — reference for how existing XML tag
  handlers are structured. New tag handlers should match this style.

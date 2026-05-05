---
title: "Killing the Dual Pipeline: How We Unified Tool Execution in Kollabor"
created: 2026-04-12
modified: 2026-04-12
status: active
---
# Killing the Dual Pipeline: How We Unified Tool Execution in Kollabor

*April 12, 2026 -- Kollabor Team*

---

There's a kind of code smell that doesn't show up in lint. It shows up when you're reading a method at 2 AM and realize you've seen this exact block before -- twice -- and both copies are wrong in different ways.

The method was `_execute_llm_turn` in `queue_processor.py`. Nine hundred and twenty-four lines. Two completely separate code paths living inside one try block, joined by an if/else that should have been a red flag from day one.

## The Problem Nobody Named

Kollabor has two ways for an LLM to invoke tools. The first is native function calling -- the API returns structured tool call objects with IDs, names, and arguments. Clean, typed, provider-supported. The second is XML tags embedded in the response text -- `<terminal>ls -la</terminal>`, `<file_edit>`, `<hub_msg to="lapis">`. Homegrown, regex-parsed, works with every provider regardless of tool calling support.

Both approaches are valid. Both are necessary. The problem was that they'd grown into entirely separate worlds.

Path A (the native path) lived at line 468. When the API returned tool calls, it extracted them, executed them in batch, formatted results in native API format, and returned early. Done. Never looked at the response text.

Path B (the XML path) lived at line 670. When the API didn't return native tool calls, it parsed the response text, extracted XML tools, executed them one at a time, formatted results as conversation messages, and displayed them incrementally. Different logging. Different history format. Different display strategy.

Both paths emitted the same events. Both paths did bridge relay. Both paths handled hub tag stripping. But each did it slightly differently, with subtly different log prefixes and formatting quirks. Copy-paste with drift.

And there was a gap. A silent, invisible gap.

## The Gap

When the LLM returned native tool calls, Path A executed them and returned early. It never called `response_parser.parse_response()`. It never looked at the response text for XML tags.

This meant: if the LLM returned native tool calls AND plugin XML tags in the same response, the plugin tags vanished. Silently. No error. No warning. Just gone.

For the hub plugin this was critical. An agent using native function calling to execute a file edit could also emit `<hub_msg to="sapphire">here's what I found</hub_msg>` in the same response. That hub_msg would never reach sapphire. The agent would think it sent a message. The message would disappear into the void.

We had 40 tags registered across the hub and agent_orchestrator plugins. Every single one was vulnerable to this silent drop.

## The Spec

We wrote a spec. `docs/specs/unified-tool-pipeline.md`. Five phases:

1. Infrastructure: `register_plugin_tag()` and `register_plugin_handler()` APIs
2. Hub plugin migration: 32 hub XML tags to pipeline
3. Agent orchestrator migration: 8 orchestrator tags
4. Queue processor unification: kill the if/else
5. SDK documentation

Phases 1-3 happened in earlier sessions. The tag registration APIs were clean -- plugins register regex patterns with the response parser and handler functions with the tool executor. The parser strips tags and extracts tool data. The executor routes to the right handler. Two calls in `initialize()`, done.

Phase 4 is where it got interesting.

## The First Attempt

I started by extracting shared helpers. Two methods: `_emit_llm_response_and_handle()` (the LLM_RESPONSE event emission and hub tag stripping) and `_bridge_relay()` (forwarding responses to external platforms like Telegram). Both were copy-pasted between paths with minor differences. Extracted them, called them from both paths.

Then I added `response_parser.parse_response()` to the native path specifically for plugin tag extraction. After native tool execution, the native path would also parse the response text, pull out plugin tools, and execute them. Targeted fix for the gap.

This was fine. Tests passed. Ruff clean. I committed it (e50064d).

But it was still two paths. The if/else at line 468 still branched. The native path still returned early. The code was better but the architecture was the same -- two worlds with a bridge between them.

The response was: "why would you even consider a partial implementation acceptable."

Fair point.

## The Full Unification

The structural challenge: the native path returns early because it needs to continue the conversation turn (the API expects tool results in a specific format on the next turn). The XML path doesn't -- it adds tool results as a batched user message to conversation history.

The insight: you don't need an early return. You need one branch point at the very end, for history format only. Everything else can be shared.

I wrote the unified method to a scratch file first. Syntax-checked it with `ast.parse`. Then swapped it in with a Python script (the edit was 386 lines -- too large for inline find/replace).

The new structure:

```
1. Extract native tool calls from API (if any) -> has_native_tools flag
2. response_parser.parse_response() ALWAYS runs
3. LLM_THINKING emission (merge native reasoning + XML thinking)
4. LLM_RESPONSE emission (shared helper)
5. Display clean text
6. Execute native tools (batch, if has_native_tools)
7. Execute XML tools (incremental + question gate, if any)
8. Bridge relay (shared helper)
9. Logging + history
   - Branch: native tools -> role=tool with tool_call_id (API requirement)
   - Branch: XML tools -> batched user message with format_result_for_conversation
10. Continuation logic
```

One path. One flow. The only structural branch is at step 9, and it's a data format difference, not a logic difference.

The result: 958 lines down to 876. Minus 247 lines net, plus 171 new lines. Same inputs, same outputs, same event emissions. 1,559 tests passing.

## The Bug Lapis Found

I shipped the unification commit and delegated code review to lapis (an agent on the hub -- yes, the agents review each other's code now). Lapis found a behavioral regression I'd introduced:

Old native path:
```python
self.message_display_service.display_complete_response(
    response=native_clean,
    tool_results=native_results,
    original_tools=original_tools,
)
```

New unified path:
```python
self.message_display_service.display_complete_response(
    response=clean_response,
    tool_results=None,  # <-- oops
)
```

Native tool results were computed but never displayed. The user would see the text response but not the tool outputs. A classic refactor bug -- I'd unified the display call and dropped the native-specific parameters.

Lapis also caught stray comments from my scratch file that leaked into the method header, and noted a subtle difference in what gets passed as `response_text` to the LLM_RESPONSE event (raw vs parsed -- low risk but worth knowing).

Fixed both. Amended the commit. This is why code review matters, and why having agents that can actually read diffs and reason about behavioral changes is useful.

## The Architecture Now

```
LLM API response
       |
       v
  Extract native tool calls (flag, not branch)
       |
       v
  response_parser.parse_response()  <-- ALWAYS runs
       |  strips: thinking, terminal, file ops, MCP, plugin tags
       |  returns: clean text + all tools sorted by position
       v
  Emit LLM_THINKING (merged native + XML)
       |
       v
  Emit LLM_RESPONSE (observation-only, no tag stripping)
       |
       v
  Display clean text (all tags already stripped)
       |
       v
  Execute native tools (batch, if any)
       |
       v
  Execute XML + plugin tools (incremental, if any)
       |
       v
  Bridge relay + logging + history
       |
       v
  Continue or complete turn
```

Plugins register their XML tags at init. The parser handles extraction and stripping. The executor routes to registered handlers. Nobody hooks into LLM_RESPONSE_POST for tag parsing anymore. LLM_RESPONSE is observation-only.

## What This Enables

The unified pipeline means:

- **Plugin tags always execute**, regardless of whether the LLM uses native function calling or XML. No more silent drops.
- **New plugins are easy**. Two registration calls in `initialize()`. One handler method. Done.
- **One code path to test**. Not two parallel worlds with subtle differences.
- **Consistent behavior**. Same events, same display, same logging, whether tools come from the API or XML.

The hub's 32 tags, the orchestrator's 8 tags, and any future plugin tags all flow through the same pipe.

## The Numbers

Commits across all 5 phases:

```
phase 1: infrastructure (register_plugin_tag, register_plugin_handler)
phase 2: 32 hub tags migrated (8 commits: 265da75..1174aef)
phase 3: 8 orchestrator tags migrated (e50f03a)
phase 4: queue_processor unification (3 commits: e922a13, e50064d, 5133dc3)
phase 5: SDK documentation (495d5dc)
```

queue_processor.py: 958 -> 876 lines (-82 net, -247 removed + 171 added)
Total tags in pipeline: 40 (32 hub + 8 orchestrator)
Tests: 1,559 passing, 0 failing
Linter: ruff clean

## The Lesson

Two code paths doing the same thing with different details is a tax. You pay it every time you touch either path. You pay it double when you need them to behave the same. And you pay it in bugs you can't see because the gap between the paths isn't visible in any diff.

The unified pipeline isn't more correct because it's cleaner. It's more correct because there's one place for the behavior to be. When we fix the display logic, we fix it once. When we add a new tool type, it goes through the same pipe. When lapis reviews the code, there's one path to reason about.

Sometimes the right refactor isn't extracting a helper method. It's deleting the branch.

---

*Kollab is open source under the MIT license. The unified tool pipeline is documented in `docs/plugins/development.md` through the `register_plugin_tag` and `register_plugin_handler` APIs.*

---
title: "ContextService"
created: 2026-04-11
modified: 2026-04-11
status: superseded
---
# ContextService

> ⚠ **SUPERSEDED — DO NOT IMPLEMENT FROM THIS FILE.**
>
> This spec was authored before the 2026-04-11 tool-calling
> architecture investigation. It assumes native openai `tool_calls`
> are kollabor's default tool protocol, which is WRONG —
> kollabor defaults to XML-in-content (see
> `docs/architecture/tool-calling-architecture.md`). The examples use
> `<file_read>` instead of the real `<read>` tag, reference
> `tool_call_id` instead of `message_uuid`, and show `role: "tool"`
> messages instead of the real `Tool result: [tag_name] <content>`
> envelope.
>
> **Use the new version instead:**
> [`docs/architecture/rfcs/RFC-2026-04-11-context-service.md`](../architecture/rfcs/RFC-2026-04-11-context-service.md)
>
> The new version fixes all the XML vs native mode issues, uses
> real kollabor tag names, and integrates with the hub-loop-prevention,
> notification system, and unified tool loading specs in the same
> folder. The core design (ledger, curation, stale hits, diff-on-change)
> is unchanged — only the surface syntax was wrong.
>
> This file is kept in place for backward compatibility with
> cross-references from other docs. Do not extend it. All future
> edits go to `docs/architecture/rfcs/RFC-2026-04-11-context-service.md`.

---

> Unified context ledger for kollab. Tracks every heavy artifact
> (file reads, tool results, attachments) that enters an agent's
> conversation. Deduplicates, versions by hash, shares across the hub,
> and drives curation-aware compaction.

status: superseded / not implemented
owner: kollabor-ai
depends on: conversation_manager, compaction plugin, hub plugin, response_parser
superseded_by: docs/architecture/rfcs/RFC-2026-04-11-context-service.md


## Why

Three problems showed up in the 2026-04-11 chronos-crown session:

1. A single `dead_code_scan` tool result dumped 254KB into history.
   Compaction never fired because the token gate reads zero from
   openrouter streaming responses AND because we didn't have 4 human
   turns yet. The 254KB got replayed on every subsequent request.
2. Agents reread the same files turn after turn with no awareness
   they already had them.
3. Hub peers working on shared files have no idea which peer has
   which version in context.

ContextService fixes all three by tracking every heavy artifact as a
first-class entry with id, hash, size, lifecycle, and curation state.


## Design principles

1. **Cache-hostile operations are explicit.** Nothing ContextService
   does rewrites the cached prefix unless the agent asks for it or
   compaction fires. Curation decisions live in memory, not in the
   system prompt.
2. **Agent-owned curation, system-owned bookkeeping.** The service
   tracks what exists and for how long. The agent decides what stays
   verbatim vs gets replaced by its own summary.
3. **Dedup by content, not by name.** A file at `plugins/hub/plugin.py`
   is identified by its `(path, content_hash)` tuple. If the hash
   matches a prior read, it's the same entry.
4. **Default behavior is cheap.** The agent gets the right answer
   without having to know ContextService exists: rereading an unchanged
   file returns a "already in context" marker, not the file contents.
5. **Force overrides always available.** Any agent can bypass dedup
   with an explicit flag when debugging or lost.
6. **Hub-shared when it makes sense.** Ledger entries can be broadcast
   to peer agents so the mesh knows who's holding what.


## Terminology

| term | meaning |
|------|---------|
| **heavy item** | any tool result or file read >= `heavy_threshold_kb` (default 8KB) |
| **ledger entry** | a tracked heavy item: `(ctx_id, kind, label, hash, size, msg_idx, decision, body)` |
| **ctx_id** | stable id assigned at entry creation, e.g. `ctx-1`, `ctx-2` |
| **curation** | the decision an agent makes about a ledger entry: `keep` or `summary` |
| **decision body** | the reason (if keep) or agent-written summary (if summary) inside the `<curate>` tag |
| **fresh read** | a file read where content has not been seen before, or hash differs |
| **stale read** | a file read whose content is already in context at the same hash |
| **diff read** | a file read that returns only the diff vs the version already in context |


## Architecture

```
packages/kollabor-ai/src/kollabor_ai/context_service/
    __init__.py
    service.py            ContextService singleton
    ledger.py             LedgerEntry dataclass + in-memory store
    file_tracker.py       path -> hash -> (ctx_id, size, last_read_at)
    curator.py            threshold detection + curator prompt rendering
    hash_utils.py         xxhash helpers (fast, non-crypto)
    models.py             dataclasses: LedgerEntry, FileVersion, Decision
    hub_bridge.py         optional: publish/subscribe ledger changes to hub
```

One `ContextService` instance per conversation context (daemon may
have multiple). Registered as a service on the event bus so every
plugin can reach it:

```python
context_service = event_bus.get_service("context_service")
```

### Relationship to existing systems

```
conversation_manager  ──► holds the actual message list
                          (unchanged, ContextService doesn't own history)

ContextService        ──► indexes heavy items by msg_idx + content_hash
                          tracks curation decisions
                          tells compaction what to keep/replace/summarize

compaction plugin     ──► on compact, asks ContextService for every
                          old message's decision and applies it
                          no longer calls LLM to summarize
                          (agent-provided summaries are used)

response_parser       ──► parses <curate>, <file_read>, <context>,
                          <evict>, <force> tags from agent responses
                          strips them from user-visible output

file_read tool        ──► before actually reading, asks ContextService
                          "have I seen this file at this hash?"
                          returns stale/fresh/diff response accordingly

hub plugin            ──► (optional phase) subscribes to ledger events
                          so peer agents see who has what in context
```


## Ephemeral injection mechanism

This is the core trick ContextService uses to surface runtime state
to the agent without breaking prefix cache. Understand this before
reading the rest of the spec.

### The problem

The agent needs to see things like:

- "here's your current ledger, decide what to keep"
- "ctx-3 is 254KB and pending, mark it"
- "decisions recorded, here's the savings"

These are all **per-turn state** that changes every turn. If we put
them in the system prompt, the system prompt changes every turn, the
prefix hash changes every turn, prefix caching breaks, every request
reprocesses the entire conversation from scratch. That's the exact
failure mode we're trying to fix.

### The solution

Inject runtime state as an **ephemeral user message** at the tail of
the request, using a bracketed prefix convention. The injection:

1. Is built at request-time by `context_service.curator.build_injection()`
2. Is added to the request `messages` list just before sending
3. Is NOT persisted to `conversation_manager.messages`
4. Is DISCARDED after the request completes
5. Uses `role: "user"` (not `system`) for provider compatibility
6. Has content starting with `[context service]` or
   `[context service: curator]` as a runtime marker the model
   recognizes from the static system prompt

### Why user role, not system role

OpenAI-compatible providers (openai, xai, openrouter, groq, mistral,
most others) **reject any `system` role message after position 0**.
Only one system message, at the top. Anthropic is more flexible but
still expects system content via the top-level `system` parameter.

Using `role: "user"` works on every provider without special casing.
The bracketed prefix tells the model the message is machinery, not
from the human — the same convention claude code uses for
`<system-reminder>` blocks and tool results.

### Why we merge instead of appending

Some providers reject TWO user-role messages in a row. Since the
ephemeral injection is user-role AND the message right before it
in history is often also user-role (either a real user input or a
tool_result, both of which are user-role in openai format),
appending the injection as its own message would produce a
consecutive user pair on those strict providers.

**ContextService's default strategy is to MERGE** the injection
into the existing last user message (or append if the last message
is assistant-role) using a `---` separator. This always produces a
valid request on every provider, no capability detection needed.

The static system prompt teaches the model:

```
When you see "---" inside a user message, the sections before and
after come from different sources. A message that contains a
tool_result followed by "---" and a [context service] block is
showing you the tool output AND a runtime notice together. Treat
each section independently.
```

The merge is lexical — literally `msg.content += "\n\n---\n\n" +
injection_payload` at request build time. After the request, only
the tool_result (or original user text) portion is persisted back
to `conversation_manager.messages`; the `---` and everything after
it are stripped. The injection never ends up in history.

### Why this preserves cache

Prefix caching works by hashing messages from position 0 up to the
last unchanged message. Because the ephemeral injection happens at
the TAIL of the request and is NEVER written back to history, the
prefix up to the last real message is bit-identical between turns.
Providers with prefix caching (anthropic, openrouter, xai) can reuse
the cached KV state for everything before the injection.

Only the last ~1KB (the injection + any new user input) has to be
reprocessed. That's the whole point.

### What triggers an injection

1. Agent emits `<context/>` or `<context filter="..."/>` in a response
   → on the NEXT request, a ledger snapshot is injected
2. Curator threshold is crossed (heavy bytes ≥ curate_threshold_kb,
   pending items exist, throttle expired)
   → on the NEXT request, the curator prompt is injected
3. Agent just submitted `<curate>` decisions
   → on the NEXT request, a small confirmation block is injected

Most turns have NO injection, and most requests are just
`[system] + [history] + [new user msg]` with no ephemeral additions.
Cache stays hot across those turns.

### The static system prompt teaches the convention

The ContextService section of the default system prompt contains this
paragraph so the model knows how to interpret injections:

```
Messages whose content begins with `[context service` are automated
runtime notices from the context ledger. They are not from the user.
Treat them the same way you'd treat a system reminder. They may
appear between the user's real messages and your own assistant
replies, and they exist only for one turn — do not reference them
as if they were persistent conversation history.
```

### Consecutive-user-role edge case

If a real user message and an ephemeral injection would both end up
as consecutive `user` messages in the same request, most providers
accept this. A few strict providers (varies by version) reject it.
`build_injection()` checks `provider.capabilities.consecutive_user`
and, if false, merges both into one user message separated by `---`:

```
[context service] ledger snapshot
  ctx-1 ...
  ctx-2 ...

---

ok continue with the fix
```

The `---` separator is also taught by the static system prompt as a
"multiple source" marker inside a single user message.

### Injection is write-only

The agent cannot directly manipulate the injection. It can only:
- Request one via `<context/>` (triggers snapshot injection next turn)
- Respond to one via `<curate>` tags (decisions recorded, confirmation
  injection appears next turn)

ContextService is the sole author of injection content. The
response_parser watches the agent's responses for trigger tags and
sets flags on ContextService; ContextService renders and injects at
request-build time.


## Data model

### LedgerEntry

```python
@dataclass
class LedgerEntry:
    ctx_id: str                       # "ctx-1", stable for session
    kind: Literal["file_read", "tool_result", "attachment"]
    tool: str                         # "file_read", "terminal", "mcp:github", etc
    label: str                        # "plugins/hub/plugin.py" | "git_log" | "deadcode_scan"

    # content identity
    content_hash: str                 # xxh64 of the raw content
    size_bytes: int

    # history placement
    msg_idx: int                      # index into conversation_manager.messages
    message_uuid: str                 # stable UUID (survives reindex)

    # lifecycle
    added_at: datetime
    last_accessed_at: datetime        # updated on stale-read hits
    ttl_seconds: Optional[int] = None # None = no expiry, int = expire after
    read_count: int = 1               # how many times the agent referenced it

    # curation
    decision: Literal["pending", "keep", "summary"] = "pending"
    decision_body: str = ""           # reason (keep) or summary (summary)
    decided_at: Optional[datetime] = None

    # for file entries specifically
    file_path: Optional[str] = None
    file_lines: Optional[tuple[int, int]] = None  # (start, end) read range
    file_version: Optional[int] = None            # monotonic per-path

    # hub sharing
    hub_shared: bool = False
    hub_holders: list[str] = field(default_factory=list)  # ["koordinator", "lapis"]
```

### FileVersion

```python
@dataclass
class FileVersion:
    """Tracks all versions of a file ever read this session."""
    path: str
    versions: list[LedgerEntry]  # ordered by read time

    @property
    def latest(self) -> LedgerEntry:
        return self.versions[-1]

    @property
    def latest_hash(self) -> str:
        return self.latest.content_hash
```


## Agent-facing surface: xml vs tool_calls

ContextService exposes two different protocols to the agent, and
which one you use depends on what you're doing. This is the most
important distinction in the spec.

### The rule

| what the agent wants to do | protocol | why |
|---|---|---|
| read a file, run a terminal command, edit code, fetch a url — **real world actions** | native openai `tool_calls` | parallel execution, provider-optimized streaming, schema validation, these are real work |
| mark a ledger entry `keep` or `summary`, query the ledger, evict an entry, toggle a modifier — **ledger metadata operations** | **xml tags in assistant content** | synchronous, no round-trip, no history bloat, just flags flipping in ContextService |

### Why split the surface

Three reasons for NOT making curate/context/evict into real openai
function tools:

1. **Round-trip cost.** Each native tool call adds TWO messages to
   history: the assistant's `tool_calls` entry plus the `role: "tool"`
   result. If the agent curates 3 items, that's 6 messages. ContextService
   exists to SHRINK history, not grow it. Making curation itself cost
   history slots defeats the point.

2. **Synchronous execution.** Curate decisions are pure metadata ops —
   they flip flags in a Python dict in memory. Treating them as async
   tool calls with a "result" to return is theater.

3. **Semantic layer.** curate/context/evict describe the agent's
   intent about its own context. They're meta-operations on the
   conversation itself. Mixing them with real-world tool calls
   (file_read, terminal) conflates two very different things.

### Why NOT make everything xml

Three reasons for KEEPING file_read and other real tools as native
openai tool_calls instead of xml:

1. **Native tool_calls support parallel execution.** The agent can
   emit 3 file_reads in one turn, all run in parallel, results come
   back together. XML-in-content pattern serializes to one at a time.

2. **Provider optimization.** openrouter/xai/openai/anthropic all
   have streaming optimizations for tool_calls. xml-in-content loses
   those.

3. **Schema validation.** native tools have JSON schemas that the
   provider enforces. xml parsing is best-effort.

### The full list

**Native openai tool_calls (real actions):**

- `file_read` — reads a file from disk (with ContextService dedup hook)
- `terminal` — runs a shell command
- `file_edit` / `file_write` — modifies a file
- any MCP-provided tool
- any hub/agent spawning commands
- any plugin-contributed tool

These behave exactly as they do today in kollab. ContextService
hooks into the file_read path to check for stale hits but does NOT
change the protocol — the agent still calls file_read as a native
function, and results still come back as `role: "tool"` messages.

**XML tags in assistant content (ledger metadata):**

- `<curate id="..." decision="keep|summary">body</curate>` — record a decision
- `<context/>` — request a ledger snapshot injection
- `<context filter="..."/>` — filtered snapshot
- `<evict id="...">reason</evict>` — drop an entry
- `<force>...</force>` — modifier wrapping a tool call (see below)

These are parsed by response_parser from the assistant's `content`
field, stripped before the content is stored to history, and their
side effects (flag updates on ContextService) happen synchronously.

### Special case: `<force>` is a modifier, not an op

The `<force>` tag is NOT a standalone operation. It's a modifier
that tells ContextService "ignore dedup on the next file_read tool
call in this turn." It has two forms:

**Form 1 — attribute on the tool call arguments:**

Since file_read is a native tool, `force` lives inside the tool's
JSON arguments:

```json
{
  "id": "call_fr2",
  "type": "function",
  "function": {
    "name": "file_read",
    "arguments": "{\"path\": \"plugins/hub/plugin.py\", \"force\": true}"
  }
}
```

The file_read tool schema has a `force: bool = false` parameter.
When true, ContextService's stale-hit check is bypassed.

**Form 2 — xml wrapper (applies to the next tool call in this turn):**

```
<force>
</force>
```

Just the `<force/>` tag alone in the assistant content sets a
one-shot flag on ContextService that applies to the NEXT tool call
from this same assistant message. This is a convenience for models
that don't reliably set the `force` argument but do emit xml tags.
Internally it's equivalent to setting `force=true` on the next
file_read's arguments.

Both forms work. Agents should prefer Form 1 (the attribute) because
it's explicit and tied to the specific tool call. Form 2 exists as
a fallback.

### Example: mixing tool_calls and xml in one response

This is the most important example in the whole spec. It shows what
a typical agent response looks like when it's doing real work AND
managing its context in the same turn.

**The assistant message:**

```json
{
  "role": "assistant",
  "content": "ok reviewing ledger state before the next batch of edits. hub/plugin.py i'm still editing so keep it verbatim. git log was enough orientation, summarizing. now pulling coordinator.py to understand the ready flag pattern.\n\n<curate id=\"ctx-1\" decision=\"keep\">\nactively editing plugins/hub/plugin.py to add the coordinator_ready guard at line 412. need verbatim for the next 2-3 turns while i patch and test.\n</curate>\n\n<curate id=\"ctx-2\" decision=\"summary\">\ngit log --oneline HEAD~40..HEAD: 40 commits, phase 4.5 daemon work dominated (c8a7eec..286ae47). key commit was c8a7eec which added coordinator_ready threading.Event. rest is docs and routine. can re-run git log if needed for specific hashes.\n</curate>",
  "tool_calls": [
    {
      "id": "call_fr2",
      "type": "function",
      "function": {
        "name": "file_read",
        "arguments": "{\"path\": \"plugins/hub/coordinator.py\"}"
      }
    }
  ]
}
```

Notice what's happening:

1. The `content` field has three things:
   - Prose narration ("ok reviewing ledger state...")
   - A `<curate id="ctx-1">` block (metadata op)
   - A `<curate id="ctx-2">` block (metadata op)

2. The `tool_calls` array has one real tool call:
   - `file_read` for coordinator.py (the real action)

3. Both happen in the SAME assistant message. The model emits them
   together. OpenRouter processes the tool_calls normally, and
   response_parser processes the curate blocks from the content.

**What response_parser does with this message:**

```python
# 1. Parse xml tags from content
curate_blocks = parse_curate_tags(assistant_msg.content)
# → [Curate(ctx-1, keep, "actively editing..."),
#    Curate(ctx-2, summary, "git log --oneline...")]

# 2. Apply them to ContextService
for c in curate_blocks:
    context_service.set_decision(c.ctx_id, c.decision, c.body)

# 3. Strip xml tags from content before storing to history
stripped_content = strip_curate_tags(assistant_msg.content)
# → "ok reviewing ledger state before the next batch of edits.
#    hub/plugin.py i'm still editing so keep it verbatim. git log
#    was enough orientation, summarizing. now pulling coordinator.py
#    to understand the ready flag pattern."

# 4. Store to history with stripped content but INTACT tool_calls
conversation_manager.add_message(
    role="assistant",
    content=stripped_content,
    tool_calls=assistant_msg.tool_calls,  # unchanged
)

# 5. Return the tool_calls to the tool pipeline for execution
return assistant_msg.tool_calls
```

The tool pipeline executes `file_read(path="plugins/hub/coordinator.py")`
normally. The result comes back as a `role: "tool"` message with
`tool_call_id="call_fr2"`. ContextService hooks the file_read
execution to check for stale hits, but the protocol is unchanged —
it's still a native tool call.

**History after this turn:**

```
...earlier messages...
msg N    assistant   content: "ok reviewing ledger state..." (tags stripped)
                     tool_calls: [call_fr2: file_read(coordinator.py)]
msg N+1  tool        tool_call_id: call_fr2
                     content: coordinator.py content (or stale marker)
```

The `<curate>` tags are gone from history — they're not needed
there. Their effect lives in ContextService as two updated
LedgerEntry records.

### Example: file_read with force attribute

The agent has already read plugins/hub/plugin.py earlier. ContextService
has it as ctx-1 with hash `abc123de`. The agent is debugging and wants
to re-read it to see if a recent edit landed, bypassing dedup.

**Assistant message:**

```json
{
  "role": "assistant",
  "content": "let me force re-read hub/plugin.py to confirm the edit landed on disk.",
  "tool_calls": [
    {
      "id": "call_fr3",
      "type": "function",
      "function": {
        "name": "file_read",
        "arguments": "{\"path\": \"plugins/hub/plugin.py\", \"force\": true}"
      }
    }
  ]
}
```

No xml tags here — force is an argument on the native tool call,
not a content-level meta tag. ContextService's file_read hook sees
`force=true` in the arguments, skips the stale-hit check, and
returns the full file content.

The tool result:

```json
{
  "role": "tool",
  "tool_call_id": "call_fr3",
  "content": "plugins/hub/plugin.py (48 KB, 697 lines, hash def456ab)\n\n... [full content] ..."
}
```

If the hash has changed since ctx-1, ContextService creates a new
ledger entry (ctx-5) pointing at this new tool result message.
ctx-1 is left alone (it still references the old tool result
which is still in history). The file_tracker now has two versions
of plugins/hub/plugin.py recorded.

### Example: `<evict>` in assistant content

The agent wants to drop ctx-2 (the 254 KB dead code report) from
history because it's extracted everything useful:

```json
{
  "role": "assistant",
  "content": "done with the deadcode report, extracted the 3 real fixes. freeing the bytes.\n\n<evict id=\"ctx-2\">\nextracted the 3 fixes from the 402 findings: hub/plugin.py:412 race, daemon.py:88 leak, state/context.py:140 cycle. rest was noise. no reason to keep the full 254 KB report in context — i have the fixes inline in my working memory. saves 253 KB before compact.\n</evict>",
  "tool_calls": []
}
```

No tool_calls — this is a pure metadata operation. response_parser
catches `<evict id="ctx-2">`, ContextService marks the entry as
evicted and rewrites the tool result message at
`ctx-2.message_uuid` to a short stub:

```
[evicted: terminal dead_code_detect, 254 KB.
 reason: "extracted the 3 fixes from the 402 findings..."
 original tool_call_id: call_dcd1]
```

**This IS a cache-breaking event.** Rewriting a message in the
middle of history means every request from now on will have a
different prefix from the agent's point of view. The cache is
broken from `ctx-2.msg_idx` forward. Agents should only evict
when the savings are worth it (big item, session will continue
long enough to recoup the cache miss cost).

The evicted assistant message itself (after tag stripping) gets
stored as:

```
done with the deadcode report, extracted the 3 real fixes. freeing the bytes.
```

The `<evict>` tag is gone from history. The effect persists in
the rewritten tool message.

### Example: `<context/>` with no tool calls

Agent wants a ledger snapshot and has nothing else to do this turn:

```json
{
  "role": "assistant",
  "content": "taking a breath before the next batch. let me check my ledger.\n\n<context/>",
  "tool_calls": []
}
```

response_parser strips `<context/>` and sets `context_query_pending = True`
on ContextService. The stored content becomes just
"taking a breath before the next batch. let me check my ledger."

The NEXT request that kollabor builds will inject a ledger snapshot
before sending. That snapshot appears as an ephemeral user-role
message (merged into a following user message if needed, as
described in the injection mechanism section).

### Summary of protocols

- **Real actions → native openai tool_calls**
  - file_read, terminal, file_edit, MCP tools, etc.
  - Tool results come back as `role: "tool"` with `tool_call_id`
  - ContextService HOOKS these (for file_read dedup) but doesn't
    change the wire format

- **Ledger metadata → xml in assistant content**
  - `<curate>`, `<context/>`, `<evict>` are parsed by response_parser
  - Stripped from stored content after processing
  - Effects apply synchronously to ContextService's in-memory state
  - Zero messages added to history (except `<evict>` which rewrites
    an existing message)

- **Modifiers → attributes on the native tool call**
  - `force: true` on file_read arguments
  - `<force/>` xml tag is a fallback that sets a one-shot flag
  - Prefer the attribute form

This hybrid is the only way to get the ContextService benefits
without bloating history or fighting the provider protocols.


## Agent-facing XML API (ledger metadata tags)

All tags are parsed from assistant responses and stripped from
user-visible output (same treatment as `<hub_msg>`, `<question>`).
These are the **ledger metadata ops** from the table above —
`file_read` and other real tools are NOT in this section, they
use native openai `tool_calls`.

### XML reference — copy-pasteable examples

This is the fast reference. Every tag, every form, every
permutation the agent might write. Copy any of these directly
into an assistant response `content` field and response_parser
will handle it.

#### `<curate>` forms

Basic keep decision:

```xml
<curate id="ctx-1" decision="keep">
actively editing this file across the next 2-3 turns while i fix
the broadcast race. need verbatim content for reference.
</curate>
```

Basic summary decision:

```xml
<curate id="ctx-2" decision="summary">
git log --oneline HEAD~40..HEAD: 40 commits, phase 4.5 daemon
work dominates (c8a7eec..286ae47). key commit c8a7eec added
coordinator_ready threading.Event. rest is docs and routine
style passes. re-run git log if specific hashes needed.
</curate>
```

Multiple curations in one response (most common case):

```xml
<curate id="ctx-1" decision="keep">
fixing the broadcast race here, need full file content for the
next few turns.
</curate>

<curate id="ctx-2" decision="summary">
git log orientation: 40 commits, phase 4.5 work, coordinator_ready
added in c8a7eec. nothing else load-bearing.
</curate>

<curate id="ctx-3" decision="summary">
dead code scan: 402 items, real fixes are at hub/plugin.py:412
(broadcaster race), daemon.py:88 (signal handler leak), and
state/context.py:140 (parent ref cycle). rest is noise — 180
unused imports, 90 TODOs, 60 unreachable branches, 30 stubs.
full report saved to /tmp/deadcode-04110002.txt.
</curate>
```

Changing a prior decision (last-write-wins):

```xml
<curate id="ctx-1" decision="summary">
done editing this file. patch landed. collapsing to a summary:
added coordinator_ready.is_set() guard at line 412, broadcast()
now drops messages cleanly when coordinator isn't ready. no
other changes. full file is still reachable via force=true if
needed.
</curate>
```

Curate with ttl (auto-demote after 600 seconds):

```xml
<curate id="ctx-4" decision="keep" ttl="600">
need this file for the next 10 minutes while i finish the fix.
after that, demote to summary automatically (auto-summary is
fine, this isn't critical past the immediate edit window).
</curate>
```

#### `<context>` forms

Full ledger snapshot request:

```xml
<context/>
```

Filter by decision status:

```xml
<context filter="pending"/>
```

Filter by kind:

```xml
<context filter="file_read"/>
<context filter="tool_result"/>
```

Filter by path (substring match):

```xml
<context filter="path:plugins/hub"/>
<context filter="path:kollabor/state"/>
```

Filter by hub peer (phase D only — requires hub integration):

```xml
<context filter="peer:lapis"/>
```

Combined filter (not yet specified, reserved for future use —
for now use one filter at a time):

```xml
<context filter="pending"/>
<context filter="path:plugins/hub"/>
```

(Emit two `<context/>` tags in one response to get two separate
filtered snapshots. Each one gets its own injection on the next
request.)

#### `<evict>` forms

Basic eviction:

```xml
<evict id="ctx-3">
done with the deadcode report, extracted the 3 real fixes.
freeing the 254 KB. session has another ~50 turns to go, savings
will pay off the cache break.
</evict>
```

Multiple evictions in one response:

```xml
<evict id="ctx-2">
git log orientation no longer needed, marked summary earlier but
actually just dropping it — won't reference it again this session.
</evict>

<evict id="ctx-3">
dead code scan done, all actionable items extracted, dropping
the 254 KB full report.
</evict>
```

Eviction is cache-breaking. The `<evict>` body should include the
agent's justification — why the savings are worth the cache miss.
If the session has few turns remaining, eviction is usually NOT
worth it (the cache miss costs more than the bytes reclaimed).

#### `<force>` forms

**Form 1 — as an argument on the native file_read tool call
(preferred):**

This isn't xml at all — it's a JSON argument on the native openai
tool call. Shown here for completeness because the rest of this
section is xml:

```json
{
  "role": "assistant",
  "content": "force re-reading to verify the edit landed on disk.",
  "tool_calls": [
    {
      "id": "call_fr1",
      "type": "function",
      "function": {
        "name": "file_read",
        "arguments": "{\"path\": \"plugins/hub/plugin.py\", \"force\": true}"
      }
    }
  ]
}
```

**Form 2 — xml tag as a one-shot flag before a tool call
(fallback):**

```xml
<force/>
```

Emitting `<force/>` alone in the assistant content sets a one-shot
flag on ContextService. The NEXT file_read tool call from the same
assistant message bypasses dedup regardless of whether it has
`force: true` in its arguments. The flag is cleared after one use.

This form exists for models that reliably emit xml tags but don't
reliably set tool call arguments correctly. Prefer Form 1 whenever
possible.

#### Complete example: mixing everything in one response

This is what a realistic agent response looks like when it's doing
real work AND managing context in the same turn. The `content`
field is plain text with xml tags mixed in. The `tool_calls` array
is for real actions.

```
i've got enough context to start the fix. reviewing my ledger
first to clean up what i don't need.

<curate id="ctx-1" decision="keep">
still editing this file for the next 2-3 turns, need verbatim.
</curate>

<curate id="ctx-2" decision="summary">
git log was orientation, key commit is c8a7eec (coordinator_ready
flag), everything else is routine.
</curate>

<evict id="ctx-3">
dead code report extracted, 3 real fixes found, dropping the
254 KB raw report.
</evict>

now checking coordinator.py to see the ready flag pattern, then
applying the broadcast guard.
```

And the same response with the real tool calls attached (this is
how it actually gets sent to the provider):

```json
{
  "role": "assistant",
  "content": "i've got enough context to start the fix. reviewing my ledger first to clean up what i don't need.\n\n<curate id=\"ctx-1\" decision=\"keep\">\nstill editing this file for the next 2-3 turns, need verbatim.\n</curate>\n\n<curate id=\"ctx-2\" decision=\"summary\">\ngit log was orientation, key commit is c8a7eec (coordinator_ready flag), everything else is routine.\n</curate>\n\n<evict id=\"ctx-3\">\ndead code report extracted, 3 real fixes found, dropping the 254 KB raw report.\n</evict>\n\nnow checking coordinator.py to see the ready flag pattern, then applying the broadcast guard.",
  "tool_calls": [
    {
      "id": "call_fr5",
      "type": "function",
      "function": {
        "name": "file_read",
        "arguments": "{\"path\": \"plugins/hub/coordinator.py\"}"
      }
    }
  ]
}
```

What response_parser does with this message:

1. Parse three xml blocks from content: 2 curates + 1 evict
2. Apply to ContextService:
   - ctx-1.decision = "keep", body = "still editing..."
   - ctx-2.decision = "summary", body = "git log was orientation..."
   - ctx-3 evicted, history rewritten at ctx-3.message_uuid
3. Strip all xml blocks from content. Stored content becomes:
   ```
   i've got enough context to start the fix. reviewing my ledger
   first to clean up what i don't need.

   now checking coordinator.py to see the ready flag pattern, then
   applying the broadcast guard.
   ```
4. Tool_calls array is UNTOUCHED — it drives the tool pipeline
   as normal, file_read for coordinator.py executes, tool result
   comes back as `role: "tool"` with `tool_call_id: call_fr5`.

The agent's full "intent" for this turn is captured in the xml
tags (curation decisions) and the tool_calls array (actions).
Both are processed in parallel by different subsystems. The stored
content in history is just the prose narration — clean, readable,
no machinery.

#### What NOT to do

**Don't put real tool calls as xml:**

```xml
<file_read path="plugins/hub/plugin.py"/>
```

❌ This is NOT how ContextService works. Real file reads MUST go
through native openai `tool_calls` with the `file_read` function
name. The xml form will NOT be executed — it will be stripped and
ignored (or worse, treated as prose the model will hallucinate a
response for).

**Don't wrap curate in tool_calls:**

```json
{
  "tool_calls": [
    {"function": {"name": "curate", "arguments": "{...}"}}
  ]
}
```

❌ `curate` is not a registered tool. ContextService does NOT
register it. If the provider allows the call at all, it will
error when the pipeline tries to route it.

**Don't omit the decision body:**

```xml
<curate id="ctx-1" decision="summary"></curate>
```

❌ Empty body is rejected. ContextService requires SOMETHING in
the body — the whole point of the agent-provided summary is that
the agent writes real content that replaces the tool result at
compaction time. An empty curate falls back to `decision=pending`
which auto-summarizes at compact time (losing the opportunity
for a high-quality human-directed summary).

**Don't use `<context/>` to read files:**

```xml
<context/>
<file_read path="..."/>
```

❌ `<context/>` is for inspecting the LEDGER, not for reading
files. File content always comes through native file_read tool
calls. `<context/>` returns a ledger snapshot on the next turn
as an ephemeral user message.

**Don't mix force and non-force arguments:**

```json
{"function": {"name": "file_read", "arguments": "{\"path\": \"x.py\", \"force\": \"true\"}"}}
```

⚠ The `force` argument MUST be a JSON boolean, not a string.
`"force": true` (boolean) works. `"force": "true"` (string) does
not — it's truthy in Python but ContextService's schema check
will warn and treat it as false.

### `<curate>` — mark an entry's compaction fate

```xml
<curate id="ctx-1" decision="keep">
reason you need this verbatim. stays in history at full size.
</curate>

<curate id="ctx-2" decision="summary">
your compressed version. this string replaces the full tool result
at compaction time. include anything future-you will need.
</curate>
```

- `id`: ctx_id from the ledger
- `decision`: `keep` or `summary`
- body: free-form text. required. empty body is rejected.
- agent can re-emit to change a decision (last-write-wins)

### `<context>` — query the ledger

```xml
<context/>
```

Triggers a tail-only injection on the next turn showing current
ledger state. Zero cost to prefix cache.

#### How the injection actually works

This is the most subtle piece of the design, so walk through it
carefully.

**Problem:** OpenAI-compatible providers (openai, xai, openrouter,
groq, mistral, most others) reject a second `role: "system"` message
anywhere except position 0. Anthropic allows more flexibility but
still expects the system prompt via the top-level `system` parameter,
not inlined.

**Solution:** The ledger snapshot is injected as a **`user` message
with a bracketed runtime prefix**. This matches how claude code
delivers its own runtime reminders, and it works on every provider.

**The convention:** Any user message whose content begins with
`[context service` is a runtime notice from the context service, not
from the human. The static system prompt explains this upfront so
the model never confuses it with real user input.

**The injection is request-level, not history-level:**
- When the agent emits `<context/>`, a flag is set in ContextService
- On the NEXT request build, the flag is checked
- If set: the ledger snapshot is inserted into the request messages
  list at the tail (just before any new real user message)
- The ledger message is NOT appended to `conversation_manager.messages`
- After the request is sent, the flag is cleared and the ledger
  message is gone forever — it existed only in that one API call

**Why this preserves cache:** The cached prefix is everything from
message 0 up to the last unchanged message. Because the ledger
injection happens AFTER the last real message in history (and is
not persisted back into history), the prefix up to that point
stays bit-identical across turns, and prefix caching on
openrouter/xai/anthropic stays hot.

**The injection payload format:**

```
[context service] ledger snapshot

  ctx-1  file_read   plugins/hub/plugin.py      48KB   keep
         "editing this file, need verbatim"
  ctx-2  tool_result terminal: git_log         176KB   summary
         "phase 4.5 work, 40 commits c8a7eec..286ae47"
  ctx-3  tool_result deadcode                  254KB   pending
  ctx-4  file_read   kollabor/state/context.py  12KB   pending

  total:      490KB  heavy items: 4
  threshold:  300KB  (curator will prompt next turn)
```

#### Full request body example — concrete walkthrough

This example uses a real kollab session. A user is debugging a
hub broadcast bug. The agent (koordinator) has read one file via
native openai tool calling and then emitted `<context/>` to
inspect its ledger before editing.

**State of the conversation history at this moment:**

```
msg 0  user
       "fix the hub broadcast race. agents are missing messages
        when coordinator flips over."

msg 1  assistant   content: "on it. starting with hub/plugin.py
                             since that's where broadcast() lives."
                   tool_calls: [ call_rd1: file_read(path=plugins/hub/plugin.py) ]

msg 2  tool        tool_call_id: call_rd1
                   content: full plugins/hub/plugin.py (48 KB)

msg 3  assistant   content: "ok broadcast() is at line 412, it
                             iterates peers without checking if
                             coordinator_ready is set. let me
                             check the commit that added the ready
                             flag before i start editing.
                             <context/> also want to see my
                             ledger — turn 4 already."
                   tool_calls: [ call_gl1: terminal(command=git log -1 c8a7eec) ]

msg 4  tool        tool_call_id: call_gl1
                   content: commit message + diff for c8a7eec (4 KB)
```

After msg 3 streams in, response_parser processes the assistant's
content. It sees `<context/>` and does two things:

1. Strips the `<context/>` tag from the content that gets stored.
   The persisted msg 3 content becomes: `"ok broadcast() is at
   line 412, it iterates peers without checking if
   coordinator_ready is set. let me check the commit that added
   the ready flag before i start editing. also want to see my
   ledger — turn 4 already."`
2. Sets `context_query_pending = True` on ContextService.

The `tool_calls` on msg 3 run normally — the terminal command
executes and the tool result comes back as msg 4.

Now kollabor is about to build the next request (to get the agent
to continue reasoning about the commit). ContextService sees the
pending context query flag and injects a ledger snapshot. Because
msg 4 is tool-role (not user-role), the injection can be a new
user-role message without merging.

**The JSON body sent to openrouter for the next turn:**

```json
{
  "model": "x-ai/grok-4.1-fast",
  "temperature": 0.7,
  "max_tokens": 8192,
  "stream": true,
  "session_id": "2604110000-chronos-crown",
  "tools": [
    {"type": "function", "function": {"name": "terminal", "description": "...", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "file_read", "description": "...", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "lines": {"type": "string"}}}}}
  ],
  "messages": [
    {
      "role": "system",
      "content": "You are koordinator, a kollab agent working on the kollab repo...\n\n## Context Service\n\nYour context is tracked by a service that dedupes file reads,\nrecords hashes, and lets you curate what stays in history.\n\nMessages whose content begins with `[context service` are\nautomated runtime notices from the context ledger. They are not\nfrom the user. Treat them the same way you'd treat a system\nreminder.\n\nWhen you see `---` inside a user message, the sections before\nand after come from different sources.\n\n[full static system prompt, ~60 KB, cached hot across every turn]"
    },
    {
      "role": "user",
      "content": "fix the hub broadcast race. agents are missing messages when coordinator flips over."
    },
    {
      "role": "assistant",
      "content": "on it. starting with hub/plugin.py since that's where broadcast() lives.",
      "tool_calls": [
        {
          "id": "call_rd1",
          "type": "function",
          "function": {
            "name": "file_read",
            "arguments": "{\"path\": \"plugins/hub/plugin.py\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_rd1",
      "content": "plugins/hub/plugin.py (48 KB, 693 lines)\n\n\"\"\"Hub plugin: peer-to-peer agent mesh.\"\"\"\nimport asyncio\nimport json\nfrom pathlib import Path\nfrom typing import Optional, Dict, List\nfrom kollabor_events.models import EventType, Hook, HookPriority\nfrom .presence import PresenceManager\nfrom .coordinator import Coordinator\nfrom .messenger import HubMessenger\nfrom .vault import VaultStore\n\nclass HubPlugin(BasePlugin):\n    def __init__(self, name, event_bus, renderer, config):\n        self.name = name\n        self.event_bus = event_bus\n        self.renderer = renderer\n        self.config = config\n        ...\n    async def broadcast(self, msg: HubMessage) -> None:\n        for peer in self.peers.values():\n            try:\n                await self.messenger.send(peer, msg)\n            except Exception as e:\n                logger.warning(f'broadcast to {peer} failed: {e}')\n    ... [689 more lines of real code] ..."
    },
    {
      "role": "assistant",
      "content": "ok broadcast() is at line 412, it iterates peers without checking if coordinator_ready is set. let me check the commit that added the ready flag before i start editing. also want to see my ledger — turn 4 already.",
      "tool_calls": [
        {
          "id": "call_gl1",
          "type": "function",
          "function": {
            "name": "terminal",
            "arguments": "{\"command\": \"git log -1 c8a7eec\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_gl1",
      "content": "commit c8a7eec\nAuthor: Example User\nDate:   2026-04-08 14:22:00\n\n    fix(hub): coordinator ready flag\n\n    Added coordinator_ready threading.Event to Coordinator.\n    Set after _elect_coordinator() acquires the flock. Agents\n    should check coordinator_ready.is_set() before broadcasting\n    so they don't send messages into a half-initialized mesh.\n\n    plugins/hub/coordinator.py | 12 ++++++++\n    1 file changed, 12 insertions(+)"
    },

    // ─────────────────────────────────────────────────────────────
    // EPHEMERAL LEDGER SNAPSHOT INJECTION
    //
    // The previous message is role "tool", not role "user", so
    // appending a user-role context service message here is NOT a
    // consecutive-same-role situation. No merging needed.
    //
    // Built at request time by curator.build_injection() because
    // context_query_pending was True. After this request completes,
    // this message is discarded and will not appear in the next
    // request. conversation_manager.messages stops at the tool
    // message above.
    // ─────────────────────────────────────────────────────────────
    {
      "role": "user",
      "content": "[context service] ledger snapshot\n\n  ctx-1  tool_result  file_read: plugins/hub/plugin.py    48 KB   pending\n         call_rd1, turn 1, hash abc123de\n\n  ctx-2  tool_result  terminal: git log -1 c8a7eec        4 KB   (under 8 KB, not tracked)\n         call_gl1, turn 3\n\n  total tracked:  48 KB  (1 item)\n  threshold:      300 KB\n  compaction:     ~128 K tokens until auto-compact fires\n\ncommands:\n  <curate id=\"ctx-N\" decision=\"keep|summary\">body</curate>\n  <context filter=\"pending|file_read|path:X\"/>\n  <evict id=\"ctx-N\">reason</evict>   (cache cost warning applies)"
    }
  ]
}
```

**What the agent sees:** a full ledger snapshot. In this case only
one heavy item is actually tracked (the 48 KB file read) because
the git log output was under the 8 KB heavy_threshold. The agent
now knows it has plenty of headroom (128 K tokens until compact)
and can work freely.

**Why cache stays warm:** the prefix from the system prompt through
msg 4 (the commit tool result) is bit-identical to what it was in
the previous request. OpenRouter's prefix cache (and xai's
underlying KV cache) reuses everything up to that point. Only the
~500 bytes of ledger snapshot at the tail have to be processed
fresh. Compared to reprocessing 550 KB of history on every turn
(what kollabor does today), this is essentially free.

**What the agent's response might look like:**

```
ledger looks fine, tons of headroom. hub/plugin.py i'm about to
edit so keeping it verbatim. the git log commit message has the
pattern i need inline above, not ledgered since it's under 8 KB.

<curate id="ctx-1" decision="keep">
actively editing plugins/hub/plugin.py at line 412 to add a
coordinator_ready.is_set() guard before the broadcast loop.
need the file verbatim for the next 2-3 turns while i patch.
</curate>

applying the fix now.
```

accompanied by a `tool_calls` on the same assistant message
invoking a file_edit or terminal patch action.

After response_parser processes this:

- ctx-1 decision stored: `keep`, body = "actively editing..."
- The `<curate>` block is stripped from the stored assistant
  content. The message persisted to history becomes:

```
ledger looks fine, tons of headroom. hub/plugin.py i'm about to
edit so keeping it verbatim. the git log commit message has the
pattern i need inline above, not ledgered since it's under 8 KB.

applying the fix now.
```

The `tool_calls` portion of the assistant message is unchanged and
drives the edit pipeline as normal.

**What the NEXT request after this looks like:** the ephemeral
ledger snapshot is GONE. The curate tag has been stripped from the
stored assistant content. ContextService noticed a decision was
recorded, so it flips `confirmation_pending = True`. On the next
request, a small confirmation block gets injected (again as its
own user-role message since the last real message will be
tool-role from whatever the edit tool returned):

```json
{
  "role": "user",
  "content": "[context service] decisions recorded\n\n  ctx-1  keep     (48 KB retained in history)\n\ntotal pending:   0\nnext compact:    still ~128 K tokens away"
}
```

That confirmation appears once and then disappears. From then on,
ContextService is back to idle — no injections, no tail additions,
just normal requests — until the next trigger condition fires
(agent emits `<context/>` again, or curator threshold is crossed,
or more decisions get recorded).

#### When merging IS required

The example above does not need merging because msg 4 is tool-role
and the ledger injection is user-role. That's the common case when
a turn ends on a tool result.

Merging IS required when the last message in history is already
user-role and ContextService wants to inject a user-role message
after it. The two cases where that happens:

1. **The agent emits `<context/>` but NO tool call after it.**
   The assistant message is stored, the agent's turn ends, and
   then the NEXT turn is triggered by the user typing something new.
   That new user input becomes a real user message. The injection
   needs to go AFTER the previous history but BEFORE user's
   message (so the agent sees the ledger before responding to
   user), which would create a consecutive user pair.

2. **The curator threshold is crossed mid-user-turn.** Rare but
   possible: the user types a message, ContextService detects that
   message alone crossed a threshold (e.g., it contained a big
   pasted log), and curator_pending gets set. Same consecutive
   user problem.

**In both cases**, ContextService merges the injection into the
user message right after it with a `---` separator:

```json
[
  ... earlier history ...,
  {
    "role": "user",
    "content": "[context service] ledger snapshot\n\n  ctx-1  tool_result  file_read: plugins/hub/plugin.py    48 KB   pending\n\n  total: 48 KB  threshold: 300 KB\n\n---\n\nok continue fixing it"
  }
]
```

The merge is lexical: literally
`injection + "\n\n---\n\n" + real_user_message`. The static system
prompt teaches the model to interpret `---` as a source boundary
inside a user message. After the request, only the real user
portion (`"ok continue fixing it"`) gets persisted to
conversation_manager.messages; the injection portion and the
separator are discarded.

This merging strategy ALWAYS produces a valid request on every
provider because it avoids consecutive same-role messages entirely.
The tradeoff is that it's slightly less clean visually for the
model, but the `---` convention is easy for LLMs to learn from one
explanation in the system prompt.

Merging logic lives in `context_service.curator.build_injection()`
and runs at request build time.

### `<context filter="...">` — filtered query

```xml
<context filter="pending"/>
<context filter="file_read"/>
<context filter="path:plugins/hub"/>
<context filter="peer:lapis"/>
```

Same injection mechanism as `<context/>` — user message with
`[context service]` prefix, filtered to matching entries only.

### `<evict>` — drop an entry from context

```xml
<evict id="ctx-3">
already extracted the 5 fixes, don't need the full report anymore
</evict>
```

Replaces the referenced message in history with a short stub
(`[evicted: deadcode_scan, 254KB, see ctx-3 reason]`) and marks the
entry as `evicted`.

**This breaks prefix cache from `msg_idx` forward.** It's the one
agent operation that does. The curator message will warn about the
cost before suggesting eviction.

### `<force>` — override default behavior

Applied to tool calls. The agent can force a fresh reread even when
ContextService would serve a stale hit:

```xml
<file_read path="plugins/hub/plugin.py" force="true"/>
```

Or on the XML command directly:

```xml
<force>
<file_read path="plugins/hub/plugin.py"/>
</force>
```

Used when debugging ("i'm lost, show me the file again even if you
think i have it").

### `<file_read>` — file reads (default behavior)

```xml
<file_read path="plugins/hub/plugin.py"/>
```

First time read: ContextService returns the full file, creates a
ledger entry, records hash.

Second time read (content unchanged): ContextService returns a
**stale marker** instead of the file content:

```
[context service: file plugins/hub/plugin.py is already in your
context as ctx-1 (48KB, read at t=1). hash unchanged. use
<force/> to reread, <context/> to inspect.]
```

Second time read (content changed): ContextService returns a **diff**:

```
[context service: file plugins/hub/plugin.py changed since ctx-1.
returning diff from hash abc123 to hash def456:

--- ctx-1  plugins/hub/plugin.py  (48KB)
+++ ctx-7  plugins/hub/plugin.py  (49KB)
@@ -410,6 +410,10 @@
     def broadcast(self, msg: HubMessage) -> None:
+        if not self.coordinator_ready:
+            logger.warning("broadcast before coordinator ready")
+            return
         for peer in self.peers.values():

use <force/> to get the full file content instead of a diff.]
```

The diff is a ledger entry of its own (`kind=file_read`,
`tool=diff`), linked to the previous version via
`prior_ctx_id` metadata.

### `<file_read lines="X-Y">` — partial read

```xml
<file_read path="plugins/hub/plugin.py" lines="400-450"/>
```

Dedup is per (path, line_range). Rereading the same lines at the
same hash returns stale. Reading new lines within a file you already
have creates a new entry linked to the same FileVersion.

### `<ttl>` — attach expiry to an entry

```xml
<curate id="ctx-3" decision="keep" ttl="600">
need this for the next 10 min while i finish the fix
</curate>
```

After the ttl elapses, entry auto-demotes to `decision=summary` (if
the agent provided one) or `pending` (if not, curator will re-prompt).


## Curator lifecycle

The curator only prompts when meaningful. It is NOT every turn.

### Trigger

Curator fires when ALL of:

1. `sum(heavy_item_sizes) >= curate_threshold_kb` (default 300KB)
2. `count(decision="pending") >= 1` (something to decide on)
3. last curator prompt was >= 2 turns ago (throttle)

First curator of the session is always shown, even if only 1 heavy
item exists, as a training wheels prompt.

### Injection point

Curator prompt is injected as a **tail-only user message** for one
turn, using the same `[context service]` bracketed-prefix convention
as `<context/>`. It is NOT persisted to `conversation_manager.messages`.
It exists only in the request sent to the LLM for that one turn, then
discarded.

The same rules apply as `<context/>`:
- Role is `user`, not `system`, for provider compatibility
- Content starts with `[context service: curator]`
- If a real user message follows in the same request, the two are
  either sent as consecutive user messages (most providers) or
  merged with a `---` separator (strict providers)
- The static system prompt teaches the model to recognize
  `[context service` as a runtime source, not the human

This is the key cache property: the curator never touches the
cached prefix. Everything before the curator message is still
cache-hot because nothing persisted in history changed.

### Prompt template

The curator injection payload (rendered as the content of a `user`
message in the request):

```
[context service: curator]

heavy items awaiting decision:

  ctx-3  tool_result deadcode         254KB  t=3  pending
  ctx-4  file_read   state/context.py  12KB  t=4  pending

already decided:

  ctx-1  file_read   hub/plugin.py     48KB  t=1  keep
  ctx-2  tool_result git_log          176KB  t=2  summary

total: 490KB / 300KB threshold
next compaction: ~60K tokens away

mark pending items with:
  <curate id="ctx-3" decision="keep">reason</curate>
  <curate id="ctx-3" decision="summary">compressed</curate>

unmarked items default to auto-summary at compaction time
(worse than your own, you choose)

commands:
  <context/>                inspect full ledger
  <evict id="ctx-X"/>       drop immediately (cache cost)
  <force/>                  override default dedup
```

### Full request example with curator injection — concrete walkthrough

This example picks up from an earlier bug hunt session. A user asked
koordinator to do a full codebase sweep. Three heavy tool results
have piled up and they push the total over 300 KB. The previous turn,
the agent did NOT emit `<context/>` — the curator fires automatically
because the threshold was crossed.

**State of history at the moment curator fires:**

```
msg 0: user
       "do a full bug sweep on the repo. check everything
        and give me a report."

msg 1: assistant
       "ok. pulling git log for orientation, then lint and
        dead code scan."
       (contained <terminal>git log --oneline HEAD~40..HEAD</terminal>)

msg 2: user
       "[tool_result from terminal]
        c8a7eec style: ruff+black pass across repo
        4262484 feat(hub,agents,cli): launcher fix, auto-detect...
        b02d304 docs: phase 4.5 documentation refresh via 5 parallel...
        278a8cd fix(cli): kollab --hub -h / --help / no-args now...
        aa4b633 fix(profile): respect persisted active
        542d742 docs: remove internal dev docs, fix CLAUDE.md and...
        286ae47 initial public release
        ... [34 more commits, 176 KB of full log output] ..."

msg 3: assistant
       "got it, phase 4.5 was the big recent work. now running
        the dead code detector."
       (contained <terminal>python scripts/dead_code_detect.py</terminal>)

msg 4: user
       "[tool_result from terminal]
        Starting dead code detection...
        [1/5] Scanning for backup files...
        [2/5] Scanning for TODO/FIXME comments...
        [3/5] Scanning for pass statements...
        [4/5] Scanning for NotImplementedError stubs...
        [5/5] Running vulture analysis...
          Vulture scan complete.

        Detection complete! Found 402 items.
        ================================================================
        DEAD CODE DETECTION REPORT
        Generated: 2026-04-11 00:02:00
        ================================================================

        SUMMARY
        --------
        Total Findings: 402

        Findings by Category:
          confirmed_dead_code: 42
          unused_imports: 180
          todo_fixme_markers: 90
          unreachable_branches: 60
          notimplemented_stubs: 30

        [... 254 KB of full report details, every finding with
        file path, line number, function name, and context ...]"

msg 5: assistant
       "402 items, lots of noise. let me check state/context.py
        since it had one of the confirmed dead items."
       (contained <file_read path="kollabor/state/context.py"/>)

msg 6: user
       "[tool_result from file_read]
        kollabor/state/context.py (12 KB, 298 lines)

        \"\"\"Conversation context registry for daemon.\"\"\"
        import asyncio
        from pathlib import Path
        from typing import Dict, Optional, List
        from kollabor_events.bus import EventBus
        from .interface import StateService
        ...298 lines of real code..."
```

At this moment ContextService evaluates its trigger:

- Total heavy bytes: 176 KB (ctx-1) + 254 KB (ctx-2) + 12 KB (ctx-3)
  = 442 KB — crosses the 300 KB curate_threshold_kb
- Pending items: 3 (all still pending)
- Throttle: no prior curator this session, so throttle not engaged
- **Curator fires.**

ContextService sets `curator_pending = True`. On the next request
build, `curator.build_injection()` is called and emits the curator
prompt payload.

**First, a critical note on roles and the tool-calling protocol.**
kollab uses native openai-style tool calling for openrouter,
xai, openai, and anthropic. That means assistant responses that
invoke a tool do NOT embed the tool call as inline xml/prose; they
use the `tool_calls` array on the assistant message, and the tool
result comes back as a `role: "tool"` message linked by
`tool_call_id`.

So the history actually looks like:

```
msg 0  user       "do a full bug sweep..."
msg 1  assistant  content: "ok. pulling git log..." + tool_calls=[...]
msg 2  tool       tool_call_id=call_git1, content=git log output (176 KB)
msg 3  assistant  content: "got it, phase 4.5..." + tool_calls=[...]
msg 4  tool       tool_call_id=call_dcd1, content=dead code report (254 KB)
msg 5  assistant  content: "402 items..." + tool_calls=[...]
msg 6  tool       tool_call_id=call_rd1, content=state/context.py read (12 KB)
```

Tool messages use `role: "tool"`, NOT `role: "user"`. This means:

- A tool-role message followed by a user-role curator injection
  is NOT a consecutive-user-role situation. Providers accept this
  pattern natively.
- **ContextService does NOT need to merge the curator payload into
  the last message when the last message is tool-role.** It can
  append the curator as its own user-role message.
- Merging is only required when the last message is already
  user-role (e.g., the user just typed something, or a turn that for
  some reason ends on a raw user input rather than a tool result).

For this example, the curator fires right after a tool_result, so
the last message is tool-role, and the curator can be appended as
its own user-role message without any merging. **The merging rule
still exists for the case where the last message is user-role;
it's just not triggered here.**

**The request body ContextService actually builds:**

```json
{
  "model": "x-ai/grok-4.1-fast",
  "temperature": 0.7,
  "max_tokens": 8192,
  "stream": true,
  "session_id": "2604110000-chronos-crown",
  "tools": [
    {"type": "function", "function": {"name": "terminal", "description": "...", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "file_read", "description": "...", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "lines": {"type": "string"}}}}}
  ],
  "messages": [
    {
      "role": "system",
      "content": "You are koordinator, a kollab agent working in the kollab repo...\n\n## Context Service\n\n[full static manual explaining <curate>, <context/>, <evict>, <force/>, and the [context service] bracketed-prefix convention]\n\n[full static system prompt, ~60 KB, cached hot across every turn of this session]"
    },
    {
      "role": "user",
      "content": "do a full bug sweep on the repo. check everything and give me a report."
    },
    {
      "role": "assistant",
      "content": "ok. pulling git log for orientation, then lint and dead code scan.",
      "tool_calls": [
        {
          "id": "call_git1",
          "type": "function",
          "function": {
            "name": "terminal",
            "arguments": "{\"command\": \"git log --oneline HEAD~40..HEAD\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_git1",
      "content": "c8a7eec style: ruff+black pass across repo\n4262484 feat(hub,agents,cli): launcher fix, auto-detect improvements, keyword sub-agent offers, gem JSON migration, roaster bundle\nb02d304 docs: phase 4.5 documentation refresh via 5 parallel tglm agents\n278a8cd fix(cli): kollab --hub -h / --help / no-args now prints hub help\naa4b633 fix(profile): respect persisted active\n542d742 docs: remove internal dev docs, fix CLAUDE.md and README.md\n286ae47 initial public release\n... [34 more commits, 176 KB of full log output] ..."
    },
    {
      "role": "assistant",
      "content": "got it, phase 4.5 was the big recent work. now running the dead code detector.",
      "tool_calls": [
        {
          "id": "call_dcd1",
          "type": "function",
          "function": {
            "name": "terminal",
            "arguments": "{\"command\": \"python scripts/dead_code_detect.py\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_dcd1",
      "content": "Starting dead code detection...\n[1/5] Scanning for backup files...\n[2/5] Scanning for TODO/FIXME comments...\n[3/5] Scanning for pass statements...\n[4/5] Scanning for NotImplementedError stubs...\n[5/5] Running vulture analysis...\n  Vulture scan complete.\n\nDetection complete! Found 402 items.\n================================================================\nDEAD CODE DETECTION REPORT\nGenerated: 2026-04-11 00:02:00\n================================================================\n\nSUMMARY\n--------\nTotal Findings: 402\n\nFindings by Category:\n  confirmed_dead_code: 42\n  unused_imports: 180\n  todo_fixme_markers: 90\n  unreachable_branches: 60\n  notimplemented_stubs: 30\n\n[... 254 KB of full report details, every finding with file path, line number, function name, and context ...]"
    },
    {
      "role": "assistant",
      "content": "402 items, lots of noise. let me check state/context.py since it had one of the confirmed dead items.",
      "tool_calls": [
        {
          "id": "call_rd1",
          "type": "function",
          "function": {
            "name": "file_read",
            "arguments": "{\"path\": \"kollabor/state/context.py\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_rd1",
      "content": "kollabor/state/context.py (12 KB, 298 lines)\n\n\"\"\"Conversation context registry for daemon.\"\"\"\nimport asyncio\nfrom pathlib import Path\nfrom typing import Dict, Optional, List\nfrom kollabor_events.bus import EventBus\nfrom .interface import StateService\n\nclass ConversationContext:\n    def __init__(self, context_id: str, event_bus: EventBus):\n        self.context_id = context_id\n        self.event_bus = event_bus\n        self.parent = None  # ← the ref cycle source\n        ... [294 more lines of real code] ..."
    },

    // ─────────────────────────────────────────────────────────────
    // EPHEMERAL CURATOR INJECTION
    //
    // The previous message is role "tool", not role "user", so
    // appending a user-role curator message here is NOT a
    // consecutive-user-role situation. ContextService can add it as
    // its own separate message without merging.
    //
    // This user message was NOT typed by the user and is NOT in
    // conversation_manager.messages. It was built at request time
    // by context_service.curator.build_injection() because
    // curator_pending was just flipped to True when the total
    // crossed 300 KB.
    //
    // Role "user" with "[context service: curator]" prefix so the
    // model recognizes it as runtime machinery, not human input.
    //
    // After this request completes, this entire message is
    // discarded. conversation_manager.messages stops at the
    // tool message above. The curator payload will not appear in
    // the next request.
    // ─────────────────────────────────────────────────────────────
    {
      "role": "user",
      "content": "[context service: curator]\n\n3 heavy items have piled up and crossed the curation threshold.\nmark each one keep or summary before the next compaction.\n\nheavy items awaiting decision:\n\n  ctx-1  tool_result  terminal: git log              176 KB   pending\n         call_git1, turn 1, hash 9f8a7b6c\n         cmd: git log --oneline HEAD~40..HEAD\n\n  ctx-2  tool_result  terminal: dead_code_detect     254 KB   pending\n         call_dcd1, turn 2, hash 4e5d6c7b\n         cmd: python scripts/dead_code_detect.py\n\n  ctx-3  tool_result  file_read: state/context.py     12 KB   pending\n         call_rd1, turn 3, hash abc123de\n         path: kollabor/state/context.py\n\n  total:       442 KB\n  threshold:   300 KB  (exceeded)\n  compaction:  ~58 K tokens until auto-compact fires\n\nfor each item, respond with ONE of:\n\n  <curate id=\"ctx-N\" decision=\"keep\">\n  explain why you need this verbatim. stays in history full size.\n  </curate>\n\n  <curate id=\"ctx-N\" decision=\"summary\">\n  your compressed version. this exact text replaces the full\n  tool result at compaction time. include anything future-you\n  will need to work with this material again.\n  </curate>\n\nunmarked items default to auto-summary at compaction time, which\nwill be a generic LLM pass. your own summaries are higher quality.\n\nthe curator won't prompt again for at least 2 turns. you can\nproactively emit <curate> tags any turn without being prompted.\n\nother commands:\n  <context/>                inspect full ledger\n  <context filter=\"pending\"/> list only undecided items\n  <evict id=\"ctx-N\">reason</evict>   drop immediately (breaks cache)\n  <force/>                  override default dedup on next file read"
    }
  ]
}
```

**Key properties of this request:**

1. Tool calls use native openai format: `tool_calls` array on the
   assistant message, `role: "tool"` messages with matching
   `tool_call_id` for the results. kollabor's response_parser
   handles this automatically via the openrouter provider's
   `OpenAIResponseTransformer`.

2. Assistant messages can have BOTH a `content` field (the model's
   narration/reasoning) AND a `tool_calls` field (the actions it's
   taking). The content portion is what the user sees rendered in
   the TUI; the tool_calls portion drives the tool pipeline.

3. The curator injection is a NEW user-role message after the last
   tool message. Because `role: "tool"` ≠ `role: "user"`, there's
   no consecutive-same-role issue and no merging is needed.

4. ContextService tracks ledger entries by the `tool_call_id`
   (call_git1, call_dcd1, call_rd1) because that's the stable
   identifier for a tool result in openai format. The ctx_ids
   (ctx-1, ctx-2, ctx-3) are ContextService's own monotonic
   counter; each one maps to exactly one `tool_call_id`.

**When merging IS needed:** if the last message in history were
user-role (e.g. the user had just typed "keep going" right before the
curator fired), the curator payload would have to be merged into
that user's message with a `---` separator to avoid back-to-back
user messages. That's the fallback path; it's not what happens in
this specific example because the last message here is tool-role.

**Persistence rule:** after the request completes, nothing is
persisted about the curator injection. `conversation_manager.messages`
stops at the tool message (`call_rd1`). The next request will be
built fresh from history plus whatever new injections apply at
that moment.

**What koordinator responds:**

```
reviewing heavy items before i keep going. git log was
orientation, i have what i need from it. deadcode report was
huge but i only need the 3 real fixes out of 402 — rest is
noise i can summarize down. state/context.py i'm about to edit
to fix the parent ref cycle at line 140, keeping it verbatim.

<curate id="ctx-1" decision="summary">
git log --oneline HEAD~40..HEAD: 40 commits dominated by phase
4.5 daemon transparency work (18 commits c8a7eec..286ae47),
hub mesh fixes (4 commits including 278a8cd flock race, 4262484
launcher auto-detect), and routine style/docs passes. main is
1 commit ahead of kollaborai/main, unpushed. nothing
security-relevant in the window. if i need specific commits
again i can git log a narrower range.
</curate>

<curate id="ctx-2" decision="summary">
dead code detector ran on the full repo, found 402 items across
5 categories. the 42 confirmed_dead_code items are the only
actionable findings; the 180 unused_imports are mostly lazy
kollabor_tui widget exports, 90 todo_fixme markers are non-
blocking, 60 unreachable branches are legacy compat shims, 30
notimplemented stubs are in agent_runtime on purpose.

real fixes extracted from the confirmed 42:
  - plugins/hub/plugin.py:412  broadcast() race on coordinator_ready
  - kollabor/daemon.py:88       signal handler file descriptor leak
  - kollabor/state/context.py:140  parent reference cycle

full report dumped to /tmp/deadcode-04110002.txt if i need to
re-reference specific findings. the 3 above are the only
things worth fixing this session.
</curate>

<curate id="ctx-3" decision="keep">
actively editing kollabor/state/context.py across the next
several turns. line 140 has the parent ref cycle that the
deadcode scan flagged: ConversationContext.parent is assigned
but never cleared when the context is removed from the
ContextRegistry, leaving dangling refs that prevent gc. i
need the full file in context to trace how parent is set and
where the cleanup should go.
</curate>

starting with state/context.py since it's the actual bug fix,
the others are quick follow-ups after.
```

After response_parser processes this response:

- ctx-1.decision = "summary", body = "git log --oneline HEAD~40..HEAD..."
- ctx-2.decision = "summary", body = "dead code detector ran on..."
- ctx-3.decision = "keep", body = "actively editing kollabor/state..."
- All three `<curate>` blocks are stripped from the assistant
  content that gets persisted to history.

**The stored (post-strip) version of koordinator's message is:**

```
reviewing heavy items before i keep going. git log was
orientation, i have what i need from it. deadcode report was
huge but i only need the 3 real fixes out of 402 — rest is
noise i can summarize down. state/context.py i'm about to edit
to fix the parent ref cycle at line 140, keeping it verbatim.

starting with state/context.py since it's the actual bug fix,
the others are quick follow-ups after.
```

### Confirmation injection on the NEXT turn

ContextService now has 3 decisions recorded and no pending items.
It flips `confirmation_pending = True`. When the next request is
built (after koordinator's next action triggers a turn), a small
confirmation block is injected as another ephemeral user message:

```json
{
  "role": "user",
  "content": "[context service] decisions recorded\n\n  ctx-1  summary  (176 KB → 412 bytes at next compact, saves 175.6 KB)\n  ctx-2  summary  (254 KB → 690 bytes at next compact, saves 253.3 KB)\n  ctx-3  keep     (12 KB retained in history)\n\n  total pending:   0\n  after compact:   12 KB heavy + 1.1 KB summaries = 13.1 KB\n  savings:         428.9 KB will be reclaimed when auto-compact fires\n  next compact:    still ~58 K tokens away (decisions don't trigger compact)"
}
```

This appears once, in the request right after curation happened,
then never again. The agent sees the exact byte savings it bought
with its decisions, which gives positive feedback and makes future
curation decisions more deliberate.

### Full session cost comparison

This is the crucial number to understand why the whole system is
worth building. Run the same session with and without ContextService:

**Without ContextService (current kollab behavior):**

```
turn 1  →  72 KB prompt   (system + initial user)
turn 2  → 248 KB prompt   (+ 176 KB git log)
turn 3  → 502 KB prompt   (+ 254 KB dead code)
turn 4  → 514 KB prompt   (+ 12 KB state/context)
turn 5  → 520 KB prompt   (grows with each assistant msg)
turn 6  → 525 KB prompt
...
turn N  → 820 KB prompt   (unbounded)

compaction never fires because openrouter streaming usage is 0.
every turn from turn 2 onward reprocesses the full history.
total input tokens across 10 turns: ~5 M
cost at grok-4.1-fast pricing: ~$0.75
```

**With ContextService:**

```
turn 1  →  72 KB prompt                    (unchanged)
turn 2  → 248 KB prompt                    (git log ingested, ctx-1 pending)
turn 3  → 502 KB prompt                    (dead code ingested, curator fires)
turn 4  → 503 KB prompt + 2 KB curator     (ephemeral injection)
         agent emits curate decisions
turn 5  → 504 KB prompt + 1 KB confirmation
         (heavy bytes unchanged, decisions stored in-memory)
turn 6  → 505 KB prompt                    (normal, no injection)
...
turn 8  → 507 KB prompt                    (prompt tokens cross 100K)
         COMPACTION FIRES
         compaction consults ledger:
           ctx-1 summary → replace 176 KB with 412 bytes
           ctx-2 summary → replace 254 KB with 690 bytes
           ctx-3 keep    → leave 12 KB alone
         compaction does NOT call LLM (agent-provided summaries)
         new prompt size: 507 KB - 176 KB - 254 KB + 1.1 KB = 78 KB
turn 9  →  79 KB prompt                    (post-compact, cache resumes)
turn 10 →  80 KB prompt

total input tokens across 10 turns: ~1.8 M  (64% reduction)
cost at grok-4.1-fast pricing: ~$0.27  (64% reduction)
one LLM call saved at compaction (no summarization pass needed).
```

The savings grow nonlinearly with session length because the big
tool results stop getting replayed on every turn.

### Response handling

Agent's next response is scanned for `<curate>` tags. Each valid one:

1. Updates `LedgerEntry.decision` and `decision_body`
2. Adds `decided_at` timestamp
3. Emits a tiny confirmation in the next turn's tail (also not
   persisted):

```
[context service] decisions recorded:
  ctx-3  summary  (254KB -> 412b at next compact, -253.6KB)
  ctx-4  keep     (12KB retained)
```

### Throttling

After first curator prompt, the agent is expected to volunteer
`<curate>` tags as it works without being asked. Re-prompting only
happens when >50% of heavy bytes are still pending AND >= 2 turns
have elapsed.


## File read lifecycle (the interesting case)

This is where ContextService pays off day-to-day.

### First read

```
agent emits:
  <file_read path="plugins/hub/plugin.py"/>

context_service.file_read_hook intercepts:
  path = "plugins/hub/plugin.py"
  file_tracker.has_version(path) -> False
  → read from disk, hash = xxh64(content) = "abc123"
  → create LedgerEntry(ctx-7, file_read, ..., hash=abc123, 48KB)
  → return full content to tool pipeline

tool result inserted into history at msg_idx=12
ledger entry linked: LedgerEntry.msg_idx = 12
```

### Second read, content unchanged

```
agent emits:
  <file_read path="plugins/hub/plugin.py"/>

context_service.file_read_hook intercepts:
  path = "plugins/hub/plugin.py"
  disk_hash = xxh64(read(path)) = "abc123"
  file_tracker.latest_hash(path) = "abc123"
  → STALE HIT

return to tool pipeline:
  "[context service: file plugins/hub/plugin.py is already in your
   context as ctx-7 (48KB, read 12 turns ago, hash abc123 unchanged).
   use <force/> to reread, <context/> to inspect.]"

size of response: ~200 bytes (vs 48KB)
ledger entry ctx-7: last_accessed_at bumped, read_count incremented
no new entry created
```

### Second read, content changed

```
agent emits:
  <file_read path="plugins/hub/plugin.py"/>

disk_hash = "def456"
file_tracker.latest_hash = "abc123"
→ CHANGED, generate diff

create LedgerEntry(ctx-11, file_read, tool="diff",
                   hash=def456, size=<diff_size>,
                   file_version=2, prior_ctx_id="ctx-7")

return to tool pipeline:
  "[context service: file plugins/hub/plugin.py changed since ctx-7.
   diff from abc123 -> def456 (~3KB of changes):
   <diff content>
   full file is 49KB. use <force/> for the full content instead.]"

ctx-7 remains as-is in history (it's what the agent saw at the time)
ctx-11 is the new diff entry
```

### Second read, with force

```
agent emits:
  <file_read path="plugins/hub/plugin.py" force="true"/>

context_service bypasses dedup entirely
  → full read
  → new LedgerEntry(ctx-11, file_read, hash=def456, 49KB)
  → full content into history

this is the "i'm lost, show me everything again" escape hatch.
```

### Partial reread of already-held file

```
agent already has ctx-7 (full file, lines 1-693)
agent emits:
  <file_read path="plugins/hub/plugin.py" lines="410-430"/>

file_tracker checks:
  hash unchanged at 410-430 -> STALE HIT with range info

return:
  "[context service: lines 410-430 of plugins/hub/plugin.py are
   part of ctx-7 which you have in full. no new content to return.]"

OR if agent had only lines 1-100 previously:
  new entry ctx-12 with lines=410-430 specifically
  linked to FileVersion(plugins/hub/plugin.py) alongside ctx-7
```


## Hub integration (optional phase)

ContextService can publish ledger events to the hub so peer agents
see who is holding what.

### Broadcast schema

```python
HubContextEvent(
    agent="koordinator",
    event="read",                       # read | curate | evict | reread
    ctx_id="ctx-7",
    kind="file_read",
    label="plugins/hub/plugin.py",
    content_hash="abc123",
    size_bytes=48000,
    decision="keep",
    timestamp="2026-04-11T09:32:29",
)
```

### Hub-aware file reads

```
koordinator already read plugins/hub/plugin.py at hash abc123.
lapis starts work, emits:
  <file_read path="plugins/hub/plugin.py"/>

lapis's context_service checks local ledger:
  → not found locally
  → checks hub.ledger_index
  → found: koordinator has ctx-7 at hash abc123
  → disk_hash matches abc123
  → lapis still reads the file (can't share raw content cross-agent)
  → BUT annotates the ledger entry:
    ctx-1 (lapis)  file_read  plugins/hub/plugin.py  48KB
                   also_held_by: [koordinator]
```

### Hub-aware asks

```xml
<hub_ask_ctx target="koordinator" ctx_id="ctx-7">
what's your summary of hub/plugin.py? i need to modify
the broadcast function and don't want to re-read if you
already know what's there.
</hub_ask_ctx>
```

Routes via hub messenger, koordinator responds with its `<curate>`
decision body (if decided) or a fresh summary. Prevents redundant
reads across the mesh.

### Divergent hash warnings

If two agents claim to have the "same" file at different hashes:

```
[context service] WARNING: plugins/hub/plugin.py has divergent hashes
across the hub:
  koordinator: ctx-7   hash abc123  (t-12)
  lapis:       ctx-1   hash def456  (t-3)

file changed between reads. koordinator may be working from stale
content. recommend koordinator emit <file_read force="true">.
```


## Compaction integration

The existing `context_compaction_plugin` is updated to consult
ContextService at compact time instead of calling an LLM to summarize.

### Before (today)

```python
# plugins/context_compaction_plugin.py
async def _run_compaction(self, history):
    old_msgs = history[:split_point]
    summary = await self._summarize_via_llm(old_msgs)  # 2nd API call!
    return [summary_msg, *history[split_point:]]
```

### After (with ContextService)

```python
async def _run_compaction(self, history):
    compacted = []
    for msg in history[:split_point]:
        entry = context_service.entry_for_message(msg.uuid)
        if entry is None:
            compacted.append(msg)  # untracked, keep as-is
            continue
        if entry.decision == "keep":
            compacted.append(msg)  # verbatim
        elif entry.decision == "summary":
            compacted.append(
                ConversationMessage(
                    role=msg.role,
                    content=f"[ctx-{entry.ctx_id} summary] {entry.decision_body}",
                    metadata={"compacted_from": msg.uuid, "ctx_id": entry.ctx_id},
                )
            )
        else:  # pending or evicted
            compacted.append(
                ConversationMessage(
                    role=msg.role,
                    content=f"[ctx-{entry.ctx_id}: {entry.kind} {entry.label}, "
                            f"{entry.size_bytes // 1024}KB, no curation, elided]",
                )
            )
    return [*compacted, *history[split_point:]]
```

Key wins:
- No LLM call at compaction time (compaction becomes synchronous, fast)
- Agent's own summaries are higher quality than a generic LLM summary
- Pending items get a clear marker so the agent knows what was dropped
- Compaction event is logged by ctx_id for later review


## System prompt addition (static, cacheable)

ContextService adds a **static** section to the system prompt. Because
it's static (no per-turn state), it doesn't break prefix cache. It's
the agent's user manual for the service.

This goes in whatever the current default system prompt is. Not via
`<trender>` — just plain text the agent always sees.

```markdown
## Context Service

Your context is tracked by a service that dedupes file reads, records
hashes, and lets you curate what stays in history.

### Reading files

Just use <file_read path="..."/> normally. If you already have the
file at the same hash, the service will return a "stale" marker
instead of the full content - this saves tokens and keeps your cache
warm. Rereading unchanged files is free.

If the file has changed since your last read, you'll get a diff
instead of the full file. Add force="true" to override and get the
full content:

    <file_read path="plugins/hub/plugin.py" force="true"/>

### Curating heavy items

Tool results and file reads over 8KB are tracked as ledger entries
with ids like ctx-1, ctx-2. When they pile up, a curator prompt will
ask you to decide what to keep verbatim vs replace with a summary.

For each heavy item, respond with one of:

    <curate id="ctx-1" decision="keep">
    reason you need it verbatim
    </curate>

    <curate id="ctx-2" decision="summary">
    your compressed version of what this contained.
    include everything future-you will need.
    </curate>

You can proactively curate anything without waiting for a prompt.
You can change a prior decision by re-emitting the tag.

### Inspecting your own context

Emit <context/> any time to see your current ledger. Filter with
<context filter="pending"/> or <context filter="file_read"/>.

### Dropping items immediately

If you're done with an item and want it gone now (not at next
compaction), emit:

    <evict id="ctx-3">
    already extracted what i needed
    </evict>

This breaks prefix cache from that point forward. Only use it when
the savings outweigh the cost (big item, long remaining session).

### Default behaviors

- file_read is stale-checked by default
- tool results over 32KB are truncated with a reference to full output
- tracked items under 8KB are not ledgered
- unmarked pending items default to auto-summary at compaction time
- ledger entries have no ttl by default; add ttl="N" in seconds to
  auto-demote
```


## Configuration

```json
{
  "plugins": {
    "context_service": {
      "enabled": true,
      "heavy_threshold_kb": 8,
      "curate_threshold_kb": 300,
      "curator_throttle_turns": 2,
      "tool_result_cap_kb": 32,
      "tool_result_overflow_action": "truncate_with_ref",
      "file_dedup_mode": "stale_hit",
      "default_decision": "summary",
      "hub_broadcast_enabled": false,
      "hash_algorithm": "xxh64",
      "ledger_max_entries": 1000
    }
  }
}
```

| key | default | meaning |
|-----|---------|---------|
| `heavy_threshold_kb` | 8 | items smaller than this aren't ledgered |
| `curate_threshold_kb` | 300 | total ledger size that triggers curator |
| `curator_throttle_turns` | 2 | min turns between curator re-prompts |
| `tool_result_cap_kb` | 32 | hard cap on tool result size in history |
| `tool_result_overflow_action` | `truncate_with_ref` | `truncate_with_ref` \| `stored_file_ref` \| `error` |
| `file_dedup_mode` | `stale_hit` | `stale_hit` \| `diff` \| `force_always` |
| `default_decision` | `summary` | what pending items become at compact time |
| `hub_broadcast_enabled` | `false` | phase-gated, off by default |
| `hash_algorithm` | `xxh64` | xxh64 \| blake3 \| sha256 |


## Config widgets

Surfaced in the `/config` modal so a user can flip everything at runtime:

```python
@staticmethod
def get_config_widgets() -> Dict[str, Any]:
    return {
        "title": "Context Service",
        "widgets": [
            {"type": "checkbox",
             "label": "Enabled",
             "config_path": "plugins.context_service.enabled",
             "help": "Track heavy items and enable curation"},
            {"type": "slider",
             "label": "Heavy Threshold (KB)",
             "config_path": "plugins.context_service.heavy_threshold_kb",
             "min_value": 1, "max_value": 64, "step": 1,
             "help": "Items smaller than this are not tracked"},
            {"type": "slider",
             "label": "Curate Threshold (KB)",
             "config_path": "plugins.context_service.curate_threshold_kb",
             "min_value": 50, "max_value": 1000, "step": 50,
             "help": "Total ledger size that triggers curator prompt"},
            {"type": "slider",
             "label": "Tool Result Cap (KB)",
             "config_path": "plugins.context_service.tool_result_cap_kb",
             "min_value": 8, "max_value": 256, "step": 8,
             "help": "Hard cap on any single tool result in history"},
            {"type": "dropdown",
             "label": "File Dedup Mode",
             "config_path": "plugins.context_service.file_dedup_mode",
             "options": ["stale_hit", "diff", "force_always"],
             "help": "What to return on re-read of unchanged/changed files"},
            {"type": "dropdown",
             "label": "Default Decision",
             "config_path": "plugins.context_service.default_decision",
             "options": ["summary", "keep", "elide"],
             "help": "Fallback for pending items at compaction time"},
            {"type": "checkbox",
             "label": "Hub Broadcast",
             "config_path": "plugins.context_service.hub_broadcast_enabled",
             "help": "Share ledger events with hub peers"},
        ],
    }
```


## Status widget

New widget on status bar row 1 (near the existing `ctx:` from compaction):

```
◈ koordinator +4   ctx: 490K ▼300K ◐ 2 pending   tokens: 72K/100K
```

- `ctx: 490K` — total ledger bytes
- `▼300K` — curate threshold
- `◐ 2 pending` — how many entries await decision (filled icon = curator active)
- replaces the old `ctx:` widget from the compaction plugin


## Slash commands

### `/context`

```
/context                          # print full ledger to screen
/context show ctx-3               # print one entry with body
/context evict ctx-3              # manual eviction (user override)
/context force reread <path>      # force reread a file, creating new entry
/context set <id> keep "reason"   # set decision manually
/context set <id> summary "body"  # set decision manually
/context stats                    # ledger byte breakdown by kind
/context export                   # dump ledger as JSON
/context clear                    # wipe ledger (does not touch history)
```

User-facing for debugging. Agent-facing is all XML tags.


## End-to-end example session

This is what a full curated session looks like from the agent's POV.

```
[system prompt: static, cached forever]
including the Context Service user manual section.

t=1  user: "run a full bug sweep on the codebase"

t=2  assistant: (todo list)
     <terminal>git status</terminal>
     <terminal>git log --oneline -40</terminal>

t=3  tool results come back, ~180KB git log.
     context_service.ingest():
       ctx-1  tool_result  git_log       2KB    ← under threshold, skip
       ctx-2  tool_result  git_log_full  176KB  ← ledgered, pending

t=4  assistant: reviews, continues
     <terminal>python scripts/dead_code_detect.py</terminal>

t=5  tool result: 254KB dead code report.
     context_service.ingest():
       ctx-3  tool_result  deadcode  254KB  pending
     total heavy: 430KB  ← crosses 300KB threshold

t=6  (curator fires at next request)
     tail-only ephemeral user message injected into the request
     with the [context service: curator] prefix:

     request body for this turn ends with:
       ...previous history...
       {"role": "user", "content":
         "[context service: curator]\n\n
          heavy items awaiting decision:\n
            ctx-2  tool_result git_log_full  176KB  t=3  pending\n
            ctx-3  tool_result deadcode       254KB  t=5  pending\n\n
          total: 430KB / 300KB threshold\n
          mark with <curate id=\"...\" decision=\"...\">...</curate>"}

     assistant response:
     reviewing — git log is orientation, summarizing. deadcode
     report has the fixes extracted, summarizing.

     <curate id="ctx-2" decision="summary">
     git log --oneline HEAD~40..HEAD: phase 4.5 daemon work
     (18 commits c8a7eec..286ae47), hub fixes (4 commits),
     style passes. main is 1 commit ahead of kollaborai/main.
     </curate>

     <curate id="ctx-3" decision="summary">
     402 items found. real fixes extracted:
     - hub/plugin.py:412 broadcaster race
     - daemon.py:88 signal handler leak
     - state/context.py:140 parent ref cycle
     ignored: 180 unused imports, 90 TODOs, 60 unreachable.
     full report: /tmp/deadcode-04110002.txt
     </curate>

     now fixing hub/plugin.py. <file_read path="plugins/hub/plugin.py"/>

     After response_parser processes this response:
       - ctx-2 decision stored: summary + body
       - ctx-3 decision stored: summary + body
       - <curate> blocks stripped from assistant content
       - <file_read> tag handled by tool pipeline
       - ephemeral curator user message is DISCARDED after the
         request completes, never written to conversation history

── t=7  FIRST READ (fresh ingest) ──────────────────────────────

The agent decides to read hub/plugin.py for the first time. It
emits a native openai tool_call.

assistant message (msg 7):
  {
    "role": "assistant",
    "content": "starting with hub/plugin.py since broadcast() lives there.",
    "tool_calls": [
      {
        "id": "call_fr1",
        "type": "function",
        "function": {
          "name": "file_read",
          "arguments": "{\"path\": \"plugins/hub/plugin.py\"}"
        }
      }
    ]
  }

kollabor's file_read tool pipeline runs the call:

  1. ContextService.file_read_hook intercepts BEFORE disk read
  2. Checks file_tracker: has_version("plugins/hub/plugin.py")? NO
  3. Reads from disk, computes hash: xxh64 = "abc123de"
  4. Not a hit, not a stale read — this is a FRESH READ
  5. Creates new LedgerEntry:
       ctx_id: "ctx-4"
       kind: "tool_result"
       tool: "file_read"
       label: "plugins/hub/plugin.py"
       content_hash: "abc123de"
       size_bytes: 48000
       tool_call_id: "call_fr1"
       decision: "pending"
  6. Returns the full file content to the tool pipeline

tool message (msg 8) added to history:
  {
    "role": "tool",
    "tool_call_id": "call_fr1",
    "content": "plugins/hub/plugin.py (48 KB, 693 lines)\n\n\"\"\"Hub plugin: peer-to-peer agent mesh.\"\"\"\n...[693 lines of real code]..."
  }

Agent sees the full file on its next turn. ctx-4 is now in the
ledger with pending status.


── t=8  REREAD, UNCHANGED (stale hit) ──────────────────────────

Two turns later, the agent wants to re-check the file after
running some tests. It emits the same file_read tool_call:

assistant message (msg 11):
  {
    "role": "assistant",
    "content": "let me double check the broadcast function before patching.",
    "tool_calls": [
      {
        "id": "call_fr2",
        "type": "function",
        "function": {
          "name": "file_read",
          "arguments": "{\"path\": \"plugins/hub/plugin.py\"}"
        }
      }
    ]
  }

ContextService intercepts:

  1. file_read_hook fires BEFORE disk read
  2. Checks file_tracker: has_version("plugins/hub/plugin.py")? YES
     → file_tracker.latest_hash = "abc123de"
  3. Reads disk_hash (fast — just hashes the bytes, no parse):
     → xxh64 = "abc123de"
  4. Hashes match. This is a STALE HIT.
  5. Does NOT create a new ledger entry.
  6. Updates ctx-4:
       last_accessed_at = now
       read_count += 1  (now 2)
  7. Returns a SHORT MARKER instead of the file content

tool message (msg 12) added to history:
  {
    "role": "tool",
    "tool_call_id": "call_fr2",
    "content": "[context service: stale hit]\nplugins/hub/plugin.py is already in your context as ctx-4 (read 4 turns ago, hash abc123de unchanged, 48 KB). The full content is in the tool result at message 8. Reference it there instead of re-reading.\n\nIf you need to force a fresh read (e.g., you suspect a silent write):\n  set \"force\": true in the file_read arguments\n\nIf you need to inspect a specific range:\n  set \"lines\": \"N-M\" in the file_read arguments (will still hit the cached version if the hash is unchanged)"
  }

The tool message is ~350 bytes instead of 48 KB. The agent's
next assistant turn sees the stale marker, understands the file
is already available at msg 8, and proceeds without consuming
48 KB of new context.

Key point: the stale marker goes through the SAME native openai
tool-result protocol as the real file read. The agent doesn't
need to know about ContextService's xml layer for this case.
All it sees is a short tool result telling it where the real
content lives. No special handling required.


── t=9  REREAD, CHANGED (diff on change) ───────────────────────

One turn later, the agent patched the file and wants to verify
the edit landed. It emits another file_read:

assistant message (msg 13):
  {
    "role": "assistant",
    "content": "patch should be in. re-reading to verify the broadcast guard is on disk.",
    "tool_calls": [
      {
        "id": "call_fr3",
        "type": "function",
        "function": {
          "name": "file_read",
          "arguments": "{\"path\": \"plugins/hub/plugin.py\"}"
        }
      }
    ]
  }

ContextService intercepts:

  1. file_read_hook fires BEFORE disk read
  2. Checks file_tracker: latest hash = "abc123de"
  3. Reads disk_hash: xxh64 = "def456ab"
  4. Hashes DIFFER. File has changed since ctx-4.
  5. Generates a unified diff from ctx-4's content to the
     current disk content.
  6. Creates NEW LedgerEntry for the DIFF specifically:
       ctx_id: "ctx-5"
       kind: "file_read"
       tool: "diff"
       label: "plugins/hub/plugin.py (diff abc123de → def456ab)"
       content_hash: "def456ab"
       size_bytes: 1200  (the diff, not the full file)
       tool_call_id: "call_fr3"
       decision: "pending"
       prior_ctx_id: "ctx-4"
       file_version: 2
  7. Updates file_tracker to record the new version alongside
     the old one. Both ctx-4 (v1) and ctx-5 (v2) now coexist.
  8. Returns the diff as the tool result.

tool message (msg 14):
  {
    "role": "tool",
    "tool_call_id": "call_fr3",
    "content": "[context service: file changed]\nplugins/hub/plugin.py changed since ctx-4 (hash abc123de → def456ab).\nReturning diff instead of full file (1.2 KB vs 49 KB).\nThis diff is ctx-5. The full prior version is still in context at ctx-4 (msg 8).\n\n--- plugins/hub/plugin.py  (ctx-4, 48 KB)\n+++ plugins/hub/plugin.py  (ctx-5, 49 KB)\n@@ -410,6 +410,11 @@\n     def broadcast(self, msg: HubMessage) -> None:\n+        if not self.coordinator.coordinator_ready.is_set():\n+            logger.warning('broadcast before coordinator ready, dropping')\n+            return\n         for peer in self.peers.values():\n             try:\n                 await self.messenger.send(peer, msg)\n\nIf you need the full current file (not just the diff):\n  set \"force\": true in the file_read arguments"
  }

The agent sees the diff and confirms the patch landed. ~1.2 KB
into history instead of 49 KB. ctx-4 is still present in msg 8
and still valid for any reasoning that needs the full prior
state. ctx-5 is the diff, linked via prior_ctx_id.

If the agent had set force=true in the arguments:

assistant message:
  {
    "role": "assistant",
    "content": "let me force a full re-read to double-check formatting.",
    "tool_calls": [
      {
        "id": "call_fr4",
        "type": "function",
        "function": {
          "name": "file_read",
          "arguments": "{\"path\": \"plugins/hub/plugin.py\", \"force\": true}"
        }
      }
    ]
  }

ContextService would SKIP the stale-hit check entirely, return
the full 49 KB content, and create ctx-6 as a new full file
entry (not a diff). ctx-4, ctx-5, and ctx-6 would all coexist
in the ledger with file_version = 1, 2, 2 respectively.

t=10 ... many more turns of small tool calls ...

t=N  prompt_tokens crosses 100K, compaction fires.
     compaction asks context_service for each old msg:
       msg 3  (ctx-2) → decision=summary, replace with body
       msg 5  (ctx-3) → decision=summary, replace with body
       msg 7  (ctx-4) → decision=pending → default auto-summary
       msg 9  (ctx-5) → under threshold, keep

     result: 430KB+48KB+1KB → ~1KB of summaries
     one cache miss at compact point, hot from there on.
```


## Phasing / implementation order

### Phase A — Bleeding fixes (must-ship, ~300 lines)

Blockers from the current broken state. Build these first,
ContextService layers on top.

1. ✎ `packages/kollabor-ai/src/kollabor_ai/providers/openrouter_provider.py`
   - Add `session_id` kwarg passthrough
   - Extend kwargs allowlist or add a `provider_params` dict pattern
2. ✎ `packages/kollabor-ai/src/kollabor_ai/providers/openrouter_provider.py`
   (+ streaming_handler)
   - Capture `usage` chunk from openrouter SSE stream so
     `get_last_token_usage()` returns real numbers
   - This alone unblocks compaction gate 1
3. ✎ `kollabor/llm/llm_coordinator.py` or the tool result handler
   - Hard cap on tool results (config: `tool_result_cap_kb`, default 32)
   - Truncated result gets a `[... N KB elided ...]` tail

### Phase B — ContextService core (~600 lines)

Can ship incrementally. Gets the dedup and curation working for a
single agent.

1. ✚ `packages/kollabor-ai/src/kollabor_ai/context_service/` skeleton
   (service.py, ledger.py, models.py, hash_utils.py)
2. ✚ `file_tracker.py` with path→hash→entry mapping
3. ✎ `response_parser.py` — parse `<curate>`, `<context>`, `<evict>`,
   `<force>`, strip from display
4. ✎ `llm_coordinator.py` / tool result handler — ingest heavy items
   into ledger on tool result arrival
5. ✎ file_read tool — consult ledger first, return stale/diff/fresh
6. ✚ `curator.py` — threshold detection + curator prompt rendering +
   ephemeral user-message injection at request build time (with the
   `[context service: curator]` bracketed prefix convention)
7. ✎ `context_compaction_plugin.py` — consult ledger at compact time,
   apply decisions
8. ✚ Static system-prompt section (user manual) added to default prompts
9. ✚ Status widget

### Phase C — UX polish (~200 lines)

1. ✚ `/context` slash command with subcommands
2. ✚ Config widgets for `/config` modal
3. ✚ Curator confirmation tail message ("decisions recorded: ...")

### Phase D — Hub integration (~400 lines, optional)

1. ✚ `hub_bridge.py` — publish ledger events to hub
2. ✎ hub plugin — subscribe, maintain cross-agent index
3. ✚ `<hub_ask_ctx>` tag — query peer summaries
4. ✚ Divergent hash warnings
5. ✚ Cross-agent dedup hints on file reads


## Open questions / decisions needed

1. **Hash algorithm** — xxh64 is fast and non-crypto. blake3 if we
   want cross-process content-addressable storage later. Leaning xxh64.

2. **Ledger persistence** — does the ledger survive across session
   resume? Current plan says no, ledger is in-memory only. But if we
   resume a conversation, we lose all curation decisions. Options:
   (a) persist ledger alongside conversation JSONL
   (b) rebuild ledger on resume by re-hashing tool results in history
   (c) accept the loss, agent re-curates on resume
   Leaning (a).

3. **File line-range dedup granularity** — does reading lines 1-100
   and then lines 50-150 count as a hit, a partial hit, or a miss?
   Simplest: treat each unique (path, line_range) as its own entry.
   More correct: track read coverage per file. Leaning simplest.

4. **Evict cascade** — if the agent evicts ctx-3 which is msg_idx=15,
   do we evict msg_idx=15 alone or also the matching tool_use call at
   msg_idx=14? Leaning: evict both (the tool_use + tool_result pair)
   to keep history coherent.

5. **`<force>` lexical scope** — does `<force/>` inside a response
   apply to all `<file_read>` in that response, or only the one it
   wraps? Options: (a) attribute `force="true"` only, (b) wrapping
   tag scopes to children. Leaning (a) for simplicity.

6. **Hub shared content** — can lapis receive koordinator's ctx-7
   content directly, or does lapis always re-read from disk? Leaning
   always re-read (agents have different working dirs potentially,
   and content sharing = serialization cost). Hub only shares
   metadata + summaries.

7. **xxhash dependency** — not in the current requirements. Adds a
   C extension dep. Alternative: use stdlib `hashlib.blake2b` which
   is fast enough and already available. Leaning blake2b to avoid
   adding a dep.


## Non-goals

- ContextService is **not** a file system cache. It does not serve
  file content from memory; it only tracks that the agent has seen
  it and serves stale markers.
- ContextService is **not** a vector store. No embeddings, no
  similarity search. Ledger is keyed by (path, hash) + ctx_id only.
- ContextService does **not** rewrite message history outside of
  compaction. Eviction is the one exception and it's explicit.
- The agent does **not** have a "memory" API separate from the
  conversation. The ledger is just metadata on what's in the
  conversation, not an alternative store.


## Testing

JSON tmux specs in `tests/tmux/specs/`:

1. `context_service_dedup.json` — read a file twice, second read
   returns stale marker
2. `context_service_diff.json` — read, modify, reread, verify diff
3. `context_service_force.json` — stale hit, then force, verify
   full content returned
4. `context_service_curator.json` — cross threshold, verify curator
   prompt injected, verify agent curation recorded
5. `context_service_compact.json` — cross compact threshold after
   curation, verify compaction uses agent summaries
6. `context_service_evict.json` — evict entry, verify history
   rewritten, verify cache break is localized
7. `context_service_ttl.json` — ttl expiry auto-demotes decisions


## File inventory

New files:

```
packages/kollabor-ai/src/kollabor_ai/context_service/__init__.py
packages/kollabor-ai/src/kollabor_ai/context_service/service.py
packages/kollabor-ai/src/kollabor_ai/context_service/ledger.py
packages/kollabor-ai/src/kollabor_ai/context_service/file_tracker.py
packages/kollabor-ai/src/kollabor_ai/context_service/curator.py
packages/kollabor-ai/src/kollabor_ai/context_service/hash_utils.py
packages/kollabor-ai/src/kollabor_ai/context_service/models.py
packages/kollabor-ai/src/kollabor_ai/context_service/hub_bridge.py  # phase D
tests/tmux/specs/context_service_dedup.json
tests/tmux/specs/context_service_diff.json
tests/tmux/specs/context_service_force.json
tests/tmux/specs/context_service_curator.json
tests/tmux/specs/context_service_compact.json
tests/tmux/specs/context_service_evict.json
tests/tmux/specs/context_service_ttl.json
```

Modified files:

```
packages/kollabor-ai/src/kollabor_ai/response_parser.py
packages/kollabor-ai/src/kollabor_ai/conversation_manager.py
packages/kollabor-ai/src/kollabor_ai/providers/openrouter_provider.py  # phase A
kollabor/llm/llm_coordinator.py
kollabor/llm/streaming_handler.py                                       # phase A
kollabor/commands/registry.py                          # /context command
plugins/context_compaction_plugin.py
```

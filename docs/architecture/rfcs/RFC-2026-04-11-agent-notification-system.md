---
title: "Agent Notification System"
doc_type: architecture-rfc
created: 2026-04-11
modified: 2026-04-20
status: shipped 2026-04-20 (phases A-D — tool_grant/revoke/action_needed/external producers deferred pending caller sites)
owner: kollabor-ai + plugins
depends_on:
  - RFC-2026-04-11-hub-loop-prevention.md
  - RFC-2026-04-11-unified-tool-loading.md
revised: 2026-04-13
---
# Agent Notification System

> A lightweight event queue that renders a compact `[env]` block at the
> top of the next user message. Gives agents situational awareness without
> querying individual subsystems.


## Why this exists

Agents currently learn about state changes through scattered,
ad-hoc injections:

- Permission prompts interrupt inline
- Hub command results inject as `[system: ...]` messages
- Context compaction happens silently
- File changes from peers only reach watchers
- MCP server connections go unannounced
- Tool grants appear as one-off inline notices

There's no single "what changed since my last turn" summary.
An agent waking from waiting state has zero catch-up mechanism.

This spec adds one: a queue that collects events between turns
and renders them as a small block the agent sees before responding.


## Design principles

1. Flat, not grouped. 3-8 events don't need 15 section headers.
2. Symbols ARE priority. No enum tiers -- the symbol tells the
   agent whether to stop and read or note and continue.
3. One-line events. If it doesn't fit one line, it's too detailed.
4. Tiny token cost. Target: 40-80 tokens for a typical render.
5. No config widgets. This isn't user-tunable, it's infrastructure.


## Symbol system

Eight symbols. Each maps to a class of event. Agents learn the
mapping from one line in the system prompt.

```
▲  capability   permissions, tool grant/revoke, mcp connect/disconnect
+  joined       peer came online
-  changed      peer went offline or changed state
~  file/context file edited, compaction fired, context event
✔  task         task assigned, completed, approved, rejected
◉  action       someone needs something from you (review, cron target, etc)
✉  message      inbound comms (hub msg while idle, email, slack, etc)
⚡  external     external system event (webhook, cron fired, api callback)
```

The hierarchy:

  ▲ = something changed about MY capabilities (stop and read)
  ◉ = someone needs something FROM me (stop and read)
  ✉ = someone sent me something (read when relevant)
  everything else = background awareness (note and continue)

Module-level constant (single source of truth):

```python
SYMBOLS = {
    "capability": "▲",
    "joined":     "+",
    "changed":    "-",
    "file":       "~",
    "task":       "✔",
    "action":     "◉",
    "message":    "✉",
    "external":   "⚡",
}
```

Producers import from this dict. Never hardcode the character.


## Data model

```python
class EnvKind(str, Enum):
    """Machine-readable event kinds for filtering and querying."""
    PERMISSION     = "permission"
    TOOL_GRANT     = "tool_grant"
    TOOL_REVOKE    = "tool_revoke"
    MCP_CONNECT    = "mcp_connect"
    MCP_DISCONNECT = "mcp_disconnect"
    PEER_ONLINE    = "peer_online"
    PEER_OFFLINE   = "peer_offline"
    PEER_STATE     = "peer_state"
    FILE_CHANGED   = "file_changed"
    COMPACTION     = "compaction"
    TASK_EVENT     = "task_event"
    ACTION_NEEDED  = "action_needed"
    MESSAGE        = "message"
    EXTERNAL       = "external"

@dataclass
class EnvEvent:
    kind: EnvKind
    symbol: str
    message: str
    timestamp: float = field(default_factory=time.time)
    collapse_key: Optional[str] = None
    count: int = 1
```

`kind` is the machine-readable identity -- lets producers and
downstream code filter/query programmatically (e.g.
`if event.kind == EnvKind.MCP_DISCONNECT`). `symbol` is the
render format. No priority enum -- symbols encode that.


## Queue

Same collapse/drain mechanic from the original spec, but simpler.

```python
class EnvQueue:
    def __init__(self, max_size: int = 50):
        self._buffer: list[EnvEvent] = []
        self._collapse_index: dict[str, int] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def push(self, event: EnvEvent) -> None:
        with self._lock:
            if event.collapse_key:
                idx = self._collapse_index.get(event.collapse_key)
                if idx is not None and idx < len(self._buffer):
                    self._buffer[idx].count += 1
                    self._buffer[idx].timestamp = event.timestamp
                    return
            self._buffer.append(event)
            if event.collapse_key:
                self._collapse_index[event.collapse_key] = len(self._buffer) - 1
            if len(self._buffer) > self._max_size:
                self._buffer = self._buffer[-self._max_size:]
                self._rebuild_collapse_index()

    def drain(self) -> list[EnvEvent]:
        with self._lock:
            result = list(self._buffer)
            self._buffer.clear()
            self._collapse_index.clear()
        return result

    def peek(self) -> list[EnvEvent]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> int:
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            self._collapse_index.clear()
        return count

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _rebuild_collapse_index(self) -> None:
        self._collapse_index = {
            e.collapse_key: i
            for i, e in enumerate(self._buffer)
            if e.collapse_key
        }
```

No observers. No priority-based eviction. When the buffer exceeds
max_size, oldest events drop. Simple.


## Rendering

Flat list. No grouping by kind.

```python
def render_env_block(events: list[EnvEvent]) -> str:
    if not events:
        return ""
    lines = [f"[env: {len(events)} event{'s' if len(events) != 1 else ''}]"]
    for e in events:
        suffix = f" x{e.count}" if e.count > 1 else ""
        lines.append(f"  {e.symbol} {e.message}{suffix}")
    return "\n".join(lines)
```

Example output (typical turn):

```
[env: 4 events]
  ▲ trust:full (was confirm_all)
  + peridot joined
  ~ plugin.py, coordinator.py (by lapis)
  ✔ auth-fix-001 completed (coder)
```

Example output (busy turn with collapses):

```
[env: 8 events]
  ▲ trust:full (was confirm_all)
  ▲ +mcp:github (5 tools)
  + peridot joined
  - lapis -> waiting
  ~ plugin.py x3, coordinator.py (by lapis)
  ✉ lapis: "auth module done, ready for review"
  ✔ auth-fix-001 completed (coder)
  ◉ coder requests review: auth-fix-001
```

Example output (agent with external integrations):

```
[env: 5 events]
  ✉ email from user@example.com: "deploy when ready"
  ✉ lapis: "finished the migration" x2
  ⚡ cron deploy-check fired
  ⚡ webhook: stripe payment.succeeded
  ◉ user requests deploy approval
```


## Render point

Same as original: drain at request build time, prepend to the
last user message with a `---` separator.

```python
# in llm_coordinator._build_messages or equivalent
queue = self.event_bus.get_service("env_queue")
if queue:
    events = queue.drain()
    if events:
        block = render_env_block(events)
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = block + "\n\n---\n\n" + messages[-1]["content"]
        else:
            messages.append({"role": "user", "content": block})
```


## Producers

No helper functions file. Each subsystem pushes directly.
The push call is one line:

```python
# permission system
queue.push(EnvEvent(
    kind=EnvKind.PERMISSION, symbol=SYMBOLS["capability"],
    message=f"trust:{new_mode} (was {old_mode})",
))

# hub plugin - peer joined
queue.push(EnvEvent(
    kind=EnvKind.PEER_ONLINE, symbol=SYMBOLS["joined"],
    message=f"{peer} joined",
    collapse_key=f"join:{peer}",
))

# hub plugin - peer state change
queue.push(EnvEvent(
    kind=EnvKind.PEER_STATE, symbol=SYMBOLS["changed"],
    message=f"{peer} -> {new_state}",
    collapse_key=f"state:{peer}",
))

# hub plugin - file changed
queue.push(EnvEvent(
    kind=EnvKind.FILE_CHANGED, symbol=SYMBOLS["file"],
    message=f"{path} (by {changed_by})",
    collapse_key=f"file:{path}",
))

# context compaction
queue.push(EnvEvent(
    kind=EnvKind.COMPACTION, symbol=SYMBOLS["file"],
    message=f"compacted r{round}: {removed} msgs, ~{tokens_saved} tokens saved",
))

# tool grant
queue.push(EnvEvent(
    kind=EnvKind.TOOL_GRANT, symbol=SYMBOLS["capability"],
    message=f"+tool:{tool_name}",
))

# tool revoke
queue.push(EnvEvent(
    kind=EnvKind.TOOL_REVOKE, symbol=SYMBOLS["capability"],
    message=f"-tool:{tool_name}",
))

# mcp server connect
queue.push(EnvEvent(
    kind=EnvKind.MCP_CONNECT, symbol=SYMBOLS["capability"],
    message=f"+mcp:{server_name} ({tool_count} tools)",
))

# mcp server disconnect
queue.push(EnvEvent(
    kind=EnvKind.MCP_DISCONNECT, symbol=SYMBOLS["capability"],
    message=f"-mcp:{server_name}",
))

# task completed
queue.push(EnvEvent(
    kind=EnvKind.TASK_EVENT, symbol=SYMBOLS["task"],
    message=f"{task_id} completed ({by})",
    collapse_key=f"task:{task_id}",
))

# action needed (review request, etc)
queue.push(EnvEvent(
    kind=EnvKind.ACTION_NEEDED, symbol=SYMBOLS["action"],
    message=f"{from_agent} requests review: {task_id}",
))

# inbound message (hub msg while idle, email, slack, etc)
queue.push(EnvEvent(
    kind=EnvKind.MESSAGE, symbol=SYMBOLS["message"],
    message=f"{from_agent}: \"{summary}\"",
    collapse_key=f"msg:{from_agent}",
))

# external event (webhook, cron fired, api callback)
queue.push(EnvEvent(
    kind=EnvKind.EXTERNAL, symbol=SYMBOLS["external"],
    message=f"cron {job_id} fired",
))
```

That's it. No wrapper functions, no producers.py module.


## Wake behavior

When an agent transitions from waiting to active, same `[env]`
format with a one-line header:

```
[wake: 3m22s idle, 5 events]
  ✉ lapis: "auth module is done, ready for review"
  + peridot joined
  - lapis -> active
  ~ plugin.py x2 (by lapis)
  ✔ auth-fix-001 completed (coder)
```

Implementation: hub plugin detects wake transition, renders
the env block with a modified header line:

```python
async def _wake_from_waiting(self, waiting_since: float) -> None:
    duration = time.time() - waiting_since
    m, s = divmod(int(duration), 60)

    queue = self.event_bus.get_service("env_queue")
    if not queue:
        return
    events = queue.drain()
    if not events:
        header = f"[wake: {m}m{s}s idle, no events]"
    else:
        lines = [f"[wake: {m}m{s}s idle, {len(events)} event{'s' if len(events) != 1 else ''}]"]
        for e in events:
            suffix = f" x{e.count}" if e.count > 1 else ""
            lines.append(f"  {e.symbol} {e.message}{suffix}")
        header = "\n".join(lines)

    llm = self.event_bus.get_service("llm_service")
    if llm and hasattr(llm, "inject_system_message"):
        await llm.inject_system_message(header, subtype="wake_header")
```


## Agent-facing tags

Two tags. Same as original, simpler output.

`<notifications/>` -- peek at queue without draining:

```python
NOTIF_QUERY = re.compile(r"<notifications\s*/>")

if NOTIF_QUERY.search(response):
    queue = event_bus.get_service("env_queue")
    if queue:
        events = queue.peek()
        rendered = render_env_block(events) if events else "[env] empty"
        cmd_results.append(rendered)
    cleaned = NOTIF_QUERY.sub("", cleaned).strip()
```

`<notifications clear/>` -- discard all pending:

```python
NOTIF_CLEAR = re.compile(r"<notifications\s+clear\s*/>")

if NOTIF_CLEAR.search(response):
    queue = event_bus.get_service("env_queue")
    if queue:
        count = queue.clear()
        cmd_results.append(f"[env] cleared {count} event(s)")
    cleaned = NOTIF_CLEAR.sub("", cleaned).strip()
```

Check clear pattern BEFORE query pattern (it matches both).


## System prompt addition

One section in `bundles/agents/_base/sections/protocols/notifications.md`:

```markdown
## Environment notifications

At the top of some messages you'll see an `[env]` block showing
what changed since your last turn. Symbol key:

  ▲ capability change (permissions, tools, mcp)
  + agent joined
  - agent left or changed state
  ~ file edited or context event
  ✔ task event
  ◉ action needed from you
  ✉ inbound message (hub, email, slack)
  ⚡ external event (webhook, cron, api callback)

▲ and ◉ mean stop and read. ✉ means read when relevant.
Everything else is background awareness.

Use <notifications/> to check the queue, <notifications clear/>
to dismiss.
```

That's the entire prompt addition. ~70 words.


## Files

New:

```
packages/kollabor-ai/src/kollabor_ai/notifications/__init__.py
packages/kollabor-ai/src/kollabor_ai/notifications/models.py   (~30 lines)
packages/kollabor-ai/src/kollabor_ai/notifications/queue.py    (~80 lines)
packages/kollabor-ai/src/kollabor_ai/notifications/render.py   (~20 lines)
bundles/agents/_base/sections/protocols/notifications.md        (~15 lines)
tests/unit/test_env_queue.py
```

Modified:

```
kollabor/llm/llm_coordinator.py          (add drain + render at build time)
plugins/hub/plugin.py                    (push join/leave/state/file events)
kollabor/llm/permissions/manager.py      (push on mode change)
packages/kollabor-agent/src/kollabor_agent/tool_registry.py  (push on grant/revoke)
plugins/context_compaction_plugin.py     (push on compaction)
packages/kollabor-ai/src/kollabor_ai/response_parser.py      (handle tags)
```

Total new code: ~150 lines of Python + ~15 lines of prompt.


## Phasing

Phase A: models.py + queue.py + render.py + unit tests
Phase B: wire producers (hub, permissions, tools, compaction)
Phase C: render point in llm_coordinator + response_parser tags
Phase D: wake integration + system prompt section


## Non-goals

- User-facing notifications (the user sees the normal UI)
- Real-time mid-turn streaming
- Persistent notification history
- Cross-agent routing (use hub_msg for that)
- Config widgets or per-agent filter UI
- Priority tiers or urgency thresholds

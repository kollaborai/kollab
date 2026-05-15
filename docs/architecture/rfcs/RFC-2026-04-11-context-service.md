---
title: "Context Service"
doc_type: architecture-rfc
created: 2026-04-11
modified: 2026-04-20
status: shipped
status: phases A-D shipped (A-C = single-agent ledger + curator + compaction; D = hub bridge MVP, see RFC-2026-04-13-context-service-phase-d-hub-bridge.md)
owner: kollabor-ai
depends_on:
  - conversation_manager
  - context_compaction_plugin
  - plugins/hub
  - response_parser
  - RFC-2026-04-11-agent-notification-system.md
  - RFC-2026-04-11-hub-loop-prevention.md
supersedes: docs/features/context-service.md
---
# Context Service

> Unified context ledger for kollab. Tracks every heavy
> artifact (file reads, tool results, attachments) that enters an
> agent's conversation. Deduplicates by content hash, versions
> files, lets agents curate what stays verbatim vs what gets
> replaced by an agent-written summary at compaction time. Uses
> kollabor's default XML tag protocol (not native tool_calls).


## For implementers

Read this whole document before writing any code. This spec is
large because the ContextService touches five subsystems:

1. **Conversation manager** — ledger entries are indexed by message
   UUID, so we piggyback on conversation history's own identity.
2. **Context compaction plugin** — instead of calling an LLM to
   summarize, compaction now consults the ledger for each old
   message and applies the agent's stored decision.
3. **Response parser** — adds `<curate>`, `<context/>`, `<evict>`
   XML tag handling alongside existing tags.
4. **File read tool** — the existing `<read>` handler gets a
   ContextService-aware hook that returns stale markers on hits.
5. **Notification system** — context events (curator fired,
   eviction happened) go into the notification queue.

**Do NOT try to make this a native `tool_calls` system.** Every
tag here is XML-in-content, following kollabor's default XML
protocol. This was the main mistake of the previous (superseded)
version of this spec. See `docs/architecture/tool-calling-architecture.md` for
the protocol reference. The tags `<curate>`, `<context/>`,
`<evict>` are parsed from assistant response content by
`response_parser.py` the same way `<hub_msg>`, `<read>`, and
`<scratchpad>` are parsed.

If you are implementing from scratch, the order is:

1. Build the ContextService core (`models.py`, `ledger.py`,
   `file_tracker.py`, `service.py`)
2. Register as a service on the event bus
3. Wire the `<read>` hook (consult ledger before reading)
4. Wire the tool-result ingestion (add heavy results to ledger)
5. Add `<curate>`, `<context/>`, `<evict>` parsing in
   response_parser.py
6. Integrate with context_compaction_plugin (consult ledger at
   compact time)
7. Integrate with notification system (push context_event
   notifications)
8. Static system prompt section teaching agents
9. JSON tmux tests

Scope is wide but the design is modular. Each step is
independently testable.

Total estimated LOC: ~1500 lines of Python + ~300 lines of
markdown (static system prompt) + ~400 lines of JSON tmux tests.


## Why this exists

### Three production problems

From the 2026-04-11 chronos-crown session:

1. **A single `dead_code_scan` tool result dumped 254KB into
   history.** Compaction never fired because the token gate reads
   zero from openrouter streaming responses AND because the dual
   gate required 4 human turns which hadn't happened yet. The
   254KB got replayed on every subsequent request, costing ~$0.76
   over 20 turns.

2. **Agents reread the same files turn after turn** with no
   awareness they already had them. If `<read>` is called twice
   on an unchanged file, both reads dump the full file content
   into history, doubling the token cost of having that file
   available.

3. **Hub peers working on shared files have no idea which peer
   has which version in context.** If agent A reads `plugin.py`
   at hash `abc123` and agent B edits it to hash `def456`, agent
   A is reasoning about stale content with no warning.

### The fix

ContextService is the first-class ledger that addresses all three:

1. **Heavy tool results are tracked as ledger entries** with
   agent-visible IDs (`ctx-1`, `ctx-2`, etc.) and
   agent-decidable fates (`keep`, `summary`, `pending`). Compaction
   consults the ledger instead of calling an LLM to summarize.

2. **File reads are hash-deduped at the tool boundary.** When an
   agent emits `<read><file>x.py</file></read>` and the file is
   unchanged since a previous read, ContextService intercepts and
   returns a short "already in context" marker instead of the
   full file. Agents get a signal without the token cost.

3. **Hub integration (phase D) publishes ledger events to peers.**
   When a file is modified, agents holding the stale version get
   a notification via the notification queue. This is
   phase-gated behind hub broadcasting, which is optional.

### What's different from the previous spec

This version of the spec:

- Uses **XML tags** (`<curate>`, `<context/>`, `<evict>`, `<read>`)
  throughout, matching kollabor's default mode.
- Uses **message_uuid** as the stable identifier for ledger
  entries (not tool_call_id, which only exists in native mode).
- Wraps tool results in the `Tool result: [tag_name] <content>`
  envelope used by kollabor's XML mode, not `role: "tool"` with
  tool_call_id.
- References real kollabor tag names (`<read>`, not `<file_read>`).
- Integrates with the notification system (see
  `RFC-2026-04-11-agent-notification-system.md`) for context events.
- Respects the waiting state from RFC-2026-04-11-hub-loop-prevention.md (curator
  doesn't fire while agents are waiting).
- Does NOT assume native tool_calls are the default.


## Terminology

| term | meaning |
|------|---------|
| **heavy item** | any tool result or file read ≥ `heavy_threshold_kb` (default 8KB) |
| **ledger entry** | a tracked heavy item: `(ctx_id, kind, label, hash, size, message_uuid, decision, body)` |
| **ctx_id** | stable id assigned at entry creation, e.g. `ctx-1`, `ctx-2`. Sequential per-session. |
| **message_uuid** | the UUID of the conversation message containing the heavy item. Stable across history edits. Comes from `ConversationMessage.uuid`. |
| **curation** | the agent's decision about a ledger entry: `keep` or `summary` |
| **decision body** | the reason (for `keep`) or agent-written summary (for `summary`) inside the `<curate>` tag |
| **fresh read** | a file read where content has not been seen before, or hash differs from prior reads |
| **stale read** | a file read whose content is already in context at the same hash |
| **diff read** | a file read that returns only the diff vs the version already in context |
| **curator** | the ContextService component that prompts agents to make curation decisions when the ledger crosses a threshold |


## Architecture

### New files

```
packages/kollabor-ai/src/kollabor_ai/context_service/__init__.py
packages/kollabor-ai/src/kollabor_ai/context_service/models.py
packages/kollabor-ai/src/kollabor_ai/context_service/service.py
packages/kollabor-ai/src/kollabor_ai/context_service/ledger.py
packages/kollabor-ai/src/kollabor_ai/context_service/file_tracker.py
packages/kollabor-ai/src/kollabor_ai/context_service/curator.py
packages/kollabor-ai/src/kollabor_ai/context_service/hash_utils.py
packages/kollabor-ai/src/kollabor_ai/context_service/hub_bridge.py  # phase D, optional
bundles/agents/_base/sections/tool-reference/context.md
bundles/agents/_base/sections/tool-reference/curate.md
tests/unit/test_context_service_ledger.py
tests/unit/test_context_service_file_tracker.py
tests/unit/test_context_service_curator.py
tests/tmux/specs/context_service_dedup.json
tests/tmux/specs/context_service_diff.json
tests/tmux/specs/context_service_force.json
tests/tmux/specs/context_service_curator.json
tests/tmux/specs/context_service_compact.json
tests/tmux/specs/context_service_evict.json
```

### Modified files

```
packages/kollabor-ai/src/kollabor_ai/response_parser.py
  # Add <curate>, <context/>, <evict> patterns and handlers

packages/kollabor-ai/src/kollabor_ai/conversation_manager.py
  # Expose message lookup by uuid for ContextService

packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py
  # Hook into ContextService for stale hit / diff generation on <read>

kollabor/llm/llm_coordinator.py
  # register_service("context_service")

plugins/context_compaction_plugin.py
  # Consult ContextService at compact time, apply decisions

kollabor/commands/system_commands/handlers/context.py  # NEW — /context command
kollabor/commands/registry.py  # register /context command

bundles/agents/_base/sections/protocols/tool-execution.md
  # Mention <curate>, <context/>, <evict> in the protocol doc
```

### Architecture diagram

```
   agent emits a response containing XML tags
                      │
                      ▼
       ┌────────────────────────────────┐
       │ response_parser.py               │
       │                                   │
       │ Parses <read>, <curate>,         │
       │ <context/>, <evict>, etc.         │
       │ Dispatches each to its handler    │
       └────────────────┬──────────────┘
                        │
           ┌────────────┼────────────┐
           │            │             │
           ▼            ▼             ▼
     <read>      <curate>      <evict>
   handler       handler       handler
        │            │             │
        ▼            ▼             ▼
  ┌─────────┐  ┌─────────┐  ┌─────────┐
  │ Tool    │  │ Ledger  │  │ Ledger  │
  │ Exec    │  │ .set_   │  │ .evict  │
  │         │  │ decision│  │         │
  └────┬────┘  └─────────┘  └─────────┘
       │
       ▼
  ┌─────────────────────────┐
  │ ContextService           │
  │ .file_read_hook()        │
  │                          │
  │ Check file_tracker:      │
  │   - hash matches → STALE │
  │   - hash differs → DIFF  │
  │   - new file    → FRESH  │
  └──────────┬───────────────┘
             │
             ▼
  ┌─────────────────────────┐
  │ On FRESH or DIFF:         │
  │ add LedgerEntry            │
  │ update file_tracker        │
  │ return tool result         │
  │                            │
  │ On STALE:                  │
  │ return short marker         │
  │ (~200 bytes instead of file) │
  └─────────────────────────┘


   at compaction time (separate flow):

       context_compaction_plugin
                 │
                 ▼
       ┌──────────────────────┐
       │ for each old message   │
       │ in history:            │
       │                         │
       │ entry = context_service  │
       │   .entry_for_message(    │
       │     msg.uuid)             │
       │                           │
       │ if entry is None:          │
       │   keep as-is               │
       │ elif decision == "keep":    │
       │   keep as-is                 │
       │ elif decision == "summary":  │
       │   replace with entry.body    │
       │ elif decision == "pending":  │
       │   replace with elision marker │
       └──────────────────────────┘
```


## Ephemeral injection mechanism

This section is identical in spirit to the injection mechanism
described in RFC-2026-04-11-hub-loop-prevention.md and RFC-2026-04-11-agent-notification-system.md,
but stated here for completeness because it's the core trick
ContextService uses to surface ledger state to the agent without
breaking prefix cache.

### The problem

The ledger changes every turn as new heavy items arrive. If we
put the ledger summary in the system prompt via a trender tag,
the system prompt changes every turn, prefix caching breaks, and
every request reprocesses the full conversation.

### The solution

Inject ledger state as a **user-role message with `[context
service]` prefix** at request build time, not in the system
prompt. The injection is ephemeral — it appears in the request
body, then gets discarded from conversation history after the
request completes.

### Why user role, not system role

OpenAI-compatible providers (openai, xai, openrouter, groq,
mistral) reject a second `role: "system"` message anywhere except
position 0. Using user role with a bracketed prefix
(`[context service]`, `[context service: curator]`) works on
every provider. The static system prompt teaches the model to
recognize these bracketed prefixes as runtime machinery.

### Injection payload format

```
[context service] ledger snapshot

  ctx-1  file_read   plugins/hub/plugin.py      48KB   keep
         "editing this file, need verbatim"
  ctx-2  tool_result terminal: git log         176KB   summary
         "phase 4.5 work, 40 commits c8a7eec..286ae47"
  ctx-3  tool_result deadcode                  254KB   pending
  ctx-4  file_read   kollabor/state/context.py  12KB   pending

  total:      490KB  heavy items: 4
  threshold:  300KB  (curator will prompt next turn)
```

### When injections fire

1. Agent emits `<context/>` — ledger snapshot is injected on
   the NEXT request build.
2. Curator threshold is crossed — curator prompt is injected on
   the NEXT request build.
3. Agent just submitted `<curate>` decisions — confirmation block
   is injected on the NEXT request build.

Most turns have NO injection. The ledger lives in memory; only
the events above cause a user-message prepend.


## Data model

### LedgerEntry

New file: `packages/kollabor-ai/src/kollabor_ai/context_service/models.py`

```python
"""Context service data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Literal, Optional


@dataclass
class LedgerEntry:
    """A single heavy item tracked by ContextService.

    Heavy items are tool results and file reads over a configurable
    size threshold (default 8KB). Each entry has a stable ctx_id
    that the agent can reference in <curate>, <evict>, and
    <context/> tags.
    """

    ctx_id: str
    """Stable identifier, e.g. 'ctx-1', 'ctx-2'. Sequential
    per session. Assigned when the entry is created. Agents use
    this to reference entries in curate/evict tags."""

    kind: Literal["file_read", "tool_result", "attachment"]
    """Category of the heavy item."""

    tool: str
    """The tool that produced this entry. For file_read entries,
    this is 'read' or 'diff'. For tool results, it's the tool
    name (e.g. 'terminal', 'grep', 'mcp:github')."""

    label: str
    """Human-readable label for the entry. For files, this is the
    file path. For tool results, a short description of what ran
    (e.g. 'git_log', 'dead_code_scan')."""

    # Content identity
    content_hash: str
    """Hash of the raw content bytes. Used for dedup."""

    size_bytes: int
    """Size of the raw content in bytes."""

    # History placement
    message_uuid: str
    """The UUID of the ConversationMessage containing this heavy
    item. This is the stable identifier — survives history
    reindexing, compaction, etc."""

    # Lifecycle
    added_at: datetime
    """When the entry was created."""

    last_accessed_at: datetime
    """Updated when a stale-read hit references this entry."""

    read_count: int = 1
    """Number of times the agent has referenced this entry.
    Starts at 1 for the original read."""

    ttl_seconds: Optional[int] = None
    """Optional time-to-live. After this many seconds from
    last_accessed_at, the entry auto-demotes to 'summary' if a
    decision was set, or 'pending' if not. None = no expiry."""

    # Curation
    decision: Literal["pending", "keep", "summary", "evicted"] = "pending"
    """Agent's decision about this entry's compaction fate:
    - pending: no decision yet, will auto-summary at compact time
    - keep: verbatim retention, survives compaction unchanged
    - summary: replace with decision_body at compact time
    - evicted: already removed from history; entry retained only
      as a historical reference for lookup"""

    decision_body: str = ""
    """For keep decisions: the reason the agent gave.
    For summary decisions: the agent-written summary that replaces
    the full tool result at compact time."""

    decided_at: Optional[datetime] = None
    """When the decision was recorded."""

    # File-specific fields
    file_path: Optional[str] = None
    """For file_read entries, the path on disk."""

    file_lines: Optional[tuple[int, int]] = None
    """For partial file reads, (start, end) as 1-indexed inclusive."""

    file_version: Optional[int] = None
    """Monotonic version number per file path. First read is
    version 1, a re-read with different hash is version 2, etc."""

    prior_ctx_id: Optional[str] = None
    """For diff entries (file changed since prior read), the
    ctx_id of the previous version this diff is relative to."""

    # Hub sharing (phase D)
    hub_shared: bool = False
    """If True, this entry has been broadcast to hub peers."""

    hub_holders: List[str] = field(default_factory=list)
    """For phase D: list of peer identities that also hold a
    version of this heavy item. Populated via hub broadcasts."""


@dataclass
class FileVersion:
    """Tracks all versions of a file ever read this session.

    Used by FileTracker to maintain per-file version history and
    to generate diffs between versions.
    """

    path: str
    """The file path."""

    versions: List[LedgerEntry] = field(default_factory=list)
    """Ordered by read time. The last entry is the most recent."""

    @property
    def latest(self) -> Optional[LedgerEntry]:
        """Get the most recent LedgerEntry for this file."""
        return self.versions[-1] if self.versions else None

    @property
    def latest_hash(self) -> Optional[str]:
        """Get the most recent hash for this file."""
        return self.latest.content_hash if self.latest else None
```


### ContextService singleton

New file: `packages/kollabor-ai/src/kollabor_ai/context_service/service.py`

```python
"""ContextService — the main entry point.

One instance per conversation context. Registered as a service on
the event bus so every plugin can reach it via
event_bus.get_service('context_service').
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .file_tracker import FileTracker
from .hash_utils import compute_hash
from .ledger import Ledger
from .models import FileVersion, LedgerEntry

logger = logging.getLogger(__name__)


class ContextService:
    """Context service — tracks heavy items and manages curation."""

    def __init__(
        self,
        heavy_threshold_kb: int = 8,
        curate_threshold_kb: int = 300,
    ):
        self._ledger = Ledger()
        self._file_tracker = FileTracker()
        self._heavy_threshold_bytes = heavy_threshold_kb * 1024
        self._curate_threshold_bytes = curate_threshold_kb * 1024

        # Ephemeral injection flags
        self._context_query_pending = False
        self._context_query_filter: Optional[str] = None
        self._curator_pending = False
        self._confirmation_pending = False

        # Curator throttling
        self._last_curator_fire_turn: int = 0
        self._curator_throttle_turns = 2

        # Event bus reference (set by the plugin that registers us)
        self._event_bus = None

    def set_event_bus(self, event_bus) -> None:
        """Set the event bus reference.

        Called by the plugin that registers ContextService as a
        service. Used for pushing notifications via the
        notification queue.
        """
        self._event_bus = event_bus

    # ------------------------------------------------------------------
    # Ledger operations
    # ------------------------------------------------------------------

    def ingest_heavy_item(
        self,
        kind: str,
        tool: str,
        label: str,
        content: bytes,
        message_uuid: str,
        file_path: Optional[str] = None,
        file_lines: Optional[tuple] = None,
        file_version: Optional[int] = None,
    ) -> Optional[LedgerEntry]:
        """Add a heavy item to the ledger.

        Args:
            kind: 'file_read', 'tool_result', or 'attachment'.
            tool: The tool that produced this (e.g. 'read',
                'terminal', 'grep').
            label: Human-readable label (file path or tool
                description).
            content: The raw bytes of the heavy item's content.
            message_uuid: UUID of the ConversationMessage
                containing this item.
            file_path: For file reads, the on-disk path.
            file_lines: For partial file reads, (start, end).
            file_version: For file reads, the monotonic version.

        Returns:
            The new LedgerEntry, or None if the item is under the
            heavy threshold and not tracked.
        """
        if len(content) < self._heavy_threshold_bytes:
            return None  # Not heavy enough to track

        now = datetime.now()
        ctx_id = self._ledger.next_ctx_id()
        content_hash = compute_hash(content)

        entry = LedgerEntry(
            ctx_id=ctx_id,
            kind=kind,
            tool=tool,
            label=label,
            content_hash=content_hash,
            size_bytes=len(content),
            message_uuid=message_uuid,
            added_at=now,
            last_accessed_at=now,
            file_path=file_path,
            file_lines=file_lines,
            file_version=file_version,
        )

        self._ledger.add(entry)

        # Update file tracker if this is a file read
        if file_path:
            self._file_tracker.record_read(file_path, entry)

        logger.info(
            f"Ingested heavy item: {ctx_id} ({label}, {len(content)} bytes)"
        )

        # Push notification
        self._notify_context_event(
            f"ingested {ctx_id} ({label}, {len(content) // 1024}KB)"
        )

        # Check if curator should fire
        self._check_curator_trigger()

        return entry

    def set_decision(
        self,
        ctx_id: str,
        decision: str,
        body: str,
    ) -> bool:
        """Record an agent's decision about a ledger entry.

        Args:
            ctx_id: The entry to update.
            decision: 'keep' or 'summary'.
            body: The reason (keep) or summary text (summary).

        Returns:
            True if the decision was recorded, False if the entry
            doesn't exist or the decision is invalid.
        """
        if decision not in ("keep", "summary"):
            return False

        entry = self._ledger.get(ctx_id)
        if entry is None:
            return False

        if not body.strip():
            logger.warning(
                f"Empty decision body for {ctx_id}, rejecting"
            )
            return False

        entry.decision = decision
        entry.decision_body = body.strip()
        entry.decided_at = datetime.now()

        logger.info(f"Decision recorded: {ctx_id} → {decision}")

        # Flag confirmation block for next request
        self._confirmation_pending = True

        self._notify_context_event(
            f"decision recorded: {ctx_id} → {decision}"
        )

        return True

    def evict(self, ctx_id: str, reason: str = "") -> bool:
        """Evict a ledger entry from history.

        Rewrites the corresponding message in history with a stub
        and marks the entry as evicted. This BREAKS prefix cache
        from the evicted message forward.

        Args:
            ctx_id: The entry to evict.
            reason: Optional explanation the agent gave.

        Returns:
            True if evicted, False if the entry doesn't exist.
        """
        entry = self._ledger.get(ctx_id)
        if entry is None:
            return False

        entry.decision = "evicted"
        entry.decision_body = reason

        # Actually rewrite the history message (done via
        # conversation_manager)
        if self._event_bus:
            conv_mgr = self._event_bus.get_service("conversation_manager")
            if conv_mgr and hasattr(conv_mgr, "rewrite_message"):
                stub = (
                    f"[evicted: {entry.tool} {entry.label}, "
                    f"{entry.size_bytes // 1024}KB]\n"
                    f"reason: {reason or 'agent requested eviction'}"
                )
                conv_mgr.rewrite_message(entry.message_uuid, stub)

        logger.info(f"Evicted: {ctx_id} (broke cache from msg {entry.message_uuid})")

        self._notify_context_event(
            f"evicted {ctx_id} ({entry.size_bytes // 1024}KB saved, cache broken)"
        )

        return True

    # ------------------------------------------------------------------
    # File read hook
    # ------------------------------------------------------------------

    def file_read_hook(
        self,
        path: str,
        disk_content: bytes,
        lines_spec: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Called by the <read> tool handler BEFORE returning content.

        Checks if we've seen this file at this hash before. Returns
        a dict describing what the caller should do:
        - 'action': 'fresh', 'stale', 'diff', 'force_fresh'
        - 'content': the content to return to the agent (stale
          marker, diff, or full content)
        - 'ledger_entry': the LedgerEntry to associate with the
          tool result (None for stale hits)

        Args:
            path: The file path.
            disk_content: The raw bytes read from disk.
            lines_spec: Optional line range spec from <lines>.
            force: If True, bypass dedup and return full content.

        Returns:
            dict with 'action', 'content', 'ledger_entry' keys.
        """
        disk_hash = compute_hash(disk_content)

        # Force override bypasses dedup
        if force:
            return {
                "action": "force_fresh",
                "content": disk_content,
                "ledger_entry": None,  # Caller creates the entry after
            }

        # Check file_tracker for prior versions
        version = self._file_tracker.get_version(path)

        if version is None:
            # Never seen this file before
            return {
                "action": "fresh",
                "content": disk_content,
                "ledger_entry": None,
            }

        if version.latest_hash == disk_hash:
            # STALE HIT — file unchanged since last read
            # Update the existing entry's last_accessed_at
            latest_entry = version.latest
            latest_entry.last_accessed_at = datetime.now()
            latest_entry.read_count += 1

            marker = self._build_stale_marker(latest_entry)
            return {
                "action": "stale",
                "content": marker.encode("utf-8"),
                "ledger_entry": None,
            }

        # Hash differs — return a diff
        diff = self._build_diff(version.latest, disk_content)
        return {
            "action": "diff",
            "content": diff.encode("utf-8"),
            "ledger_entry": None,  # Caller creates a new diff entry
            "prior_ctx_id": version.latest.ctx_id,
        }

    def _build_stale_marker(self, entry: LedgerEntry) -> str:
        """Build the short marker returned for stale reads."""
        return (
            f"[context service: stale hit]\n"
            f"{entry.label} is already in your context as {entry.ctx_id} "
            f"(read {entry.read_count - 1} turn(s) ago, hash "
            f"{entry.content_hash[:8]} unchanged, "
            f"{entry.size_bytes // 1024}KB). "
            f"the full content is in the tool result at message "
            f"{entry.message_uuid}. reference it there instead of re-reading.\n\n"
            f"if you need to force a fresh read (e.g., you suspect a "
            f"silent write), set force=\"true\" on the <read> tag:\n"
            f"  <read force=\"true\"><file>{entry.file_path}</file></read>"
        )

    def _build_diff(
        self, prior_entry: LedgerEntry, new_content: bytes
    ) -> str:
        """Build a unified diff from prior_entry's content to new_content.

        For this to work, we need prior_entry's actual content,
        which we don't store in the ledger entry itself. Instead,
        we look it up in conversation_manager by message_uuid.
        """
        import difflib

        prior_content = ""
        if self._event_bus:
            conv_mgr = self._event_bus.get_service("conversation_manager")
            if conv_mgr:
                msg = conv_mgr.get_message_by_uuid(prior_entry.message_uuid)
                if msg:
                    prior_content = msg.content

        # Strip the tool result prefix to get raw content
        prefix = f"Tool result: [{prior_entry.tool}]"
        if prior_content.startswith(prefix):
            prior_content = prior_content[len(prefix):].lstrip(" \n")
            # Strip the ✓ Read... header
            lines = prior_content.split("\n", 2)
            if len(lines) >= 3:
                prior_content = lines[2]

        new_text = new_content.decode("utf-8", errors="replace")
        diff_lines = list(
            difflib.unified_diff(
                prior_content.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"{prior_entry.label} (ctx={prior_entry.ctx_id})",
                tofile=f"{prior_entry.label} (current)",
            )
        )

        diff_text = "".join(diff_lines)
        if not diff_text:
            diff_text = "(empty diff — content identical after normalization)"

        return (
            f"[context service: file changed]\n"
            f"{prior_entry.label} changed since {prior_entry.ctx_id} "
            f"(hash {prior_entry.content_hash[:8]} → "
            f"{compute_hash(new_content)[:8]}).\n"
            f"returning diff instead of full file "
            f"({len(diff_text)} bytes vs {len(new_text)} bytes).\n\n"
            f"{diff_text}\n"
            f"if you need the full current file (not just the diff), "
            f"set force=\"true\" on the <read> tag."
        )

    # ------------------------------------------------------------------
    # Curator
    # ------------------------------------------------------------------

    def _check_curator_trigger(self) -> None:
        """Check if the curator should fire based on ledger size."""
        total = self._ledger.total_bytes()
        if total < self._curate_threshold_bytes:
            return

        pending = self._ledger.count_pending()
        if pending == 0:
            return  # Nothing to curate

        # Throttle
        if (
            self._ledger.turn_count - self._last_curator_fire_turn
            < self._curator_throttle_turns
        ):
            return

        # Fire the curator
        self._curator_pending = True
        self._last_curator_fire_turn = self._ledger.turn_count

        logger.info(
            f"Curator triggered: {total // 1024}KB ledger, {pending} pending"
        )
        self._notify_context_event(
            f"curator triggered ({total // 1024}KB, {pending} pending)",
            priority="high",
        )

    def build_curator_injection(self) -> Optional[str]:
        """Build the curator prompt for injection.

        Returns None if the curator shouldn't fire this turn.
        """
        if not self._curator_pending:
            return None

        self._curator_pending = False  # One-shot

        pending_entries = [
            e for e in self._ledger.all() if e.decision == "pending"
        ]
        decided_entries = [
            e for e in self._ledger.all() if e.decision in ("keep", "summary")
        ]

        lines = [
            "[context service: curator]",
            "",
            f"{len(pending_entries)} heavy item(s) have piled up and crossed "
            "the curation threshold. mark each one keep or summary before "
            "the next compaction.",
            "",
            "heavy items awaiting decision:",
            "",
        ]
        for entry in pending_entries:
            lines.append(
                f"  {entry.ctx_id}  {entry.kind:<12} "
                f"{entry.label:<40} {entry.size_bytes // 1024:>5}KB  pending"
            )
        lines.append("")

        if decided_entries:
            lines.append("already decided:")
            lines.append("")
            for entry in decided_entries:
                lines.append(
                    f"  {entry.ctx_id}  {entry.kind:<12} "
                    f"{entry.label:<40} {entry.size_bytes // 1024:>5}KB  "
                    f"{entry.decision}"
                )
            lines.append("")

        total = self._ledger.total_bytes()
        lines.extend([
            f"  total:       {total // 1024}KB",
            f"  threshold:   {self._curate_threshold_bytes // 1024}KB  (exceeded)",
            "",
            "for each pending item, respond with ONE of:",
            "",
            '  <curate id="ctx-N" decision="keep">',
            "  explain why you need this verbatim. stays in history full size.",
            "  </curate>",
            "",
            '  <curate id="ctx-N" decision="summary">',
            "  your compressed version. this exact text replaces the full",
            "  tool result at compaction time. include anything future-you",
            "  will need to work with this material again.",
            "  </curate>",
            "",
            "unmarked items default to auto-summary at compaction time.",
            "your own summaries are higher quality than the fallback.",
            "",
            "the curator won't prompt again for at least 2 turns. you can",
            "proactively emit <curate> tags any turn without being prompted.",
            "",
            "other commands:",
            "  <context/>                inspect full ledger",
            '  <evict id="ctx-N">reason</evict>   drop immediately (breaks cache)',
            "  <read force=\"true\"><file>path</file></read>   override dedup",
        ])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Injection builders
    # ------------------------------------------------------------------

    def request_context_snapshot(self, filter: Optional[str] = None) -> None:
        """Flag that a <context/> snapshot should be injected on the next request."""
        self._context_query_pending = True
        self._context_query_filter = filter

    def build_context_snapshot(self) -> Optional[str]:
        """Build a ledger snapshot for injection."""
        if not self._context_query_pending:
            return None

        self._context_query_pending = False
        filter_spec = self._context_query_filter
        self._context_query_filter = None

        entries = self._ledger.all()

        # Apply filter if given
        if filter_spec:
            if filter_spec == "pending":
                entries = [e for e in entries if e.decision == "pending"]
            elif filter_spec == "file_read":
                entries = [e for e in entries if e.kind == "file_read"]
            elif filter_spec == "tool_result":
                entries = [e for e in entries if e.kind == "tool_result"]
            elif filter_spec.startswith("path:"):
                path_substr = filter_spec[5:]
                entries = [
                    e for e in entries
                    if e.file_path and path_substr in e.file_path
                ]

        lines = ["[context service] ledger snapshot"]
        if filter_spec:
            lines.append(f"(filter: {filter_spec})")
        lines.append("")

        if not entries:
            lines.append("(empty)")
        else:
            for entry in entries:
                lines.append(
                    f"  {entry.ctx_id}  {entry.kind:<12} "
                    f"{entry.label:<40} {entry.size_bytes // 1024:>5}KB  "
                    f"{entry.decision}"
                )
                if entry.decision_body:
                    # Show first line of decision body, truncated
                    body_preview = entry.decision_body.split("\n", 1)[0][:80]
                    lines.append(f'         "{body_preview}"')

        lines.append("")
        total = self._ledger.total_bytes()
        lines.append(f"  total:      {total // 1024}KB  heavy items: {len(entries)}")
        lines.append(f"  threshold:  {self._curate_threshold_bytes // 1024}KB")

        return "\n".join(lines)

    def build_confirmation_injection(self) -> Optional[str]:
        """Build a confirmation block after decisions were recorded."""
        if not self._confirmation_pending:
            return None

        self._confirmation_pending = False

        lines = ["[context service] decisions recorded"]
        lines.append("")

        total_saved = 0
        for entry in self._ledger.all():
            if entry.decision == "keep":
                lines.append(
                    f"  {entry.ctx_id}  keep     "
                    f"({entry.size_bytes // 1024}KB retained in history)"
                )
            elif entry.decision == "summary":
                saved = entry.size_bytes - len(entry.decision_body)
                total_saved += saved
                lines.append(
                    f"  {entry.ctx_id}  summary  "
                    f"({entry.size_bytes // 1024}KB → "
                    f"{len(entry.decision_body)} bytes, "
                    f"-{saved // 1024}KB)"
                )

        lines.append("")
        lines.append(
            f"  total pending: {self._ledger.count_pending()}"
        )
        if total_saved > 0:
            lines.append(
                f"  savings at next compact: ~{total_saved // 1024}KB"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Lookup methods (used by compaction plugin)
    # ------------------------------------------------------------------

    def entry_for_message(self, message_uuid: str) -> Optional[LedgerEntry]:
        """Look up a ledger entry by its message UUID.

        Called by the context_compaction_plugin at compact time
        to find the agent's decision for each old message.
        """
        for entry in self._ledger.all():
            if entry.message_uuid == message_uuid:
                return entry
        return None

    def all_entries(self) -> List[LedgerEntry]:
        """Return all ledger entries."""
        return self._ledger.all()

    def increment_turn(self) -> None:
        """Called by the plugin to signal a new turn started.

        Used for curator throttling.
        """
        self._ledger.turn_count += 1

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify_context_event(
        self, message: str, priority: str = "medium"
    ) -> None:
        """Push a context_event notification to the queue."""
        if not self._event_bus:
            return

        queue = self._event_bus.get_service("notification_queue")
        if queue is None:
            return

        from kollabor_ai.notifications.models import (
            Notification,
            NotificationKind,
            NotificationPriority,
        )

        priority_enum = {
            "low": NotificationPriority.LOW,
            "medium": NotificationPriority.MEDIUM,
            "high": NotificationPriority.HIGH,
            "urgent": NotificationPriority.URGENT,
        }.get(priority, NotificationPriority.MEDIUM)

        queue.push(
            Notification(
                kind=NotificationKind.CONTEXT_EVENT,
                message=message,
                priority=priority_enum,
                source="context_service",
            )
        )
```


### Ledger

New file: `packages/kollabor-ai/src/kollabor_ai/context_service/ledger.py`

```python
"""In-memory ledger for ContextService.

Thread-safe. Stores LedgerEntry instances keyed by ctx_id, with
a monotonic counter for generating new IDs.
"""

import threading
from typing import Dict, List, Optional

from .models import LedgerEntry


class Ledger:
    """Thread-safe in-memory ledger."""

    def __init__(self):
        self._entries: Dict[str, LedgerEntry] = {}
        self._next_id: int = 1
        self._lock = threading.Lock()
        self.turn_count: int = 0

    def next_ctx_id(self) -> str:
        """Generate the next sequential ctx_id."""
        with self._lock:
            ctx_id = f"ctx-{self._next_id}"
            self._next_id += 1
            return ctx_id

    def add(self, entry: LedgerEntry) -> None:
        """Add an entry to the ledger."""
        with self._lock:
            self._entries[entry.ctx_id] = entry

    def get(self, ctx_id: str) -> Optional[LedgerEntry]:
        """Look up an entry by ctx_id."""
        with self._lock:
            return self._entries.get(ctx_id)

    def all(self) -> List[LedgerEntry]:
        """Return all entries, sorted by ctx_id."""
        with self._lock:
            entries = list(self._entries.values())

        def sort_key(e: LedgerEntry) -> int:
            # ctx_id is 'ctx-N'; sort by N
            try:
                return int(e.ctx_id.split("-", 1)[1])
            except (IndexError, ValueError):
                return 0

        entries.sort(key=sort_key)
        return entries

    def total_bytes(self) -> int:
        """Total size of all tracked entries (non-evicted)."""
        with self._lock:
            return sum(
                e.size_bytes
                for e in self._entries.values()
                if e.decision != "evicted"
            )

    def count_pending(self) -> int:
        """Count entries with pending decision."""
        with self._lock:
            return sum(
                1 for e in self._entries.values() if e.decision == "pending"
            )
```


### FileTracker

New file: `packages/kollabor-ai/src/kollabor_ai/context_service/file_tracker.py`

```python
"""File version tracking for ContextService.

Maintains a per-file history of LedgerEntry versions so we can
dedup by hash and generate diffs on re-read.
"""

import threading
from typing import Dict, Optional

from .models import FileVersion, LedgerEntry


class FileTracker:
    """Thread-safe per-file version history."""

    def __init__(self):
        self._versions: Dict[str, FileVersion] = {}
        self._lock = threading.Lock()

    def record_read(self, path: str, entry: LedgerEntry) -> None:
        """Record a new read of a file.

        Args:
            path: The file path.
            entry: The LedgerEntry for this read.
        """
        with self._lock:
            version = self._versions.setdefault(path, FileVersion(path=path))
            entry.file_version = len(version.versions) + 1
            version.versions.append(entry)

    def get_version(self, path: str) -> Optional[FileVersion]:
        """Get the version history for a file."""
        with self._lock:
            return self._versions.get(path)

    def has_any_version(self, path: str) -> bool:
        """Check if we've ever read this file."""
        with self._lock:
            return path in self._versions
```


### Hash utils

New file: `packages/kollabor-ai/src/kollabor_ai/context_service/hash_utils.py`

```python
"""Hash utilities for ContextService.

Uses blake2b from stdlib (no new dependencies). Fast enough for
this use case (hashing ~50KB file content per read).
"""

import hashlib


def compute_hash(content: bytes) -> str:
    """Compute a content hash.

    Uses blake2b with 8-byte digest, returned as 16 hex chars.
    Non-cryptographic (good enough for dedup, not for security).
    """
    return hashlib.blake2b(content, digest_size=8).hexdigest()
```


## The XML tags

### `<curate>` — mark an entry's compaction fate

Agent writes this in their response content. The tag has a
required `id` attribute (the ctx_id) and a required `decision`
attribute (`keep` or `summary`). The tag body is the agent-provided
reason (keep) or summary (summary). Multi-line bodies are
supported via `re.DOTALL`.

```xml
<curate id="ctx-1" decision="keep">
actively editing this file across the next 2-3 turns while i fix
the broadcast race. need verbatim content for reference.
</curate>
```

```xml
<curate id="ctx-2" decision="summary">
git log --oneline HEAD~40..HEAD: 40 commits, phase 4.5 daemon
work dominates (c8a7eec..286ae47). key commit c8a7eec added
coordinator_ready threading.Event. rest is docs and routine.
</curate>
```

### Regex

```python
CURATE_PATTERN = re.compile(
    r'<curate\s+id="([^"]+)"\s+decision="(keep|summary)"\s*>(.*?)</curate>',
    re.DOTALL,
)
```

### Handler in response_parser.py

Add to the parsing block alongside existing hub / file tag
handlers:

```python
# --- Context service: curate tags ---
if "<curate" in response and self.context_service:
    curate_matches = CURATE_PATTERN.findall(response)
    for ctx_id, decision, body in curate_matches:
        if self.context_service.set_decision(ctx_id, decision, body.strip()):
            cmd_results.append(
                f"[curate] {ctx_id} → {decision} "
                f"({len(body.strip())} bytes recorded)"
            )
        else:
            cmd_results.append(
                f"[curate] error: {ctx_id} not found or invalid decision"
            )
    cleaned = CURATE_PATTERN.sub("", cleaned).strip()
```


### `<context/>` — query the ledger

```xml
<context/>
<context filter="pending"/>
<context filter="file_read"/>
<context filter="path:plugins/hub"/>
```

### Regex

```python
CONTEXT_QUERY_PATTERN = re.compile(
    r'<context(?:\s+filter="([^"]*)")?\s*/>',
    re.DOTALL,
)
```

### Handler

```python
# --- Context service: context query tag ---
if "<context" in response and self.context_service:
    query_matches = CONTEXT_QUERY_PATTERN.findall(response)
    for filter_spec in query_matches:
        self.context_service.request_context_snapshot(
            filter=filter_spec or None
        )
        cmd_results.append(
            f"[context] snapshot requested"
            + (f" (filter: {filter_spec})" if filter_spec else "")
            + " — will appear in next request"
        )
    cleaned = CONTEXT_QUERY_PATTERN.sub("", cleaned).strip()
```


### `<evict>` — drop an entry from history

```xml
<evict id="ctx-3">
extracted the 3 fixes i needed, don't need the full 254KB report
anymore. session has ~30 more turns, savings will pay for the
cache break.
</evict>
```

### Regex

```python
EVICT_PATTERN = re.compile(
    r'<evict\s+id="([^"]+)"\s*>(.*?)</evict>',
    re.DOTALL,
)
```

### Handler

```python
# --- Context service: evict tag ---
if "<evict" in response and self.context_service:
    evict_matches = EVICT_PATTERN.findall(response)
    for ctx_id, reason in evict_matches:
        if self.context_service.evict(ctx_id, reason.strip()):
            cmd_results.append(
                f"[evict] {ctx_id} evicted from history "
                f"(cache broken from this message forward)"
            )
        else:
            cmd_results.append(f"[evict] error: {ctx_id} not found")
    cleaned = EVICT_PATTERN.sub("", cleaned).strip()
```


## File read integration

### Hook into `<read>` tool handler

The existing `<read>` handler in
`packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`
at `_execute_read` needs to be modified to consult ContextService
before returning the file content.

**Before (existing code, simplified):**

```python
def _execute_read(self, path: str, lines_spec: Optional[str] = None):
    # ... validation ...
    with open(path, "r") as f:
        content = f.read()
    # ... format response ...
    return {
        "success": True,
        "output": f"✓ Read {line_count} lines from {path}:\n\n{content}",
    }
```

**After (with ContextService hook):**

```python
def _execute_read(
    self,
    path: str,
    lines_spec: Optional[str] = None,
    force: bool = False,  # NEW: force flag from <read> tag attribute
):
    # ... existing validation ...
    with open(path, "rb") as f:
        disk_content_bytes = f.read()

    # Consult ContextService
    context_service = self._get_context_service()
    if context_service is not None:
        hook_result = context_service.file_read_hook(
            path=path,
            disk_content=disk_content_bytes,
            lines_spec=lines_spec,
            force=force,
        )

        if hook_result["action"] == "stale":
            # Return the short stale marker
            return {
                "success": True,
                "output": hook_result["content"].decode("utf-8"),
            }

        if hook_result["action"] == "diff":
            # Return the diff
            return {
                "success": True,
                "output": hook_result["content"].decode("utf-8"),
            }

        # fresh or force_fresh: fall through to normal read path

    # ... existing read + format logic ...
    content = disk_content_bytes.decode("utf-8", errors="replace")
    # ... apply line range ...

    output = f"✓ Read {line_count} lines from {path}:\n\n{content}"

    # Ingest into ledger AFTER the tool result has been logged
    # to conversation_manager — we need the message UUID. This is
    # done by the caller in queue_processor.py, not here.

    return {
        "success": True,
        "output": output,
    }
```

**Where does `did_ingest` happen?** The ledger ingestion needs the
message UUID, which isn't available inside the executor. It's
done in the turn-processing layer after the tool result is added
to conversation history:

```python
# In queue_processor.py or wherever tool results get logged
tool_result = self.tool_executor.execute(...)
await self.conversation_logger.log_tool_result(tool_result, ...)

# NEW: ingest into ContextService
context_service = self.event_bus.get_service("context_service")
if context_service is not None:
    message_uuid = self.conversation_manager.get_last_message_uuid()
    context_service.ingest_heavy_item(
        kind="tool_result" if tool_result.tool_type != "read" else "file_read",
        tool=tool_result.tool_type,
        label=tool_result.tool_input.get("file", tool_result.tool_type),
        content=tool_result.output.encode("utf-8"),
        message_uuid=message_uuid,
        file_path=tool_result.tool_input.get("file"),
    )
```


### `<read>` tag force attribute

The existing `<read>` regex doesn't support attributes. Update it
to support `force="true"`:

**Before:**

```python
READ_PATTERN = re.compile(r"<read>(.*?)</read>", re.DOTALL)
```

**After:**

```python
READ_PATTERN = re.compile(r"<read(?:\s+force=\"(true|false)\")?>(.*?)</read>", re.DOTALL)
```

The matcher now returns `(force_str, body)` tuples. Parse:

```python
for force_str, body in READ_PATTERN.findall(response):
    force = force_str == "true"
    # ... parse <file> sub-element from body ...
    self._execute_read(file_path, lines_spec, force=force)
```

Alternative (simpler): accept `<force/>` as a sibling tag before
`<read>` that sets a one-shot flag:

```python
<force/>
<read><file>plugins/hub/plugin.py</file></read>
```

Both forms can be supported. Prefer the attribute form
because it's tied to the specific read.


## Compaction integration

### Existing behavior

The existing `plugins/context_compaction_plugin.py` is supposed to
compact conversation history when the prompt exceeds a threshold.
It currently makes a separate LLM API call to generate a summary
of old messages.

### New behavior with ContextService

Instead of calling the LLM, compaction consults ContextService
for each old message. If the message is a heavy item with a
decision, apply the decision. If not, fall back to the previous
auto-summary behavior.

**Modified `_run_compaction` method:**

```python
async def _run_compaction(self, history: List[ConversationMessage]) -> List[ConversationMessage]:
    """Compact history by consulting the context service ledger."""
    context_service = None
    if self.event_bus:
        context_service = self.event_bus.get_service("context_service")

    if context_service is None:
        # Fallback: existing auto-summary path
        return await self._run_compaction_legacy(history)

    # New path: use ledger decisions
    split_point = self._find_split_point(history)
    old_msgs = history[:split_point]
    recent_msgs = history[split_point:]

    compacted = []
    for msg in old_msgs:
        entry = context_service.entry_for_message(msg.uuid)

        if entry is None:
            # Untracked message — keep as-is
            compacted.append(msg)
            continue

        if entry.decision == "keep":
            # Verbatim retention
            compacted.append(msg)
            continue

        if entry.decision == "summary":
            # Replace with agent-provided summary
            new_content = (
                f"[ctx-{entry.ctx_id} summary] {entry.decision_body}"
            )
            new_msg = ConversationMessage(
                role=msg.role,
                content=new_content,
                metadata={
                    "compacted_from": msg.uuid,
                    "ctx_id": entry.ctx_id,
                },
            )
            compacted.append(new_msg)
            continue

        if entry.decision == "evicted":
            # Already rewritten; keep the stub as-is
            compacted.append(msg)
            continue

        # Pending — fall back to auto-summary marker
        new_content = (
            f"[{entry.ctx_id} {entry.kind} {entry.label}, "
            f"{entry.size_bytes // 1024}KB, no curation, elided]"
        )
        new_msg = ConversationMessage(
            role=msg.role,
            content=new_content,
            metadata={
                "compacted_from": msg.uuid,
                "ctx_id": entry.ctx_id,
                "elided": True,
            },
        )
        compacted.append(new_msg)

    return compacted + recent_msgs
```

**Key wins:**

- No LLM call at compaction time (compaction becomes synchronous
  and fast)
- Agent-written summaries are higher quality than generic LLM
  summaries because the agent knows what it used the content for
- Pending items get clear elision markers so the agent knows what
  was dropped


## Request build integration

The LLM coordinator's `_build_messages` method already has the
notification dashboard injection (from the notification-system
spec). ContextService's injections go in the same place, in a
specific order:

```python
def _build_messages(self, history, new_user_msg=None):
    """Build messages array with ephemeral injections."""
    messages = [{"role": m.role, "content": m.content} for m in history]

    if new_user_msg:
        messages.append({"role": "user", "content": new_user_msg})

    # Gather all ephemeral injections
    injections = []

    # 1. Notification dashboard (from notification-system spec)
    queue = self.event_bus.get_service("notification_queue")
    if queue:
        from kollabor_ai.notifications.dashboard import NotificationDashboard
        notifs = queue.drain()
        if notifs:
            rendered = NotificationDashboard().render(notifs)
            if rendered:
                injections.append(rendered)

    # 2. Context service injections
    context_service = self.event_bus.get_service("context_service")
    if context_service:
        # Curator is checked before the snapshot — a curator fire
        # implicitly includes a ledger view
        curator_text = context_service.build_curator_injection()
        if curator_text:
            injections.append(curator_text)
        else:
            # Only show snapshot if not showing curator
            snapshot_text = context_service.build_context_snapshot()
            if snapshot_text:
                injections.append(snapshot_text)

            confirmation_text = context_service.build_confirmation_injection()
            if confirmation_text:
                injections.append(confirmation_text)

    # Prepend all injections to the last user message
    if injections:
        combined = "\n\n---\n\n".join(injections)
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = (
                combined + "\n\n---\n\n" + messages[-1]["content"]
            )
        else:
            messages.append({"role": "user", "content": combined})

    return messages
```

**Order of injections within one request:**

1. Notification dashboard (highest priority — agent needs to see
   state changes first)
2. Curator prompt (if pending — requires immediate agent action)
3. Context snapshot (if requested — informational)
4. Confirmation block (if decisions just happened — feedback)

Each is separated by `---` so the agent can parse them as
distinct sections.


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
      "file_dedup_mode": "stale_hit",
      "default_decision": "summary",
      "hub_broadcast_enabled": false,
      "hash_algorithm": "blake2b",
      "ledger_max_entries": 1000
    }
  }
}
```

| key | default | meaning |
|-----|---------|---------|
| `enabled` | `true` | Master switch for ContextService |
| `heavy_threshold_kb` | 8 | Items smaller than this aren't ledgered |
| `curate_threshold_kb` | 300 | Total ledger size that triggers curator |
| `curator_throttle_turns` | 2 | Min turns between curator re-prompts |
| `tool_result_cap_kb` | 32 | Hard cap on tool result size |
| `tool_result_overflow_action` | `truncate_with_ref` | How to handle oversized results |
| `file_dedup_mode` | `stale_hit` | `stale_hit`, `diff`, or `force_always` |
| `default_decision` | `summary` | What pending items become at compact time |
| `hub_broadcast_enabled` | `false` | Phase D — broadcast ledger events to peers |
| `hash_algorithm` | `blake2b` | `blake2b` (stdlib) or `xxh64` (new dep) |


## Config widgets

```python
@staticmethod
def get_config_widgets() -> Dict[str, Any]:
    return {
        "title": "Context Service",
        "widgets": [
            {
                "type": "checkbox",
                "label": "Enabled",
                "config_path": "plugins.context_service.enabled",
                "help": "Track heavy items and enable curation",
            },
            {
                "type": "slider",
                "label": "Heavy Threshold (KB)",
                "config_path": "plugins.context_service.heavy_threshold_kb",
                "min_value": 1,
                "max_value": 64,
                "step": 1,
                "help": "Items smaller than this are not tracked",
            },
            {
                "type": "slider",
                "label": "Curate Threshold (KB)",
                "config_path": "plugins.context_service.curate_threshold_kb",
                "min_value": 50,
                "max_value": 1000,
                "step": 50,
                "help": "Total ledger size that triggers curator prompt",
            },
            {
                "type": "slider",
                "label": "Tool Result Cap (KB)",
                "config_path": "plugins.context_service.tool_result_cap_kb",
                "min_value": 8,
                "max_value": 256,
                "step": 8,
                "help": "Hard cap on any single tool result in history",
            },
            {
                "type": "dropdown",
                "label": "File Dedup Mode",
                "config_path": "plugins.context_service.file_dedup_mode",
                "options": ["stale_hit", "diff", "force_always"],
                "help": "What to return on re-read of unchanged/changed files",
            },
            {
                "type": "dropdown",
                "label": "Default Decision",
                "config_path": "plugins.context_service.default_decision",
                "options": ["summary", "keep", "elide"],
                "help": "Fallback for pending items at compaction time",
            },
        ],
    }
```


## Static system prompt sections

### New file: `bundles/agents/_base/sections/tool-reference/curate.md`

```markdown
## Curating context

Your conversation history grows as you work. Tool results, file
reads, and terminal output accumulate, and eventually you'll cross
a compaction threshold. The context service tracks every heavy
item (over 8KB) as a "ledger entry" with an ID like `ctx-1`,
`ctx-2`, etc.

When the ledger gets large, the context service asks you to decide
what should stay verbatim vs what should be replaced with a summary
at compaction time. You make these decisions with `<curate>` tags.

### Marking an entry to keep verbatim

```
<curate id="ctx-1" decision="keep">
explain why you need this verbatim
</curate>
```

Use this for files you're actively editing or data you need to
reference exactly. Kept entries survive compaction unchanged.

### Marking an entry for summary replacement

```
<curate id="ctx-2" decision="summary">
write a compressed version of the content here. this exact text
replaces the full tool result at compaction time. include
everything future-you will need to work with this material again.
</curate>
```

Use this for material you've already extracted what you need from.
Your own summary is higher quality than the generic fallback the
system would use.

### When to curate

- When the curator prompts you (automatic, at 300KB threshold)
- Proactively, whenever you notice a heavy item you're done with
- Before ending a major work phase (clean up for the next one)

### Changing a prior decision

Emit a new `<curate>` tag with the same id. Last-write-wins —
your new decision overwrites the old one.

### Inspecting the ledger

Emit `<context/>` to see your current ledger on the next turn.
This doesn't drain the queue or change anything — it just shows
you what's tracked.

Filters:

- `<context filter="pending"/>` — only show undecided entries
- `<context filter="file_read"/>` — only show file reads
- `<context filter="path:plugins/hub"/>` — only show entries
  whose paths contain 'plugins/hub'

### Evicting an entry immediately

If you're done with an entry and want it gone NOW (not at next
compaction), emit:

```
<evict id="ctx-3">
explain why you're dropping it now
</evict>
```

**Eviction breaks prefix cache from that message forward.** Use
it only when the cache savings from the removed content outweigh
the cost of reprocessing everything after it. Rule of thumb: if
the session will continue for ≥10 more turns AND the evicted
entry is ≥32KB, eviction is probably worth it.

### Free operations

These operations don't cost anything and can be used freely:

- `<context/>` — query the ledger (read-only, free)
- `<curate>` — update decisions (in-memory flag flip, free)

These operations DO cost cache invalidation:

- `<evict>` — rewrites a history message, breaks cache forward
```


### New file: `bundles/agents/_base/sections/tool-reference/context.md`

Same content as above but shorter — serves as a quick reference.

Actually, merge both into one file. Make the tool-reference file
just one: `context.md`. Remove `curate.md` from the file list.


## Slash commands

### `/context`

New slash command for a user to inspect the ledger directly.

New file: `kollabor/commands/system_commands/handlers/context.py`

```python
"""Context service slash command handlers."""

import logging
from typing import Any

from kollabor_events.models import CommandResult

logger = logging.getLogger(__name__)


async def handle_context_command(args: str, event_bus: Any) -> CommandResult:
    """Handle /context and subcommands."""
    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    context_service = event_bus.get_service("context_service")
    if context_service is None:
        return CommandResult(
            success=False,
            message="context service is not running",
            display_type="error",
        )

    if not subcmd or subcmd == "show":
        # /context or /context show — display all entries
        snapshot = context_service.build_context_snapshot_display()
        return CommandResult(
            success=True, message=snapshot, display_type="info"
        )

    if subcmd == "evict":
        # /context evict ctx-3 [reason]
        ctx_id = rest.split(maxsplit=1)
        if not ctx_id:
            return CommandResult(
                success=False,
                message="usage: /context evict <ctx_id> [reason]",
                display_type="error",
            )
        target = ctx_id[0]
        reason = ctx_id[1] if len(ctx_id) > 1 else "user requested eviction"
        if context_service.evict(target, reason):
            return CommandResult(
                success=True,
                message=f"evicted {target}",
                display_type="info",
            )
        return CommandResult(
            success=False,
            message=f"entry {target} not found",
            display_type="error",
        )

    if subcmd == "stats":
        entries = context_service.all_entries()
        total_bytes = sum(e.size_bytes for e in entries if e.decision != "evicted")
        stats = {
            "total_entries": len(entries),
            "total_bytes": total_bytes,
            "pending": sum(1 for e in entries if e.decision == "pending"),
            "keep": sum(1 for e in entries if e.decision == "keep"),
            "summary": sum(1 for e in entries if e.decision == "summary"),
            "evicted": sum(1 for e in entries if e.decision == "evicted"),
        }
        msg = (
            f"context service stats:\n"
            f"  entries:   {stats['total_entries']}\n"
            f"  bytes:     {stats['total_bytes'] // 1024}KB\n"
            f"  pending:   {stats['pending']}\n"
            f"  keep:      {stats['keep']}\n"
            f"  summary:   {stats['summary']}\n"
            f"  evicted:   {stats['evicted']}"
        )
        return CommandResult(success=True, message=msg, display_type="info")

    if subcmd == "clear":
        # /context clear — wipe ledger (does not touch history)
        context_service._ledger._entries.clear()
        return CommandResult(
            success=True, message="ledger cleared", display_type="info"
        )

    return CommandResult(
        success=False,
        message=f"unknown subcommand: {subcmd}",
        display_type="error",
    )
```

Register in `kollabor/commands/registry.py`:

```python
from kollabor.commands.system_commands.handlers.context import handle_context_command
from kollabor_events.models import CommandDefinition, CommandCategory, SubcommandInfo

context_cmd = CommandDefinition(
    name="context",
    description="Context service ledger management",
    category=CommandCategory.SYSTEM,
    handler=handle_context_command,
    subcommands=[
        SubcommandInfo("show", "", "Display the current ledger"),
        SubcommandInfo("evict", "<ctx_id> [reason]", "Evict a ledger entry"),
        SubcommandInfo("stats", "", "Show ledger statistics"),
        SubcommandInfo("clear", "", "Clear the ledger (does not touch history)"),
    ],
)
command_registry.register_command(context_cmd)
```


## Testing

### Unit tests

New file: `tests/unit/test_context_service_ledger.py`

```python
"""Unit tests for the ContextService ledger."""

from datetime import datetime

from kollabor_ai.context_service.ledger import Ledger
from kollabor_ai.context_service.models import LedgerEntry


def _make_entry(ctx_id="ctx-1", size=10000, decision="pending"):
    return LedgerEntry(
        ctx_id=ctx_id,
        kind="file_read",
        tool="read",
        label="test.py",
        content_hash="abc123",
        size_bytes=size,
        message_uuid="uuid-1",
        added_at=datetime.now(),
        last_accessed_at=datetime.now(),
        decision=decision,
    )


def test_next_ctx_id_sequential():
    ledger = Ledger()
    assert ledger.next_ctx_id() == "ctx-1"
    assert ledger.next_ctx_id() == "ctx-2"
    assert ledger.next_ctx_id() == "ctx-3"


def test_add_and_get():
    ledger = Ledger()
    entry = _make_entry()
    ledger.add(entry)
    assert ledger.get("ctx-1") is entry
    assert ledger.get("ctx-999") is None


def test_all_sorted_by_ctx_id():
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-10"))
    ledger.add(_make_entry(ctx_id="ctx-2"))
    ledger.add(_make_entry(ctx_id="ctx-5"))
    entries = ledger.all()
    assert [e.ctx_id for e in entries] == ["ctx-2", "ctx-5", "ctx-10"]


def test_total_bytes_excludes_evicted():
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", size=10000))
    ledger.add(_make_entry(ctx_id="ctx-2", size=20000, decision="evicted"))
    assert ledger.total_bytes() == 10000


def test_count_pending():
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", decision="pending"))
    ledger.add(_make_entry(ctx_id="ctx-2", decision="keep"))
    ledger.add(_make_entry(ctx_id="ctx-3", decision="pending"))
    assert ledger.count_pending() == 2
```

### TMux tests

#### `context_service_dedup.json`

```json
{
  "name": "file read dedup stale hit",
  "description": "Reading the same file twice returns a stale marker",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "read plugins/hub/plugin.py then read it again" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 5 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "stale hit" }
  ]
}
```

#### `context_service_diff.json`

```json
{
  "name": "file read diff on change",
  "description": "Re-reading a file after modification returns a diff",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "read plugins/hub/plugin.py, then edit one line, then read it again" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 8 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "file changed" }
  ]
}
```

#### `context_service_curator.json`

```json
{
  "name": "curator fires at threshold",
  "description": "Enough heavy items triggers the curator prompt",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "read 4 large files to cross the 300KB threshold" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 10 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "context service: curator" }
  ]
}
```


## Open questions

### Q1: Hash algorithm

**Recommendation:** blake2b from stdlib. Fast enough (hashes 50KB
in under a millisecond), no new dependencies, 8-byte digest is
sufficient for dedup.

**Fallback:** xxh64 via `xxhash` package. Faster but adds a C
extension dependency. Only worth it if profiling shows hashing
is a bottleneck.

### Q2: Ledger persistence across session resume

**Recommendation:** no persistence in v1. Ledger is in-memory only.
On resume, rebuild by rehashing tool results in history if
needed, OR just start with an empty ledger and let the agent
re-curate.

**Fallback:** persist as JSONL alongside the conversation file.
Adds I/O but means resuming sessions retains curation decisions.

### Q3: File line-range dedup granularity

If I read lines 1-100, then lines 50-150, do they dedup?

**Recommendation:** treat each unique `(path, line_range)` as its
own entry. Simple and correct for the common case. Over-fragments
if the agent reads many overlapping ranges.

**Fallback:** track read coverage per file as a set of intervals,
dedup by interval overlap. More correct but more complex.

### Q4: Evict cascade

If the agent evicts ctx-3 which maps to msg_idx=15 (a tool
result), do we also evict msg_idx=14 (the corresponding assistant
message that contained the tool call)?

**Recommendation:** evict both. The tool_use + tool_result pair
must stay coherent in history. Rewriting just the result would
leave an orphan call.

**Fallback:** evict only the result. Simpler but produces
incoherent history that some providers may reject on next
request.

### Q5: Force scope

Does `force="true"` apply to all `<read>` tags in the response,
or only the one it's attributed to?

**Recommendation:** only the tag with the attribute. One-shot
per-tag. This is the simplest semantic.

**Fallback:** a wrapping `<force>...</force>` block applies to
all reads inside it. More flexible but confusing.

### Q6: Hub shared content (phase D)

Can lapis receive koordinator's ctx-7 content directly (without
re-reading from disk)?

**Recommendation:** no, lapis always re-reads from disk. The hub
bridge only shares metadata (hash, size, decision body) — content
is too large to shuffle over the mesh efficiently.

**Fallback:** share full content. Higher bandwidth but means
lapis can work without file system access to the same files.

### Q7: Phase D hub integration in first PR?

**Recommendation:** defer. Ship phases A-C (single-agent
ContextService) first. Phase D is complex and needs the
notification system and hub-loop-prevention specs fully
implemented as dependencies.

**Fallback:** include phase D in the first release. More work but
enables multi-agent benefits from day one.

### Q8: Does the curator fire for waiting agents?

**Recommendation:** no. Waiting agents have just declared they
have nothing to do. Firing curator on them wakes them up for
something they didn't ask for. Skip the curator check if
`self._identity.state == "waiting"`.

**Fallback:** fire anyway. The curator message is helpful
information even for waiting agents. But violates the spirit of
waiting state.


## Phasing

### Phase A (MVP — single-agent core)

- `ToolDefinition` / `LedgerEntry` / `FileVersion` models
- `Ledger` class
- `FileTracker` class
- `ContextService` core class
- `<curate>`, `<context/>`, `<evict>` regex parsing in response_parser
- `<read>` hook integration
- Tool result ingestion in queue_processor
- Unit tests for ledger, file tracker, service

### Phase B (compaction integration)

- Modified `_run_compaction` in context_compaction_plugin
- Verify agent-provided summaries replace old messages
- Verify no LLM call at compact time when ledger has decisions

### Phase C (UX polish)

- Curator prompt injection
- Context snapshot injection
- Confirmation block injection
- `/context` slash command
- Static system prompt section
- Config widgets
- Notification system integration

### Phase D (hub bridge)

- `hub_bridge.py` — publish ledger events to hub peers
- Cross-agent divergent hash warnings
- `<hub_ask_ctx>` tag for querying peer summaries
- Peer-aware filter (`<context filter="peer:lapis"/>`)


## Non-goals

- **Content storage outside history.** The ledger indexes content
  that lives in conversation_manager. It does NOT store its own
  copy. If a message is removed from history, the ledger entry
  becomes stale.
- **Vector search / similarity.** No embeddings, no fuzzy matching.
  Content identity is by exact hash only.
- **Multi-session persistence.** Ledger is per-session.
  Cross-session sharing is not supported.
- **Real-time hub content sync.** Hub bridge (phase D) shares
  metadata only, not content. Peer agents re-read from disk when
  they need content.


## File inventory

New files:

```
packages/kollabor-ai/src/kollabor_ai/context_service/__init__.py
packages/kollabor-ai/src/kollabor_ai/context_service/models.py
packages/kollabor-ai/src/kollabor_ai/context_service/service.py
packages/kollabor-ai/src/kollabor_ai/context_service/ledger.py
packages/kollabor-ai/src/kollabor_ai/context_service/file_tracker.py
packages/kollabor-ai/src/kollabor_ai/context_service/curator.py
packages/kollabor-ai/src/kollabor_ai/context_service/hash_utils.py
packages/kollabor-ai/src/kollabor_ai/context_service/hub_bridge.py  # phase D
bundles/agents/_base/sections/tool-reference/context.md
kollabor/commands/system_commands/handlers/context.py
tests/unit/test_context_service_ledger.py
tests/unit/test_context_service_file_tracker.py
tests/unit/test_context_service_curator.py
tests/tmux/specs/context_service_dedup.json
tests/tmux/specs/context_service_diff.json
tests/tmux/specs/context_service_force.json
tests/tmux/specs/context_service_curator.json
tests/tmux/specs/context_service_compact.json
tests/tmux/specs/context_service_evict.json
```

Modified files:

```
packages/kollabor-ai/src/kollabor_ai/response_parser.py
packages/kollabor-ai/src/kollabor_ai/conversation_manager.py
packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py
kollabor/llm/llm_coordinator.py
plugins/context_compaction_plugin.py
kollabor/commands/registry.py
bundles/agents/_base/sections/protocols/tool-execution.md
```

Do NOT modify:

```
packages/kollabor-events/   # event system unchanged
packages/kollabor-tui/      # terminal rendering unchanged
plugins/hub/                # unchanged by this spec (phase D ref only)
```

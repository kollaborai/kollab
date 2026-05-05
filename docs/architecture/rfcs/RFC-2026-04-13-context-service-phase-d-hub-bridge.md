---
title: "Context Service — Phase D: Hub Bridge"
doc_type: architecture-rfc
created: 2026-04-13
modified: 2026-04-13
status: MVP shipped 2026-04-20 (notification system pending — divergent warnings use fallback inline injection)
parent: RFC-2026-04-11-context-service.md
owner: kollabor-ai + plugins/hub
depends_on:
  - RFC-2026-04-11-context-service.md
  - RFC-2026-04-11-agent-notification-system.md
  - RFC-2026-04-11-hub-loop-prevention.md
extracted_from: RFC-2026-04-11-context-service.md
---
# Context Service — Phase D: Hub Bridge

> Multi-agent extension to the Context Service. Shares ledger metadata
> across hub peers so agents working the same codebase can detect divergent
> file versions and query each other's context summaries. Content is never
> shared over the mesh; agents always re-read from disk.


## Why this is a separate spec

The parent spec (`RFC-2026-04-11-context-service.md`) explicitly recommends deferring
phase D from the initial implementation:

> **Q7: Phase D hub integration in first PR?**
> Recommendation: defer. Ship phases A-C (single-agent ContextService)
> first. Phase D is complex and needs the notification system and
> hub-loop-prevention specs fully implemented as dependencies.

This document extracts the phase D content into a standalone spec so
it can be implemented independently once its dependencies are ready.

Dependencies (must be implemented first):
1. **RFC-2026-04-11-context-service.md phases A-C** — the single-agent ContextService
   with models, ledger, file tracker, curator, and compaction
   integration must be fully operational.
2. **RFC-2026-04-11-agent-notification-system.md** — phase D pushes divergent-hash
   warnings to peers via the notification queue. The notification
   system must be in place to deliver these.
3. **RFC-2026-04-11-hub-loop-prevention.md** — the `<wait_for_user/>` tag and waiting
   state must exist. Phase D must not fire curator prompts or hub
   broadcasts on waiting agents.

If any of these are not implemented, stop and implement them first.


## For implementers

Read the parent spec (`RFC-2026-04-11-context-service.md`) in full before starting.
Phase D adds a module (`hub_bridge.py`) to the existing context service
package. You need to understand the models, ledger, and service
architecture from the parent spec.

Implementation order:
1. Add `hub_shared` and `hub_holders` fields to `LedgerEntry` (if not
   already present from phase A-C)
2. Implement `hub_bridge.py`
3. Add `hub_broadcast_enabled` config (if not already present)
4. Register hub bridge hooks in the event pipeline
5. Add `<hub_ask_ctx>` tag handler
6. Add peer-aware filter support
7. Add tests


## What phase D adds

Phase D extends the single-agent ContextService with four capabilities:

### 1. Ledger event broadcasting

When the ledger records a new heavy item or detects a changed file,
`hub_bridge.py` broadcasts a metadata-only event to hub peers.

Broadcast payload (via `<hub_broadcast>` or hub plugin's internal
message bus):

```json
{
  "type": "context_ledger_update",
  "source": "koordinator",
  "entry": {
    "ctx_id": "ctx-7",
    "content_hash": "blake2b:abc123...",
    "file_path": "kollabor/llm/service.py",
    "file_version": 3,
    "size_kb": 45,
    "decision": "verbatim"
  },
  "timestamp": "2026-04-13T14:00:00Z"
}
```

Content itself is never included — only metadata. Peers that need
the content re-read from disk.

### 2. Cross-agent divergent hash warnings

When agent A broadcasts that it holds version 3 of `service.py` with
hash X, and agent B holds version 2 with hash Y, the hub bridge on
agent B's side detects the divergence and pushes a notification:

```
[notification queue]
  ⚠ divergent file: kollabor/llm/service.py
    your version: v2 (blake2b:Y...)
    peer koordinator: v3 (blake2b:X...)
    re-read recommended if this file matters to your current task
```

This goes through the notification system (RFC-2026-04-11-agent-notification-system.md),
not directly into the conversation. It appears on the agent's next
mission-control dashboard.

### 3. `<hub_ask_ctx>` tag

A new XML tag that lets an agent query a peer's context summary:

```xml
<hub_ask_ctx peer="lapis" filter="file:kollabor/" />
```

The peer responds with a summary of its ledger entries matching the
filter. The response is metadata-only:

```
Tool result: [hub_ask_ctx]
  peer lapis context (kollabor/ files):
    ctx-3: kollabor/llm/service.py v2 (45kb, verbatim)
    ctx-5: kollabor/commands/registry.py v1 (12kb, summary)
    total: 57kb tracked, 2 entries
```

### 4. Peer-aware filter for `<context/>` tag

Extends the existing `<context/>` snapshot tag with a peer filter:

```xml
<context filter="peer:lapis" />
```

Returns the intersection of the agent's ledger with what it knows
about the peer's context (from received broadcasts). Useful for
seeing what files the peer has already read, so the agent can avoid
duplicating work.

Filter syntax: `peer:<identity>` — shows only entries where the
named peer also holds a version (per `hub_holders`).


## New file

```
packages/kollabor-ai/src/kollabor_ai/context_service/hub_bridge.py
```

### hub_bridge.py sketch

```python
"""Phase D: Hub bridge for cross-agent context metadata sharing.

Broadcasts ledger events to hub peers and processes incoming
broadcasts from other agents. Never shares file content — only
metadata (hash, size, decision, file version).

Dependencies:
  - RFC-2026-04-11-context-service.md phases A-C (ledger, models)
  - RFC-2026-04-11-agent-notification-system.md (notification queue)
  - RFC-2026-04-11-hub-loop-prevention.md (waiting state check)
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class PeerLedgerSnapshot:
    """A peer's broadcast ledger entry that we've received."""
    source_identity: str
    ctx_id: str
    content_hash: str
    file_path: Optional[str] = None
    file_version: Optional[int] = None
    size_kb: float = 0
    decision: str = "pending"
    timestamp: str = ""


class HubBridge:
    """Publishes ledger events to hub peers and processes
    incoming broadcasts.

    Lifecycle:
    1. Created by ContextService if hub_broadcast_enabled is true
    2. Hooks into ledger events (new entry, file version change)
    3. Broadcasts metadata to peers via hub plugin
    4. Receives peer broadcasts, stores in peer_snapshots
    5. Detects divergent hashes, pushes notifications
    """

    def __init__(self, identity: str, ledger, config: Dict[str, Any]):
        self.identity = identity
        self.ledger = ledger
        self.config = config
        self.peer_snapshots: Dict[str, List[PeerLedgerSnapshot]] = {}
        self._enabled = config.get("hub_broadcast_enabled", False)

    def on_ledger_event(self, entry) -> None:
        """Called when the ledger records a new or updated entry.
        Broadcasts metadata to hub peers."""
        if not self._enabled:
            return
        # Don't broadcast for waiting agents
        # (check via presence state — RFC-2026-04-11-hub-loop-prevention.md)
        payload = self._build_broadcast_payload(entry)
        self._broadcast(payload)
        entry.hub_shared = True

    def on_peer_broadcast(self, broadcast: Dict[str, Any]) -> None:
        """Called when a peer's ledger event arrives.
        Stores the snapshot and checks for divergent hashes."""
        snapshot = PeerLedgerSnapshot(
            source_identity=broadcast["source"],
            ctx_id=broadcast["entry"]["ctx_id"],
            content_hash=broadcast["entry"]["content_hash"],
            file_path=broadcast["entry"].get("file_path"),
            file_version=broadcast["entry"].get("file_version"),
            size_kb=broadcast["entry"].get("size_kb", 0),
            decision=broadcast["entry"].get("decision", "pending"),
            timestamp=broadcast.get("timestamp", ""),
        )
        source = snapshot.source_identity
        if source not in self.peer_snapshots:
            self.peer_snapshots[source] = []
        self.peer_snapshots[source].append(snapshot)

        # Check for divergent hashes
        if snapshot.file_path:
            self._check_divergence(snapshot)

    def _check_divergence(self, peer_snapshot: PeerLedgerSnapshot) -> None:
        """Compare peer's file version against our ledger.
        If hashes differ, push a notification."""
        our_entry = self.ledger.find_by_path(peer_snapshot.file_path)
        if our_entry is None:
            return  # We don't have this file
        if our_entry.content_hash == peer_snapshot.content_hash:
            return  # Same version, no divergence

        # Push divergent-hash warning via notification system
        notification = {
            "type": "divergent_file",
            "file_path": peer_snapshot.file_path,
            "our_version": our_entry.file_version,
            "our_hash": our_entry.content_hash,
            "peer": peer_snapshot.source_identity,
            "peer_version": peer_snapshot.file_version,
            "peer_hash": peer_snapshot.content_hash,
        }
        self._push_notification(notification)

        # Record peer as a holder on our entry
        if peer_snapshot.source_identity not in our_entry.hub_holders:
            our_entry.hub_holders.append(peer_snapshot.source_identity)

    def handle_hub_ask_ctx(
        self, peer: str, filter_str: Optional[str] = None
    ) -> str:
        """Handle <hub_ask_ctx> tag. Returns a text summary
        of the requested peer's known context."""
        if peer not in self.peer_snapshots:
            return f"no context data for peer '{peer}'"
        snapshots = self.peer_snapshots[peer]
        if filter_str:
            snapshots = self._apply_filter(snapshots, filter_str)
        return self._format_snapshot_summary(peer, snapshots)

    def _build_broadcast_payload(self, entry) -> Dict[str, Any]:
        """Build the broadcast payload from a ledger entry."""
        return {
            "type": "context_ledger_update",
            "source": self.identity,
            "entry": {
                "ctx_id": entry.ctx_id,
                "content_hash": entry.content_hash,
                "file_path": entry.file_path,
                "file_version": entry.file_version,
                "size_kb": entry.size_kb,
                "decision": entry.decision,
            },
            "timestamp": "",  # ISO 8601, set at broadcast time
        }

    def _broadcast(self, payload: Dict[str, Any]) -> None:
        """Send payload to hub peers via hub plugin."""
        # Implementation uses hub plugin's internal message bus
        # or emits a <hub_broadcast> tag
        raise NotImplementedError("wire to hub plugin in integration step")

    def _push_notification(self, notification: Dict[str, Any]) -> None:
        """Push notification via the notification system."""
        # Implementation wires to RFC-2026-04-11-agent-notification-system.md
        raise NotImplementedError("wire to notification system in integration step")

    def _apply_filter(
        self, snapshots: List[PeerLedgerSnapshot], filter_str: str
    ) -> List[PeerLedgerSnapshot]:
        """Apply a filter string to snapshots.
        Supports: file:<prefix>, peer:<identity>"""
        if filter_str.startswith("file:"):
            prefix = filter_str[5:]
            return [s for s in snapshots if s.file_path and s.file_path.startswith(prefix)]
        return snapshots

    def _format_snapshot_summary(
        self, peer: str, snapshots: List[PeerLedgerSnapshot]
    ) -> str:
        """Format peer context snapshots as a readable summary."""
        if not snapshots:
            return f"peer {peer}: no matching context entries"
        lines = [f"peer {peer} context:"]
        total_kb = 0
        for s in snapshots:
            total_kb += s.size_kb
            path = s.file_path or "(no file)"
            lines.append(
                f"  {s.ctx_id}: {path} v{s.file_version or '?'} "
                f"({s.size_kb:.0f}kb, {s.decision})"
            )
        lines.append(f"  total: {total_kb:.0f}kb tracked, {len(snapshots)} entries")
        return "\n".join(lines)
```


## Model changes (LedgerEntry)

Phase D adds two fields to `LedgerEntry` (from RFC-2026-04-11-context-service.md):

```python
# In models.py LedgerEntry dataclass

hub_shared: bool = False
"""If True, this entry has been broadcast to hub peers."""

hub_holders: List[str] = field(default_factory=list)
"""List of peer identities that also hold a version of this
heavy item. Populated via hub broadcasts."""
```

These fields should already exist in the phase A-C models (the parent
spec includes them) but they remain unused until phase D is implemented.


## Config changes

One config entry controls phase D:

| key | default | meaning |
|-----|---------|---------|
| `hub_broadcast_enabled` | `false` | Enable phase D hub bridge |

When `false` (default), `HubBridge` is not instantiated and no
broadcasts occur. The phase A-C ContextService works identically
whether this is true or false.

One new config widget:

```python
{
    "type": "checkbox",
    "label": "Hub Broadcast",
    "config_path": "plugins.context_service.hub_broadcast_enabled",
    "help": "Share context metadata with hub peers (phase D)",
}
```


## Tag handlers

### `<hub_ask_ctx>`

Registered as a plugin tag via `response_parser.register_plugin_tag()`.

Regex: `<hub_ask_ctx\s+peer="([^"]+)"(?:\s+filter="([^"]+)")?\s*/>`

Handler method in `HubBridge.handle_hub_ask_ctx()` returns a
`ToolExecutionResult` with the peer summary text.

Example usage:

```xml
<hub_ask_ctx peer="lapis" filter="file:kollabor/" />
```

Example result:

```
Tool result: [hub_ask_ctx]
  peer lapis context:
    ctx-3: kollabor/llm/service.py v2 (45kb, verbatim)
    ctx-5: kollabor/commands/registry.py v1 (12kb, summary)
    total: 57kb tracked, 2 entries
```

### Peer-aware `<context filter="peer:lapis" />`

Extends the existing `<context/>` snapshot handler (from phase B/C)
with a new filter type. When `filter="peer:X"` is specified, the
handler:

1. Looks at `hub_holders` on each ledger entry
2. Returns only entries where identity X is in the holders list
3. Includes peer's known version info if available


## Integration points

### Hub plugin (plugins/hub/plugin.py)

Phase D requires the hub plugin to:
1. Route incoming `context_ledger_update` broadcasts to the
   `HubBridge.on_peer_broadcast()` method
2. Provide the broadcast mechanism for outgoing events
3. Handle `<hub_ask_ctx>` as a routed tag (similar to existing
   hub tag handlers)

### Notification system (RFC-2026-04-11-agent-notification-system.md)

Phase D pushes divergent-hash warnings through the notification
queue. The notification type is `divergent_file` with fields:
- `file_path`, `our_version`, `our_hash`
- `peer`, `peer_version`, `peer_hash`

### Presence state (RFC-2026-04-11-hub-loop-prevention.md)

Hub bridge must check presence state before broadcasting:
- `active` agents: broadcast normally
- `waiting` agents: skip broadcast, queue for when agent wakes

This prevents waking a waiting agent just to relay context metadata.


## Event flow

### Outgoing broadcast

```
Ledger records new entry
  → HubBridge.on_ledger_event(entry)
  → _build_broadcast_payload(entry)
  → _broadcast(payload)
  → hub plugin sends to all peers
```

### Incoming broadcast

```
Hub plugin receives context_ledger_update
  → HubBridge.on_peer_broadcast(broadcast)
  → store PeerLedgerSnapshot
  → _check_divergence(snapshot)
  → if divergent: _push_notification(warning)
  → update entry.hub_holders
```

### Query flow

```
Agent emits <hub_ask_ctx peer="lapis" filter="file:kollabor/" />
  → response_parser strips tag, routes to handler
  → HubBridge.handle_hub_ask_ctx("lapis", "file:kollabor/")
  → returns formatted summary text
```


## Tests

New test file:

```
tests/unit/test_context_service_hub_bridge.py
```

Test cases:

1. **Broadcast on new entry** — verify payload structure and
   `hub_shared` flag set
2. **No broadcast when disabled** — verify nothing sent when
   `hub_broadcast_enabled` is false
3. **Divergent hash detection** — receive peer broadcast with
   different hash, verify notification pushed
4. **Same hash, no notification** — receive peer broadcast with
   same hash, verify no notification
5. **hub_ask_ctx with matching filter** — query peer context,
   verify filtered results
6. **hub_ask_ctx with unknown peer** — query unknown peer,
   verify graceful response
7. **Peer-aware context filter** — `<context filter="peer:lapis"/>`
   returns only shared entries
8. **Waiting agent skip** — agent in waiting state does not
   broadcast
9. **hub_holders accumulation** — multiple broadcasts from same
   peer don't duplicate entries


## Open questions

### Q1: Should hub_ask_ctx go through the hub or read peer_snapshots directly?

**Recommendation:** read `peer_snapshots` directly. The data is
already local (received via previous broadcasts). No need for a
round-trip to the peer.

**Fallback:** send a `<hub_msg>` to the peer requesting its current
ledger summary. More accurate but slower and requires the peer to
be active.

### Q2: How often should broadcasts fire?

**Recommendation:** on every new ledger entry and every file version
change. These are infrequent events (agents don't read hundreds of
files per turn), so the volume is manageable.

**Fallback:** batch broadcasts every N seconds or N entries. More
efficient for bursty patterns but introduces staleness.

### Q3: Stale peer snapshots — when to evict?

**Recommendation:** keep peer snapshots for the session duration.
Sessions are finite, and disk is cheap. Add a max-age config entry
later if needed.

**Fallback:** evict snapshots older than N minutes. Prevents
stale data but may lose useful context in long sessions.


## Non-goals

- **Content sharing over the mesh.** Only metadata is broadcast.
  Peers re-read files from disk. Content is too large and the
  hub mesh is not designed for bulk data transfer.
- **Cross-session persistence.** Peer snapshots are per-session.
  They do not survive session restart.
- **Conflict resolution.** If two agents edit the same file
  concurrently, phase D detects the divergence but does not
  resolve it. Agents handle that through their normal workflow.
- **Vector search across peers.** No embeddings, no similarity
  search. Exact hash comparison only.


## Do not modify

These files are out of scope:

- `packages/kollabor-events/` — no new event types needed
- `packages/kollabor-tui/` — no UI changes (notifications render
  through existing notification queue)
- `kollabor/application.py` — no lifecycle changes
- Phase A-C context service files — only additive changes to
  `models.py` (the two fields already exist)

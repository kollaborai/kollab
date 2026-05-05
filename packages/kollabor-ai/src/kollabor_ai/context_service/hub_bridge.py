"""Phase D: Hub bridge for cross-agent context metadata sharing.

Broadcasts ledger events to hub peers and processes incoming
broadcasts from other agents. Never shares file content — only
metadata (hash, size, decision, file version).

MVP scope (see docs/architecture/rfcs/RFC-2026-04-13-context-service-phase-d-hub-bridge.md):
  - outgoing ledger broadcasts via hub plugin
  - incoming broadcast storage + divergence detection
  - divergent-hash warnings queued on the context service for inline
    injection at the next turn (fallback until the agent notification
    system lands)
  - <hub_ask_ctx peer="X" filter="..." /> query handler
  - <context filter="peer:X" /> extension to the ledger snapshot

Feature-flagged off by default via
plugins.context_service.hub_broadcast_enabled. When the flag is
false, HubBridge is not instantiated and no traffic is emitted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PeerLedgerSnapshot:
    """A peer's broadcast ledger entry that we've received."""

    source_identity: str
    ctx_id: str
    content_hash: str
    file_path: Optional[str] = None
    file_version: Optional[int] = None
    size_kb: float = 0.0
    decision: str = "pending"
    timestamp: str = ""


# Broadcaster signature: async callable that takes a payload dict and
# ships it to peers. Provided by the hub plugin at wire time so we stay
# independent of hub internals.
Broadcaster = Callable[[Dict[str, Any]], Awaitable[None]]


class HubBridge:
    """Publishes ledger events to hub peers and processes incoming broadcasts.

    Lifecycle:
      1. Created by the plugin that integrates ContextService when
         ``hub_broadcast_enabled`` is true.
      2. ``on_ledger_event`` is called by ContextService when a new
         or updated entry is recorded.
      3. Outgoing broadcasts go through the injected ``broadcaster``
         (wired by the hub plugin to HubMessage(action="context_ledger_update")).
      4. Incoming broadcasts arrive via ``on_peer_broadcast`` from the
         hub plugin's dispatcher. Divergent hashes are queued on the
         context service for inline warning injection.
      5. ``handle_hub_ask_ctx`` answers <hub_ask_ctx/> queries from the
         local agent using stored peer snapshots.

    Content is never broadcast — only metadata.
    """

    def __init__(
        self,
        identity: str,
        context_service: Any,
        broadcaster: Optional[Broadcaster] = None,
        is_waiting: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.identity = identity
        self._context_service = context_service
        self._broadcaster = broadcaster
        self._is_waiting = is_waiting or (lambda: False)
        self.peer_snapshots: Dict[str, List[PeerLedgerSnapshot]] = {}

    # ------------------------------------------------------------------
    # Outgoing broadcasts
    # ------------------------------------------------------------------

    async def on_ledger_event(
        self, entry: Any, _was_waiting: Optional[bool] = None
    ) -> None:
        """Broadcast a new or updated ledger entry to peers.

        Called by ContextService after a ledger add. Skips the
        broadcast if the agent was in waiting state at schedule time
        (hub-loop-prevention) or if no broadcaster is wired.

        Args:
            entry: The LedgerEntry that was added or updated.
            _was_waiting: Waiting-state snapshot captured at schedule time
                (from ContextService._maybe_broadcast_ledger_event). Falls
                back to live _is_waiting() when not provided.
        """
        if self._broadcaster is None:
            return
        waiting = _was_waiting if _was_waiting is not None else self._is_waiting()
        if waiting:
            logger.debug(
                "Skipping ledger broadcast (agent in waiting state): %s",
                entry.ctx_id,
            )
            return

        payload = self._build_broadcast_payload(entry)
        try:
            await self._broadcaster(payload)
            entry.hub_shared = True
        except Exception as e:
            logger.debug("Ledger broadcast failed for %s: %s", entry.ctx_id, e)

    def _build_broadcast_payload(self, entry: Any) -> Dict[str, Any]:
        """Build the broadcast payload from a ledger entry."""
        return {
            "type": "context_ledger_update",
            "source": self.identity,
            "entry": {
                "ctx_id": entry.ctx_id,
                "content_hash": entry.content_hash,
                "file_path": entry.file_path,
                "file_version": entry.file_version,
                "size_kb": entry.size_bytes / 1024.0,
                "decision": entry.decision,
            },
            "timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Incoming broadcasts
    # ------------------------------------------------------------------

    def on_peer_broadcast(self, broadcast: Dict[str, Any]) -> None:
        """Store a peer's ledger event and check for divergent hashes."""
        try:
            entry = broadcast["entry"]
            snapshot = PeerLedgerSnapshot(
                source_identity=broadcast.get("source", ""),
                ctx_id=entry.get("ctx_id", ""),
                content_hash=entry.get("content_hash", ""),
                file_path=entry.get("file_path"),
                file_version=entry.get("file_version"),
                size_kb=float(entry.get("size_kb", 0) or 0),
                decision=entry.get("decision", "pending"),
                timestamp=broadcast.get("timestamp", ""),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Malformed peer broadcast ignored: %s", e)
            return

        if not snapshot.source_identity or snapshot.source_identity == self.identity:
            return

        bucket = self.peer_snapshots.setdefault(snapshot.source_identity, [])
        # Replace any prior entry with the same ctx_id (peer updated it)
        bucket[:] = [s for s in bucket if s.ctx_id != snapshot.ctx_id]
        bucket.append(snapshot)

        if snapshot.file_path:
            self._check_divergence(snapshot)

    def _check_divergence(self, peer_snapshot: PeerLedgerSnapshot) -> None:
        """Compare peer's file version against our ledger.

        If hashes differ, queue a divergent-file warning on the context
        service. No notification queue exists yet — warnings ride the
        same injection rail as <context/> snapshots and the curator.
        """
        ledger = getattr(self._context_service, "_ledger", None)
        if ledger is None or not hasattr(ledger, "find_by_path"):
            return

        our_entry = ledger.find_by_path(peer_snapshot.file_path)
        if our_entry is None:
            return
        if our_entry.content_hash == peer_snapshot.content_hash:
            return

        warning = {
            "file_path": peer_snapshot.file_path,
            "our_version": our_entry.file_version,
            "our_hash": our_entry.content_hash,
            "peer": peer_snapshot.source_identity,
            "peer_version": peer_snapshot.file_version,
            "peer_hash": peer_snapshot.content_hash,
        }

        # Prefer the env notification queue if wired; fall back to the
        # inline ContextService injection rail when no queue is present
        # (single-agent sessions, tests, boot timing).
        pushed_to_queue = False
        event_bus = getattr(self._context_service, "_event_bus", None)
        if event_bus is not None:
            try:
                from kollabor_ai.notifications.producer import push_env

                push_env(
                    event_bus,
                    "file",
                    (
                        f"{peer_snapshot.file_path} diverges "
                        f"(v{our_entry.file_version} local vs "
                        f"v{peer_snapshot.file_version} {peer_snapshot.source_identity})"
                    ),
                    kind="file_changed",
                    collapse_key=f"diverge:{peer_snapshot.file_path}",
                )
                pushed_to_queue = (
                    event_bus.get_service("env_queue") is not None
                )
            except Exception:
                pushed_to_queue = False

        if not pushed_to_queue and hasattr(
            self._context_service, "queue_divergence_warning"
        ):
            self._context_service.queue_divergence_warning(warning)

        if peer_snapshot.source_identity not in our_entry.hub_holders:
            our_entry.hub_holders.append(peer_snapshot.source_identity)

    # ------------------------------------------------------------------
    # Query handling (<hub_ask_ctx/>)
    # ------------------------------------------------------------------

    def handle_hub_ask_ctx(
        self, peer: str, filter_str: Optional[str] = None
    ) -> str:
        """Return a text summary of a peer's known context entries.

        Reads from local ``peer_snapshots`` — the data was collected from
        prior broadcasts, no round-trip to the peer.
        """
        if peer not in self.peer_snapshots:
            return f"no context data for peer '{peer}'"
        snapshots = self.peer_snapshots[peer]
        if filter_str:
            snapshots = self._apply_filter(snapshots, filter_str)
        return self._format_snapshot_summary(peer, snapshots)

    def _apply_filter(
        self, snapshots: List[PeerLedgerSnapshot], filter_str: str
    ) -> List[PeerLedgerSnapshot]:
        """Apply a filter string to snapshots.

        Supported:
          file:<prefix>   only entries whose file_path starts with prefix
        """
        if filter_str.startswith("file:"):
            prefix = filter_str[5:]
            return [
                s
                for s in snapshots
                if s.file_path and s.file_path.startswith(prefix)
            ]
        return snapshots

    def _format_snapshot_summary(
        self, peer: str, snapshots: List[PeerLedgerSnapshot]
    ) -> str:
        if not snapshots:
            return f"peer {peer}: no matching context entries"
        lines = [f"peer {peer} context:"]
        total_kb = 0.0
        for s in snapshots:
            total_kb += s.size_kb
            path = s.file_path or "(no file)"
            version = s.file_version if s.file_version is not None else "?"
            lines.append(
                f"  {s.ctx_id}: {path} v{version} "
                f"({s.size_kb:.0f}kb, {s.decision})"
            )
        lines.append(
            f"  total: {total_kb:.0f}kb tracked, {len(snapshots)} entries"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Peer-aware filter for <context filter="peer:X" />
    # ------------------------------------------------------------------

    def peers_holding_entries(self) -> Dict[str, List[str]]:
        """Map peer identity -> list of ctx_ids we know they hold.

        Used by the <context filter="peer:X" /> extension.
        """
        out: Dict[str, List[str]] = {}
        for peer, snapshots in self.peer_snapshots.items():
            out[peer] = [s.ctx_id for s in snapshots]
        return out

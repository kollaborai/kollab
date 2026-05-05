"""ContextService — the main entry point for context tracking.

One instance per conversation context. Registered as a service on
the event bus so every plugin can reach it via
event_bus.get_service('context_service').

Tracks heavy items (file reads, tool results >= threshold) in a
ledger. Agents can curate entries via <curate>, <context/>, <evict>
XML tags. File reads are deduplicated by content hash.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .file_tracker import FileTracker
from .hash_utils import compute_hash
from .ledger import Ledger
from .models import LedgerEntry

logger = logging.getLogger(__name__)


class ContextService:
    """Context service — tracks heavy items and manages curation.

    Attributes:
        _ledger: Thread-safe ledger storing LedgerEntry instances.
        _file_tracker: Per-file version history for dedup.
        _heavy_threshold_bytes: Items below this size are not tracked.
        _curate_threshold_bytes: Ledger size that triggers curator.
    """

    def __init__(
        self,
        heavy_threshold_kb: int = 8,
        curate_threshold_kb: int = 300,
    ) -> None:
        self._ledger = Ledger()
        self._file_tracker = FileTracker()
        self._heavy_threshold_bytes = heavy_threshold_kb * 1024
        self._curate_threshold_bytes = curate_threshold_kb * 1024

        # Ephemeral injection flags
        self._context_query_pending: bool = False
        self._context_query_filter: Optional[str] = None
        self._curator_pending: bool = False
        self._confirmation_pending: bool = False

        # Curator throttling
        self._last_curator_fire_turn: int = -10
        self._curator_throttle_turns: int = 2

        # Event bus reference (set by the plugin that registers us)
        self._event_bus: Any = None
        # Captured running loop for scheduling bridge broadcasts (set in set_event_bus)
        self._loop: Any = None

        # Phase D: hub bridge for cross-agent metadata sharing
        self._hub_bridge: Any = None
        # Divergent-hash warnings pending inline injection (fallback
        # path until the agent notification system lands)
        self._divergence_warnings_pending: List[Dict[str, Any]] = []

    def set_event_bus(self, event_bus: Any) -> None:
        """Set the event bus reference.

        Called by the plugin that registers ContextService as a
        service.
        """
        self._event_bus = event_bus
        # Capture the running loop now (called from async context) so
        # _maybe_broadcast_ledger_event can schedule tasks without
        # relying on asyncio.get_event_loop() (deprecated in 3.12).
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Emit ready event so plugins (e.g. agent_orchestrator) can
        # register triggers or discover the service.
        if self._loop is not None:
            from kollabor_events import EventType

            async def _emit_ready():
                await event_bus.emit_with_hooks(
                    EventType.CONTEXT_SERVICE_READY,
                    {
                        "service": self,
                        "message": "ContextService (ledger) ready",
                    },
                    "context_service",
                )

            self._loop.create_task(_emit_ready())

    # ------------------------------------------------------------------
    # Compatibility shims — consumers of the old ContextService API
    # (e.g. agent_orchestrator) call these on whatever object they
    # receive via the CONTEXT_SERVICE_READY event.  The new service
    # does not support keyword triggers, so these are no-ops.
    # ------------------------------------------------------------------

    def register_trigger(self, keyword: str, context_id: str) -> None:
        """No-op compatibility shim for old ContextService API."""
        logger.debug(f"register_trigger shim: {keyword} -> {context_id} (no-op)")

    def register_loader(self, context_type: str, loader_func) -> None:
        """No-op compatibility shim for old ContextService API."""
        logger.debug(f"register_loader shim: {context_type} (no-op)")

    # ------------------------------------------------------------------
    # Phase D: hub bridge integration
    # ------------------------------------------------------------------

    def set_hub_bridge(self, bridge: Any) -> None:
        """Attach a HubBridge instance.

        Called by the plugin that wires cross-agent sharing when
        ``plugins.context_service.hub_broadcast_enabled`` is true. A
        ``None`` bridge disables the feature.
        """
        self._hub_bridge = bridge

    def get_hub_bridge(self) -> Any:
        """Return the attached HubBridge, or None if disabled."""
        return self._hub_bridge

    def queue_divergence_warning(self, warning: Dict[str, Any]) -> None:
        """Queue a divergent-file warning for inline injection.

        Fallback rail until RFC-2026-04-11-agent-notification-system.md ships. Warnings
        flush via :meth:`build_divergence_warnings` at the same turn
        boundary as <context/> snapshots.
        """
        self._divergence_warnings_pending.append(warning)

    def build_divergence_warnings(self) -> Optional[str]:
        """Drain pending divergence warnings and return an injection block."""
        if not self._divergence_warnings_pending:
            return None

        warnings = self._divergence_warnings_pending
        self._divergence_warnings_pending = []

        lines = ["[context service] divergent files detected", ""]
        for w in warnings:
            path = w.get("file_path", "(unknown)")
            our_v = w.get("our_version", "?")
            peer = w.get("peer", "?")
            peer_v = w.get("peer_version", "?")
            lines.append(f"  {path}")
            lines.append(f"    your version: v{our_v}")
            lines.append(f"    peer {peer}: v{peer_v}")
        lines.append("")
        lines.append("  re-read the file if it matters to your current task.")
        return "\n".join(lines)

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
            heavy threshold.
        """
        if len(content) < self._heavy_threshold_bytes:
            return None

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

        if file_path:
            self._file_tracker.record_read(file_path, entry)

        logger.info(
            f"Ingested heavy item: {ctx_id} ({label}, "
            f"{len(content) // 1024}KB)"
        )

        self._check_curator_trigger()
        self._maybe_broadcast_ledger_event(entry)

        return entry

    def _maybe_broadcast_ledger_event(self, entry: LedgerEntry) -> None:
        """Fire HubBridge.on_ledger_event if a bridge is wired.

        Schedules the async broadcast on the running event loop. Silent
        when no bridge is attached (feature disabled).
        """
        bridge = self._hub_bridge
        if bridge is None:
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            logger.debug("No running event loop for ledger broadcast: %s", entry.ctx_id)
            return
        # Snapshot waiting state NOW (at schedule time, not at task execution
        # time). The agent may transition to WAITING between ingest and when
        # the async task runs, which would cause the guard in on_ledger_event
        # to incorrectly skip the broadcast.
        was_waiting = bridge._is_waiting()
        try:
            loop.create_task(bridge.on_ledger_event(entry, _was_waiting=was_waiting))
        except Exception as e:
            logger.debug("Failed to schedule ledger broadcast: %s", e)

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
            True if recorded, False if entry not found or invalid.
        """
        if decision not in ("keep", "summary"):
            return False

        entry = self._ledger.get(ctx_id)
        if entry is None:
            return False

        if not body.strip():
            logger.warning(f"Empty decision body for {ctx_id}, rejecting")
            return False

        entry.decision = decision
        entry.decision_body = body.strip()
        entry.decided_at = datetime.now()

        logger.info(f"Decision recorded: {ctx_id} -> {decision}")

        self._confirmation_pending = True

        return True

    def evict(self, ctx_id: str, reason: str = "") -> bool:
        """Evict a ledger entry from history.

        Marks the entry as evicted. Actual history rewriting is
        handled by the caller (e.g. conversation_manager) since
        this service doesn't own message history.

        Args:
            ctx_id: The entry to evict.
            reason: Optional explanation the agent gave.

        Returns:
            True if evicted, False if entry not found.
        """
        entry = self._ledger.get(ctx_id)
        if entry is None:
            return False

        entry.decision = "evicted"
        entry.decision_body = reason

        logger.info(
            f"Evicted: {ctx_id} "
            f"({entry.size_bytes // 1024}KB, "
            f"msg {entry.message_uuid})"
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
        """Check if a file read should be deduplicated.

        Called by the <read> tool handler BEFORE returning content.

        Args:
            path: The file path.
            disk_content: The raw bytes read from disk.
            lines_spec: Optional line range spec.
            force: If True, bypass dedup and return full content.

        Returns:
            dict with 'action', 'content', 'ledger_entry' keys.
            action is one of: 'fresh', 'stale', 'diff', 'force_fresh'.
        """
        disk_hash = compute_hash(disk_content)

        if force:
            return {
                "action": "force_fresh",
                "content": disk_content,
                "ledger_entry": None,
            }

        version = self._file_tracker.get_version(path)

        if version is None:
            return {
                "action": "fresh",
                "content": disk_content,
                "ledger_entry": None,
            }

        if version.latest_hash == disk_hash:
            latest_entry = version.latest
            if latest_entry is not None:
                latest_entry.last_accessed_at = datetime.now()
                latest_entry.read_count += 1

                marker = self._build_stale_marker(latest_entry)
                return {
                    "action": "stale",
                    "content": marker.encode("utf-8"),
                    "ledger_entry": None,
                }

        # Hash differs — return a diff
        prior_entry = version.latest
        if prior_entry is not None:
            diff = self._build_diff(prior_entry, disk_content, disk_hash)
            return {
                "action": "diff",
                "content": diff.encode("utf-8"),
                "ledger_entry": None,
                "prior_ctx_id": prior_entry.ctx_id,
            }

        # No prior entry despite version existing (edge case)
        return {
            "action": "fresh",
            "content": disk_content,
            "ledger_entry": None,
        }

    def _build_stale_marker(self, entry: LedgerEntry) -> str:
        """Build the short marker returned for stale reads."""
        lines_ago = entry.read_count - 1
        return (
            f"[context service: stale hit]\n"
            f"{entry.label} is already in your context as "
            f"{entry.ctx_id} (read {lines_ago} turn(s) ago, "
            f"hash {entry.content_hash[:8]} unchanged, "
            f"{entry.size_bytes // 1024}KB). "
            f"The full content is in the tool result at message "
            f"{entry.message_uuid}. Reference it there instead of "
            f"re-reading.\n\n"
            f"If you need to force a fresh read (e.g., you suspect "
            f"a silent write), set force=\"true\" on the <read> tag:\n"
            f"  <read force=\"true\">"
            f"<file>{entry.file_path}</file></read>"
        )

    def _build_diff(
        self,
        prior_entry: LedgerEntry,
        new_content: bytes,
        new_hash: str,
    ) -> str:
        """Build a diff report from prior version to new content.

        Since we don't store the raw content in the ledger, we
        generate a metadata-only diff report with the hash change.
        """
        size_diff = len(new_content) - prior_entry.size_bytes

        return (
            f"[context service: file changed]\n"
            f"{prior_entry.label} changed since {prior_entry.ctx_id} "
            f"(hash {prior_entry.content_hash[:8]} -> "
            f"{new_hash[:8]}).\n"
            f"File size change: {size_diff:+d} bytes "
            f"({prior_entry.size_bytes // 1024}KB -> "
            f"{len(new_content) // 1024}KB).\n"
            f"The full current file will be returned as a new "
            f"ledger entry.\n\n"
            f"If you only need the diff, use the diff tool on the "
            f"two versions. If you need the full current file "
            f"(not just metadata), it's already being returned."
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
            return

        if (
            self._ledger.turn_count - self._last_curator_fire_turn
            < self._curator_throttle_turns
        ):
            return

        self._curator_pending = True
        self._last_curator_fire_turn = self._ledger.turn_count

        logger.info(
            f"Curator triggered: {total // 1024}KB ledger, "
            f"{pending} pending"
        )

    def build_curator_injection(self) -> Optional[str]:
        """Build the curator prompt for injection.

        Returns None if the curator shouldn't fire this turn.
        """
        if not self._curator_pending:
            return None

        self._curator_pending = False

        pending_entries = [
            e for e in self._ledger.all() if e.decision == "pending"
        ]
        decided_entries = [
            e for e in self._ledger.all()
            if e.decision in ("keep", "summary")
        ]

        lines = [
            "[context service: curator]",
            "",
            f"{len(pending_entries)} heavy item(s) have piled up and "
            "crossed the curation threshold. Mark each one keep or "
            "summary before the next compaction.",
            "",
            "heavy items awaiting decision:",
            "",
        ]

        for entry in pending_entries:
            lines.append(
                f"  {entry.ctx_id}  {entry.kind:<12} "
                f"{entry.label:<40} "
                f"{entry.size_bytes // 1024:>5}KB  pending"
            )
        lines.append("")

        if decided_entries:
            lines.append("already decided:")
            lines.append("")
            for entry in decided_entries:
                lines.append(
                    f"  {entry.ctx_id}  {entry.kind:<12} "
                    f"{entry.label:<40} "
                    f"{entry.size_bytes // 1024:>5}KB  "
                    f"{entry.decision}"
                )
            lines.append("")

        total = self._ledger.total_bytes()
        lines.extend([
            f"  total:       {total // 1024}KB",
            f"  threshold:   "
            f"{self._curate_threshold_bytes // 1024}KB  (exceeded)",
            "",
            "For each pending item, respond with ONE of:",
            "",
            '  <curate id="ctx-N" decision="keep">',
            "  Explain why you need this verbatim. Stays in history "
            "full size.",
            "  </curate>",
            "",
            '  <curate id="ctx-N" decision="summary">',
            "  Your compressed version. This exact text replaces the "
            "full",
            "  tool result at compaction time. Include anything "
            "future-you",
            "  will need to work with this material again.",
            "  </curate>",
            "",
            "Unmarked items default to auto-summary at compaction "
            "time.",
            "Your own summaries are higher quality than the fallback.",
            "",
            "The curator won't prompt again for at least 2 turns. "
            "You can",
            "proactively emit <curate> tags any turn without being "
            "prompted.",
            "",
            "Other commands:",
            "  <context/>                inspect full ledger",
            '  <evict id="ctx-N">reason</evict>   '
            "drop immediately (breaks cache)",
            '  <read force="true"><file>path</file></read>   '
            "override dedup",
        ])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Injection builders
    # ------------------------------------------------------------------

    def request_context_snapshot(
        self, filter_spec: Optional[str] = None
    ) -> None:
        """Flag that a <context/> snapshot should be injected."""
        self._context_query_pending = True
        self._context_query_filter = filter_spec

    def build_context_snapshot(self) -> Optional[str]:
        """Build a ledger snapshot for injection."""
        if not self._context_query_pending:
            return None

        self._context_query_pending = False
        filter_spec = self._context_query_filter
        self._context_query_filter = None

        entries = self._ledger.all()

        if filter_spec:
            if filter_spec == "pending":
                entries = [
                    e for e in entries if e.decision == "pending"
                ]
            elif filter_spec == "file_read":
                entries = [
                    e for e in entries if e.kind == "file_read"
                ]
            elif filter_spec == "tool_result":
                entries = [
                    e for e in entries if e.kind == "tool_result"
                ]
            elif filter_spec.startswith("path:"):
                path_substr = filter_spec[5:]
                entries = [
                    e for e in entries
                    if e.file_path and path_substr in e.file_path
                ]
            elif filter_spec.startswith("peer:"):
                peer = filter_spec[5:]
                entries = [
                    e for e in entries if peer in e.hub_holders
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
                    f"{entry.label:<40} "
                    f"{entry.size_bytes // 1024:>5}KB  "
                    f"{entry.decision}"
                )
                if entry.decision_body:
                    body_preview = (
                        entry.decision_body.split("\n", 1)[0][:80]
                    )
                    lines.append(f'         "{body_preview}"')

        lines.append("")
        total = self._ledger.total_bytes()
        lines.append(
            f"  total:      {total // 1024}KB  "
            f"heavy items: {len(entries)}"
        )
        lines.append(
            f"  threshold:  "
            f"{self._curate_threshold_bytes // 1024}KB"
        )

        return "\n".join(lines)

    def build_confirmation_injection(self) -> Optional[str]:
        """Build a confirmation block after decisions were recorded."""
        if not self._confirmation_pending:
            return None

        self._confirmation_pending = False

        lines = ["[context service] decisions recorded", ""]

        total_saved = 0
        for entry in self._ledger.all():
            if entry.decision == "keep":
                lines.append(
                    f"  {entry.ctx_id}  keep     "
                    f"({entry.size_bytes // 1024}KB retained)"
                )
            elif entry.decision == "summary":
                saved = entry.size_bytes - len(
                    entry.decision_body.encode("utf-8")
                )
                total_saved += saved
                lines.append(
                    f"  {entry.ctx_id}  summary  "
                    f"({entry.size_bytes // 1024}KB -> "
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

    def build_context_snapshot_display(self) -> str:
        """Build a ledger snapshot for display (always returns content).

        Unlike build_context_snapshot, this is for the /context command
        and does not consume the pending flag.
        """
        entries = self._ledger.all()

        lines = ["context service ledger", ""]

        if not entries:
            lines.append("(empty)")
        else:
            for entry in entries:
                dec = entry.decision or "pending"
                lines.append(
                    f"  {entry.ctx_id}  {entry.kind:<12} "
                    f"{entry.label:<40} "
                    f"{entry.size_bytes // 1024:>5}KB  "
                    f"{dec}"
                )
                if entry.decision_body:
                    body_preview = (
                        entry.decision_body.split("\n", 1)[0][:80]
                    )
                    lines.append(f'         "{body_preview}"')

        lines.append("")
        total = self._ledger.total_bytes()
        lines.append(
            f"  total:      {total // 1024}KB  "
            f"entries: {len(entries)}"
        )
        lines.append(
            f"  threshold:  "
            f"{self._curate_threshold_bytes // 1024}KB"
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Lookup methods (used by compaction plugin)
    # ------------------------------------------------------------------

    def entry_for_message(
        self, message_uuid: str
    ) -> Optional[LedgerEntry]:
        """Look up a ledger entry by its message UUID.

        Called by the context_compaction_plugin at compact time.
        """
        for entry in self._ledger.all():
            if entry.message_uuid == message_uuid:
                return entry
        return None

    def all_entries(self) -> List[LedgerEntry]:
        """Return all ledger entries."""
        return self._ledger.all()

    def increment_turn(self) -> None:
        """Signal a new turn started. Used for curator throttling."""
        self._ledger.turn_count += 1

    # ------------------------------------------------------------------
    # Config widgets
    # ------------------------------------------------------------------

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        """Return config widget definitions for the /config UI."""
        return {
            "title": "Context Service",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": (
                        "plugins.context_service.enabled"
                    ),
                    "help": (
                        "Track heavy items and enable curation"
                    ),
                },
                {
                    "type": "slider",
                    "label": "Heavy Threshold (KB)",
                    "config_path": (
                        "plugins.context_service.heavy_threshold_kb"
                    ),
                    "min_value": 1,
                    "max_value": 64,
                    "step": 1,
                    "help": (
                        "Items smaller than this are not tracked"
                    ),
                },
                {
                    "type": "slider",
                    "label": "Curate Threshold (KB)",
                    "config_path": (
                        "plugins.context_service.curate_threshold_kb"
                    ),
                    "min_value": 50,
                    "max_value": 1000,
                    "step": 50,
                    "help": (
                        "Total ledger size that triggers curator prompt"
                    ),
                },
                {
                    "type": "slider",
                    "label": "Curator Throttle (turns)",
                    "config_path": (
                        "plugins.context_service.curator_throttle_turns"
                    ),
                    "min_value": 1,
                    "max_value": 10,
                    "step": 1,
                    "help": (
                        "Minimum turns between curator re-prompts"
                    ),
                },
                {
                    "type": "dropdown",
                    "label": "File Dedup Mode",
                    "config_path": (
                        "plugins.context_service.file_dedup_mode"
                    ),
                    "options": [
                        "stale_hit", "diff", "force_always"
                    ],
                    "help": (
                        "How to handle re-reads of "
                        "unchanged/changed files"
                    ),
                },
                {
                    "type": "dropdown",
                    "label": "Default Decision",
                    "config_path": (
                        "plugins.context_service.default_decision"
                    ),
                    "options": ["summary", "keep", "elide"],
                    "help": (
                        "Fallback for pending items "
                        "at compaction time"
                    ),
                },
                {
                    "type": "checkbox",
                    "label": "Hub Broadcast (phase D)",
                    "config_path": (
                        "plugins.context_service.hub_broadcast_enabled"
                    ),
                    "help": (
                        "Share ledger metadata with hub peers. "
                        "Enables <hub_ask_ctx/> and divergent-file "
                        "warnings. Metadata only — file content is "
                        "never sent."
                    ),
                },
            ],
        }

"""ContextRegistry: per-daemon registry of ConversationContexts.

Phase 4.5 step 6c (option C scoped-down multi-context).

The registry holds N named ConversationContexts. Exactly one is "live"
at any moment -- its state is synced into LLMCoordinator. Switching
contexts is a snapshot-and-swap:

  1. snapshot the live LLMCoordinator state back into the old context
  2. load the new context's state into LLMCoordinator
  3. mark the new context as live and touch its last_active_at

The registry never holds a reference to LLMCoordinator internals
continuously -- it reaches in through a small accessor surface only
at swap time. This means external callers of LLMCoordinator
(QueueProcessor, SessionManager, hub plugin) keep working without
any changes. They still read/write self.conversation_history on
LLMCoordinator -- the registry just swaps the list contents under
them between turns.

Critical constraint: the swap happens under an asyncio.Lock and only
at quiescence points (no turn in progress, no tool execution
in flight). A switch DURING a turn would corrupt state because
QueueProcessor is holding references and mid-iteration state.
Phase 4.5 enforces quiescence by refusing swaps when is_processing is
True -- callers get a ValueError.

Persistence: registries serialize to a JSON file per-daemon under the
active hub contexts directory. Loaded on startup, saved after every
write operation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from kollabor_events.data_models import ConversationMessage

from .context import ContextListSnapshot, ConversationContext
from .snapshots import MessageDto

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_NAME = "main"


def get_contexts_dir() -> Path:
    """Location of the per-daemon context registry JSON files.

    Routes through plugins.hub.presence.get_hub_dir() so the contexts
    dir siloes per-project when plugins.hub.project_scoped is enabled.
    """
    from plugins.hub.presence import get_hub_dir

    d = get_hub_dir() / "contexts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _message_to_dto(m: Any) -> MessageDto:
    """Best-effort conversion of a ConversationMessage (or dict) to MessageDto."""
    if isinstance(m, MessageDto):
        return m
    role = getattr(m, "role", "") or ""
    content = getattr(m, "content", "") or ""
    ts = getattr(m, "timestamp", None)
    timestamp_iso = ""
    if ts is not None:
        try:
            # datetime
            if hasattr(ts, "isoformat"):
                timestamp_iso = ts.isoformat()
            else:
                timestamp_iso = str(ts)
        except Exception:
            timestamp_iso = ""
    metadata = dict(getattr(m, "metadata", None) or {})
    thinking = getattr(m, "thinking", None)
    return MessageDto(
        role=role,
        content=content,
        timestamp=timestamp_iso,
        metadata=metadata,
        thinking=thinking,
    )


def _dto_to_message(dto: MessageDto) -> ConversationMessage:
    """Convert a MessageDto back to a ConversationMessage for LLMCoordinator.

    Drops the timestamp-as-string conversion back to a plain string
    (ConversationMessage doesn't require datetime). The whole point is
    to round-trip the visible fields that LLMCoordinator cares about.
    """
    return ConversationMessage(
        role=dto.role,
        content=dto.content,
        metadata=dict(dto.metadata or {}),
        thinking=dto.thinking,
    )


class ContextRegistry:
    """Registry of named ConversationContexts for a single daemon.

    Dependencies:
      llm_service: the LLMCoordinator instance. The registry reads/writes
                   llm_service.conversation_history and its
                   active profile / agent / system prompt at swap time.
      identity:    optional daemon identity string used to pick the
                   on-disk file for persistence. Defaults to the daemon
                   pid if not provided.
    """

    def __init__(
        self,
        llm_service: Any,
        *,
        identity: str | None = None,
        persistence_path: Path | None = None,
    ) -> None:
        self._llm = llm_service
        self._identity = identity or f"pid-{__import__('os').getpid()}"
        self._contexts: dict[str, ConversationContext] = {}
        self._active_name: str = DEFAULT_CONTEXT_NAME
        self._lock = asyncio.Lock()
        self._persistence_path = persistence_path or (
            get_contexts_dir() / f"{self._identity}.json"
        )

        # Try to restore previous contexts from disk. On any error, start
        # fresh -- we never want a corrupt file to prevent startup.
        loaded_from_disk = self._try_load_from_disk()

        # Ensure at least the default context exists. If the registry
        # was loaded from disk with contexts but no 'main', the active
        # context is whatever was marked active; otherwise seed from
        # the live LLMCoordinator state.
        if not self._contexts:
            seeded = self._snapshot_live(DEFAULT_CONTEXT_NAME)
            self._contexts[DEFAULT_CONTEXT_NAME] = seeded
            self._active_name = DEFAULT_CONTEXT_NAME
        elif self._active_name not in self._contexts:
            # Stale pointer -- pick the most recently active context.
            self._active_name = max(
                self._contexts.keys(),
                key=lambda n: self._contexts[n].last_active_at,
            )

        # If we loaded contexts from disk, push the active one's state
        # into the llm_service so subsequent reads (via get_context,
        # list_all) don't overwrite the stored snapshot with the empty
        # llm state. This is only done on cold start -- mid-session
        # switches go through attach_to which handles loading already.
        if loaded_from_disk and self._active_name in self._contexts:
            try:
                self._load_into_live(self._contexts[self._active_name])
            except Exception as e:
                logger.debug("failed to load restored active context into llm: %s", e)

    # ------------------------------------------------------------------
    # Public API (async -- lock-protected writes, sync reads are OK)
    # ------------------------------------------------------------------

    def get_active_name(self) -> str:
        """Return the name of the currently live context."""
        return self._active_name



    def get_context(self, name: str) -> ConversationContext | None:
        """Return a snapshot of the named context, or None.

        If the name is the live context, the snapshot's
        conversation_history is refreshed from the current
        llm_service.conversation_history so callers see up-to-date
        messages. The non-history fields (profile, agent, prompt)
        are preserved from the stored context and NOT re-read from
        llm_service -- that would overwrite the stored values with
        whatever the live llm happens to report, which in test
        harnesses (or attach-client shadow state) can be empty.

        Non-live contexts return their stored snapshots as-is.
        """
        if name not in self._contexts:
            return None
        if name == self._active_name:
            # Refresh only the history from the live llm_service,
            # preserving the stored profile/agent/prompt.
            stored = self._contexts[name]
            refreshed_history = self._snapshot_history()
            self._contexts[name] = ConversationContext(
                name=stored.name,
                conversation_history=refreshed_history,
                active_profile_name=stored.active_profile_name,
                active_agent_name=stored.active_agent_name,
                system_prompt=stored.system_prompt,
                created_at=stored.created_at,
                last_active_at=stored.last_active_at,
                archived=stored.archived,
                metadata=dict(stored.metadata),
            )
        return self._contexts[name]

    def _snapshot_history(self) -> list[MessageDto]:
        """Read llm_service.conversation_history and convert to MessageDto."""
        llm = self._llm
        history = getattr(llm, "conversation_history", None) or []
        result: list[MessageDto] = []
        for m in history:
            try:
                result.append(_message_to_dto(m))
            except Exception as e:
                logger.debug("snapshot_history: skipping message: %s", e)
        return result

    def list_all(self, *, include_archived: bool = False) -> ContextListSnapshot:
        """Return a ContextListSnapshot for display.

        The live context's history is refreshed from LLMCoordinator
        before returning so message_count is current. Other fields
        (profile, agent, prompt) are preserved from the stored context.
        """
        # Refresh the live context via get_context so the same history
        # preservation logic applies.
        self.get_context(self._active_name)

        filtered = [
            c for c in self._contexts.values() if include_archived or not c.archived
        ]
        # Sort by last_active_at descending so most recent are first.
        filtered.sort(key=lambda c: c.last_active_at, reverse=True)
        return ContextListSnapshot(
            active=self._active_name,
            contexts=filtered,
        )

    async def create(
        self,
        name: str,
        *,
        profile_name: str = "",
        agent_name: str = "",
        system_prompt: str = "",
    ) -> ConversationContext:
        """Create a new empty context.

        Raises ValueError if the name is empty, already exists, or
        contains characters that would be unsafe in filenames.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("context name is required")
        if not self._valid_name(name):
            raise ValueError(f"context name contains invalid characters: {name!r}")
        async with self._lock:
            if name in self._contexts:
                raise ValueError(f"context already exists: {name!r}")
            ctx = ConversationContext(
                name=name,
                active_profile_name=profile_name,
                active_agent_name=agent_name,
                system_prompt=system_prompt,
                created_at=time.time(),
                last_active_at=time.time(),
            )
            self._contexts[name] = ctx
            self._save_to_disk()
            logger.info("created context: %s", name)
            return ctx

    async def attach_to(self, name: str) -> ConversationContext:
        """Switch the live context. Snapshots the current live state
        back into the old context first, then loads the new one.

        Raises:
            ValueError: if the name doesn't exist or is archived.
            RuntimeError: if a turn is in progress on the live context
                (check llm_service.is_processing).
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("context name is required")

        async with self._lock:
            if name not in self._contexts:
                available = [n for n, c in self._contexts.items() if not c.archived]
                raise ValueError(
                    f"context not found: {name!r}"
                    + (
                        f" (available: {', '.join(sorted(available))})"
                        if available
                        else ""
                    )
                )
            target = self._contexts[name]
            if target.archived:
                raise ValueError(f"context is archived: {name!r}")

            if name == self._active_name:
                # No-op: just refresh the history and touch.
                stored = self._contexts[name]
                stored.conversation_history = self._snapshot_history()
                stored.touch()
                self._save_to_disk()
                return stored

            # Refuse to swap if a turn is in progress. Quiescence is a
            # hard requirement -- mid-turn swaps corrupt QueueProcessor.
            if self._is_processing():
                raise RuntimeError(
                    "cannot switch contexts while a turn is in progress; "
                    "wait for the current turn to complete"
                )

            # 1. Snapshot the current live history back into the old
            #    context, preserving its stored profile/agent/prompt.
            old = self._contexts[self._active_name]
            old.conversation_history = self._snapshot_history()

            # 2. Load the target context into the live LLMCoordinator.
            self._load_into_live(target)

            # 3. Mark the target as live and touch.
            self._active_name = name
            target.touch()
            self._contexts[name] = target

            self._save_to_disk()
            logger.info(
                "switched live context: %s -> %s (%d messages)",
                old.name,
                target.name,
                len(target.conversation_history),
            )
            return target

    async def archive(self, name: str) -> ConversationContext:
        """Mark a context as archived (soft delete). Cannot archive the
        currently live context -- attach to another first.

        Raises:
            ValueError: if the name doesn't exist or is the live context.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("context name is required")
        async with self._lock:
            if name not in self._contexts:
                raise ValueError(f"context not found: {name!r}")
            if name == self._active_name:
                raise ValueError(
                    f"cannot archive the live context: {name!r} "
                    "(attach to another first)"
                )
            self._contexts[name].archived = True
            self._save_to_disk()
            logger.info("archived context: %s", name)
            return self._contexts[name]

    # ------------------------------------------------------------------
    # Snapshot / load helpers (touch LLMCoordinator attributes)
    # ------------------------------------------------------------------

    def _snapshot_live(self, name: str) -> ConversationContext:
        """Build a ConversationContext from the current live LLMCoordinator state.

        Reads llm_service.conversation_history, the active profile, the
        active agent, and the system prompt. Returns a ConversationContext
        ready to stash in the registry.
        """
        llm = self._llm

        # History: LLMCoordinator holds a List[ConversationMessage]
        history_dtos: list[MessageDto] = []
        history = getattr(llm, "conversation_history", None) or []
        for m in history:
            try:
                history_dtos.append(_message_to_dto(m))
            except Exception as e:
                logger.debug("snapshot: skipping unserializable message: %s", e)

        # Profile name (best-effort)
        profile_name = ""
        pm = getattr(llm, "profile_manager", None)
        if pm is not None:
            try:
                profile_name = getattr(pm, "active_profile_name", "") or ""
            except Exception:
                profile_name = ""

        # Agent name (best-effort)
        agent_name = ""
        am = getattr(llm, "agent_manager", None)
        if am is not None:
            try:
                active = am.get_active_agent()
                if active is not None:
                    agent_name = getattr(active, "name", "") or ""
            except Exception:
                agent_name = ""

        # System prompt (best-effort)
        system_prompt = ""
        try:
            if hasattr(llm, "system_prompt"):
                system_prompt = str(getattr(llm, "system_prompt", "") or "")
        except Exception:
            system_prompt = ""

        # Preserve the existing context's created_at if we already have one
        # (so snapshot updates don't reset it).
        existing = self._contexts.get(name)
        created_at = existing.created_at if existing else time.time()
        archived = existing.archived if existing else False
        metadata = dict(existing.metadata) if existing else {}

        return ConversationContext(
            name=name,
            conversation_history=history_dtos,
            active_profile_name=profile_name,
            active_agent_name=agent_name,
            system_prompt=system_prompt,
            created_at=created_at,
            last_active_at=time.time(),
            archived=archived,
            metadata=metadata,
        )

    def _load_into_live(self, ctx: ConversationContext) -> None:
        """Overwrite LLMCoordinator state with the target context's snapshot.

        Uses list.clear() + list.extend() to preserve the list object
        identity -- callers that stashed a reference to
        self.conversation_history (QueueProcessor, etc.) will see the
        new contents without re-binding.
        """
        llm = self._llm

        history = getattr(llm, "conversation_history", None)
        if history is None:
            # No history attribute at all -- create one. This path is
            # only hit in test harnesses with mocked llm_service.
            llm.conversation_history = []
            history = llm.conversation_history

        # In-place replacement preserves the reference identity.
        history.clear()
        history.extend(_dto_to_message(m) for m in ctx.conversation_history)

        # Best-effort profile switch
        pm = getattr(llm, "profile_manager", None)
        if pm is not None and ctx.active_profile_name:
            try:
                if hasattr(pm, "set_active_profile"):
                    pm.set_active_profile(ctx.active_profile_name, persist=False)
            except Exception as e:
                logger.debug("_load_into_live: profile switch failed: %s", e)

        # Best-effort agent switch
        am = getattr(llm, "agent_manager", None)
        if am is not None and ctx.active_agent_name:
            try:
                if hasattr(am, "set_active_agent"):
                    am.set_active_agent(ctx.active_agent_name)
            except Exception as e:
                logger.debug("_load_into_live: agent switch failed: %s", e)

        # Best-effort system prompt install
        if ctx.system_prompt and hasattr(llm, "system_prompt"):
            try:
                llm.system_prompt = ctx.system_prompt
            except Exception as e:
                logger.debug("_load_into_live: system prompt install failed: %s", e)

        # Rebuild the system prompt so any agent/profile swap takes effect.
        if hasattr(llm, "rebuild_system_prompt"):
            try:
                result = llm.rebuild_system_prompt()
                # Ignore if it's sync; we're in a non-async call path.
                if hasattr(result, "__await__"):
                    # We can't await from a sync method. Swap-time
                    # rebuilds get scheduled by the caller (attach_to
                    # holds a lock, doesn't await).
                    logger.debug(
                        "_load_into_live: rebuild_system_prompt returned "
                        "coroutine, not awaiting here"
                    )
            except Exception as e:
                logger.debug("_load_into_live: rebuild_system_prompt failed: %s", e)

    def _is_processing(self) -> bool:
        """Return True if a turn is currently in progress on llm_service."""
        llm = self._llm
        try:
            # llm_service has an is_processing flag set during a turn
            return bool(getattr(llm, "is_processing", False))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_to_disk(self) -> None:
        """Atomic write the current registry to disk."""
        try:
            data = {
                "active": self._active_name,
                "contexts": {
                    name: ctx.to_dict() for name, ctx in self._contexts.items()
                },
            }
            tmp = self._persistence_path.with_suffix(".json.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._persistence_path)
        except Exception as e:
            logger.debug("context registry save failed: %s", e)

    def _try_load_from_disk(self) -> bool:
        """Load the registry from disk, ignoring errors.

        Returns True if contexts were successfully restored from the
        file; False if the file didn't exist, was empty, corrupt, or
        had no contexts.
        """
        path = self._persistence_path
        if not path.exists():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return False
            data = json.loads(raw)
            raw_contexts = data.get("contexts", {}) or {}
            if not isinstance(raw_contexts, dict):
                logger.warning(
                    "context registry file has unexpected shape, ignoring: %s",
                    path,
                )
                return False
            self._contexts = {
                name: ConversationContext.from_dict(ctx_data)
                for name, ctx_data in raw_contexts.items()
                if isinstance(ctx_data, dict)
            }
            active = data.get("active", "")
            if isinstance(active, str) and active:
                self._active_name = active
            logger.info(
                "loaded %d contexts from %s (active=%s)",
                len(self._contexts),
                path,
                self._active_name,
            )
            return bool(self._contexts)
        except Exception as e:
            logger.warning("context registry load failed: %s", e)
            self._contexts = {}
            return False

    # ------------------------------------------------------------------
    # Name validation
    # ------------------------------------------------------------------

    @staticmethod
    def _valid_name(name: str) -> bool:
        """Context names must be safe for filenames and hub message routing.

        Allowed: letters, digits, dashes, underscores, dots. Must not
        start with a dot (hidden files) or contain path separators.
        """
        if not name or name.startswith("."):
            return False
        bad = set("/\\ \t\n\r\"'`;|&<>*?[]{}()")
        return not any(c in bad for c in name)

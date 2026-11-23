"""Trusted in-process context for one daemon-owned WebUI queue admission."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class QueueHandoffImage:
    source_ref: str
    mime: str
    data: bytes = field(repr=False)


@dataclass(slots=True)
class QueueHandoffContext:
    """Capability carried from the queue worker through the actual input path.

    It is constructed only by the daemon worker and is never decoded from an
    RPC or message body. State fields are updated as history and provider
    custody cross their respective durable boundaries.
    """

    request_id: str
    conversation_uid: str
    conversation_revision: int
    images: tuple[QueueHandoffImage, ...] = field(repr=False)
    side_effects_started: bool = False
    provider_admission_started: bool = False
    history_row_uuid: str | None = None
    durable_media_refs: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    recording_status: str = "unknown"
    provider_custody_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("queue request identity is invalid")
        if not isinstance(self.conversation_uid, str) or not self.conversation_uid:
            raise ValueError("queue conversation identity is invalid")
        if (
            isinstance(self.conversation_revision, bool)
            or not isinstance(self.conversation_revision, int)
            or self.conversation_revision < 0
        ):
            raise ValueError("queue conversation revision is invalid")
        if not isinstance(self.images, tuple) or len(self.images) > 20:
            raise ValueError("queue attachment list is invalid")
        source_refs = [image.source_ref for image in self.images]
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("queue attachment refs are duplicated")

    @property
    def source_attachment_refs(self) -> tuple[str, ...]:
        return tuple(image.source_ref for image in self.images)

    def _history_metadata(self) -> dict[str, Any]:
        return {
            "version": 1,
            "conversation_uid": self.conversation_uid,
            "conversation_revision": self.conversation_revision,
            "request_id": self.request_id,
            "source_attachment_refs": list(self.source_attachment_refs),
            "media_refs": [
                {"source_ref": source_ref, "ref": media_ref}
                for source_ref, media_ref in self.durable_media_refs
            ],
        }

    async def record_user_history(
        self,
        logger: Any,
        content: str,
        *,
        parent_uuid: str | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Write an idempotent JSONL row and bind verified image bytes to it."""
        if not isinstance(content, str):
            raise TypeError("queue history content must be text")
        if not callable(getattr(logger, "log_user_message", None)):
            raise RuntimeError("conversation logger is unavailable")
        conversations_dir = getattr(logger, "conversations_dir", None)
        session_id = getattr(logger, "session_id", None)
        if self.images and (conversations_dir is None or not isinstance(session_id, str)):
            raise RuntimeError("durable media history is unavailable")

        self.side_effects_started = True
        try:
            if self.images:
                from kollabor_engine.media_store import save_queue_handoff_media

                self.durable_media_refs = save_queue_handoff_media(
                    conversations_dir,
                    session_id,
                    request_id=self.request_id,
                    attachments=tuple(
                        (image.source_ref, image.mime, image.data) for image in self.images
                    ),
                )

            metadata = dict(message_metadata or {})
            existing_handoff = metadata.get("queue_handoff")
            handoff_metadata = self._history_metadata()
            if existing_handoff is not None:
                stable_keys = (
                    "version",
                    "conversation_uid",
                    "conversation_revision",
                    "request_id",
                    "source_attachment_refs",
                )
                if (
                    not isinstance(existing_handoff, dict)
                    or any(
                        existing_handoff.get(key) != handoff_metadata.get(key)
                        for key in stable_keys
                    )
                    or existing_handoff.get("media_refs") not in ([], handoff_metadata["media_refs"])
                ):
                    raise ValueError("queue history metadata conflicts with its capability")
            metadata["queue_handoff"] = handoff_metadata
            row_uuid = await logger.log_user_message(
                content,
                parent_uuid=parent_uuid,
                message_metadata=metadata,
            )
            if not isinstance(row_uuid, str) or not row_uuid:
                raise RuntimeError("conversation logger returned no durable row ID")
            self.history_row_uuid = row_uuid

            if self.images:
                from kollabor_engine.media_store import bind_queue_handoff

                bind_queue_handoff(
                    conversations_dir,
                    session_id,
                    request_id=self.request_id,
                    history_row_uuid=row_uuid,
                    source_media_refs=self.durable_media_refs,
                )
            self.recording_status = "recorded"
            return row_uuid
        except BaseException:
            self.recording_status = "failed"
            raise

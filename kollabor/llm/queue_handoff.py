"""Trusted in-process context for one daemon-owned WebUI queue admission."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


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
    attempt_id: str
    force_send: bool = False
    queue_scope_id: str | None = None
    queue_incarnation: str | None = None
    batch_id: str | None = None
    claim_id: str | None = None
    side_effects_started: bool = False
    provider_admission_started: bool = False
    history_row_uuid: str | None = None
    history_row_preexisting: bool = False
    admission_failure_reason: str | None = None
    durable_media_refs: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    recording_status: str = "unknown"
    provider_custody_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("queue request identity is invalid")
        if not isinstance(self.attempt_id, str) or not self.attempt_id:
            raise ValueError("queue dispatch attempt identity is invalid")
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
        if not isinstance(self.force_send, bool):
            raise ValueError("queue force-send flag is invalid")
        if self.force_send and any(
            not isinstance(value, str) or not value
            for value in (
                self.queue_scope_id,
                self.queue_incarnation,
                self.batch_id,
                self.claim_id,
            )
        ):
            raise ValueError("queue batch ownership identity is invalid")
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
            "force_send": self.force_send,
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
        prepare_admission: Callable[[str, dict[str, Any]], bool],
        rollback_admission: Callable[[str], bool],
        validate_admission: Callable[[], bool] | None = None,
        parent_uuid: str | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Pair provider admission, JSONL, and verified media without yielding.

        The logger acquires its rewrite/write locks before calling the two
        synchronous callbacks. ``prepare_admission`` stages media and commits
        the exact queue item before the row append; ``rollback_admission`` can
        remove only that same item if the append or media binding fails.
        """
        if not isinstance(content, str):
            raise TypeError("queue history content must be text")
        if not callable(getattr(logger, "log_user_message", None)):
            raise RuntimeError("conversation logger is unavailable")
        if not callable(prepare_admission) or not callable(rollback_admission):
            raise TypeError("queue admission transaction callbacks are required")
        conversations_dir = getattr(logger, "conversations_dir", None)
        session_id = getattr(logger, "session_id", None)
        if self.images and (conversations_dir is None or not isinstance(session_id, str)):
            raise RuntimeError("durable media history is unavailable")

        self.side_effects_started = True
        transaction_status: dict[str, Any] = {}

        def discard_staged(row_uuid: str) -> None:
            if not self.durable_media_refs:
                return
            from kollabor_engine.media_store import discard_queue_handoff_media

            discard_queue_handoff_media(
                conversations_dir,
                session_id,
                request_id=self.request_id,
                source_media_refs=self.durable_media_refs,
                history_row_uuid=row_uuid,
            )
            self.durable_media_refs = ()

        def prepare(row_uuid: str, row: dict[str, Any]) -> bool:
            self.history_row_uuid = row_uuid
            row_message = row.get("message")
            metadata = (
                row_message.get("metadata") if isinstance(row_message, dict) else None
            )
            if not isinstance(metadata, dict):
                raise ValueError("queue history metadata is unavailable")
            current_handoff = metadata.get("queue_handoff")
            expected = self._history_metadata()
            if current_handoff != expected:
                stable_keys = (
                    "version",
                    "conversation_uid",
                    "conversation_revision",
                    "request_id",
                    "source_attachment_refs",
                )
                if (
                    not isinstance(current_handoff, dict)
                    or any(
                        current_handoff.get(key) != expected.get(key)
                        for key in stable_keys
                    )
                    or current_handoff.get("media_refs") not in ([], expected["media_refs"])
                ):
                    raise ValueError(
                        "queue history metadata conflicts with its capability"
                    )
            if validate_admission is not None and validate_admission() is not True:
                self.recording_status = "failed"
                return False
            try:
                if self.images:
                    from kollabor_engine.media_store import save_queue_handoff_media

                    self.durable_media_refs = save_queue_handoff_media(
                        conversations_dir,
                        session_id,
                        request_id=self.request_id,
                        attachments=tuple(
                            (image.source_ref, image.mime, image.data)
                            for image in self.images
                        ),
                    )
                metadata["queue_handoff"] = self._history_metadata()
                if prepare_admission(row_uuid, metadata) is not True:
                    self.recording_status = "failed"
                    discard_staged(row_uuid)
                    return False
                return True
            except BaseException:
                if not self.provider_admission_started and not self.provider_custody_id:
                    try:
                        discard_staged(row_uuid)
                    except BaseException:
                        self.recording_status = "uncertain"
                        raise
                raise

        def finalize(row_uuid: str, _row: dict[str, Any]) -> None:
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

        def rollback(row_uuid: str, _row: dict[str, Any]) -> bool:
            try:
                if rollback_admission(row_uuid) is not True:
                    self.recording_status = "uncertain"
                    return False
                self.provider_admission_started = False
                self.provider_custody_id = None
                discard_staged(row_uuid)
                self.recording_status = "failed"
                return True
            except BaseException:
                self.recording_status = "uncertain"
                return False

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
        queue_transaction = {
            "prepare": prepare,
            "finalize": finalize,
            "rollback": rollback,
            "status": transaction_status,
        }
        try:
            row_uuid = await logger.log_user_message(
                content,
                parent_uuid=parent_uuid,
                message_metadata=metadata,
                queue_transaction=queue_transaction,
            )
            if not isinstance(row_uuid, str) or not row_uuid:
                raise RuntimeError("conversation logger returned no durable row ID")
            self.history_row_uuid = row_uuid
            if self.recording_status != "recorded":
                raise RuntimeError("queue history transaction was not finalized")
            return row_uuid
        except BaseException:
            self.history_row_preexisting = transaction_status.get("row_preexisting") is True
            if self.history_row_preexisting:
                self.history_row_uuid = transaction_status.get("row_uuid")
                self.recording_status = "conflict"
            elif self.recording_status == "recorded" and self.history_row_uuid:
                # JSONL and media binding are durable; only optional logger
                # bookkeeping after that commit failed.
                return self.history_row_uuid
            elif (
                transaction_status.get("row_persisted") is True
                or transaction_status.get("append_uncertain") is True
            ):
                self.recording_status = "uncertain"
            elif self.recording_status not in ("failed", "uncertain"):
                self.recording_status = (
                    "uncertain"
                    if self.provider_admission_started or self.provider_custody_id
                    else "failed"
                )
            raise

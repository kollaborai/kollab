"""Trusted bridge from daemon-owned WebUI queue items to real chat admission."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from kollabor.llm.queue_handoff import QueueHandoffContext, QueueHandoffImage

from .web_message_attachments import (
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
    verified_image_mime,
)
from .web_message_queue_worker import (
    AdmissionAccepted,
    AdmissionRejectedBeforeCustody,
    DispatchRequest,
    RecordingStatus,
)

_REASON_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


class WebMessageQueueDispatcher:
    """Open only item-owned staged bytes and submit through the real state path."""

    def __init__(self, state_service: Any, attachments: WebMessageAttachmentRegistry):
        if state_service is None:
            raise TypeError("daemon state service is required")
        if not isinstance(attachments, WebMessageAttachmentRegistry):
            raise TypeError("a daemon attachment registry is required")
        self._state_service = state_service
        self._attachments = attachments

    async def __call__(
        self, request: DispatchRequest
    ) -> AdmissionAccepted | AdmissionRejectedBeforeCustody:
        if not isinstance(request, DispatchRequest):
            raise TypeError("trusted queue dispatch request is required")

        identity_reader = getattr(self._state_service, "get_conversation_identity", None)
        if not callable(identity_reader):
            return self._rejected(request, "identity_unavailable")
        try:
            current = identity_reader()
        except Exception:
            return self._rejected(request, "identity_unavailable")
        if (
            getattr(current, "uid", None) != request.conversation_uid
            or getattr(current, "revision", None) != request.conversation_revision
        ):
            return self._rejected(request, "stale_conversation")

        try:
            payload = json.loads(request.payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._rejected(request, "invalid_queue_payload")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"text"}
            or not isinstance(payload.get("text"), str)
        ):
            return self._rejected(request, "invalid_queue_payload")
        text = payload["text"]
        refs = request.attachment_refs
        if (
            not isinstance(refs, tuple)
            or len(refs) > 20
            or any(not isinstance(ref, str) or not ref for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            return self._rejected(request, "invalid_attachment_refs")
        if not text.strip() and not refs:
            return self._rejected(request, "empty_message")

        scope = self._attachments._bind_internal_scope(
            AttachmentScopeBinding(request.queue_scope_id, request.queue_incarnation)
        )
        images = []
        total_bytes = 0
        for ref in refs:
            try:
                staged = await scope.open_for_owner(request.request_id, ref)
            except Exception:
                return self._rejected(request, "attachment_unavailable")
            try:
                mime = verified_image_mime(staged.mime, staged.data)
            except Exception:
                return self._rejected(request, "attachment_integrity_failed")
            total_bytes += len(staged.data)
            if total_bytes > 20 * 1024 * 1024:
                return self._rejected(request, "attachment_limit_exceeded")
            images.append(QueueHandoffImage(staged.ref, mime, staged.data))

        parts = ([{"type": "text", "text": text}] if text else [])
        parts.extend(
            {
                "type": "image",
                "image": f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}",
            }
            for image in images
        )
        message: str | list[dict[str, str]] = parts if images else text
        context = QueueHandoffContext(
            request_id=request.request_id,
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            images=tuple(images),
        )
        submit = getattr(self._state_service, "submit_queue_message", None)
        if not callable(submit):
            return self._rejected(request, "queue_admission_unavailable")
        try:
            outcome = await submit(message, queue_handoff=context)
        except Exception:
            if context.provider_admission_started or context.provider_custody_id:
                raise RuntimeError("queue admission outcome is uncertain") from None
            return self._rejected(request, "queue_admission_failed")

        if not isinstance(outcome, dict):
            if context.provider_admission_started or context.provider_custody_id:
                raise RuntimeError("queue admission outcome is uncertain")
            return self._rejected(request, "invalid_admission_result")
        if not context.provider_custody_id:
            if context.provider_admission_started:
                raise RuntimeError("queue admission outcome is uncertain")
            reason = outcome.get("reason")
            if not isinstance(reason, str) or not _REASON_CODE.fullmatch(reason):
                reason = "input_not_accepted"
            return self._rejected(request, reason)

        try:
            recording_status = RecordingStatus(context.recording_status)
        except ValueError:
            recording_status = RecordingStatus.UNKNOWN
        if recording_status is RecordingStatus.RECORDED:
            # Provider custody and the JSONL/media binding are both proven.
            # Only now can the staged owner release its bounded image bytes.
            await scope.release_owner(request.request_id)
        return AdmissionAccepted(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            custody_id=context.provider_custody_id,
            recording_status=recording_status,
        )

    @staticmethod
    def _rejected(
        request: DispatchRequest, reason_code: str
    ) -> AdmissionRejectedBeforeCustody:
        return AdmissionRejectedBeforeCustody(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            reason_code=reason_code,
        )

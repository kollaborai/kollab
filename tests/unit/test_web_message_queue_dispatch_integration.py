"""Real queue-to-provider custody and durable attachment regressions for #1366."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor.state.web_message_attachments import (
    AttachmentScopeBinding,
    InvalidAttachment,
    WebMessageAttachmentRegistry,
)
from kollabor.state.web_message_queue import WebMessage, WebMessageQueue
from kollabor.state.web_message_queue_worker import (
    AdmissionAccepted,
    AdmissionRejectedBeforeCustody,
    ConversationIdentity,
    DispatchRequest,
    WebMessageQueueWorker,
    WorkerStatus,
)
from kollabor_rpc import RpcCallerContext

PNG = b"\x89PNG\r\n\x1a\nqueue-test-image"


async def _queue_with_image():
    queue = WebMessageQueue(scope_id="conversation-scope")
    request_id = queue.new_request_id()
    owner_id = [request_id]
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, candidate: candidate in owner_id,
    )
    attachment_scope = await attachments.bind_scope(
        {}, RpcCallerContext.from_unix_peer(501)
    )
    upload_id = attachment_scope.new_operation_id()
    staged = await attachment_scope.stage(upload_id, "image/png", PNG)
    payload = json.dumps({"text": "inspect this"}, separators=(",", ":")).encode()
    await queue.enqueue_batch_with_attachments(
        queue.new_operation_id(),
        queue.new_batch_id(),
        [WebMessage(request_id, payload, (staged.ref,))],
        attachment_scope=attachment_scope,
    )
    snapshot = await queue.snapshot()
    return queue, attachments, attachment_scope, request_id, staged, snapshot


@pytest.mark.asyncio
async def test_worker_passes_exact_staged_refs_to_the_provider_adapter():
    queue, _store, _scope, request_id, staged, snapshot = await _queue_with_image()
    identity = ConversationIdentity("conversation-a", 4)
    received = []

    async def dispatch(request):
        received.append(request)
        return AdmissionAccepted(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            custody_id="native-queue:" + request.request_id,
        )

    worker = WebMessageQueueWorker(queue, identity, lambda: identity, dispatch)
    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.ADMITTED
    assert len(received) == 1
    assert received[0].request_id == request_id
    assert received[0].attachment_refs == (staged.ref,)


@pytest.mark.asyncio
async def test_authoritative_staging_rejects_bytes_that_disagree_with_declared_mime():
    queue = WebMessageQueue(scope_id="conversation-scope")
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, _candidate: True,
    )
    attachment_scope = await attachments.bind_scope(
        {}, RpcCallerContext.from_unix_peer(501)
    )

    with pytest.raises(InvalidAttachment, match="format|MIME|bytes"):
        await attachment_scope.stage(
            attachment_scope.new_operation_id(), "image/jpeg", PNG
        )


async def _real_local_state(tmp_path):
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor.state import LocalStateService
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_events import EventBus

    config = MagicMock()
    config.get.side_effect = lambda _key, default=None: default
    event_bus = EventBus()
    llm = LLMService(config, event_bus, MagicMock())
    event_bus.register_service("llm_service", llm)
    llm.api_service.supports_image_input = lambda: True
    logger = KollaborConversationLogger(tmp_path / "conversations")
    logger.reset_session(llm.conversation_logger.session_id)
    llm.conversation_logger = logger
    llm.config._conversations_dir = logger.conversations_dir
    llm.conversation_manager.current_session_id = logger.session_id
    uid = llm.conversation_manager.conversation_uid
    llm.is_processing = True
    await llm.register_hooks()
    state = LocalStateService(
        llm_service=llm,
        profile_manager=MagicMock(),
        event_bus=event_bus,
    )
    return state, llm, logger, uid


@pytest.mark.asyncio
async def test_real_dispatch_writes_history_and_binds_exact_image_before_releasing_owner(
    tmp_path,
):
    from kollabor_engine import media_store
    from kollabor_engine.history_store import _wire_message

    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    dispatcher = WebMessageQueueDispatcher(state, attachments)
    worker = WebMessageQueueWorker(queue, identity, state.get_conversation_identity, dispatcher)

    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.ADMITTED
    assert result.items[0].recording_status.value == "recorded"
    assert llm.processing_queue.qsize() == 1
    admitted = llm.processing_queue._queue[0]
    assert admitted.request_ids == (request_id,)

    raw = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    )
    assert raw["message"]["metadata"]["queue_handoff"]["request_id"] == request_id
    history_row = _wire_message(raw)
    assert history_row is not None
    assert history_row["uuid"] == raw["uuid"]
    rows = [history_row]
    media_store.attach_media_to_rows(
        logger.conversations_dir, logger.session_id, rows
    )
    assert rows[0]["media"][0]["mime"] == "image/png"
    data, mime = media_store.open_media(
        logger.conversations_dir,
        rows[0]["media"][0]["ref"],
        session_id=logger.session_id,
    )
    assert mime == "image/png"
    assert data == PNG
    assert attachments.attachment_count == 0


@pytest.mark.asyncio
async def test_failed_native_queue_commit_does_not_leave_a_phantom_history_row(tmp_path):
    """A failed provider-queue commit must not leave a durable user message."""
    from kollabor_engine import media_store

    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    dispatcher = WebMessageQueueDispatcher(state, attachments)
    worker = WebMessageQueueWorker(queue, identity, state.get_conversation_identity, dispatcher)

    # Model cancellation/revocation between durable recording and the
    # reserved provider-queue commit. The worker must preserve the retryable
    # queue item without publishing a user row that was never admitted.
    llm._queue_processor.commit_admission_slot = lambda *_args, **_kwargs: False

    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert result.items[0].request_id == request_id
    rows = (
        [json.loads(line) for line in logger.session_file.read_text().splitlines()]
        if logger.session_file.exists()
        else []
    )
    assert not any(row.get("type") == "user" for row in rows)
    assert llm.processing_queue.empty()
    assert not any(
        entry.get("queue_request_id") == request_id
        for entry in media_store.list_for(logger.conversations_dir, logger.session_id)
    )
    queue_snapshot = await queue.snapshot()
    assert queue_snapshot.batches[0].items[0].request_id == request_id
    assert attachments.attachment_count == 1


@pytest.mark.asyncio
async def test_history_bind_failure_rolls_back_unconsumed_native_admission(
    tmp_path, monkeypatch
):
    from kollabor_engine import media_store

    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )

    def fail_bind(*_args, **_kwargs):
        raise OSError("injected media index bind failure")

    monkeypatch.setattr("kollabor_engine.media_store.bind_queue_handoff", fail_bind)
    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert result.items[0].request_id == request_id
    assert llm.processing_queue.empty(), "failed history bind left provider custody"
    rows = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
    assert not any(row.get("type") == "user" for row in rows)
    assert not any(
        entry.get("queue_request_id") == request_id
        for entry in media_store.list_for(logger.conversations_dir, logger.session_id)
    )
    assert attachments.attachment_count == 1
    queued = await queue.snapshot()
    assert queued.batches[0].items[0].request_id == request_id


@pytest.mark.asyncio
async def test_existing_queue_history_is_not_replayed_into_provider_custody(tmp_path):
    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue = WebMessageQueue(scope_id="conversation-scope")
    request_id = queue.new_request_id()
    batch_id = queue.new_batch_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        (WebMessage(request_id, b'{"text":"inspect this"}'),),
    )
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, _owner: True,
    )
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    handoff_metadata = {
        "version": 1,
        "conversation_uid": uid,
        "conversation_revision": identity.revision,
        "request_id": request_id,
        "source_attachment_refs": [],
        "media_refs": [],
    }
    await logger.log_user_message(
        "inspect this", message_metadata={"queue_handoff": handoff_metadata}
    )
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )

    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert llm.processing_queue.empty()
    rows = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
    assert sum(row.get("type") == "user" for row in rows) == 1
    queued = await queue.snapshot()
    assert queued.batches[0].items[0].request_id == request_id


@pytest.mark.asyncio
async def test_stop_while_history_lock_waits_fails_before_row_or_custody(tmp_path):
    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )
    entered_log = asyncio.Event()
    original_log = logger.log_user_message

    async def observe_log(*args, **kwargs):
        entered_log.set()
        return await original_log(*args, **kwargs)

    logger.log_user_message = observe_log
    await logger._write_lock.acquire()
    task = asyncio.create_task(worker.dispatch_batch(snapshot.batches[0].batch_id))
    try:
        await asyncio.wait_for(entered_log.wait(), timeout=1)
        llm._queue_processor.request_cancellation(origin="human")
    finally:
        logger._write_lock.release()
    result = await asyncio.wait_for(task, timeout=2)

    assert result.status is WorkerStatus.NOT_SENT
    assert result.items[0].request_id == request_id
    assert llm.processing_queue.empty()
    rows = (
        [json.loads(line) for line in logger.session_file.read_text().splitlines()]
        if logger.session_file.exists()
        else []
    )
    assert not any(row.get("type") == "user" for row in rows)


@pytest.mark.asyncio
async def test_jsonl_partial_write_rolls_back_exact_native_admission(tmp_path, monkeypatch):
    import os

    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    from kollabor_agent.queue_processor import TaggedInput

    older = TaggedInput("older queued work", ("older-request",))
    llm.processing_queue.put_nowait(older)
    original_commit = llm._queue_processor.commit_admission_slot
    commits = 0

    def count_commit(*args, **kwargs):
        nonlocal commits
        commits += 1
        return original_commit(*args, **kwargs)

    llm._queue_processor.commit_admission_slot = count_commit
    real_write = os.write
    write_calls = 0

    def partial_then_fail(fd, data):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            return real_write(fd, data[: max(1, len(data) // 3)])
        raise OSError("injected JSONL append failure")

    monkeypatch.setattr("kollabor_ai.conversation_logger.os.write", partial_then_fail)
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )

    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert commits == 1, "the injected append fault must occur after provider commit"
    assert result.status is WorkerStatus.NOT_SENT
    assert len(llm.processing_queue._queue) == 1
    assert llm.processing_queue._queue[0] is older
    assert llm.processing_queue._queue[0].request_ids == ("older-request",)
    rows = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
    assert not any(row.get("type") == "user" for row in rows)
    queued = await queue.snapshot()
    assert queued.batches[0].items[0].request_id == request_id


@pytest.mark.asyncio
async def test_real_dispatch_rejects_stale_revision_before_history_or_provider_custody(
    tmp_path,
):
    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    queue, attachments, _scope, request_id, _staged, _snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    current = state.get_conversation_identity()
    dispatcher = WebMessageQueueDispatcher(state, attachments)
    request = DispatchRequest(
        conversation_uid=uid,
        conversation_revision=current.revision + 1,
        request_id=request_id,
        queue_scope_id=queue.scope_id,
        queue_incarnation=queue.incarnation,
        queue_revision=0,
        batch_id=queue.new_batch_id(),
        claim_id=queue.new_operation_id(),
        attempt_id=queue.new_operation_id(),
        payload=json.dumps({"text": "inspect this"}).encode(),
        attachment_refs=(),
    )

    result = await dispatcher(request)

    assert isinstance(result, AdmissionRejectedBeforeCustody)
    assert result.reason_code == "stale_conversation"
    assert llm.processing_queue.empty()
    assert not logger.session_file.exists()


@pytest.mark.asyncio
async def test_real_acp_admission_records_and_binds_the_exact_queue_image(tmp_path):
    from kollabor_engine import media_store
    from kollabor_engine.history_store import read_history_from_disk

    from kollabor.llm.acp_bridge import ACPAdmission
    from kollabor.llm.acp_orchestrator import ACPOrchestrator
    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    class PendingRunner:
        generation = 3
        session_id = "acp-session"
        binding = None
        supports_image_input = True

        def __init__(self):
            self.admissions = []

        def admit(self, text, *, request_id, source, thread_id=None, origin=None, **kwargs):
            future = asyncio.get_running_loop().create_future()
            admission = ACPAdmission(
                request_id, source, thread_id, self.generation,
                len(self.admissions) + 1, text, future, origin or {},
            )
            self.admissions.append((admission, kwargs))
            return admission

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    llm.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
    )
    orchestrator = ACPOrchestrator(llm.event_bus)
    runner = PendingRunner()
    orchestrator._runner = runner
    llm.event_bus.register_service("acp_orchestrator", orchestrator)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    dispatcher = WebMessageQueueDispatcher(state, attachments)
    worker = WebMessageQueueWorker(queue, identity, state.get_conversation_identity, dispatcher)

    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.ADMITTED
    assert len(runner.admissions) == 1
    assert result.items[0].custody_id.startswith("acp:3:")
    rows = read_history_from_disk(logger.conversations_dir, logger.session_id)
    media_store.attach_media_to_rows(logger.conversations_dir, logger.session_id, rows)
    row = next(row for row in rows if row.get("metadata", {}).get("queue_handoff", {}).get("request_id") == request_id)
    assert len(row["media"]) == 1
    data, mime = media_store.open_media(
        logger.conversations_dir, row["media"][0]["ref"], session_id=logger.session_id
    )
    assert (data, mime) == (PNG, "image/png")
    assert attachments.attachment_count == 0

    for task in tuple(orchestrator._completion_tasks):
        task.cancel()
    await asyncio.gather(*tuple(orchestrator._completion_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_acp_bind_failure_cancels_exact_deferred_admission_before_completion(
    tmp_path, monkeypatch
):
    from kollabor_engine import media_store

    from kollabor.llm.acp_bridge import ACPAdmission
    from kollabor.llm.acp_orchestrator import ACPOrchestrator
    from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher

    class PendingRunner:
        generation = 3
        session_id = "acp-session"
        binding = None
        supports_image_input = True

        def __init__(self):
            self.admissions = []
            self.cancelled = []

        def admit(self, text, *, request_id, source, thread_id=None, origin=None, **kwargs):
            future = asyncio.get_running_loop().create_future()
            admission = ACPAdmission(
                request_id,
                source,
                thread_id,
                self.generation,
                len(self.admissions) + 1,
                text,
                future,
                origin or {},
            )
            self.admissions.append(admission)
            return admission

        def cancel_queued(self, request_id, admission=None):
            for candidate in tuple(self.admissions):
                if candidate is admission and candidate.request_id == request_id:
                    self.admissions.remove(candidate)
                    self.cancelled.append(candidate)
                    candidate.future.cancel()
                    return True
            return False

    queue, attachments, _scope, request_id, _staged, snapshot = await _queue_with_image()
    state, llm, logger, uid = await _real_local_state(tmp_path)
    llm.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
    )
    orchestrator = ACPOrchestrator(llm.event_bus)
    runner = PendingRunner()
    orchestrator._runner = runner
    llm.event_bus.register_service("acp_orchestrator", orchestrator)
    identity = ConversationIdentity(uid, state.get_conversation_revision())
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )

    def fail_bind(*_args, **_kwargs):
        raise OSError("injected ACP media bind failure")

    monkeypatch.setattr(media_store, "bind_queue_handoff", fail_bind)
    result = await worker.dispatch_batch(snapshot.batches[0].batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert len(runner.cancelled) == 1
    assert runner.cancelled[0].request_id == request_id
    assert runner.cancelled[0].future.cancelled()
    assert not orchestrator._completion_tasks
    assert not orchestrator._deferred_queue_admissions
    assert llm.processing_queue.empty()
    rows = (
        [json.loads(line) for line in logger.session_file.read_text().splitlines()]
        if logger.session_file.exists()
        else []
    )
    assert not any(row.get("type") == "user" for row in rows)
    assert not any(
        entry.get("queue_request_id") == request_id
        for entry in media_store.list_for(logger.conversations_dir, logger.session_id)
    )
    assert attachments.attachment_count == 1
    queued = await queue.snapshot()
    assert queued.batches[0].items[0].request_id == request_id


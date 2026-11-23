"""Hub shutdown must preserve providers while queue ownership is unsettled."""

import asyncio
from types import SimpleNamespace

import pytest

import plugins.hub.plugin as hub_module
from kollabor.state.web_message_queue import WebMessage, WebMessageQueue
from kollabor.state.web_message_queue_worker import (
    AdmissionAccepted,
    ConversationIdentity,
    RecordingStatus,
    WebMessageQueueWorker,
)
from plugins.hub.plugin import HubPlugin


@pytest.mark.asyncio
async def test_hub_shutdown_does_not_exit_or_stop_providers_when_queue_is_unsettled(
    monkeypatch,
):
    queue = WebMessageQueue(scope_id="hub-shutdown-test")
    request_id = queue.new_request_id()
    batch_id = queue.new_batch_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        (WebMessage(request_id, b'{"text":"held"}'),),
    )
    identity = ConversationIdentity("hub-shutdown-conversation", 1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stuck_dispatch(request):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return AdmissionAccepted(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            custody_id="hub-shutdown-custody",
            recording_status=RecordingStatus.RECORDED,
        )

    worker = WebMessageQueueWorker(queue, identity, lambda: identity, stuck_dispatch)
    worker_task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), timeout=0.5)

    class Runtime:
        async def close(self):
            return await worker.close(timeout=0.01)

    class ProviderRuntime:
        stopped = False

        def stop(self):
            self.stopped = True

    plugin = HubPlugin()
    plugin._web_message_queue_runtime = Runtime()
    plugin._acp_hub_tools = ProviderRuntime()
    plugin._self_stop_requested = True
    plugin._identity = SimpleNamespace(identity="hub-shutdown-agent", agent_id="test")

    async def no_intake():
        return None

    plugin.stop_message_intake = no_intake
    exit_codes = []
    monkeypatch.setattr(hub_module.os, "_exit", exit_codes.append)

    try:
        result = await plugin.shutdown()

        assert exit_codes == []
        assert result is False
        assert plugin._acp_hub_tools.stopped is False
        assert plugin._shutdown_in_progress is False
        assert not worker_task.done()
        snapshot = await queue.snapshot()
        assert snapshot.batches[0].items[0].status.value == "in_doubt"
    finally:
        release.set()
        await asyncio.wait_for(worker_task, timeout=0.5)

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


class _EventBus:
    def __init__(self, app):
        self.app = app

    def get_service(self, name):
        return self.app if name == "app" else None


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["self", "remote"])
async def test_hub_stop_hands_provider_teardown_to_application_cleanup(
    monkeypatch, stop_kind
):
    app = SimpleNamespace(running=True, _background_tasks=[])
    plugin = HubPlugin(event_bus=_EventBus(app))
    plugin._identity = SimpleNamespace(identity="hub-stop-agent", agent_id="test")
    shutdown_calls = []

    async def plugin_shutdown(*, exit_process=True):
        shutdown_calls.append(exit_process)
        return False

    plugin.shutdown = plugin_shutdown
    # Stop-watchdog behavior is covered separately; don't leave delayed tasks
    # alive after this routing-only check.
    plugin._schedule_stop_watchdog = lambda timeout, identity: None
    scheduled = []
    original_ensure_future = asyncio.ensure_future

    def capture_task(coro):
        task = original_ensure_future(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(hub_module.asyncio, "ensure_future", capture_task)
    try:
        if stop_kind == "self":
            result = await plugin._handle_stop_command("hub-stop-agent")
        else:
            result = await plugin._on_remote_shutdown("test remote stop")
        await asyncio.sleep(0)

        assert app.running is False
        assert shutdown_calls == []
        if stop_kind == "self":
            assert result == "hub-stop-agent shutting down..."
    finally:
        for task in scheduled:
            if not task.done():
                task.cancel()
        if scheduled:
            await asyncio.gather(*scheduled, return_exceptions=True)

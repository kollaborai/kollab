"""Goal images reach native requests without entering conversation history."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from kollabor.goals.driver import GoalTurnDriver
from kollabor.goals.service import GoalService
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.goal_store import GoalStore
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.message_content import EphemeralImageStore
from kollabor_ai.providers.models import OpenAIResponsesConfig, ProviderType
from kollabor_ai.providers.openai_responses_provider import OpenAIResponsesProvider

IMAGE_1 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
IMAGE_2 = "data:image/jpeg;base64,anBlZyBieXRlcw=="


@contextmanager
def active_provider_image_diagnostics():
    from kollabor_ai.goal_image_scope import (
        goal_image_diagnostics_scope, goal_image_request_scope, goal_image_scope,
    )
    queue, api = object(), object()
    with goal_image_scope([{"type": "image"}], queue, api):
        with goal_image_request_scope(queue):
            with goal_image_diagnostics_scope(api):
                yield


class CaptureQueue:
    def __init__(self, coord, provider, calls=1):
        self.coord = coord
        self.provider = provider
        self.preparer = APICommunicationService.__new__(APICommunicationService)
        self.processing_queue = asyncio.Queue()
        self.is_processing = False
        self.turn_completed = False
        self.cancel_processing = False
        self.last_turn_error = None
        self.remaining = calls
        self.on_tool_batch = None

    async def continue_conversation(self, current_parent_uuid):
        from kollabor_ai.goal_image_scope import goal_image_request_scope

        with goal_image_request_scope(self):
            messages = self.preparer._prepare_messages(self.coord.conversation_history)
            await self.provider.call(messages, tools=[])
        self.remaining -= 1
        self.turn_completed = self.remaining == 0
        return current_parent_uuid


@pytest.mark.asyncio
async def test_goal_images_reach_real_http_request_across_continuations(tmp_path):
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    refs = [service.promote_image_attachment(image) for image in (IMAGE_1, IMAGE_2)]
    goal = service.create_goal("conv-a", "inspect the images", "/repo", attachment_refs=refs, auto_turn_limit=1)
    # A fresh service and media store must rehydrate the durable artifacts.
    store.close()
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="restarted")
    captured = []

    def capture(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"id": "resp_test", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
        )

    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(provider=ProviderType.OPENAI_RESPONSES, api_key="test", model="gpt-5.4")
    )
    provider._client = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(capture))
    provider._initialized = True
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
    coord.tool_executor = SimpleNamespace(register_plugin_handler=lambda *a, **k: None)
    coord.renderer = SimpleNamespace(pipe_mode=False)
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore()
    existing_id = coord._image_store.add_data_url(IMAGE_1)
    coord.supports_image_input = lambda: True
    coord._queue_processor = CaptureQueue(coord, provider, calls=2)
    coord.api_service = coord._queue_processor.preparer
    provider.set_media_resolver(coord.resolve_media)
    driver = GoalTurnDriver(service, coord)
    try:
        await driver.start(goal.goal_id)
        assert len(captured) == 2
        for body in captured:
            parts = [part for item in body["input"] for part in item.get("content", []) if isinstance(part, dict)]
            assert [part["image_url"] for part in parts if part.get("type") == "input_image"] == [IMAGE_1, IMAGE_2]
        assert coord._image_store.count == 1
        assert coord._image_store.resolve(existing_id) == IMAGE_1
        assert all(isinstance(msg.content, str) for msg in coord.conversation_history)
        assert IMAGE_1 not in repr(coord.conversation_history)
        assert IMAGE_2 not in repr(coord.conversation_history)
        assert coord.conversation_history[0].metadata["agent_hud"] is True
        ordinary = coord._queue_processor.preparer._prepare_messages(coord.conversation_history)
        await provider.call(ordinary, tools=[])
        assert "input_image" not in repr(captured[-1])
    finally:
        await provider._client.aclose()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unsupported", "capacity", "missing", "corrupt"])
async def test_goal_images_fail_closed_before_provider_dispatch(tmp_path, fault):
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    ref = service.promote_image_attachment(IMAGE_1)
    goal = service.create_goal("conv-a", "inspect", "/repo", attachment_refs=[ref], auto_turn_limit=1)
    artifact = tmp_path / "artifacts" / ref["ref"]
    if fault == "missing":
        artifact.unlink()
    if fault == "corrupt":
        artifact.write_bytes(b"corrupted")
    calls = []
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
    coord.tool_executor = SimpleNamespace(register_plugin_handler=lambda *a, **k: None)
    coord.renderer = SimpleNamespace(pipe_mode=False)
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore(max_total_bytes=4 if fault == "capacity" else 100)
    coord.supports_image_input = lambda: fault != "unsupported"
    coord._queue_processor = CaptureQueue(coord, SimpleNamespace(call=lambda *a, **kw: calls.append(True)))
    try:
        await GoalTurnDriver(service, coord).start(goal.goal_id)
        assert not calls
        assert coord._image_store.count == 0
        record = store.get(goal.goal_id)
        assert record.status == "paused"
        if fault in ("missing", "corrupt"):
            assert "artifact_missing" in record.blocked_reason
        else:
            assert "image" in record.last_reason
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_goal_image_scope_cleans_up_when_request_fails_or_is_cancelled(tmp_path, cancel):
    from kollabor_ai.goal_image_scope import current_goal_image_scope

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    ref = service.promote_image_attachment(IMAGE_1)
    goal = service.create_goal("conv-a", "inspect", "/repo", attachment_refs=[ref], auto_turn_limit=1)
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
    coord.tool_executor = SimpleNamespace(register_plugin_handler=lambda *a, **k: None)
    coord.renderer = SimpleNamespace(pipe_mode=False)
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore()
    existing = coord._image_store.add_data_url(IMAGE_2)
    coord.supports_image_input = lambda: True
    coord._queue_processor = CaptureQueue(coord, None)

    async def fail(**_kwargs):
        assert current_goal_image_scope() is not None
        if cancel:
            raise asyncio.CancelledError()
        raise RuntimeError("provider failed")

    coord._queue_processor.continue_conversation = fail
    try:
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await GoalTurnDriver(service, coord).start(goal.goal_id)
        else:
            await GoalTurnDriver(service, coord).start(goal.goal_id)
            assert store.get(goal.goal_id).status == "paused"
        assert current_goal_image_scope() is None
        assert coord._image_store.count == 1
        assert coord._image_store.resolve(existing) == IMAGE_2
        assert all(isinstance(msg.content, str) for msg in coord.conversation_history)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_goal_scope_revokes_inherited_child_tasks():
    from kollabor_ai.goal_image_scope import current_goal_image_scope, goal_image_scope

    resume = asyncio.Event()

    async def child():
        await resume.wait()
        return current_goal_image_scope()

    with goal_image_scope([{"type": "image"}], object(), object()) as scope:
        task = asyncio.create_task(child())
    resume.set()
    assert await task is None
    assert scope.parts == []


def test_missing_goal_carrier_blocks_request_instead_of_dropping_image():
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_ai.message_content import MessageContentError

    preparer = APICommunicationService.__new__(APICommunicationService)
    queue = object()
    with goal_image_scope([{"type": "image"}], queue, preparer) as scope:
        scope.carrier = object()  # removed by a compactor or replaced by a different turn
        with goal_image_request_scope(queue):
            with pytest.raises(MessageContentError, match="goal image context"):
                preparer._prepare_messages([{"role": "user", "content": "unrelated"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", [None, "url", "base64", "partial", "stream-code", "hook-failure", "context-overflow"])
async def test_goal_images_through_real_queue_and_api_service(tmp_path, caplog, echo):
    """Exercise the runtime queue and API before the HTTP transport capture."""
    from unittest.mock import AsyncMock, MagicMock

    from kollabor.llm.streaming_handler import StreamingHandler
    from kollabor_agent.queue_processor import QueueProcessor
    from kollabor_agent.tool_executor import ToolExecutionResult

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    ref = service.promote_image_attachment(IMAGE_1)
    goal = service.create_goal("conv", "inspect image", "/repo", attachment_refs=[ref], auto_turn_limit=1)
    captured = []

    def capture(request):
        captured.append(json.loads(request.content))
        if echo:
            encoded = IMAGE_1.split(",", 1)[1]
            if echo == "context-overflow":
                return httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}})
            if echo == "stream-code":
                event = json.dumps({"response": {"error": {"message": "provider failed", "code": encoded}}})
                return httpx.Response(200, text=f"event: response.failed\ndata: {event}\n\n", headers={"content-type": "text/event-stream"})
            detail = {"url": IMAGE_1, "base64": encoded, "partial": encoded[:35], "hook-failure": IMAGE_1}[echo]
            return httpx.Response(400, json={"error": {"message": f"provider echoed {detail}"}})
        payload = {
            "id": "resp_test",
            "object": "response",
            "model": "gpt-5.4",
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "text", "text": "observed"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if len(captured) == 1:
            payload.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        return httpx.Response(200, json=payload)

    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": echo == "stream-code",
        "kollabor.llm.max_retries": 0,
        "kollabor.llm.processing_delay": 0,
        "kollabor.llm.thinking_delay": 0,
    }.get(key, default)
    profile = MagicMock()
    profile.provider = "openai_responses"
    profile.name = "capture"
    profile.api_key = "test"
    profile.get_model.return_value = "gpt-5.4"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 100
    profile.get_timeout.return_value = 10
    api = APICommunicationService(config, tmp_path / "raw", profile)
    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(provider=ProviderType.OPENAI_RESPONSES, api_key="test", model="gpt-5.4")
    )
    provider._client = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(capture))
    provider._initialized = True
    api._provider = provider
    api._initialized = True
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore()
    coord.api_service = api
    coord.supports_image_input = api.supports_image_input
    provider.set_media_resolver(coord.resolve_media)
    coord.renderer = MagicMock(pipe_mode=False)
    coord.response_parser = MagicMock()
    coord.response_parser.parse_response.side_effect = [
        {"content": "observed", "turn_completed": False, "components": {}},
        {"content": "observed", "turn_completed": True, "components": {}},
    ]
    coord.response_parser.get_all_tools.side_effect = [[{"id": "tool-1", "type": "file_read"}], []]
    coord.tool_executor = MagicMock()
    coord.tool_executor.is_cancelled.return_value = False
    coord.tool_executor.execute_tool = AsyncMock(
        return_value=ToolExecutionResult(tool_id="tool-1", tool_type="file_read", success=True, output="tool result")
    )
    coord.tool_executor.format_result_for_conversation.return_value = "tool result"
    coord.tool_executor.take_executed_count.return_value = 0
    event_bus = MagicMock()
    event_bus.get_service.return_value = None
    event_bus.emit_with_hooks = AsyncMock(return_value={})
    if echo == "hook-failure":
        from kollabor_events.models import EventType
        async def fail_error_hook(event, *_args):
            if event == EventType.LLM_REQUEST_ERROR:
                raise RuntimeError("test error hook failed")
            return {}
        event_bus.emit_with_hooks.side_effect = fail_error_hook
    coord.event_bus = event_bus
    display = MagicMock()
    display.is_streaming_active.return_value = False
    native_tools = MagicMock()
    native_tools.tools = []
    native_tools.tool_calling_enabled = False
    native_tools.discovery_complete = asyncio.Event()
    native_tools.discovery_complete.set()
    streaming = StreamingHandler(api, display, coord.renderer)
    coord._queue_processor = QueueProcessor(
        conversation_history=coord.conversation_history,
        session_stats={
            "messages": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        },
        stats={"total_thinking_time": 0},
        queue_metrics={},
        task_config=SimpleNamespace(
            queue=SimpleNamespace(
                overflow_strategy="drop_oldest", log_queue_events=False, enable_queue_metrics=False, block_timeout=None
            )
        ),
        api_service=api,
        tool_executor=coord.tool_executor,
        response_parser=coord.response_parser,
        message_display_service=display,
        renderer=coord.renderer,
        config=config,
        event_bus=event_bus,
        conversation_logger=AsyncMock(),
        streaming_handler=streaming,
        native_tools_handler=native_tools,
        add_message_fn=coord._add_conversation_message,
        max_queue_size=10,
    )
    try:
        await GoalTurnDriver(service, coord).start(goal.goal_id)
        assert IMAGE_1 not in repr(provider.last_request_payload)
        assert IMAGE_1.split(",", 1)[1] not in repr(provider.last_request_payload)
        if echo:
            detail = IMAGE_1 if echo in ("url", "hook-failure") else IMAGE_1.split(",", 1)[1]
            if echo == "partial":
                detail = detail[:35]
            record = store.get(goal.goal_id)
            assert record.status == "paused"
            assert "details withheld" in record.last_reason
            assert detail not in record.last_reason
            assert detail not in caplog.text
            assert detail not in repr(event_bus.emit_with_hooks.await_args_list)
            if echo == "context-overflow":
                from kollabor_events.models import EventType
                events = [call.args[1] for call in event_bus.emit_with_hooks.await_args_list if call.args[0] == EventType.LLM_REQUEST_ERROR]
                assert events[-1]["context_window_exceeded"] is True
            assert detail not in "".join(p.read_text() for p in (tmp_path / "raw").glob("*.jsonl"))
            assert coord._image_store.count == 0
            return
        assert coord._queue_processor.last_turn_error is None
        # Initial request, truncation retry, and tool-result continuation.
        assert len(captured) == 3
        coord.tool_executor.execute_tool.assert_awaited_once()
        for body in captured:
            parts = [part for item in body["input"] for part in item.get("content", []) if isinstance(part, dict)]
            assert [part["image_url"] for part in parts if part.get("type") == "input_image"] == [IMAGE_1]
        assert coord._image_store.count == 0
        assert all(isinstance(msg.content, str) for msg in coord.conversation_history)
        assert IMAGE_1 not in repr(coord.conversation_history)
        raw_logs = list((tmp_path / "raw").glob("*.jsonl"))
        assert raw_logs
        assert IMAGE_1 not in "".join(p.read_text() for p in raw_logs)
    finally:
        await provider._client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_active_goal_scope_excludes_other_tasks_apis_and_nested_empty_scope():
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_events.data_models import ConversationMessage

    api = APICommunicationService.__new__(APICommunicationService)
    other_api = APICommunicationService.__new__(APICommunicationService)
    queue = object()
    message = ConversationMessage(role="user", content="goal context")
    part = {"type": "image", "source": {"kind": "url", "url": "https://example.test/image.png"}}
    with goal_image_scope([part], queue, api) as scope:
        scope.carrier = message
        with goal_image_request_scope(queue):
            assert isinstance(api._prepare_messages([message])[0]["content"], list)
            assert other_api._prepare_messages([message])[0]["content"] == "goal context"

            async def unrelated_child():
                return api._prepare_messages([message])[0]["content"]

            assert await asyncio.create_task(unrelated_child()) == "goal context"
            with goal_image_scope([], queue, api):
                assert api._prepare_messages([message])[0]["content"] == "goal context"
            assert isinstance(api._prepare_messages([message])[0]["content"], list)


def test_compaction_copy_keeps_only_the_matching_goal_carrier():
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_ai.message_content import MessageContentError
    from kollabor_events.data_models import ConversationMessage
    from plugins.context_compaction_plugin import ContextCompactionPlugin

    api = APICommunicationService.__new__(APICommunicationService)
    queue = object()
    carrier = ConversationMessage(role="user", content="goal context", metadata={"agent_hud": True})
    image = {"type": "image", "source": {"kind": "url", "url": "https://example.test/image.png"}}
    with goal_image_scope([image], queue, api) as scope:
        carrier._goal_image_marker = scope.marker
        scope.carrier = carrier
        compactor = ContextCompactionPlugin.__new__(ContextCompactionPlugin)
        copied = compactor._truncate_message_for_rollover(carrier)
        assert copied is not carrier
        with goal_image_request_scope(queue):
            assert api._prepare_messages([copied])[0]["content"][-1] == image
            with pytest.raises(MessageContentError, match="goal image context"):
                api._prepare_messages([carrier, copied])


@pytest.mark.asyncio
async def test_oversized_artifact_is_read_with_a_bound_before_encoding(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import patch

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    image = "data:image/png;base64," + __import__("base64").b64encode(b"x" * 1_000_000).decode()
    ref = service.promote_image_attachment(image)
    goal = service.create_goal("conv-a", "inspect", "/repo", attachment_refs=[ref], auto_turn_limit=1)
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
    coord.tool_executor = SimpleNamespace(register_plugin_handler=lambda *a, **k: None)
    coord.renderer = SimpleNamespace(pipe_mode=False)
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore(max_total_bytes=4)
    coord.supports_image_input = lambda: True
    coord._queue_processor = CaptureQueue(coord, None)
    artifact = tmp_path / "artifacts" / ref["ref"]
    reads = []
    original_open = Path.open

    class BoundedReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, count=-1):
            reads.append(count)
            return self.wrapped.read(count)

    def observed_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        return BoundedReader(opened) if path == artifact and args and args[0] == "rb" else opened

    monkeypatch.setattr(Path, "open", observed_open)
    try:
        with patch("kollabor.goals.driver.base64.b64encode", side_effect=AssertionError("encoded oversized artifact")):
            await GoalTurnDriver(service, coord).start(goal.goal_id)
        assert reads == [5]
        assert store.get(goal.goal_id).status == "paused"
        assert coord._image_store.count == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_queued_user_turn_drains_after_goal_images_are_revoked(tmp_path):
    from kollabor_ai.goal_image_scope import goal_image_request_scope

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    ref = service.promote_image_attachment(IMAGE_1)
    goal = service.create_goal("conv", "inspect", "/repo", attachment_refs=[ref], auto_turn_limit=1)
    coord = LLMService.__new__(LLMService)
    coord.profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai"))
    coord.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
    coord.tool_executor = SimpleNamespace(register_plugin_handler=lambda *a, **k: None)
    coord.renderer = SimpleNamespace(pipe_mode=False)
    coord.conversation_history = []
    coord.current_parent_uuid = None
    coord._pending_agent_hud = []
    coord._image_store = EphemeralImageStore()
    coord.supports_image_input = lambda: True
    coord._queue_processor = CaptureQueue(coord, None)
    coord.api_service = coord._queue_processor.preparer
    drained = asyncio.Event()
    observed = []

    async def goal_request(current_parent_uuid):
        with goal_image_request_scope(coord._queue_processor):
            observed.append(coord.api_service._prepare_messages(coord.conversation_history))
        await coord._queue_processor.processing_queue.put("unrelated user message")
        coord._queue_processor.turn_completed = False
        return current_parent_uuid

    async def drain():
        with goal_image_request_scope(coord._queue_processor):
            observed.append(coord.api_service._prepare_messages(coord.conversation_history))
        drained.set()

    coord._queue_processor.continue_conversation = goal_request
    coord._process_queue = drain
    coord.create_background_task = lambda fn, name=None: asyncio.create_task(fn())
    try:
        await GoalTurnDriver(service, coord).start(goal.goal_id)
        await asyncio.wait_for(drained.wait(), 1)
        assert isinstance(observed[0][0]["content"], list)
        assert isinstance(observed[1][0]["content"], str)
        assert coord._image_store.count == 0
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_error_traceback_does_not_reveal_goal_image(stream):
    import traceback
    from kollabor_ai.goal_image_scope import goal_image_scope
    from kollabor_ai.providers.errors import ProviderError

    def reject(_request):
        return httpx.Response(400, json={"error": {"message": f"bad input: {IMAGE_1}"}})

    provider = OpenAIResponsesProvider(OpenAIResponsesConfig(provider=ProviderType.OPENAI_RESPONSES, api_key="test", model="gpt-5.4"))
    provider._client = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(reject))
    provider._initialized = True
    encoded = IMAGE_1.split(",", 1)[1]
    try:
        with active_provider_image_diagnostics():
            with pytest.raises(ProviderError) as caught:
                if stream:
                    async for _ in provider.stream([{"role": "user", "content": "inspect"}], tools=[]):
                        pass
                else:
                    await provider.call([{"role": "user", "content": "inspect"}], tools=[])
            trace = "".join(traceback.format_exception(caught.value))
            assert IMAGE_1 not in trace
            assert encoded not in trace
    finally:
        await provider._client.aclose()


def test_grouped_provider_error_redacts_nested_tracebacks():
    import traceback
    from kollabor_ai.goal_image_scope import goal_image_scope, safe_goal_error_cause
    from kollabor_ai.providers.errors import ProviderError, map_openai_error

    encoded = IMAGE_1.split(",", 1)[1]
    truncated = encoded[:35]
    assert len(truncated) < len(encoded)
    error = ExceptionGroup("provider failed", [RuntimeError(IMAGE_1), ExceptionGroup("nested", [RuntimeError(truncated)])])
    with active_provider_image_diagnostics():
        try:
            raise map_openai_error(error, "openai") from safe_goal_error_cause(error)
        except ProviderError as caught:
            trace = "".join(traceback.format_exception(caught))
            assert IMAGE_1 not in trace
            assert truncated not in trace


def test_provider_snapshot_does_not_scan_every_prompt_field_for_goal_image(monkeypatch):
    from kollabor_ai import goal_image_scope as scope_module

    scans = []
    monkeypatch.setattr(scope_module, "goal_image_diagnostics_active", lambda: scans.append(True) or True)
    provider = OpenAIResponsesProvider(OpenAIResponsesConfig(provider=ProviderType.OPENAI_RESPONSES, api_key="test", model="gpt-5.4"))
    with scope_module.goal_image_scope([{"type": "image"}], object(), object()):
        provider.last_request_payload = {"input": [{"content": "long ordinary prompt field"}] * 20, "image_url": IMAGE_1}
    assert not scans
    assert provider.last_request_payload["image_url"] == "[image data redacted]"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["group", "bytes", "pydantic"])
async def test_native_sdk_exception_traceback_never_exposes_goal_image(kind, caplog):
    import traceback
    from unittest.mock import AsyncMock, MagicMock

    from pydantic import BaseModel, ValidationError
    from kollabor_ai.goal_image_scope import goal_image_scope
    from kollabor_ai.providers.errors import ProviderError
    from kollabor_ai.providers.models import OpenAIConfig
    from kollabor_ai.providers.openai_provider import OpenAIProvider

    encoded = IMAGE_1.split(",", 1)[1]
    if kind == "group":
        original = ExceptionGroup("image " + encoded[:11], [RuntimeError("nested")])
    elif kind == "bytes":
        original = RuntimeError(("image " + encoded[:11]).encode())
    else:
        class Parsed(BaseModel):
            number: int
        try:
            Parsed(number=encoded[:11])
        except ValidationError as exc:
            original = exc

    provider = OpenAIProvider(OpenAIConfig(provider=ProviderType.OPENAI, api_key="test", model="gpt-5.4"))
    provider._initialized = True
    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(side_effect=original)
    with active_provider_image_diagnostics():
        with pytest.raises(ProviderError) as caught:
            await provider.call([{"role": "user", "content": "inspect"}], tools=[])
        trace = "".join(traceback.format_exception(caught.value))
        assert encoded[:11] not in trace
        assert encoded[:11] not in caplog.text
        assert caught.value.__cause__ is None
        assert caught.value.original_error is None
        assert "details withheld" in str(caught.value)


@pytest.mark.parametrize("message", [
    "context window exceeded", "context_window exceeded", "maximum context length exceeded",
    "too many tokens", "prompt is too long", "context_length_exceeded",
])
def test_image_goal_provider_errors_preserve_context_overflow_classification(message):
    from kollabor_ai.goal_image_scope import goal_image_scope
    from kollabor_ai.providers.errors import ProviderError

    with active_provider_image_diagnostics():
        error = ProviderError(message, "test", error_code="invalid_request")
        assert error.error_code == "context_window_exceeded"
        assert "details withheld" in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Network error", "HTTP 429", "HTTP 503"])
async def test_image_goal_keeps_retry_classification_without_logging_error_text(message, caplog):
    from unittest.mock import AsyncMock, MagicMock
    from kollabor_ai.goal_image_scope import goal_image_scope
    from tests.unit.test_retry_hardening import _make_service

    api = _make_service(max_retries=1)
    api._provider = SimpleNamespace(provider_name="test", supports_vision=True)
    api._call_provider = AsyncMock(side_effect=[RuntimeError(f"{message} {IMAGE_1}"), "ok"])
    api._sleep_or_cancel = AsyncMock()
    api._log_raw_interaction = MagicMock()
    from kollabor_ai.goal_image_scope import goal_image_request_scope
    from kollabor_events.data_models import ConversationMessage
    queue = object()
    carrier = ConversationMessage(role="user", content="inspect")
    image = {"type": "image", "source": {"kind": "url", "url": "https://example.test/image.png"}}
    with goal_image_scope([image], queue, api) as scope:
        scope.carrier = carrier
        with goal_image_request_scope(queue):
            assert await api.call_llm([carrier]) == "ok"
    assert api._call_provider.await_count == 2
    assert IMAGE_1 not in caplog.text


@pytest.mark.asyncio
async def test_other_api_child_retains_its_own_diagnostics_and_retry(caplog):
    from unittest.mock import AsyncMock, MagicMock
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_events.data_models import ConversationMessage
    from tests.unit.test_retry_hardening import _make_service

    owner, other = _make_service(max_retries=0), _make_service(max_retries=1)
    for api in (owner, other):
        api._provider = SimpleNamespace(provider_name="test", supports_vision=True)
        api._sleep_or_cancel = AsyncMock()
        api._log_raw_interaction = MagicMock()
    other._call_provider = AsyncMock(side_effect=[RuntimeError("Network error"), "child ok"])

    async def owner_request(*_args):
        return await asyncio.create_task(other.call_llm([{"role": "user", "content": "child"}]))

    owner._call_provider = owner_request
    queue = object()
    carrier = ConversationMessage(role="user", content="goal")
    image = {"type": "image", "source": {"kind": "url", "url": "https://example.test/image.png"}}
    with goal_image_scope([image], queue, owner) as scope:
        scope.carrier = carrier
        with goal_image_request_scope(queue):
            assert await owner.call_llm([carrier]) == "child ok"
    assert other._call_provider.await_count == 2
    assert "Network error" in caplog.text


@pytest.mark.asyncio
async def test_image_api_returns_fresh_exception_without_raw_context():
    from unittest.mock import AsyncMock, MagicMock
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_ai.providers.errors import ProviderError
    from kollabor_events.data_models import ConversationMessage
    from tests.unit.test_retry_hardening import _make_service

    api = _make_service(max_retries=0)
    api._provider = SimpleNamespace(provider_name="test", supports_vision=True)
    api._call_provider = AsyncMock(side_effect=RuntimeError(IMAGE_1))
    api._log_raw_interaction = MagicMock()
    queue = object()
    carrier = ConversationMessage(role="user", content="goal")
    image = {"type": "image", "source": {"kind": "url", "url": "https://example.test/image.png"}}
    with goal_image_scope([image], queue, api) as scope:
        scope.carrier = carrier
        with goal_image_request_scope(queue):
            with pytest.raises(ProviderError) as caught:
                await api.call_llm([carrier])
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.original_error is None
    assert IMAGE_1 not in repr(caught.value)


@pytest.mark.asyncio
async def test_openai_sdk_debug_never_logs_goal_image_but_keeps_other_debug(caplog):
    import logging
    from unittest.mock import AsyncMock, MagicMock
    from openai import AsyncOpenAI
    from kollabor_ai.goal_image_scope import goal_image_request_scope, goal_image_scope
    from kollabor_ai.message_content import normalize_message_content
    from kollabor_ai.providers.models import OpenAIConfig
    from kollabor_ai.providers.openai_provider import OpenAIProvider
    from kollabor_events.data_models import ConversationMessage
    from tests.unit.test_retry_hardening import _make_service

    captured = []
    def capture(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "chat-test", "object": "chat.completion", "created": 0, "model": "gpt-5.4", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(capture))
    provider = OpenAIProvider(OpenAIConfig(provider=ProviderType.OPENAI, api_key="test", model="gpt-5.4"))
    provider._client = AsyncOpenAI(api_key="test", base_url="https://example.test/v1", http_client=client)
    provider._initialized = True
    api = _make_service(max_retries=0)
    api._provider = provider
    api._log_raw_interaction = MagicMock()
    api._sleep_or_cancel = AsyncMock()
    media = EphemeralImageStore()
    images = normalize_message_content([{"type": "image", "image": IMAGE_1}], media)
    provider.set_media_resolver(media.resolve)
    queue = object()
    carrier = ConversationMessage(role="user", content="goal")
    caplog.set_level(logging.DEBUG, logger="openai._base_client")
    try:
        with goal_image_scope(images, queue, api) as scope:
            scope.carrier = carrier
            with goal_image_request_scope(queue):
                assert await api.call_llm([carrier]) == "ok"
        logging.getLogger("openai._base_client").debug("ordinary sdk debug remains")
        assert IMAGE_1 in json.dumps(captured)
        assert IMAGE_1 not in caplog.text
        assert "ordinary sdk debug remains" in caplog.text
    finally:
        await provider._client.close()

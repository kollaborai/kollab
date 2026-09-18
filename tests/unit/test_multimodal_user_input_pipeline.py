"""Regression tests for structured user input through Kollab hooks and attach."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.hook_system import LLMHookSystem
from kollabor_ai.message_content import (
    EphemeralImageStore,
    content_to_text,
    normalize_message_content,
    serialize_openai_chat_content,
)
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_tui.display_tap import DisplayTap
from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin
from plugins.deep_thought.plugin import DeepThoughtPlugin
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.plugin import HubPlugin

PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


class _Config:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class _CrystalProbe:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def nudge(self, input_text: str, top_k: int = 5) -> list:
        self.inputs.append(input_text)
        return []


def _content_cases():
    return [
        pytest.param("plain text", "text-only", id="text-only"),
        pytest.param(
            [{"type": "image", "image": PNG_DATA_URL}],
            "image-only",
            id="image-only",
        ),
        pytest.param(
            [
                {"type": "text", "text": "Please spawn an agent"},
                {"type": "image", "image": PNG_DATA_URL},
            ],
            "mixed",
            id="mixed-text-image",
        ),
    ]


def _assert_hook_results_succeeded(result: dict) -> None:
    hook_results = [
        *result["pre"].get("hook_results", []),
        *result["main"].get("hook_results", []),
        *result["post"].get("hook_results", []),
    ]
    assert hook_results
    assert all(item["success"] for item in hook_results), hook_results


def _provider_wire(content):
    """Build provider content without making a provider/network call."""
    store = EphemeralImageStore()
    normalized = normalize_message_content(content, store)
    return serialize_openai_chat_content(normalized, store.resolve)


async def _build_input_pipeline(mode: str):
    """Build the real EventBus with the text-only consumers installed."""
    event_bus = EventBus()
    llm_hook_system = LLMHookSystem(event_bus)
    await llm_hook_system.register_hooks()

    orchestrator = AgentOrchestratorPlugin(
        event_bus=event_bus,
        config=_Config({"plugins.agent_orchestrator.enable_mode": mode}),
    )
    orchestrator.message_injector = SimpleNamespace(inject=AsyncMock(return_value=True))
    await orchestrator.register_hooks()

    deep_thought = DeepThoughtPlugin(event_bus=event_bus)
    deep_thought._enabled = True
    deep_thought._should_ponder = MagicMock(return_value=False)
    await event_bus.register_hook(
        Hook(
            name="deep_thought_input_regression",
            plugin_name="deep_thought_regression",
            event_type=EventType.USER_INPUT_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=deep_thought._intercept_user_input,
        )
    )

    hub = HubPlugin(event_bus=event_bus)
    hub._started = True
    hub._identity = SimpleNamespace(state="idle", identity="local")
    hub._presence = SimpleNamespace(
        discover_agents_async=AsyncMock(return_value=[SimpleNamespace(identity="peer")])
    )
    hub._cli_args = None
    hub._deliver_to_agent = AsyncMock()
    crystal_probe = _CrystalProbe()
    hub._crystal_store = crystal_probe
    hub._global_crystal_store = None

    await event_bus.register_hook(
        Hook(
            name="hub_input_broadcast_regression",
            plugin_name="hub_regression",
            event_type=EventType.USER_INPUT_POST,
            priority=HookPriority.POSTPROCESSING.value,
            callback=hub._broadcast_user_input,
        )
    )
    await event_bus.register_hook(
        Hook(
            name="hub_input_nudge_regression",
            plugin_name="hub_regression",
            event_type=EventType.USER_INPUT_POST,
            priority=HookPriority.DISPLAY.value,
            callback=hub._crystal_nudge_on_input,
        )
    )

    return event_bus, orchestrator, deep_thought, hub, crystal_probe


@pytest.mark.asyncio
@pytest.mark.parametrize("content, label", _content_cases())
async def test_user_input_consumers_keep_structured_content_separate_from_text(
    content, label: str
) -> None:
    event_bus, orchestrator, deep_thought, hub, crystal_probe = (
        await _build_input_pipeline("keyword")
    )

    result = await event_bus.emit_with_hooks(
        EventType.USER_INPUT,
        {"message": content},
        "test",
    )

    assert not result["cancelled"]
    _assert_hook_results_succeeded(result)
    final_message = result["post"]["final_data"]["message"]
    assert final_message == content, label

    # Text-only consumers see the projection, while the event data remains
    # the original content that the LLM/provider path will consume.
    if len(content_to_text(content)) >= 10:
        assert crystal_probe.inputs[-1] == content_to_text(content)
    else:
        assert crystal_probe.inputs == []
    delivered = hub._deliver_to_agent.await_args.args[1]
    assert delivered.content == content_to_text(content)
    assert PNG_DATA_URL not in delivered.content
    assert deep_thought._should_ponder.call_args.args[0] == content_to_text(content)

    wire_content = _provider_wire(final_message)
    if isinstance(content, list):
        assert any(part.get("type") == "image_url" for part in wire_content)
        assert any(
            part.get("type") == "image_url"
            and part.get("image_url", {}).get("url") == PNG_DATA_URL
            for part in wire_content
        )
    else:
        assert wire_content == content

    if label == "mixed":
        orchestrator.message_injector.inject.assert_awaited_once()
    else:
        orchestrator.message_injector.inject.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_startup_mode_prepends_without_flattening_image_parts() -> (
    None
):
    event_bus, _, _, _, _ = await _build_input_pipeline("startup")
    original = [
        {"type": "text", "text": "inspect this"},
        {"type": "image", "image": PNG_DATA_URL},
    ]
    data = {"message": original, "input": original}

    result = await event_bus.emit_with_hooks(EventType.USER_INPUT, data, "test")

    assert not result["cancelled"]
    _assert_hook_results_succeeded(result)
    final_data = result["pre"]["final_data"]
    injected = final_data["message"]
    assert isinstance(injected, list)
    assert injected[0]["type"] == "text"
    assert "<sys_msg>" in injected[0]["text"]
    assert injected[1:] == original
    assert final_data["input"][1:] == original

    wire_content = _provider_wire(injected)
    assert any(part.get("type") == "image_url" for part in wire_content)
    assert any(
        part.get("type") == "image_url"
        and part.get("image_url", {}).get("url") == PNG_DATA_URL
        for part in wire_content
    )


@pytest.fixture
def short_socket_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give the isolated daemon runtime a short Unix-socket path."""
    short_dir = Path(f"/tmp/kollab-image-{uuid.uuid4().hex[:8]}")
    short_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: short_dir)
    yield short_dir
    for path in short_dir.iterdir():
        path.unlink(missing_ok=True)
    short_dir.rmdir()


@pytest.mark.asyncio
async def test_attached_daemon_submission_preserves_mixed_content(
    short_socket_dir: Path,
) -> None:
    """A real attach socket must deliver image parts into the hook pipeline."""
    event_bus, _, _, hub, _ = await _build_input_pipeline("keyword")
    received: list[tuple[object, dict]] = []
    delivered = asyncio.Event()

    original_emit_with_hooks = event_bus.emit_with_hooks

    async def capture_attached_submission(event_type, data, source):
        result = await original_emit_with_hooks(event_type, data, source)
        if event_type == EventType.USER_INPUT and source == "hub_plugin":
            received.append((data["message"], result))
            delivered.set()
        return result

    event_bus.emit_with_hooks = capture_attached_submission

    server = AgentSocketServer(
        agent_id=f"t{uuid.uuid4().hex[:6]}",
        on_message=lambda _message: None,
        on_input_inject=hub._inject_attacher_input,
        socket_name="image-attach",
    )
    server._display_tap = DisplayTap(history_size=2)
    socket_path = await server.start()
    reader, writer = await asyncio.open_unix_connection(socket_path)

    content = [
        {"type": "text", "text": "Please inspect this"},
        {"type": "image", "image": PNG_DATA_URL},
    ]
    try:
        writer.write(
            (
                json.dumps(
                    {
                        "action": "attach",
                        "mode": "interactive",
                        "client_id": "image-regression",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        ack = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
        assert ack["type"] == "attach_ack"

        writer.write((json.dumps({"type": "input", "text": content}) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(delivered.wait(), 2)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()

    assert len(received) == 1
    received_content, result = received[0]
    assert received_content == content
    assert result["post"]["final_data"]["message"] == content
    assert not result["cancelled"]
    _assert_hook_results_succeeded(result)

    provider_content = _provider_wire(received_content)
    assert any(part.get("type") == "image_url" for part in provider_content)
    assert any(
        part.get("type") == "image_url"
        and part.get("image_url", {}).get("url") == PNG_DATA_URL
        for part in provider_content
    )
    broadcast = hub._deliver_to_agent.await_args.args[1]
    assert broadcast.content == "Please inspect this\n[image1]"
    assert PNG_DATA_URL not in broadcast.content

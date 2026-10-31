"""ACP goal admission must fail before the native continuation is run."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.commands.system_commands.handlers.goal import GoalCommandHandler
from kollabor.goals.driver import GoalTurnDriver
from kollabor.goals.service import GoalService
from kollabor.llm.agent_hud import AgentHudEntry
from kollabor.state.goal_store import GoalStore
from kollabor.state.local import LocalStateService
from kollabor.commands.parser import SlashCommandParser
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.profile_manager import LLMProfile
from kollabor_events.models import SlashCommand


@pytest.fixture
def goal_runtime(tmp_path):
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test-daemon")
    profile = LLMProfile(name="codex-cli", provider="acp", command=("codex-acp",))
    manager = SimpleNamespace(get_active_profile=lambda: profile)
    queue = SimpleNamespace(
        processing_queue=asyncio.Queue(),
        is_processing=False,
        turn_completed=False,
        cancel_processing=False,
        last_turn_error=None,
        session_stats={},
    )
    coordinator = SimpleNamespace(
        profile_manager=manager,
        response_parser=SimpleNamespace(register_plugin_tag=Mock()),
        tool_executor=SimpleNamespace(register_plugin_handler=Mock()),
        renderer=SimpleNamespace(pipe_mode=False),
        _queue_processor=queue,
        inject_system_message=AsyncMock(),
        _continue_conversation=AsyncMock(),
    )
    state = SimpleNamespace(get_conversation_uid=lambda: "conversation-1")
    services = {"goal_service": service, "llm_service": coordinator, "state_service": state}
    bus = SimpleNamespace(get_service=services.get)
    coordinator.event_bus = bus
    driver = GoalTurnDriver(service, coordinator)
    driver.schedule = Mock()
    services["goal_driver"] = driver
    handler = GoalCommandHandler(None, bus)
    yield SimpleNamespace(
        store=store,
        service=service,
        profile=profile,
        coordinator=coordinator,
        driver=driver,
        handler=handler,
    )
    store.close()


def image_command(runtime, text, uid="conversation-1", ref="image"):
    request_id = runtime.service.stage_attachment_request(uid, text, [{"ref": ref}])
    command = SlashCommand(name="goal", raw_input=text)
    command._goal_attachment_request = (uid, request_id)
    return command, request_id


@pytest.mark.parametrize("name", ["codex-cli", "claude-code", "custom-acp"])
def test_acp_goal_creation_is_rejected_before_state_or_turn(goal_runtime, name):
    goal_runtime.profile.name = name
    command, request_id = image_command(goal_runtime, "/goal examine this")
    result = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not result.success
    assert "ACP" in result.message
    assert "evidence" in result.message and "token" in result.message
    assert "native provider" in result.message and "\n" not in result.message
    assert request_id in result.message and "/goal retry" in result.message
    assert goal_runtime.store.get_active("conversation-1") is None
    assert goal_runtime.service.pending_attachment_requests("conversation-1")[0]["request_id"] == request_id
    goal_runtime.driver.schedule.assert_not_called()


def test_rejected_images_are_only_used_by_explicit_retry(goal_runtime):
    command, request_id = image_command(goal_runtime, "/goal inspect the image", ref="private")
    rejected = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not rejected.success
    goal_runtime.profile.provider = "openai"
    unrelated = asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal write a poem")))
    assert unrelated.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record.attachment_refs == []
    refused = asyncio.run(
        goal_runtime.handler.handle_goal(
            SlashCommand(name="goal", raw_input=f"/goal retry {request_id} inspect the image")
        )
    )
    assert not refused.success and request_id in refused.message
    assert goal_runtime.service.pending_attachment_requests("conversation-1")
    assert asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal clear"))).success
    retried = asyncio.run(
        goal_runtime.handler.handle_goal(
            SlashCommand(name="goal", raw_input=f"/goal retry {request_id} inspect the image")
        )
    )
    assert retried.success
    assert goal_runtime.store.get_active("conversation-1").attachment_refs == [{"ref": "private"}]
    assert goal_runtime.service.pending_attachment_requests("conversation-1") == []
    goal_runtime.driver.schedule.reset_mock()
    receipt = asyncio.run(
        goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input=f"/goal retry {request_id}"))
    )
    assert receipt.success and "already created" in receipt.message
    goal_runtime.driver.schedule.assert_not_called()
    different = asyncio.run(
        goal_runtime.handler.handle_goal(
            SlashCommand(name="goal", raw_input=f"/goal retry {request_id} different objective")
        )
    )
    assert not different.success


def test_invalid_options_can_be_corrected_at_retry(goal_runtime):
    goal_runtime.profile.provider = "openai"
    command, request_id = image_command(goal_runtime, "/goal --invalid inspect")
    rejected = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not rejected.success and request_id in rejected.message
    result = asyncio.run(
        goal_runtime.handler.handle_goal(
            SlashCommand(name="goal", raw_input=f"/goal retry {request_id} --budget 42 inspect")
        )
    )
    assert result.success
    goal = goal_runtime.store.get_active("conversation-1")
    assert goal.objective == "inspect" and goal.token_budget == 42
    assert goal.attachment_refs == [{"ref": "image"}]


def test_replayed_original_request_returns_receipt_after_profile_switch(goal_runtime):
    goal_runtime.profile.provider = "openai"
    command, request_id = image_command(goal_runtime, "/goal inspect")
    created = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert created.success
    goal_runtime.driver.schedule.reset_mock()
    goal_runtime.profile.provider = "acp"
    replayed = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert replayed.success and "already created" in replayed.message
    assert goal_runtime.service.attachment_request("conversation-1", request_id)["goal_id"] == created.data["goal_id"]
    goal_runtime.driver.schedule.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "/goal",
        "/goal show",
        "/goal pause",
        "/goal resume",
        "/goal clear",
        "/goal history",
        "/goal pending",
        "/goal requests",
        "/goal retry xyz",
        "/goal discard xyz",
    ],
)
def test_controls_with_images_do_not_execute_or_lose_images(goal_runtime, text):
    record = goal_runtime.service.create_goal("conversation-1", "existing", "/repo")
    command, request_id = image_command(goal_runtime, text)
    result = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not result.success and request_id in result.message
    assert goal_runtime.store.get(record.goal_id).status == "active"
    assert goal_runtime.service.pending_attachment_requests("conversation-1")


def test_pending_and_discard_are_conversation_scoped(goal_runtime):
    command, request_id = image_command(goal_runtime, "/goal inspect")
    goal_runtime.profile.provider = "openai"
    goal_runtime.handler.event_bus.get_service("state_service").get_conversation_uid = lambda: "other"
    changed = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not changed.success and request_id not in changed.message
    pending = asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal pending")))
    assert request_id not in pending.message
    retry = asyncio.run(
        goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input=f"/goal retry {request_id} other"))
    )
    assert not retry.success and request_id not in retry.message
    discard = asyncio.run(
        goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input=f"/goal discard {request_id}"))
    )
    assert not discard.success
    assert goal_runtime.service.pending_attachment_requests("conversation-1")


def test_acp_resume_preserves_paused_goal_and_pending_images(goal_runtime):
    record = goal_runtime.service.create_goal("conversation-1", "objective", "/repo")
    goal_runtime.service.pause(record.goal_id)
    _, request_id = image_command(goal_runtime, "/goal another")
    before = goal_runtime.store.get(record.goal_id)
    result = asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal resume")))
    after = goal_runtime.store.get(record.goal_id)
    assert not result.success and "ACP" in result.message
    assert after.status == "paused" and after.execution_generation == before.execution_generation
    goal_runtime.driver.schedule.assert_not_called()
    assert asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal show"))).success
    assert asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal clear"))).success
    assert goal_runtime.service.pending_attachment_requests("conversation-1")[0]["request_id"] == request_id


@pytest.mark.parametrize("broken", [False, True])
def test_unknown_selected_profile_keeps_its_image_request(goal_runtime, broken):
    def unavailable_profile():
        if broken:
            raise RuntimeError("profile lookup failed")
        return None

    goal_runtime.coordinator.profile_manager.get_active_profile = unavailable_profile
    command, request_id = image_command(goal_runtime, "/goal inspect")
    result = asyncio.run(goal_runtime.handler.handle_goal(command))
    assert not result.success and "profile" in result.message.lower() and request_id in result.message
    assert goal_runtime.store.get_active("conversation-1") is None
    assert goal_runtime.service.pending_attachment_requests("conversation-1")
    goal_runtime.driver.schedule.assert_not_called()


def test_missing_profile_manager_does_not_start_goal(goal_runtime):
    goal_runtime.coordinator.profile_manager = None
    result = asyncio.run(
        goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal inspect the project"))
    )
    assert not result.success
    assert "profile" in result.message.lower()
    assert goal_runtime.store.get_active("conversation-1") is None
    goal_runtime.driver.schedule.assert_not_called()


def test_missing_coordinator_does_not_start_goal(goal_runtime):
    goal_runtime.coordinator.event_bus.get_service = lambda name: {
        "goal_service": goal_runtime.service,
        "state_service": SimpleNamespace(get_conversation_uid=lambda: "conversation-1"),
    }.get(name)
    result = asyncio.run(
        goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal inspect the project"))
    )
    assert not result.success
    assert "profile" in result.message.lower()
    assert goal_runtime.store.get_active("conversation-1") is None
    goal_runtime.driver.schedule.assert_not_called()


def test_active_goal_switching_to_acp_pauses_before_provider_call(goal_runtime):
    record = goal_runtime.service.create_goal("conversation-1", "objective", "/repo")
    api = object.__new__(APICommunicationService)
    api._profile = goal_runtime.profile
    api._provider = None
    api._connection_stats = {"total_requests": 0, "last_activity": 0}
    api._prepare_messages = lambda history: history

    async def native_provider_call():
        await api.call_llm([])

    goal_runtime.coordinator._continue_conversation = AsyncMock(side_effect=native_provider_call)
    asyncio.run(goal_runtime.driver.start(record.goal_id))
    after = goal_runtime.store.get(record.goal_id)
    assert after.status == "paused"
    assert "ACP" in (after.last_reason or "")
    assert after.turn_count == 0
    assert goal_runtime.store.unfinished_attempts(record.goal_id) == []
    goal_runtime.coordinator._continue_conversation.assert_not_awaited()


def test_active_goal_with_unknown_profile_pauses_before_provider_call(goal_runtime):
    record = goal_runtime.service.create_goal("conversation-1", "objective", "/repo")
    goal_runtime.coordinator.profile_manager.get_active_profile = lambda: None

    asyncio.run(goal_runtime.driver.start(record.goal_id))

    after = goal_runtime.store.get(record.goal_id)
    assert after.status == "paused"
    assert "profile" in (after.last_reason or "").lower()
    assert after.turn_count == 0
    assert goal_runtime.store.unfinished_attempts(record.goal_id) == []
    goal_runtime.coordinator._continue_conversation.assert_not_awaited()


def test_native_profile_still_creates_and_resumes_goals(goal_runtime):
    goal_runtime.profile.provider = "openai"
    result = asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal investigate")))
    assert result.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record is not None
    goal_runtime.driver.schedule.assert_called_once_with(record.goal_id)
    goal_runtime.driver.schedule.reset_mock()
    goal_runtime.service.pause(record.goal_id)
    result = asyncio.run(goal_runtime.handler.handle_goal(SlashCommand(name="goal", raw_input="/goal resume")))
    assert result.success
    goal_runtime.driver.schedule.assert_called_once_with(record.goal_id)


def test_profile_switch_during_goal_context_injection_never_calls_native_provider(goal_runtime):
    goal_runtime.profile.provider = "openai"
    record = goal_runtime.service.create_goal("conversation-1", "objective", "/repo")
    unrelated = AgentHudEntry(section="hub", label="notice", content="keep this")
    goal_runtime.coordinator._pending_agent_hud = [unrelated]

    async def switch_while_injecting(content, subtype):
        goal_runtime.coordinator._pending_agent_hud.append(
            AgentHudEntry(section="system", label=subtype, content=content)
        )
        goal_runtime.profile.provider = "acp"

    goal_runtime.coordinator.inject_system_message = AsyncMock(side_effect=switch_while_injecting)
    asyncio.run(goal_runtime.driver.start(record.goal_id))
    after = goal_runtime.store.get(record.goal_id)
    assert after.status == "paused"
    assert "ACP" in (after.last_reason or "")
    goal_runtime.coordinator._continue_conversation.assert_not_awaited()
    assert goal_runtime.coordinator._pending_agent_hud == [unrelated]

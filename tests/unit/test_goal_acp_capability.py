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
        store=store, service=service, profile=profile, coordinator=coordinator,
        driver=driver, handler=handler,
    )
    store.close()


@pytest.mark.parametrize("name", ["codex-cli", "claude-code", "custom-acp"])
def test_acp_goal_creation_is_rejected_before_state_or_turn(goal_runtime, name):
    goal_runtime.profile.name = name
    goal_runtime.service.stage_attachments("conversation-1", [{"ref": "staged"}])
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal examine this")
    ))
    assert not result.success
    assert "ACP" in result.message
    assert "evidence" in result.message and "token" in result.message
    assert "native provider" in result.message and "\n" not in result.message
    assert "next goal creation" in result.message and "/goal clear" in result.message
    assert "clears any active goal" in result.message
    assert "daemon stops" in result.message and "until replaced" in result.message
    assert "do not yet send images to the model" not in result.message
    assert goal_runtime.store.get_active("conversation-1") is None
    assert goal_runtime.service.take_staged_attachments("conversation-1") == [{"ref": "staged"}]
    goal_runtime.driver.schedule.assert_not_called()


def test_rejected_goal_attachments_are_reused_by_next_goal(goal_runtime):
    refs = [{"ref": "image-one"}, {"ref": "image-two"}]
    goal_runtime.service.stage_attachments("conversation-1", refs)
    rejected = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the image")
    ))
    assert not rejected.success

    goal_runtime.profile.provider = "openai"
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the image")
    ))
    assert result.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record.attachment_refs == refs
    assert goal_runtime.service.take_staged_attachments("conversation-1") == []


def test_rejection_explains_attachments_go_to_next_goal_even_if_different(goal_runtime):
    goal_runtime.service.stage_attachments("conversation-1", [{"ref": "private-image"}])
    rejected = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the image")
    ))
    assert not rejected.success

    goal_runtime.profile.provider = "openai"
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal write a poem")
    ))
    assert result.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record.attachment_refs == [{"ref": "private-image"}]


def test_existing_invalid_option_staging_is_conversation_scoped(goal_runtime):
    goal_runtime.profile.provider = "openai"
    goal_runtime.service.stage_attachments("conversation-1", [{"ref": "private-image"}])
    rejected = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal --invalid inspect the image")
    ))
    assert not rejected.success
    assert "next goal creation" in rejected.message and "/goal clear" in rejected.message
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal write a poem")
    ))
    assert result.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record.attachment_refs == [{"ref": "private-image"}]


def test_clear_discards_rejected_attachments_without_an_active_goal(goal_runtime):
    service = goal_runtime.service
    service.stage_attachments("conversation-1", [{"ref": "staged"}])
    service.stage_attachments("conversation-2", [{"ref": "other"}])
    rejected = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect this")
    ))
    assert not rejected.success
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal clear")
    ))
    assert result.success and "staged" in result.message
    assert service.take_staged_attachments("conversation-1") == []
    assert service.take_staged_attachments("conversation-2") == [{"ref": "other"}]
    goal_runtime.profile.provider = "openai"
    created = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal write a poem")
    ))
    assert created.success
    assert goal_runtime.store.get_active("conversation-1").attachment_refs == []


def test_clear_removes_active_goal_and_its_conversation_staging(goal_runtime):
    service = goal_runtime.service
    record = service.create_goal("conversation-1", "existing", "/repo")
    service.stage_attachments("conversation-1", [{"ref": "staged"}])
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal clear")
    ))
    assert result.success and "staged" in result.message
    assert goal_runtime.store.get(record.goal_id).status == "cleared"
    assert service.take_staged_attachments("conversation-1") == []


def test_native_create_failure_preserves_staged_attachments(goal_runtime):
    goal_runtime.profile.provider = "openai"
    service = goal_runtime.service
    service.create_goal("conversation-1", "existing", "/repo")
    service.stage_attachments("conversation-1", [{"ref": "staged"}])
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal another goal")
    ))
    assert not result.success
    assert "remain" in result.message and "/goal clear" in result.message
    assert service.take_staged_attachments("conversation-1") == [{"ref": "staged"}]


@pytest.mark.asyncio
async def test_structured_image_goal_is_staged_before_acp_refusal(goal_runtime):
    services = {"goal_service": goal_runtime.service, "llm_service": goal_runtime.coordinator,
                "goal_driver": goal_runtime.driver, "slash_parser": SlashCommandParser()}
    bus = SimpleNamespace(get_service=services.get)
    state = LocalStateService(goal_runtime.coordinator, goal_runtime.coordinator.profile_manager,
                              event_bus=bus)
    state.get_conversation_uid = lambda: "conversation-1"
    services["state_service"] = state
    handler = GoalCommandHandler(None, bus)

    class Executor:
        result = None

        async def execute_command(self, command, event_bus):
            self.result = await handler.handle_goal(command)
            return self.result

    executor = Executor()
    services["command_executor"] = executor
    tasks = []

    def start_task(coroutine, *, name=None):
        task = asyncio.create_task(coroutine, name=name)
        tasks.append(task)
        return task

    goal_runtime.coordinator.create_background_task = start_task
    result = await state.send_message([
        {"type": "text", "text": "/goal inspect the screenshot"},
        {"type": "image", "image": "data:image/png;base64,aGVsbG8="},
        {"type": "image", "image": "data:image/png;base64,d29ybGQ="},
    ])
    assert result == {"accepted": True, "reason": "goal command"}
    await asyncio.gather(*tasks)
    assert not executor.result.success
    assert "ACP" in executor.result.message and "next goal creation" in executor.result.message
    refs = goal_runtime.service.take_staged_attachments("conversation-1")
    assert len(refs) == 2 and all(ref["kind"] == "image" for ref in refs)
    assert [goal_runtime.service.load_attachment(ref) for ref in refs] == [b"hello", b"world"]
    assert goal_runtime.store.get_active("conversation-1") is None
    goal_runtime.driver.schedule.assert_not_called()


def test_unexpected_storage_error_preserves_staged_attachments(goal_runtime, monkeypatch):
    goal_runtime.profile.provider = "openai"
    service = goal_runtime.service
    service.stage_attachments("conversation-1", [{"ref": "staged"}])

    def unavailable_store(**kwargs):
        raise OSError("store unavailable")

    monkeypatch.setattr(service.store, "create", unavailable_store)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect this")
    ))
    assert not result.success
    assert "store unavailable" in result.message and "next goal creation" in result.message
    assert service.take_staged_attachments("conversation-1") == [{"ref": "staged"}]


@pytest.mark.parametrize("new_ref", ["first", "second"])
def test_created_goal_does_not_consume_newly_staged_attachments(goal_runtime, new_ref):
    goal_runtime.profile.provider = "openai"
    service = goal_runtime.service
    service.stage_attachments("conversation-1", [{"ref": "first"}])

    def stage_another(**kwargs):
        service.stage_attachments("conversation-1", [{"ref": new_ref}])

    service.set_state_publisher(stage_another)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the first image")
    ))
    assert result.success
    assert goal_runtime.store.get_active("conversation-1").attachment_refs == [{"ref": "first"}]
    assert service.take_staged_attachments("conversation-1") == [{"ref": new_ref}]


@pytest.mark.parametrize("new_ref", ["first", "second"])
def test_clear_does_not_consume_attachments_staged_during_its_notification(goal_runtime, new_ref):
    service = goal_runtime.service
    record = service.create_goal("conversation-1", "existing", "/repo")
    service.stage_attachments("conversation-1", [{"ref": "first"}])

    def stage_another(**kwargs):
        service.stage_attachments("conversation-1", [{"ref": new_ref}])

    service.set_state_publisher(stage_another)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal clear")
    ))
    assert result.success
    assert goal_runtime.store.get(record.goal_id).status == "cleared"
    assert service.take_staged_attachments("conversation-1") == [{"ref": new_ref}]


def test_acp_resume_preserves_paused_goal_and_controls(goal_runtime):
    record = goal_runtime.service.create_goal("conversation-1", "objective", "/repo")
    goal_runtime.service.pause(record.goal_id)
    goal_runtime.service.stage_attachments("conversation-1", [{"ref": "staged"}])
    before = goal_runtime.store.get(record.goal_id)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal resume")
    ))
    after = goal_runtime.store.get(record.goal_id)
    assert not result.success
    assert "ACP" in result.message
    assert after.status == "paused"
    assert after.execution_generation == before.execution_generation
    assert goal_runtime.service.has_staged_attachments("conversation-1")
    goal_runtime.driver.schedule.assert_not_called()
    shown = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal show")
    ))
    assert shown.success
    assert goal_runtime.service.has_staged_attachments("conversation-1")
    cleared = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal clear")
    ))
    assert cleared.success
    assert goal_runtime.store.get(record.goal_id).status == "cleared"
    assert not goal_runtime.service.has_staged_attachments("conversation-1")


@pytest.mark.parametrize("broken", [False, True])
def test_unknown_selected_profile_does_not_start_goal(goal_runtime, broken):
    def unavailable_profile():
        if broken:
            raise RuntimeError("profile lookup failed")
        return None

    goal_runtime.coordinator.profile_manager.get_active_profile = unavailable_profile
    goal_runtime.service.stage_attachments("conversation-1", [{"ref": "staged"}])
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the project")
    ))
    assert not result.success
    assert "profile" in result.message.lower()
    assert "next goal creation" in result.message and "/goal clear" in result.message
    assert goal_runtime.store.get_active("conversation-1") is None
    assert goal_runtime.service.take_staged_attachments("conversation-1") == [{"ref": "staged"}]
    goal_runtime.driver.schedule.assert_not_called()


def test_missing_profile_manager_does_not_start_goal(goal_runtime):
    goal_runtime.coordinator.profile_manager = None
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the project")
    ))
    assert not result.success
    assert "profile" in result.message.lower()
    assert goal_runtime.store.get_active("conversation-1") is None
    goal_runtime.driver.schedule.assert_not_called()


def test_missing_coordinator_does_not_start_goal(goal_runtime):
    goal_runtime.coordinator.event_bus.get_service = lambda name: {
        "goal_service": goal_runtime.service,
        "state_service": SimpleNamespace(get_conversation_uid=lambda: "conversation-1"),
    }.get(name)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal inspect the project")
    ))
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
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal investigate")
    ))
    assert result.success
    record = goal_runtime.store.get_active("conversation-1")
    assert record is not None
    goal_runtime.driver.schedule.assert_called_once_with(record.goal_id)
    goal_runtime.driver.schedule.reset_mock()
    goal_runtime.service.pause(record.goal_id)
    result = asyncio.run(goal_runtime.handler.handle_goal(
        SlashCommand(name="goal", raw_input="/goal resume")
    ))
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

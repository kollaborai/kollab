"""Request-level coverage of tool exposure without real services or processes."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.streaming_handler import StreamingHandler
from kollabor_agent.agent_manager import Agent, AgentManager, SkillLibrary
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.native_tools_handler import NativeToolsHandler
from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_agent.tool_registry import ToolRegistry
from kollabor_ai.profile_manager import ProfileManager
from kollabor_ai.response_parser import ResponseParser
from kollabor_ai.system_prompt_builder import SystemPromptBuilder
from kollabor_events.data_models import ConversationMessage
from kollabor_events.models import EventType


class _Config:
    def __init__(self, native=True, registry=True):
        self.values = {
            "kollabor.llm.native_tool_calling": native,
            "kollabor.tool_registry.use_registry": registry,
            "kollabor.llm.system_prompt.include_project_structure": False,
            "kollabor.llm.lazy_mcp_tools": False,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Profiles(ProfileManager):
    """Use the real manager interface with an isolated fake active profile."""

    def __init__(self, capability):
        self.profile = SimpleNamespace(
            name="fixture", get_supports_tools=lambda: capability
        )

    def get_active_profile(self):
        return self.profile


class _ProfilesWithDirectCapability(_Profiles):
    """Exercise a controlled adapter that exposes capability directly."""

    def get_supports_tools(self):
        return self.profile.get_supports_tools()


class _PromptRenderer:
    def __init__(self, **_kwargs):
        pass

    def render(self, text):
        return text

    def active_provider(self):
        return None


class _API:
    request = None

    async def call_llm(self, **kwargs):
        self.request = kwargs
        return "fixture response"


class _Hooks:
    def __init__(self, deny=False):
        self.deny = deny
        self.pre_calls = 0

    async def emit_with_hooks(self, event_type, _data, _source):
        if event_type == EventType.TOOL_CALL_PRE:
            self.pre_calls += 1
            if self.deny:
                return {
                    "cancelled": True,
                    "main": {
                        "final_data": {
                            "permission_decision": {"reason": "fixture denied"}
                        }
                    },
                }
        return {}


@pytest.fixture
def agent_and_mcp(monkeypatch, tmp_path):
    # The real managers are used; filesystem, prompt-rendering, and MCP boundaries
    # are kept in a temporary directory and no service is connected.
    import kollabor_ai.prompt_renderer as prompt_renderer
    import kollabor_config.config_utils as config_utils

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        config_utils, "get_bundled_agents_dir", lambda: tmp_path / "bundled"
    )
    monkeypatch.setattr(
        config_utils, "get_global_agents_dir", lambda: tmp_path / "global"
    )
    monkeypatch.setattr(
        config_utils, "get_local_agents_dir", lambda _path: tmp_path / "local"
    )
    monkeypatch.setattr(
        config_utils, "get_config_directory", lambda: tmp_path / "config"
    )
    monkeypatch.setattr(
        config_utils, "get_config_directory_candidates", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr(SkillLibrary, "_discover", lambda self: None)
    monkeypatch.setattr(prompt_renderer, "PromptRenderer", _PromptRenderer)
    monkeypatch.setattr(prompt_renderer, "added_sections_text", lambda *_args: "")
    monkeypatch.setattr(Agent, "_load_hub_protocol", lambda self, *_args: None)
    monkeypatch.setattr(MCPIntegration, "_load_mcp_config", lambda self: None)
    monkeypatch.setattr(ToolRegistry, "_instance", None)

    manager = AgentManager(project_path=tmp_path)
    agent = Agent(
        name="fixture",
        directory=tmp_path,
        system_prompt=(
            "Fixture agent. Static examples: <read><file>x</file></read> "
            "<tool-search><query>hub</query></tool-search>."
        ),
        tools=["tool-search", "hub-msg"],
    )
    manager._agents = {"fixture": AgentRuntime.from_agent(agent)}
    manager._agents_loaded = True
    manager._active_agent_name = "fixture"
    manager.get_agent_memory = lambda: None
    mcp = MCPIntegration(event_bus=None, workspace=tmp_path, agent_manager=manager)
    return manager, mcp


def _request(manager, mcp, capability, native, profiles_type=_Profiles, registry=True):
    config = _Config(native=native, registry=registry)
    profiles = profiles_type(capability)
    builder = SystemPromptBuilder(
        config=config,
        agent_manager=manager,
        profile_manager=profiles,
        mcp_integration=mcp,
    )
    prompt = builder.build()
    api = _API()
    native_handler = NativeToolsHandler(mcp, profiles, api, config)
    handler = StreamingHandler(
        api_service=api,
        renderer=SimpleNamespace(update_thinking=lambda *_args: None),
        message_display_service=SimpleNamespace(is_streaming_active=lambda: False),
    )

    async def send():
        await native_handler.load_tools()
        complete = asyncio.Event()
        complete.set()
        return await handler.call_llm(
            conversation_history=[ConversationMessage(role="system", content=prompt)],
            native_tools=native_handler.tools,
            native_tools_provider=lambda: native_handler.tools,
            mcp_discovery_complete=complete,
            is_cancelled_fn=lambda: False,
        )

    response = asyncio.run(send())
    assert response == "fixture response"
    return api.request


@pytest.mark.parametrize("profiles_type", [_Profiles, _ProfilesWithDirectCapability])
@pytest.mark.parametrize("capability", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_request_exposes_scoped_tools_via_effective_mode(
    agent_and_mcp, profiles_type, capability, native
):
    manager, mcp = agent_and_mcp
    request = _request(manager, mcp, capability, native, profiles_type)
    prompt = request["conversation_history"][0].content
    tool_names = {tool["name"] for tool in request["tools"] or []}
    native_active = capability and native
    # Current ProfileManager has no direct get_supports_tools method, so its
    # existing XML fallback remains even when native schemas are also present.
    reference_expected = not (
        native_active and profiles_type is _ProfilesWithDirectCapability
    )

    assert ("# Tool Reference" in prompt) is reference_expected
    assert ("### `<hub_msg>`" in prompt) is reference_expected
    assert "### `<hub_broadcast>`" not in prompt
    assert ("hub_msg" in tool_names) is native_active
    assert ("tool_search" in tool_names) is native_active
    assert "hub_broadcast" not in tool_names
    assert "Static examples:" in prompt


@pytest.mark.parametrize("native", [False, True])
def test_registry_disable_preserves_manual_prompt_and_independent_native_path(
    agent_and_mcp, native
):
    manager, mcp = agent_and_mcp
    request = _request(manager, mcp, True, native, registry=False)
    prompt = request["conversation_history"][0].content
    assert "# Tool Reference" not in prompt
    assert "<read><file>x</file></read>" in prompt
    assert "<tool-search><query>hub</query></tool-search>" in prompt
    assert bool(request["tools"]) is native


@pytest.mark.parametrize("failure", ["error", "empty"])
def test_native_schema_load_failure_preserves_only_existing_manual_guidance(
    agent_and_mcp, monkeypatch, failure
):
    # Record the current lifecycle limitation without connecting to a service:
    # after a suppressed XML reference, a failed/empty load does not rebuild it.
    manager, mcp = agent_and_mcp

    def unavailable():
        if failure == "error":
            raise RuntimeError("fixture schema load failed")
        return []

    monkeypatch.setattr(mcp, "get_tool_definitions_for_api", unavailable)
    request = _request(manager, mcp, True, True, _ProfilesWithDirectCapability)
    prompt = request["conversation_history"][0].content
    assert request["tools"] is None
    assert "# Tool Reference" not in prompt
    assert "<tool-search><query>hub</query></tool-search>" in prompt


def test_xml_tool_search_still_parses_and_dispatches(agent_and_mcp):
    manager, mcp = agent_and_mcp
    parser = ResponseParser()
    LLMService._register_registry_tool_tags(SimpleNamespace(response_parser=parser))
    parsed = parser.parse_response("<tool-search><query>hub-msg</query></tool-search>")
    calls = parsed["components"]["plugin_tools"]
    assert len(calls) == 1
    hooks = _Hooks()
    executor = ToolExecutor(mcp_integration=mcp, event_bus=hooks)
    executor.set_bundle_scope(manager.get_active_agent().tools)
    result = asyncio.run(executor.execute_tool(calls[0]))
    assert result.success, result.error
    assert "hub-msg" in result.output
    assert hooks.pre_calls == 1


@pytest.mark.parametrize("denied", [False, True])
def test_in_scope_call_obeys_permission_hook(agent_and_mcp, denied):
    manager, mcp = agent_and_mcp
    hooks = _Hooks(deny=denied)
    executor = ToolExecutor(mcp_integration=mcp, event_bus=hooks)
    executor.set_bundle_scope(manager.get_active_agent().tools)
    handled = []

    async def fake_hub_message(tool_data):
        handled.append(tool_data)
        return ToolExecutionResult(
            tool_id="1", tool_type="hub-msg", success=True, output="ok"
        )

    executor.register_plugin_handler("hub_msg", fake_hub_message)
    result = asyncio.run(executor.execute_tool({"type": "hub-msg", "id": "1"}))
    assert result.success is not denied
    assert bool(handled) is not denied
    assert hooks.pre_calls == 1
    if denied:
        assert result.metadata["permission_denied"] is True
        assert result.error == "fixture denied"


def test_out_of_scope_call_is_rejected_before_hooks(agent_and_mcp):
    manager, mcp = agent_and_mcp
    hooks = _Hooks()
    executor = ToolExecutor(mcp_integration=mcp, event_bus=hooks)
    executor.set_bundle_scope(manager.get_active_agent().tools)
    result = asyncio.run(executor.execute_tool({"type": "hub-broadcast", "id": "2"}))
    assert not result.success
    assert result.metadata["scope_denied"] is True
    assert hooks.pre_calls == 0

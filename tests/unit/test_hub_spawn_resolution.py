"""Unit tests for hub spawn identity/type resolution."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from plugins.hub.plugin import HubPlugin


class _FakePresence:
    def __init__(self, identities=()):
        self.identities = identities

    def scan_all_presence(self):
        return [SimpleNamespace(identity=name) for name in self.identities]


class _FakeAgentManager:
    def get_agent(self, name):
        if name == "research":
            return SimpleNamespace(name="research", profile="")
        if name in {"coder", "pr-shepherd", "kollabor"}:
            return SimpleNamespace(name=name, profile="default")
        return None


class _FakeProfileManager:
    active_profile_name = "openai-oauth"

    def get_profile(self, name):
        if name == "codex-cli":
            return SimpleNamespace(provider="acp", get_provider=lambda: "acp")
        if name in {"openai-oauth", "deepseek"}:
            return SimpleNamespace(provider="openai", get_provider=lambda: "openai")
        return None


class _FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def spawn(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _FakeOrchestratorPlugin:
    def __init__(self):
        self.orchestrator = _FakeOrchestrator()


class _FakeEventBus:
    def __init__(self):
        self.orchestrator_plugin = _FakeOrchestratorPlugin()
        self.agent_manager = _FakeAgentManager()
        self.profile_manager = _FakeProfileManager()

    def get_service(self, name):
        if name == "agent_orchestrator":
            return self.orchestrator_plugin
        if name == "agent_manager":
            return self.agent_manager
        if name == "profile_manager":
            return self.profile_manager
        return None


class TestHubSpawnResolution(IsolatedAsyncioTestCase):
    def test_spawn_tool_exposes_explicit_identity_for_native_and_xml_calls(self):
        from kollabor_agent.tool_definitions.hub import hub_spawn

        parameter_names = {parameter.name for parameter in hub_spawn.parameters}
        self.assertIn("identity", parameter_names)
        self.assertIn("identity", hub_spawn.xml_attributes)

    async def test_acp_bundle_role_resolves_to_available_roster_identity(self):
        """A harness owns the tools, but a role name still selects a persona identity."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
        plugin._presence = _FakePresence()
        personas = [
            SimpleNamespace(name="jasper", agent="coder"),
            SimpleNamespace(name="bismuth", agent="coder"),
        ]
        lookup = {persona.name: persona for persona in personas}

        with (
            patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
            patch("plugins.hub.plugin.find_persona", side_effect=lambda name: lookup.get(name)),
            patch("plugins.hub.plugin.roster_personas", return_value=personas),
        ):
            result = await plugin._handle_spawn_command(
                {"name": "coder", "task": "review the patch", "llm": "codex-cli"}
            )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertIn("Created agent 'jasper' (harness: codex-cli)", result)
        self.assertEqual(call["name"], "jasper")
        self.assertEqual(call["identity"], "jasper")
        self.assertEqual(call["agent_type"], "")
        self.assertTrue(call["harness"])

    async def test_profile_name_wins_over_colliding_loadout_for_harness_detection(self):
        """Explicit profile selection outranks a loadout with the same name."""
        from unittest.mock import patch

        for profile_provider, loadout_provider, expected_harness in (
            ("openai", "acp", False),
            ("acp", "openai", True),
        ):
            with self.subTest(profile_provider=profile_provider):
                event_bus = _FakeEventBus()
                native_or_acp_profile = SimpleNamespace(
                    provider=profile_provider,
                    get_provider=lambda provider=profile_provider: provider,
                )
                fallback = SimpleNamespace(
                    provider=loadout_provider,
                    get_provider=lambda provider=loadout_provider: provider,
                )
                event_bus.profile_manager.get_profile = lambda name: {
                    "shared": native_or_acp_profile,
                    "loadout-target": fallback,
                }.get(name)
                loadout = SimpleNamespace(provider_profile="loadout-target")
                plugin = HubPlugin(event_bus=event_bus)
                plugin._identity = SimpleNamespace(
                    identity="koordinator", profile="openai-oauth", is_coordinator=True
                )
                plugin._presence = _FakePresence()
                with (
                    patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(loadout, [])) as resolve,
                    patch("plugins.hub.plugin.find_persona", return_value=None),
                    patch(
                        "plugins.hub.plugin.roster_personas",
                        return_value=[SimpleNamespace(name="jasper", agent="coder")],
                    ),
                ):
                    await plugin._handle_spawn_command(
                        {"name": "coder", "task": "inspect", "llm": "shared"}
                    )

                call = event_bus.orchestrator_plugin.orchestrator.calls[0]
                self.assertEqual(call.get("harness", False), expected_harness)
                self.assertEqual(resolve.call_count, 0)

    async def test_inherited_acp_profile_uses_harness_role_resolution(self):
        """An inherited ACP profile has the same role semantics as explicit ACP."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator", profile="codex-cli", is_coordinator=True
        )
        plugin._presence = _FakePresence()
        personas = [SimpleNamespace(name="jasper", agent="coder")]
        with (
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch("plugins.hub.plugin.roster_personas", return_value=personas),
        ):
            await plugin._handle_spawn_command({"name": "coder", "task": "inspect"})

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertTrue(call["harness"])
        self.assertEqual(call["identity"], "jasper")
        self.assertEqual(call["profile"], "codex-cli")

    async def test_loadout_fallback_uses_its_selected_profile_and_keeps_role_identity(self):
        """A loadout without a same-named profile selects its provider profile."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        acp_profile = SimpleNamespace(provider="acp", get_provider=lambda: "acp")
        event_bus.profile_manager.get_profile = lambda name: (
            acp_profile if name == "acp-target" else None
        )
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator", profile="openai-oauth", is_coordinator=True
        )
        plugin._presence = _FakePresence()
        with (
            patch(
                "kollabor_ai.loadout_manager.LoadoutManager.resolve",
                return_value=(SimpleNamespace(provider_profile="acp-target"), []),
            ),
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch(
                "plugins.hub.plugin.roster_personas",
                return_value=[SimpleNamespace(name="jasper", agent="coder")],
            ),
        ):
            await plugin._handle_spawn_command(
                {"name": "coder", "task": "inspect", "llm": "shared-loadout"}
            )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertTrue(call["harness"])
        self.assertEqual(call["identity"], "jasper")
        self.assertEqual(call["profile"], "shared-loadout")

    async def test_bundle_profile_wins_parent_profile_for_role_and_persona_names(self):
        """The profile classified for launch is also the profile passed to child startup."""
        from unittest.mock import patch

        for bundle_profile, parent_profile, expected_harness in (
            ("codex-cli", "openai-oauth", True),
            ("openai-oauth", "codex-cli", False),
        ):
            for spawn_name in ("coder", "jasper"):
                with self.subTest(
                    bundle_profile=bundle_profile,
                    parent_profile=parent_profile,
                    spawn_name=spawn_name,
                ):
                    event_bus = _FakeEventBus()
                    event_bus.agent_manager.get_agent = lambda _name, profile=bundle_profile: SimpleNamespace(
                        name="coder", profile=profile
                    )
                    plugin = HubPlugin(event_bus=event_bus)
                    plugin._identity = SimpleNamespace(
                        identity="koordinator",
                        profile=parent_profile,
                        is_coordinator=True,
                    )
                    plugin._presence = _FakePresence()
                    personas = [SimpleNamespace(name="jasper", agent="coder")]
                    with (
                        patch(
                            "plugins.hub.plugin.find_persona",
                            side_effect=lambda ref: personas[0] if ref == "jasper" else None,
                        ),
                        patch("plugins.hub.plugin.roster_personas", return_value=personas),
                    ):
                        await plugin._handle_spawn_command(
                            {"name": spawn_name, "task": "inspect"}
                        )

                    call = event_bus.orchestrator_plugin.orchestrator.calls[0]
                    self.assertEqual(call["profile"], bundle_profile)
                    self.assertEqual(call.get("harness", False), expected_harness)
                    self.assertEqual(call["identity"], "jasper")

    async def test_default_llm_placeholder_does_not_override_bundle_or_parent_profile(self):
        """`llm=default` is empty so normal bundle/parent precedence applies."""
        from unittest.mock import patch

        for bundle_profile, parent_profile, expected_profile in (
            ("codex-cli", "openai-oauth", "codex-cli"),
            ("default", "codex-cli", "codex-cli"),
        ):
            with self.subTest(bundle_profile=bundle_profile):
                event_bus = _FakeEventBus()
                event_bus.agent_manager.get_agent = lambda _name, profile=bundle_profile: SimpleNamespace(
                    name="coder", profile=profile
                )
                plugin = HubPlugin(event_bus=event_bus)
                plugin._identity = SimpleNamespace(
                    identity="koordinator", profile=parent_profile, is_coordinator=True
                )
                plugin._presence = _FakePresence()
                personas = [SimpleNamespace(name="jasper", agent="coder")]
                with (
                    patch("plugins.hub.plugin.find_persona", return_value=None),
                    patch("plugins.hub.plugin.roster_personas", return_value=personas),
                ):
                    await plugin._handle_spawn_command(
                        {"name": "coder", "task": "inspect", "llm": "default"}
                    )

                call = event_bus.orchestrator_plugin.orchestrator.calls[0]
                self.assertEqual(call["profile"], expected_profile)
                self.assertEqual(call.get("harness", False), True)

    async def test_harness_detection_uses_effective_environment_provider(self):
        """`get_provider()` env overrides match LLM coordinator provider selection."""
        from unittest.mock import patch

        for stored_provider, effective_provider, expected_harness in (
            ("acp", "openai", False),
            ("openai", "acp", True),
        ):
            with self.subTest(effective_provider=effective_provider):
                event_bus = _FakeEventBus()
                selected = SimpleNamespace(
                    provider=stored_provider,
                    get_provider=lambda provider=effective_provider: provider,
                )
                event_bus.profile_manager.get_profile = lambda name: (
                    selected if name == "shared" else None
                )
                plugin = HubPlugin(event_bus=event_bus)
                plugin._identity = SimpleNamespace(
                    identity="koordinator", profile="openai-oauth", is_coordinator=True
                )
                plugin._presence = _FakePresence()
                with (
                    patch("plugins.hub.plugin.find_persona", return_value=None),
                    patch(
                        "plugins.hub.plugin.roster_personas",
                        return_value=[SimpleNamespace(name="jasper", agent="coder")],
                    ),
                ):
                    await plugin._handle_spawn_command(
                        {"name": "coder", "task": "inspect", "llm": "shared"}
                    )

                call = event_bus.orchestrator_plugin.orchestrator.calls[0]
                self.assertEqual(call.get("harness", False), expected_harness)
                self.assertEqual(call["identity"], "jasper")

    async def test_acp_exact_persona_and_explicit_custom_identity_are_preserved(self):
        """Exact names and an explicit identity beat role-to-persona assignment."""
        from unittest.mock import patch

        personas = [
            SimpleNamespace(name="jasper", agent="coder"),
            SimpleNamespace(name="coder", agent="research"),
        ]
        lookup = {persona.name: persona for persona in personas}

        for name, identity, expected in (
            ("jasper", "", "jasper"),
            ("coder", "", "coder"),
            ("coder", "kollabor", "kollabor"),
        ):
            with self.subTest(name=name, identity=identity):
                event_bus = _FakeEventBus()
                plugin = HubPlugin(event_bus=event_bus)
                plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
                plugin._presence = _FakePresence()
                with (
                    patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
                    patch("plugins.hub.plugin.find_persona", side_effect=lambda ref: lookup.get(ref)),
                    patch("plugins.hub.plugin.roster_personas", return_value=personas),
                ):
                    await plugin._handle_spawn_command(
                        {"name": name, "identity": identity, "task": "inspect", "llm": "codex-cli"}
                    )
                self.assertEqual(
                    event_bus.orchestrator_plugin.orchestrator.calls[0]["identity"],
                    expected,
                )

    async def test_acp_known_bundle_without_a_matching_persona_uses_next_free_identity(self):
        """Known bundles still resolve to a real roster identity under ACP."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
        plugin._presence = _FakePresence()
        personas = [SimpleNamespace(name="jasper", agent="coder")]
        with (
            patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch("plugins.hub.plugin.roster_personas", return_value=personas),
        ):
            for role in ("coder", "pr-shepherd", "kollabor"):
                with self.subTest(role=role):
                    event_bus.orchestrator_plugin.orchestrator.calls.clear()
                    await plugin._handle_spawn_command(
                        {"name": role, "task": "inspect", "llm": "codex-cli"}
                    )
                    self.assertEqual(
                        event_bus.orchestrator_plugin.orchestrator.calls[0]["identity"],
                        "jasper",
                    )

    async def test_acp_busy_role_does_not_fall_back_to_another_roles_identity(self):
        """A free research persona cannot substitute for busy coder personas."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
        plugin._presence = _FakePresence(("jasper", "bismuth"))
        personas = [
            SimpleNamespace(name="jasper", agent="coder"),
            SimpleNamespace(name="bismuth", agent="coder"),
            SimpleNamespace(name="opal", agent="research"),
        ]
        with (
            patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch("plugins.hub.plugin.roster_personas", return_value=personas),
        ):
            result = await plugin._handle_spawn_command(
                {"name": "coder", "task": "inspect", "llm": "codex-cli"}
            )

        self.assertIn("all coder agents are busy: jasper, bismuth", result)
        self.assertEqual(event_bus.orchestrator_plugin.orchestrator.calls, [])

    async def test_unknown_acp_name_remains_an_explicit_harness_identity(self):
        """Adapter names such as codex remain usable when they are not bundle roles."""
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
        plugin._presence = _FakePresence()
        with (
            patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch("plugins.hub.plugin.roster_personas", return_value=[]),
        ):
            await plugin._handle_spawn_command(
                {"name": "codex", "task": "inspect", "llm": "codex-cli"}
            )
        self.assertEqual(
            event_bus.orchestrator_plugin.orchestrator.calls[0]["identity"], "codex"
        )

    async def test_agent_bundle_name_uses_free_pool_identity(self):
        """`research` is an agent bundle, not a pool identity."""
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile="deepseek",
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_spawn_command(
            {"name": "research", "task": "review this project"}
        )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertIn("Created agent 'lapis' (agent type: research)", result)
        self.assertEqual(call["name"], "lapis")
        self.assertEqual(call["identity"], "lapis")
        self.assertEqual(call["agent_type"], "research")
        self.assertEqual(call["task"], "review this project")
        self.assertEqual(call["parent_identity"], "koordinator")
        self.assertEqual(call["parent_agent_id"], "")

    async def test_native_busy_role_does_not_fall_back_to_another_roles_identity(self):
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="deepseek", is_coordinator=True)
        plugin._presence = _FakePresence(("jasper", "bismuth"))
        personas = [
            SimpleNamespace(name="jasper", agent="coder"),
            SimpleNamespace(name="bismuth", agent="coder"),
            SimpleNamespace(name="opal", agent="research"),
        ]

        from unittest.mock import patch

        with patch("plugins.hub.plugin.roster_personas", return_value=personas):
            result = await plugin._handle_spawn_command(
                {"name": "coder", "task": "inspect"}
            )

        self.assertIn("all coder agents are busy: jasper, bismuth", result)
        self.assertEqual(event_bus.orchestrator_plugin.orchestrator.calls, [])

    async def test_native_spawn_reads_identity_and_type_from_input(self):
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile="deepseek",
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_hub_spawn_tool(
            {
                "id": "call_spawn",
                "type": "hub_spawn",
                "name": "hub_spawn",
                "input": {
                    "name": "lapis",
                    "type": "research",
                    "task": "audit",
                },
            }
        )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertTrue(result.success)
        self.assertEqual(call["name"], "lapis")
        self.assertEqual(call["agent_type"], "research")
        self.assertEqual(call["task"], "audit")

    async def test_native_spawn_inherits_profile_from_profile_manager_service(self):
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile=None,
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_hub_spawn_tool(
            {
                "id": "call_spawn_profile",
                "type": "hub_spawn",
                "name": "hub_spawn",
                "input": {"name": "lapis", "task": "smoke"},
            }
        )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertTrue(result.success)
        self.assertEqual(call["profile"], "openai-oauth")

    async def test_native_acp_spawn_honors_explicit_identity(self):
        from unittest.mock import patch

        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
        plugin._presence = _FakePresence()
        personas = [SimpleNamespace(name="jasper", agent="coder")]
        with (
            patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
            patch("plugins.hub.plugin.find_persona", return_value=None),
            patch("plugins.hub.plugin.roster_personas", return_value=personas),
        ):
            result = await plugin._handle_hub_spawn_tool(
                {
                    "id": "call_spawn_named_acp",
                    "type": "hub_spawn",
                    "name": "hub_spawn",
                    "input": {
                        "name": "coder",
                        "identity": "kollabor",
                        "task": "review the work",
                        "llm": "codex-cli",
                    },
                }
            )
        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertTrue(result.success)
        self.assertEqual(call["identity"], "kollabor")
        self.assertTrue(call["harness"])


    async def test_bundle_default_inherits_named_parent_profile(self):
        """The bundle placeholder must not replace a parent's --llm profile."""
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile="default",
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_spawn_command(
            {"name": "coder", "task": "implement safely"}
        )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertIn("Created agent", result)
        self.assertEqual(call["profile"], "openai-oauth")

    async def test_spawn_fails_closed_without_named_profile(self):
        """A placeholder default must never start a child through auto-detect."""
        event_bus = _FakeEventBus()
        event_bus.profile_manager.active_profile_name = "default"
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile="default",
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_spawn_command(
            {"name": "coder", "task": "implement safely"}
        )

        self.assertIn("no explicit LLM profile", result)
        self.assertFalse(event_bus.orchestrator_plugin.orchestrator.calls)

    async def test_detached_chain_break_report_targets_spawn_parent(self):
        plugin = HubPlugin()
        plugin._identity = SimpleNamespace(
            identity="lapis",
            agent_id="lapis-1",
            parent_identity="koordinator",
            current_task="review the terminal recovery path",
        )
        plugin._route_message = AsyncMock(return_value=[])

        result = await plugin.report_detached_chain_break(
            reason="3 identical tool errors"
        )

        self.assertTrue(result)
        message = plugin._route_message.await_args.args[0]
        self.assertEqual(message.to, "koordinator")
        self.assertTrue(message.force)
        self.assertTrue(message.metadata["chain_break"])
        self.assertIn("review the terminal recovery path", message.content)

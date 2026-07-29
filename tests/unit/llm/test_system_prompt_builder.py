"""Tests for SystemPromptBuilder."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kollabor_ai import SystemPromptBuilder
from kollabor_events.data_models import ConversationMessage


class TestSystemPromptBuilder(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock()
        self.config.get = MagicMock(return_value=False)

        self.agent_manager = None

        self.builder = SystemPromptBuilder(
            config=self.config, agent_manager=self.agent_manager
        )

    def test_init(self):
        """Test SystemPromptBuilder initialization."""
        self.assertIsNotNone(self.builder)
        self.assertEqual(self.builder.config, self.config)
        self.assertIsNone(self.builder._plugin_instances)

    def test_set_plugin_instances(self):
        """Test setting plugin instances."""
        plugins = {"plugin1": MagicMock(), "plugin2": MagicMock()}
        self.builder.set_plugin_instances(plugins)

        self.assertEqual(self.builder._plugin_instances, plugins)

    def test_build_with_agent_prompt(self):
        """Test building system prompt from agent."""
        agent_manager = MagicMock()
        agent_manager.get_system_prompt = MagicMock(return_value="agent system prompt")
        active_agent = MagicMock()
        active_agent.directory = Path("/agents/test")
        agent_manager.get_active_agent = MagicMock(return_value=active_agent)
        agent_manager.active_agent_name = "test-agent"

        builder = SystemPromptBuilder(config=self.config, agent_manager=agent_manager)

        # The builder uses lazy imports, so we need to provide the utils
        mock_utils = {
            "render_system_prompt": MagicMock(return_value="rendered agent prompt"),
            "get_system_prompt_content": MagicMock(),
            "initialize_system_prompt": MagicMock(),
            "get_system_prompt_path": MagicMock(),
            "format_aliases_for_prompt": MagicMock(),
        }
        builder._util_imports = mock_utils

        with patch.object(
            builder, "_finalize_system_prompt", return_value="rendered agent prompt"
        ):
            result = builder.build()

        self.assertEqual(result, "rendered agent prompt")
        agent_manager.get_system_prompt.assert_called_once()

    def test_build_without_agent(self):
        """Test building system prompt without agent."""
        mock_utils = {
            "render_system_prompt": MagicMock(return_value="rendered base prompt"),
            "get_system_prompt_content": MagicMock(return_value="base prompt"),
            "initialize_system_prompt": MagicMock(),
            "get_system_prompt_path": MagicMock(
                return_value=Path("/tmp/fake/prompt.md")
            ),
            "format_aliases_for_prompt": MagicMock(),
        }
        self.builder._util_imports = mock_utils

        with patch.object(
            self.builder, "_finalize_system_prompt", return_value="final prompt"
        ):
            result = self.builder.build()

        self.assertEqual(result, "final prompt")

    def test_rebuild_success(self):
        """Test rebuilding system prompt successfully."""
        conversation_history = [
            ConversationMessage(role="system", content="old prompt")
        ]

        with patch.object(self.builder, "build", return_value="new prompt"):
            result = self.builder.rebuild(conversation_history)

        self.assertTrue(result)
        self.assertEqual(conversation_history[0].content, "new prompt")

    def test_rebuild_no_system_message(self):
        """Test rebuild fails when no system message found."""
        conversation_history = []

        result = self.builder.rebuild(conversation_history)

        self.assertFalse(result)

    def test_rebuild_first_message_not_system(self):
        """Test rebuild when first message is not system role."""
        conversation_history = [ConversationMessage(role="user", content="hello")]

        with patch.object(self.builder, "build", return_value="new prompt"):
            result = self.builder.rebuild(conversation_history)

        self.assertFalse(result)

    def test_get_tree_output_success(self):
        """Test getting tree output successfully."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "tree output here"
            mock_run.return_value = mock_result

            result = self.builder._get_tree_output()

        self.assertIn("tree output here", result)

    def test_get_tree_output_fallback_to_ls(self):
        """Test tree output falls back to ls on failure."""
        with patch("subprocess.run") as mock_run:
            # tree fails, ls succeeds
            mock_run.side_effect = [
                MagicMock(returncode=1),  # tree fails
                MagicMock(returncode=0, stdout="ls output"),  # ls succeeds
            ]

            result = self.builder._get_tree_output()

        self.assertIn("ls output", result)

    def test_get_tree_output_exception(self):
        """Test tree output handles exceptions."""
        with patch("subprocess.run", side_effect=Exception("test error")):
            result = self.builder._get_tree_output()

        self.assertIn("Could not get directory listing", result)

    def test_finalize_system_prompt_with_structure(self):
        """Test finalizing system prompt with project structure."""
        self.config.get = MagicMock(
            side_effect=lambda k, d=None: {
                "kollabor.llm.system_prompt.include_project_structure": True,
                "kollabor.llm.system_prompt.attachment_files": [],
                "kollabor.llm.system_prompt.custom_prompt_files": [],
                "terminal.interactive_shell": False,
            }.get(k, d)
        )

        with patch.object(self.builder, "_get_tree_output", return_value="tree here"):
            with patch.object(
                self.builder, "_get_plugin_system_prompt_additions", return_value=[]
            ):
                result = self.builder._finalize_system_prompt(["base prompt"])

        self.assertIn("## Project Structure", result)
        self.assertIn("tree here", result)

    def test_finalize_system_prompt_with_attachment_files(self):
        """Test finalizing system prompt with attachment files."""
        self.config.get = MagicMock(
            side_effect=lambda k, d=None: {
                "kollabor.llm.system_prompt.include_project_structure": False,
                "kollabor.llm.system_prompt.attachment_files": ["README.md"],
                "kollabor.llm.system_prompt.custom_prompt_files": [],
                "terminal.interactive_shell": False,
            }.get(k, d)
        )

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "file content"

        with patch("kollabor_ai.system_prompt_builder.Path") as MockPath:
            MockPath.cwd.return_value.__truediv__ = MagicMock(return_value=mock_path)
            with patch.object(
                self.builder, "_get_plugin_system_prompt_additions", return_value=[]
            ):
                result = self.builder._finalize_system_prompt(["base prompt"])

        self.assertIn("## README.md", result)
        self.assertIn("file content", result)

    def test_finalize_system_prompt_with_custom_files(self):
        """Test finalizing system prompt with custom prompt files."""
        self.config.get = MagicMock(
            side_effect=lambda k, d=None: {
                "kollabor.llm.system_prompt.include_project_structure": False,
                "kollabor.llm.system_prompt.attachment_files": [],
                "kollabor.llm.system_prompt.custom_prompt_files": ["custom.md"],
                "terminal.interactive_shell": False,
            }.get(k, d)
        )

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "custom instructions"

        with patch("kollabor_ai.system_prompt_builder.Path") as MockPath:
            MockPath.cwd.return_value.__truediv__ = MagicMock(return_value=mock_path)
            with patch.object(
                self.builder, "_get_plugin_system_prompt_additions", return_value=[]
            ):
                result = self.builder._finalize_system_prompt(["base prompt"])

        self.assertIn("## Custom Instructions (custom.md)", result)
        self.assertIn("custom instructions", result)

    def test_get_plugin_system_prompt_additions_no_plugins(self):
        """Test getting plugin additions when no plugins set."""
        self.builder._plugin_instances = None
        additions = self.builder._get_plugin_system_prompt_additions()

        self.assertEqual(additions, [])

    def test_get_plugin_system_prompt_additions_with_plugins(self):
        """Test getting plugin additions from plugins."""
        plugin1 = MagicMock()
        plugin1.get_system_prompt_addition = MagicMock(return_value="plugin 1 addition")

        plugin2 = MagicMock()
        plugin2.get_system_prompt_addition = MagicMock(return_value="plugin 2 addition")

        plugin3 = MagicMock(spec=[])  # spec=[] means no attributes
        # plugin3 doesn't have get_system_prompt_addition

        self.builder._plugin_instances = {
            "plugin1": plugin1,
            "plugin2": plugin2,
            "plugin3": plugin3,
        }

        additions = self.builder._get_plugin_system_prompt_additions()

        self.assertEqual(len(additions), 2)
        self.assertIn("plugin 1 addition", additions)
        self.assertIn("plugin 2 addition", additions)

    def test_get_plugin_system_prompt_additions_handles_errors(self):
        """Test plugin additions handle errors gracefully."""
        plugin = MagicMock()
        plugin.get_system_prompt_addition = MagicMock(
            side_effect=Exception("plugin error")
        )

        self.builder._plugin_instances = {"plugin": plugin}

        additions = self.builder._get_plugin_system_prompt_additions()

        # Should return empty list on error
        self.assertEqual(additions, [])


class TestMCPToolSummaries(unittest.TestCase):
    """Tests for lazy MCP tool injection."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock()

        def config_get(key, default=None):
            # Enable lazy MCP tools by default
            if key == "kollabor.llm.lazy_mcp_tools":
                return True
            return default if default is not None else False

        self.config.get = MagicMock(side_effect=config_get)

        self.builder = SystemPromptBuilder(config=self.config)

    def test_no_mcp_integration_returns_none(self):
        """Test that None is returned when MCP integration is not set."""
        result = self.builder._get_mcp_tool_summaries()
        self.assertIsNone(result)

    def test_empty_tool_registry_returns_none(self):
        """Test that None is returned when no MCP tools are registered."""
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {}
        self.builder.mcp_integration = mock_mcp

        result = self.builder._get_mcp_tool_summaries()
        self.assertIsNone(result)

    def test_summaries_generated_correctly(self):
        """Test that MCP tool summaries are generated with correct format."""
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {
            "mentiko:navigate": {
                "server": "mentiko",
                "enabled": True,
                "definition": {
                    "name": "mentiko:navigate",
                    "description": "Navigate to a route",
                    "parameters": {},
                },
            },
            "mentiko:list_chains": {
                "server": "mentiko",
                "enabled": True,
                "definition": {
                    "name": "mentiko:list_chains",
                    "description": "List all chains",
                    "parameters": {},
                },
            },
            "github:create_issue": {
                "server": "github",
                "enabled": True,
                "definition": {
                    "name": "github:create_issue",
                    "description": "Create a GitHub issue",
                    "parameters": {},
                },
            },
        }
        self.builder.mcp_integration = mock_mcp

        result = self.builder._get_mcp_tool_summaries()

        self.assertIsNotNone(result)
        self.assertIn("MCP Tools (3 available", result)
        self.assertIn("mentiko (2 tools)", result)
        self.assertIn("github (1 tools)", result)
        self.assertIn("`mentiko:navigate` — Navigate to a route", result)
        self.assertIn("`mentiko:list_chains` — List all chains", result)
        self.assertIn("`github:create_issue` — Create a GitHub issue", result)
        self.assertIn("tool-search/tool-load", result)

    def test_disabled_tools_excluded(self):
        """Test that disabled MCP tools are excluded from summaries."""
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {
            "enabled_tool": {
                "server": "test",
                "enabled": True,
                "definition": {"name": "enabled_tool", "description": "Active"},
            },
            "disabled_tool": {
                "server": "test",
                "enabled": False,
                "definition": {"name": "disabled_tool", "description": "Inactive"},
            },
        }
        self.builder.mcp_integration = mock_mcp

        result = self.builder._get_mcp_tool_summaries()

        self.assertIsNotNone(result)
        self.assertIn("enabled_tool", result)
        self.assertNotIn("disabled_tool", result)

    def test_long_descriptions_truncated(self):
        """Test that long descriptions are truncated to 80 chars."""
        long_desc = "A" * 100
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {
            "long_tool": {
                "server": "test",
                "enabled": True,
                "definition": {"name": "long_tool", "description": long_desc},
            },
        }
        self.builder.mcp_integration = mock_mcp

        result = self.builder._get_mcp_tool_summaries()

        self.assertIsNotNone(result)
        self.assertIn("A" * 77 + "...", result)
        self.assertNotIn("A" * 100, result)

    def test_lazy_disabled_returns_none(self):
        """Test that summaries are skipped when lazy_mcp_tools is False."""
        config = MagicMock()
        config.get = MagicMock(return_value=False)

        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {"tool": {"server": "s", "enabled": True, "definition": {}}}

        builder = SystemPromptBuilder(config=config, mcp_integration=mock_mcp)
        result = builder._get_mcp_tool_summaries()
        self.assertIsNone(result)

    def test_set_mcp_integration(self):
        """Test the set_mcp_integration setter."""
        builder = SystemPromptBuilder(config=self.config)
        self.assertIsNone(builder.mcp_integration)

        mock_mcp = MagicMock()
        builder.set_mcp_integration(mock_mcp)
        self.assertEqual(builder.mcp_integration, mock_mcp)

    def test_no_description_shown_gracefully(self):
        """Test that tools without descriptions are handled."""
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {
            "no_desc_tool": {
                "server": "test",
                "enabled": True,
                "definition": {"name": "no_desc_tool", "description": ""},
            },
        }
        self.builder.mcp_integration = mock_mcp

        result = self.builder._get_mcp_tool_summaries()

        self.assertIsNotNone(result)
        self.assertIn("`no_desc_tool`", result)



if __name__ == "__main__":
    unittest.main()

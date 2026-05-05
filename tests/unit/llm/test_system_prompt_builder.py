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


if __name__ == "__main__":
    unittest.main()

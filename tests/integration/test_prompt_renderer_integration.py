"""Integration tests for system prompt rendering in ConfigLoader."""

import unittest

from kollabor_config.config_utils import get_config_directory
from kollabor_config.loader import ConfigLoader
from kollabor_config.manager import ConfigManager


class TestPromptRendererIntegration(unittest.TestCase):
    """Test that ConfigLoader properly renders system prompts."""

    def setUp(self):
        """Set up test fixtures."""
        config_dir = get_config_directory()
        self.config_manager = ConfigManager(config_dir)
        self.loader = ConfigLoader(self.config_manager)

    def test_load_system_prompt_with_trender(self):
        """Test that _load_system_prompt can render tags if present."""
        # Load the system prompt
        system_prompt = self.loader._load_system_prompt()

        # Should load successfully
        self.assertIsNotNone(system_prompt)
        self.assertGreater(len(system_prompt), 100)

        # Should not contain unprocessed <trender> tags
        # (if tags were present, they should be rendered)
        self.assertNotIn("<trender>", system_prompt.lower())

        print("\n\n=== SYSTEM PROMPT LOADED ===")
        print(f"Length: {len(system_prompt)} characters")
        print(f"First 200 chars: {system_prompt[:200]}")
        print("=" * 40)

    def test_base_config_includes_rendered_prompt(self):
        """Test that get_base_config includes the rendered system prompt."""
        config = self.loader.get_base_config()

        # Config should have system prompt
        self.assertIn("kollabor", config)
        self.assertIn("llm", config["kollabor"])
        self.assertIn("system_prompt", config["kollabor"]["llm"])
        self.assertIn("base_prompt", config["kollabor"]["llm"]["system_prompt"])

        base_prompt = config["kollabor"]["llm"]["system_prompt"]["base_prompt"]

        # Should be rendered (no trender tags)
        self.assertNotIn("<trender>", base_prompt)

        # Should contain content
        self.assertGreater(len(base_prompt), 100)

    def test_complete_config_load(self):
        """Test loading complete configuration with rendered prompt."""
        config = self.loader.load_complete_config()

        # Should have all the standard config sections
        self.assertIn("terminal", config)
        self.assertIn("input", config)
        self.assertIn("kollabor", config)

        # System prompt should be rendered
        system_prompt = config["kollabor"]["llm"]["system_prompt"]["base_prompt"]
        self.assertNotIn("<trender>", system_prompt)


if __name__ == "__main__":
    unittest.main()

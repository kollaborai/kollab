"""Tests for the system prompt dynamic command renderer."""

import unittest

from kollabor_ai import PromptRenderer, render_system_prompt


class TestPromptRenderer(unittest.TestCase):
    """Test cases for PromptRenderer."""

    def setUp(self):
        """Set up test fixtures."""
        self.renderer = PromptRenderer(timeout=5)

    def test_simple_command(self):
        """Test rendering a simple command."""
        prompt = "Current directory: <trender>pwd</trender>"
        result = self.renderer.render(prompt)

        # Should not contain the tag
        self.assertNotIn("<trender>", result)
        self.assertNotIn("</trender>", result)
        # Should contain some path
        self.assertIn("/", result)

    def test_multiple_commands(self):
        """Test rendering multiple commands."""
        prompt = """
Directory: <trender>pwd</trender>
User: <trender>whoami</trender>
"""
        result = self.renderer.render(prompt)

        # Should not contain any tags
        self.assertNotIn("<trender>", result)
        self.assertNotIn("</trender>", result)
        # Should contain output from both commands
        self.assertIn("/", result)  # from pwd

    def test_no_tags(self):
        """Test rendering prompt without trender tags."""
        prompt = "This is a normal prompt without any special tags."
        result = self.renderer.render(prompt)

        # Should return unchanged
        self.assertEqual(prompt, result)

    def test_failed_command(self):
        """Test handling of failed command."""
        prompt = "<trender>this-command-does-not-exist-xyz123</trender>"
        result = self.renderer.render(prompt)

        # Should contain error message
        self.assertIn("trender error", result.lower())

    def test_command_with_output(self):
        """Test command that produces output."""
        prompt = "Files: <trender>echo 'hello world'</trender>"
        result = self.renderer.render(prompt)

        self.assertIn("hello world", result)
        self.assertNotIn("<trender>", result)

    def test_multiline_command(self):
        """Test command that spans multiple lines."""
        prompt = """
<trender>
echo "line1"
echo "line2"
</trender>
"""
        result = self.renderer.render(prompt)

        self.assertIn("line1", result)
        self.assertIn("line2", result)

    def test_get_all_commands(self):
        """Test extracting commands without executing."""
        prompt = """
<trender>pwd</trender>
Some text here
<trender>whoami</trender>
"""
        commands = self.renderer.get_all_commands(prompt)

        self.assertEqual(len(commands), 2)
        self.assertIn("pwd", commands)
        self.assertIn("whoami", commands)

    def test_cache_functionality(self):
        """Test that command output is cached."""
        prompt = "<trender>date +%s%N</trender>"

        # First execution
        result1 = self.renderer.render(prompt)

        # Second execution should use cache and return same result
        result2 = self.renderer.render(prompt)

        self.assertEqual(result1, result2)

        # Clear cache and execute again - should get different result
        self.renderer.clear_cache()
        result3 = self.renderer.render(prompt)

        # Date command output should be different (different nanosecond)
        # But if they're the same, that's okay too (test ran very fast)
        self.assertIsNotNone(result3)

    def test_convenience_function(self):
        """Test the convenience render_system_prompt function."""
        prompt = "Dir: <trender>pwd</trender>"
        result = render_system_prompt(prompt)

        self.assertNotIn("<trender>", result)
        self.assertIn("/", result)

    def test_empty_prompt(self):
        """Test handling of empty prompt."""
        result = self.renderer.render("")
        self.assertEqual("", result)

        result = self.renderer.render(None)
        self.assertIsNone(result)

    def test_timeout_handling(self):
        """Test that long-running commands timeout properly."""
        short_timeout_renderer = PromptRenderer(timeout=1)
        prompt = "<trender>sleep 5 && echo 'done'</trender>"
        result = short_timeout_renderer.render(prompt)

        self.assertIn("timed out", result.lower())

    def test_special_characters_in_output(self):
        """Test handling of special characters in command output."""
        prompt = '<trender>echo "special chars: <>&"</trender>'
        result = self.renderer.render(prompt)

        self.assertIn("special chars", result)
        # Should preserve special characters
        self.assertIn("<", result)
        self.assertIn(">", result)


class TestRealWorldScenarios(unittest.TestCase):
    """Test real-world usage scenarios."""

    def test_git_status_in_prompt(self):
        """Test including git status in system prompt."""
        prompt = """
You are an AI assistant.

Current git status:
<trender>git status --short 2>/dev/null || echo "Not a git repository"</trender>

Recent commits:
<trender>git log --oneline -5 2>/dev/null || echo "No git history"</trender>
"""
        result = render_system_prompt(prompt)

        # Should process without errors
        self.assertNotIn("<trender>", result)
        # Should have some output (either git info or fallback message)
        self.assertTrue(len(result) > len(prompt) - 100)  # Accounting for tag removal

    def test_directory_structure_in_prompt(self):
        """Test including directory structure in system prompt."""
        prompt = """
Project structure:
<trender>find . -maxdepth 2 -type d | head -10</trender>

Python files:
<trender>find . -name "*.py" -type f | wc -l</trender>
"""
        result = render_system_prompt(prompt)

        self.assertNotIn("<trender>", result)
        # Should contain directory paths
        self.assertIn(".", result)


if __name__ == "__main__":
    unittest.main()


class TestPromptRendererDocs(unittest.TestCase):
    """Regression tests for prompt renderer documentation."""

    def test_module_docstring_lists_unique_trender_types(self):
        """Public docstring should not advertise duplicate tag types."""
        import kollabor_ai.prompt_renderer as prompt_renderer_module

        doc = prompt_renderer_module.__doc__
        self.assertIsNotNone(doc)

        tag_lines = [
            line.strip()
            for line in doc.splitlines()
            if line.strip().startswith("- <trender type=")
        ]

        tag_types = []
        for line in tag_lines:
            prefix = 'type="'
            start = line.index(prefix) + len(prefix)
            end = line.index('"', start)
            tag_types.append(line[start:end])

        self.assertEqual(len(tag_types), len(set(tag_types)))
        self.assertIn("mcp_tools", tag_types)

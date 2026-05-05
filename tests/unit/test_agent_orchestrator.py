"""Tests for Agent Orchestrator plugin components."""

import unittest

from kollabor_tui.message_renderer import MessageType
from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin
from plugins.agent_orchestrator.xml_parser import XMLCommandParser


class TestXMLParserStatusCommand(unittest.TestCase):
    """Test cases for XML parser status command detection."""

    def setUp(self):
        """Create parser instance."""
        self.parser = XMLCommandParser()

    def test_status_proper_format_detected(self):
        """Proper <status></status> format is detected."""
        test_cases = [
            "<status></status>",
            "<status>  </status>",
            "before\n<status></status>\nafter",
            "checking now:\n<status></status>",
        ]
        for text in test_cases:
            result = self.parser._parse_status(text)
            self.assertEqual(len(result), 1, f"Should detect status in: {repr(text)}")
            self.assertEqual(result[0].type, "status")

    def test_status_prose_mention_ignored(self):
        """Status mentions in prose are NOT detected as commands."""
        test_cases = [
            "available commands: <status> - check status",
            "You can use <status /> to check",
            "The <status> command shows agents",
            "type <status> to see running agents",
            "use the <status /> tag",
            "<status> displays agent info",
            "commands include <status>, <stop>, and <message>",
        ]
        for text in test_cases:
            result = self.parser._parse_status(text)
            self.assertEqual(
                result, [], f"Should NOT detect status in prose: {repr(text)}"
            )

    def test_status_self_closing_ignored(self):
        """Self-closing <status /> format is NOT detected (too ambiguous)."""
        test_cases = [
            "<status />",
            "<status/>",
            "<status  />",
        ]
        for text in test_cases:
            result = self.parser._parse_status(text)
            self.assertEqual(
                result, [], f"Self-closing should be ignored: {repr(text)}"
            )


class TestXMLParserAgentCommand(unittest.TestCase):
    """Test cases for XML parser agent command detection."""

    def setUp(self):
        """Create parser instance."""
        self.parser = XMLCommandParser()

    def test_agent_block_detected(self):
        """Agent blocks are properly detected."""
        text = """
        <agent>
          <test-agent>
            <task>Do something useful</task>
            <files>
              <file>src/main.py</file>
            </files>
          </test-agent>
        </agent>
        """
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "agent")
        self.assertEqual(len(result[0].agents), 1)
        self.assertEqual(result[0].agents[0].name, "test-agent")
        self.assertEqual(result[0].agents[0].task, "Do something useful")
        self.assertEqual(result[0].agents[0].files, ["src/main.py"])

    def test_multiple_agents_in_block(self):
        """Multiple agents in single block are detected."""
        text = """
        <agent>
          <agent-one>
            <task>Task one</task>
          </agent-one>
          <agent-two>
            <task>Task two</task>
          </agent-two>
        </agent>
        """
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].agents), 2)
        names = [a.name for a in result[0].agents]
        self.assertIn("agent-one", names)
        self.assertIn("agent-two", names)


class TestXMLParserMessageCommand(unittest.TestCase):
    """Test cases for XML parser message command detection."""

    def setUp(self):
        """Create parser instance."""
        self.parser = XMLCommandParser()

    def test_message_command_detected(self):
        """Message commands are properly detected."""
        text = '<message to="agent1">Hello there!</message>'
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "message")
        self.assertEqual(result[0].target, "agent1")
        self.assertEqual(result[0].content, "Hello there!")

    def test_message_with_single_quotes(self):
        """Message with single quotes works."""
        text = "<message to='agent1'>Hello</message>"
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target, "agent1")


class TestXMLParserStopCommand(unittest.TestCase):
    """Test cases for XML parser stop command detection."""

    def setUp(self):
        """Create parser instance."""
        self.parser = XMLCommandParser()

    def test_stop_single_agent(self):
        """Stop single agent is detected."""
        text = "<stop>agent1</stop>"
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "stop")
        self.assertEqual(result[0].targets, ["agent1"])

    def test_stop_multiple_agents(self):
        """Stop multiple agents is detected."""
        text = "<stop>agent1, agent2, agent3</stop>"
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].targets, ["agent1", "agent2", "agent3"])


class TestXMLParserCaptureCommand(unittest.TestCase):
    """Test cases for XML parser capture command detection."""

    def setUp(self):
        """Create parser instance."""
        self.parser = XMLCommandParser()

    def test_capture_with_lines(self):
        """Capture with line count is detected."""
        text = "<capture>agent1 100</capture>"
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "capture")
        self.assertEqual(result[0].target, "agent1")
        self.assertEqual(result[0].lines, 100)

    def test_capture_default_lines(self):
        """Capture without line count uses default."""
        text = "<capture>agent1</capture>"
        result = self.parser.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].lines, 50)  # default


class TestPluginDisplayFilter(unittest.TestCase):
    """Test cases for plugin's XML stripping filter."""

    def setUp(self):
        """Create plugin instance."""
        self.plugin = AgentOrchestratorPlugin()

    def test_strips_agent_blocks(self):
        """Agent blocks are stripped from content."""
        content = "Before <agent><test><task>x</task></test></agent> After"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<agent>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_status_commands(self):
        """Status commands are stripped from content."""
        content = "Before <status></status> After"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<status>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_message_commands(self):
        """Message commands are stripped from content."""
        content = 'Before <message to="agent1">Hello</message> After'
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<message", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_stop_commands(self):
        """Stop commands are stripped from content."""
        content = "Before <stop>agent1</stop> After"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<stop>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_capture_commands(self):
        """Capture commands are stripped from content."""
        content = "Before <capture>agent1 100</capture> After"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<capture>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_clone_commands(self):
        """Clone commands are stripped from content."""
        content = "Before <clone><agent1><task>x</task></agent1></clone> After"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<clone>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_team_commands(self):
        """Team commands are stripped from content."""
        content = 'Before <team lead="boss" workers="3">task</team> After'
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<team", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_broadcast_commands(self):
        """Broadcast commands are stripped from content."""
        content = 'Before <broadcast to="*">msg</broadcast> After'
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<broadcast", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_preserves_prose_status_mention(self):
        """Prose mentions of <status> are NOT stripped."""
        content = "Available commands: <status> - check agent status"
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        # The prose <status> without closing tag should remain
        self.assertIn("<status>", result)

    def test_multiline_content(self):
        """Multiline content with commands is handled correctly."""
        content = """Here is my plan:

<agent>
  <test-agent>
    <task>Do something</task>
  </test-agent>
</agent>

I will also check:
<status></status>

And send:
<message to="agent1">Hello</message>

That's all."""
        result = self.plugin._strip_orchestrator_xml(content, MessageType.ASSISTANT)
        self.assertNotIn("<agent>", result)
        self.assertNotIn("<status>", result)
        self.assertNotIn("<message", result)
        self.assertIn("Here is my plan:", result)
        self.assertIn("That's all.", result)

    def test_strips_sys_msg_from_user_messages(self):
        """<sys_msg> tags are stripped from USER messages."""
        content = """<sys_msg>
## Agent Orchestration

You can spawn parallel sub-agents to work on tasks concurrently.
</sys_msg>

execute a subagent to find a bug"""
        result = self.plugin._strip_orchestrator_xml(content, MessageType.USER)
        self.assertNotIn("<sys_msg>", result)
        self.assertNotIn("Agent Orchestration", result)
        self.assertIn("execute a subagent to find a bug", result)

    def test_sys_msg_stripping_only_for_user_messages(self):
        """<sys_msg> stripping only happens for USER messages, not ASSISTANT."""
        content = """<sys_msg>Instructions</sys_msg>

Some content"""
        # For user messages, sys_msg should be stripped
        user_result = self.plugin._strip_orchestrator_xml(content, MessageType.USER)
        self.assertNotIn("<sys_msg>", user_result)
        self.assertNotIn("Instructions", user_result)
        # For assistant messages, sys_msg is NOT stripped (different behavior)
        assistant_result = self.plugin._strip_orchestrator_xml(
            content, MessageType.ASSISTANT
        )
        # Assistant messages strip other XML tags but not sys_msg
        self.assertIn("Some content", assistant_result)


if __name__ == "__main__":
    unittest.main()

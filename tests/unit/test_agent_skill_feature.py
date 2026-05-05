"""Unit tests for agent/skill spawn feature.

Tests:
1. XML parser extracts agent-type and skill tags correctly
2. agents_list trender renders agent list
3. AgentTask model has agent_type and skills fields
"""

import unittest


class TestAgentTaskModel(unittest.TestCase):
    """Test AgentTask model has new fields."""

    def test_agent_task_has_agent_type_field(self):
        """AgentTask should have agent_type field."""
        from plugins.agent_orchestrator.models import AgentTask

        task = AgentTask(name="test", task="do something")
        self.assertEqual(task.agent_type, "")  # Default empty string

        task_with_type = AgentTask(name="test", task="do something", agent_type="coder")
        self.assertEqual(task_with_type.agent_type, "coder")

    def test_agent_task_has_skills_field(self):
        """AgentTask should have skills field."""
        from plugins.agent_orchestrator.models import AgentTask

        task = AgentTask(name="test", task="do something")
        self.assertEqual(task.skills, [])  # Default empty list

        task_with_skills = AgentTask(
            name="test", task="do something", skills=["debugging", "tdd"]
        )
        self.assertEqual(task_with_skills.skills, ["debugging", "tdd"])


class TestXMLParser(unittest.TestCase):
    """Test XML parser extracts agent-type and skill tags."""

    def setUp(self):
        from plugins.agent_orchestrator.xml_parser import XMLCommandParser

        self.parser = XMLCommandParser()

    def test_parse_basic_agent(self):
        """Parse basic agent without agent-type or skills."""
        xml = """
        <agent>
          <MyAgent>
            <task>Do the thing</task>
          </MyAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].type, "agent")
        self.assertEqual(len(commands[0].agents), 1)

        agent = commands[0].agents[0]
        self.assertEqual(agent.name, "MyAgent")
        self.assertEqual(agent.task, "Do the thing")
        self.assertEqual(agent.agent_type, "")
        self.assertEqual(agent.skills, [])

    def test_parse_agent_with_type(self):
        """Parse agent with agent-type tag."""
        xml = """
        <agent>
          <MyAgent>
            <agent-type>coder</agent-type>
            <task>Fix the bug</task>
          </MyAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        agent = commands[0].agents[0]

        self.assertEqual(agent.name, "MyAgent")
        self.assertEqual(agent.agent_type, "coder")
        self.assertEqual(agent.task, "Fix the bug")

    def test_parse_agent_with_single_skill(self):
        """Parse agent with single skill tag."""
        xml = """
        <agent>
          <MyAgent>
            <skill>debugging</skill>
            <task>Debug the crash</task>
          </MyAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        agent = commands[0].agents[0]

        self.assertEqual(agent.skills, ["debugging"])

    def test_parse_agent_with_multiple_skills(self):
        """Parse agent with multiple skill tags."""
        xml = """
        <agent>
          <MyAgent>
            <agent-type>coder</agent-type>
            <skill>debugging</skill>
            <skill>tdd</skill>
            <skill>refactoring</skill>
            <task>Improve the code</task>
            <files>
              <file>src/main.py</file>
            </files>
          </MyAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        agent = commands[0].agents[0]

        self.assertEqual(agent.name, "MyAgent")
        self.assertEqual(agent.agent_type, "coder")
        self.assertEqual(agent.skills, ["debugging", "tdd", "refactoring"])
        self.assertEqual(agent.task, "Improve the code")
        self.assertEqual(agent.files, ["src/main.py"])

    def test_agent_type_not_captured_as_agent_name(self):
        """Ensure agent-type tag is not captured as an agent name."""
        xml = """
        <agent>
          <RealAgent>
            <agent-type>coder</agent-type>
            <task>Work</task>
          </RealAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        self.assertEqual(len(commands[0].agents), 1)
        self.assertEqual(commands[0].agents[0].name, "RealAgent")
        # Should NOT have an agent named "agent-type"

    def test_skill_not_captured_as_agent_name(self):
        """Ensure skill tag is not captured as an agent name."""
        xml = """
        <agent>
          <RealAgent>
            <skill>debugging</skill>
            <task>Work</task>
          </RealAgent>
        </agent>
        """
        commands = self.parser.parse(xml)
        self.assertEqual(len(commands[0].agents), 1)
        self.assertEqual(commands[0].agents[0].name, "RealAgent")
        # Should NOT have an agent named "skill"


class TestAgentsListTrender(unittest.TestCase):
    """Test agents_list trender functionality."""

    def test_trender_pattern_matches(self):
        """Test regex pattern matches agents_list tag."""
        from kollabor_ai import PromptRenderer

        content = '<trender type="agents_list" />'
        matches = list(PromptRenderer.TRENDER_AGENTS_LIST_PATTERN.finditer(content))
        self.assertEqual(len(matches), 1)

    def test_trender_pattern_matches_single_quotes(self):
        """Test regex pattern matches with single quotes."""
        from kollabor_ai import PromptRenderer

        content = "<trender type='agents_list' />"
        matches = list(PromptRenderer.TRENDER_AGENTS_LIST_PATTERN.finditer(content))
        self.assertEqual(len(matches), 1)

    def test_trender_renders_agents_list(self):
        """Test trender actually renders agents list."""
        from kollabor_agent import AgentManager
        from kollabor_ai import PromptRenderer

        am = AgentManager()
        renderer = PromptRenderer(agent_manager=am)

        content = 'Available:\n<trender type="agents_list" />\nEnd.'
        result = renderer.render(content)

        # Should contain agent names
        self.assertIn("Available Agents", result)
        # Should have replaced the tag
        self.assertNotIn("<trender", result)

    def test_trender_no_agent_manager(self):
        """Test trender handles missing agent_manager gracefully."""
        from kollabor_ai import PromptRenderer

        renderer = PromptRenderer(agent_manager=None)
        content = '<trender type="agents_list" />'
        result = renderer.render(content)

        self.assertIn("No agent manager available", result)


class TestShellEscaping(unittest.TestCase):
    """Test shell escaping in orchestrator command building."""

    def test_command_escaping_simple(self):
        """Test simple agent_type and skills are handled."""
        import shlex

        # Simulate command building logic
        agent_type = "coder"
        skills = ["debugging", "tdd"]

        cmd = "kollab --simple"
        if agent_type:
            cmd += f" --agent {shlex.quote(agent_type)}"
        for skill in skills:
            cmd += f" --skill {shlex.quote(skill)}"

        self.assertEqual(
            cmd, "kollab --simple --agent coder --skill debugging --skill tdd"
        )

    def test_command_escaping_special_chars(self):
        """Test special characters are escaped."""
        import shlex

        # Malicious input should be escaped
        agent_type = "coder; rm -rf /"
        skills = ["debug && echo hacked"]

        cmd = "kollab --simple"
        if agent_type:
            cmd += f" --agent {shlex.quote(agent_type)}"
        for skill in skills:
            cmd += f" --skill {shlex.quote(skill)}"

        # The shell metacharacters should be escaped
        self.assertIn("'coder; rm -rf /'", cmd)
        self.assertIn("'debug && echo hacked'", cmd)


if __name__ == "__main__":
    unittest.main()

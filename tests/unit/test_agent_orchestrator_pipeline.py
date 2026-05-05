"""Unit tests for agent_orchestrator pipeline tag handlers.

Tests the async handler methods migrated to the pipeline architecture
in phase 3:
  - _handle_status_tool
  - _handle_capture_tool
  - _handle_stop_tool
  - _handle_message_tool
  - _handle_broadcast_tool
  - _handle_clone_tool
  - _handle_team_tool
  - _handle_agent_tool

Also tests _parse_agent_defs_from_text helper and the regex patterns
used in extract_fn callbacks.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.agent_orchestrator.models import AgentTask


def _make_plugin():
    """Create a minimal AgentOrchestratorPlugin with handler methods.

    Avoids full instantiation (heavy deps) by building a lightweight
    mock that has the same attributes the handlers read.
    """
    from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin

    plugin = object.__new__(AgentOrchestratorPlugin)

    # Mock orchestrator
    plugin.orchestrator = MagicMock()

    # Mock activity monitor
    plugin.activity_monitor = MagicMock()
    plugin.activity_monitor.track = MagicMock()
    plugin.activity_monitor.untrack = MagicMock()
    plugin.activity_monitor.reset_agent_state = MagicMock()

    # Mock message injector
    plugin.message_injector = None

    # Mock event bus (async for emit_with_hooks)
    plugin.event_bus = MagicMock()
    plugin.event_bus.emit_with_hooks = AsyncMock()

    # Mock config
    plugin.config = None

    # Mock renderer
    plugin.renderer = None

    return plugin


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ================================================================== #
#  _parse_agent_defs_from_text
# ================================================================== #


class TestParseAgentDefs(unittest.TestCase):
    """Tests for _parse_agent_defs_from_text helper."""

    def setUp(self):
        self.plugin = _make_plugin()

    def test_single_agent(self):
        block = "<my-worker><task>fix the bug</task></my-worker>"
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].name, "my-worker")
        self.assertEqual(agents[0].task, "fix the bug")

    def test_multiple_agents(self):
        block = """
<worker-a><task>task a</task></worker-a>
<worker-b><task>task b</task></worker-b>
"""
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 2)
        self.assertEqual(agents[0].name, "worker-a")
        self.assertEqual(agents[1].name, "worker-b")

    def test_agent_with_files(self):
        block = """
<coder><task>fix foo</task><files><file>foo.py</file><file>bar.py</file></files></coder>
"""
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].files, ["foo.py", "bar.py"])

    def test_agent_with_type(self):
        block = """
<myagent><task>do stuff</task><agent-type>coder</agent-type></myagent>
"""
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].agent_type, "coder")

    def test_agent_with_skills(self):
        block = """
<myagent><task>do stuff</task><skill>debugging</skill><skill>tdd</skill></myagent>
"""
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].skills, ["debugging", "tdd"])

    def test_empty_block(self):
        agents = self.plugin._parse_agent_defs_from_text("")
        self.assertEqual(len(agents), 0)

    def test_reserved_tags_excluded(self):
        # <task>, <files>, <file>, <todo> etc should NOT be parsed as agent names
        block = "<task>do this</task>"
        agents = self.plugin._parse_agent_defs_from_text(block)
        self.assertEqual(len(agents), 0)


# ================================================================== #
#  _handle_status_tool
# ================================================================== #


class TestHandleStatusTool(unittest.TestCase):
    """Tests for _handle_status_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.list_agents = MagicMock(return_value=[])

    def test_no_agents(self):
        result = _run(self.plugin._handle_status_tool({"id": "t1"}))
        self.assertTrue(result.success)
        self.assertIn("none active", result.output)

    def test_with_agents(self):
        mock_agent = MagicMock()
        mock_agent.name = "worker-1"
        mock_agent.status = "running"
        mock_agent.duration = "5m00s"
        self.plugin.orchestrator.list_agents.return_value = [mock_agent]

        result = _run(self.plugin._handle_status_tool({"id": "t1"}))
        self.assertTrue(result.success)
        self.assertIn("worker-1", result.output)
        self.assertIn("running", result.output)


# ================================================================== #
#  _handle_capture_tool
# ================================================================== #


class TestHandleCaptureTool(unittest.TestCase):
    """Tests for _handle_capture_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.capture_output = MagicMock(return_value="line1\nline2")
        mock_agent = MagicMock()
        mock_agent.duration = "2m30s"
        self.plugin.orchestrator.get_agent = MagicMock(return_value=mock_agent)

    def test_normal_capture(self):
        result = _run(self.plugin._handle_capture_tool({
            "id": "t1", "target": "worker-1", "lines": 50,
        }))
        self.assertTrue(result.success)
        self.assertIn("worker-1", result.output)

    def test_no_target(self):
        result = _run(self.plugin._handle_capture_tool({
            "id": "t1", "target": "", "lines": 50,
        }))
        self.assertFalse(result.success)
        self.assertIn("no target", result.error)


# ================================================================== #
#  _handle_stop_tool
# ================================================================== #


class TestHandleStopTool(unittest.TestCase):
    """Tests for _handle_stop_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.stop = AsyncMock(return_value=("output", "3m00s"))

    def test_stop_single(self):
        result = _run(self.plugin._handle_stop_tool({
            "id": "t1", "targets": ["worker-1"],
        }))
        self.assertTrue(result.success)
        self.assertIn("stopped", result.output)

    def test_stop_multiple(self):
        result = _run(self.plugin._handle_stop_tool({
            "id": "t1", "targets": ["worker-1", "worker-2"],
        }))
        self.assertTrue(result.success)

    def test_no_targets(self):
        result = _run(self.plugin._handle_stop_tool({
            "id": "t1", "targets": [],
        }))
        self.assertFalse(result.success)
        self.assertIn("no targets", result.error)


# ================================================================== #
#  _handle_message_tool
# ================================================================== #


class TestHandleMessageTool(unittest.TestCase):
    """Tests for _handle_message_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.message = AsyncMock(return_value=True)

    def test_message_sent(self):
        result = _run(self.plugin._handle_message_tool({
            "id": "t1", "target": "worker-1", "content": "hello",
        }))
        self.assertTrue(result.success)
        self.assertIn("message sent", result.output.lower())

    def test_no_target(self):
        result = _run(self.plugin._handle_message_tool({
            "id": "t1", "target": "", "content": "hello",
        }))
        self.assertFalse(result.success)

    def test_agent_not_found(self):
        self.plugin.orchestrator.message = AsyncMock(return_value=False)
        result = _run(self.plugin._handle_message_tool({
            "id": "t1", "target": "nonexistent", "content": "hello",
        }))
        self.assertTrue(result.success)
        self.assertIn("not found", result.output)


# ================================================================== #
#  _handle_broadcast_tool
# ================================================================== #


class TestHandleBroadcastTool(unittest.TestCase):
    """Tests for _handle_broadcast_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.find_agents = MagicMock(return_value=["w1", "w2"])
        self.plugin.orchestrator.message = AsyncMock(return_value=True)

    def test_broadcast(self):
        result = _run(self.plugin._handle_broadcast_tool({
            "id": "t1", "pattern": "worker-*", "content": "hello team",
        }))
        self.assertTrue(result.success)
        self.assertIn("2 agents", result.output)

    def test_no_pattern(self):
        result = _run(self.plugin._handle_broadcast_tool({
            "id": "t1", "pattern": "", "content": "hello",
        }))
        self.assertFalse(result.success)


# ================================================================== #
#  _handle_clone_tool
# ================================================================== #


class TestHandleCloneTool(unittest.TestCase):
    """Tests for _handle_clone_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin._export_conversation = AsyncMock(return_value="/tmp/conv.json")
        self.plugin.orchestrator.spawn_clone = AsyncMock(return_value=True)

    def test_clone_success(self):
        result = _run(self.plugin._handle_clone_tool({
            "id": "t1",
            "agent_name": "worker-clone",
            "task": "continue work",
            "files": [],
            "agent_type": "",
            "skills": [],
        }))
        self.assertTrue(result.success)
        self.assertIn("cloned", result.output)

    def test_no_name(self):
        result = _run(self.plugin._handle_clone_tool({
            "id": "t1",
            "agent_name": "",
            "task": "",
            "files": [],
            "agent_type": "",
            "skills": [],
        }))
        self.assertFalse(result.success)


# ================================================================== #
#  _handle_team_tool
# ================================================================== #


class TestHandleTeamTool(unittest.TestCase):
    """Tests for _handle_team_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.spawn_team_lead = AsyncMock(return_value=True)

    def test_team_spawn(self):
        result = _run(self.plugin._handle_team_tool({
            "id": "t1",
            "lead": "lead-agent",
            "workers": 3,
            "agents": [{"name": "lead-agent", "task": "coordinate", "files": [], "agent_type": "", "skills": []}],
        }))
        self.assertTrue(result.success)
        self.assertIn("team spawned", result.output)

    def test_team_spawn_failure(self):
        self.plugin.orchestrator.spawn_team_lead = AsyncMock(return_value=False)
        result = _run(self.plugin._handle_team_tool({
            "id": "t1",
            "lead": "lead-agent",
            "workers": 3,
            "agents": [{"name": "lead-agent", "task": "coordinate", "files": [], "agent_type": "", "skills": []}],
        }))
        self.assertFalse(result.success)


# ================================================================== #
#  _handle_agent_tool
# ================================================================== #


class TestHandleAgentTool(unittest.TestCase):
    """Tests for _handle_agent_tool handler."""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin.orchestrator.spawn = AsyncMock(return_value=True)

    def test_spawn_single(self):
        result = _run(self.plugin._handle_agent_tool({
            "id": "t1",
            "agents": [{"name": "worker-1", "task": "fix bugs", "files": [], "agent_type": "", "skills": []}],
        }))
        self.assertTrue(result.success)
        self.assertIn("spawned", result.output)
        self.plugin.activity_monitor.track.assert_called_once_with("worker-1")

    def test_spawn_multiple(self):
        result = _run(self.plugin._handle_agent_tool({
            "id": "t1",
            "agents": [
                {"name": "w1", "task": "fix", "files": [], "agent_type": "", "skills": []},
                {"name": "w2", "task": "build", "files": [], "agent_type": "", "skills": []},
            ],
        }))
        self.assertTrue(result.success)
        self.assertIn("w1", result.output)
        self.assertIn("w2", result.output)

    def test_no_agents(self):
        result = _run(self.plugin._handle_agent_tool({
            "id": "t1",
            "agents": [],
        }))
        self.assertFalse(result.success)
        self.assertIn("no agents", result.error)

    def test_spawn_failure(self):
        self.plugin.orchestrator.spawn = AsyncMock(return_value=False)
        result = _run(self.plugin._handle_agent_tool({
            "id": "t1",
            "agents": [{"name": "bad", "task": "fail", "files": [], "agent_type": "", "skills": []}],
        }))
        self.assertFalse(result.success)


# ================================================================== #
#  Regex pattern tests (extract_fn patterns)
# ================================================================== #


class TestRegexPatterns(unittest.TestCase):
    """Test the regex patterns used in register_plugin_tag extract_fns."""

    def test_status_empty_tag(self):
        import re
        pat = re.compile(r"<status>\s*</status>", re.IGNORECASE)
        self.assertTrue(pat.search("<status></status>"))
        self.assertTrue(pat.search("<status>  </status>"))
        # Should NOT match prose
        self.assertFalse(pat.search("check the status of things"))
        # Should NOT match opening tag alone
        self.assertFalse(pat.search("<status>"))

    def test_capture_pattern(self):
        import re
        pat = re.compile(r"<capture>(.*?)</capture>", re.DOTALL | re.IGNORECASE)
        m = pat.search("<capture>worker-1 50</capture>")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "worker-1 50")

    def test_stop_pattern(self):
        import re
        pat = re.compile(r"<stop>(.*?)</stop>", re.DOTALL | re.IGNORECASE)
        m = pat.search("<stop>worker-1, worker-2</stop>")
        self.assertIsNotNone(m)
        self.assertIn("worker-1", m.group(1))

    def test_message_pattern(self):
        import re
        pat = re.compile(
            r'<message\s+to=["\']([^"\']+)["\']>(.*?)</message>',
            re.DOTALL | re.IGNORECASE,
        )
        m = pat.search('<message to="worker-1">hello there</message>')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "worker-1")
        self.assertEqual(m.group(2).strip(), "hello there")

    def test_broadcast_pattern(self):
        import re
        pat = re.compile(
            r'<broadcast\s+to=["\']([^"\']+)["\']>(.*?)</broadcast>',
            re.DOTALL | re.IGNORECASE,
        )
        m = pat.search('<broadcast to="workers">hello team</broadcast>')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "workers")

    def test_agent_pattern(self):
        import re
        pat = re.compile(r"<agent>(.*?)</agent>", re.DOTALL | re.IGNORECASE)
        m = pat.search("<agent><myworker><task>do stuff</task></myworker></agent>")
        self.assertIsNotNone(m)
        self.assertIn("myworker", m.group(1))

    def test_clone_pattern(self):
        import re
        pat = re.compile(r"<clone>(.*?)</clone>", re.DOTALL | re.IGNORECASE)
        m = pat.search("<clone><myagent><task>clone task</task></myagent></clone>")
        self.assertIsNotNone(m)

    def test_team_pattern(self):
        import re
        pat = re.compile(
            r'<team\s+lead=["\']([^"\']+)["\']\s+workers=["\'](\d+)["\']>(.*?)</team>',
            re.DOTALL | re.IGNORECASE,
        )
        m = pat.search('<team lead="boss" workers="3">do work</team>')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "boss")
        self.assertEqual(m.group(2), "3")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for AgentOrchestrator._create_session cmd construction.

Regression: hub_spawn used _send_keys to write the initial task to
proc.stdin, but --detached spawned children dup2 their stdin to /dev/null
right after fork. The bytes went into a pipe nobody read. Fix delivers
the task as a positional arg in the kollab cmd instead.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.agent_orchestrator.orchestrator import AgentOrchestrator


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestCreateSessionInitialTask(unittest.TestCase):
    def setUp(self):
        self.orch = object.__new__(AgentOrchestrator)
        self.orch.agents = {}
        self.orch.project_name = "test-proj"
        self.orch.session_init_delay = 0.0

    def _make_proc_mock(self):
        proc = MagicMock()
        proc.pid = 12345
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline = MagicMock(return_value=b"")
        return proc

    def test_find_command_prefers_same_environment_script(self):
        """Installed tools should not fall through to pyenv/homebrew shims."""

        def fake_exists(path):
            text = str(path)
            if text.endswith("/main.py"):
                return False
            return text == "/tmp/kollab-tool/bin/kollab"

        with (
            patch(
                "plugins.agent_orchestrator.orchestrator.sys.executable",
                "/tmp/kollab-tool/bin/python",
            ),
            patch(
                "plugins.agent_orchestrator.orchestrator.Path.exists",
                fake_exists,
            ),
        ):
            self.assertEqual(
                self.orch._find_kollab_command(),
                ["/tmp/kollab-tool/bin/kollab"],
            )

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("plugins.agent_orchestrator.orchestrator.subprocess.Popen")
    def test_spawn_cmd_uses_real_kollab_entrypoint(self, mock_popen, mock_thread):
        """Child agents must launch Kollab, not cwd/main.py."""
        mock_popen.return_value = self._make_proc_mock()

        with TemporaryDirectory() as project_dir:
            with patch("plugins.agent_orchestrator.orchestrator.Path.cwd") as mock_cwd:
                mock_cwd.return_value = Path(project_dir)
                _run(
                    self.orch._create_session(
                        full_name="test-proj-lapis",
                        agent_name="lapis",
                        agent_type="coder",
                        identity="lapis",
                        initial_task="review the project",
                    )
                )

        cmd = mock_popen.call_args.args[0]
        self.assertNotEqual(cmd[:2], ["python3", "main.py"])
        if len(cmd) > 1 and str(cmd[1]).endswith("main.py"):
            self.assertTrue(Path(cmd[1]).is_absolute())
        self.assertIn("--detached", cmd)
        self.assertEqual(mock_popen.call_args.kwargs["cwd"], project_dir)

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("plugins.agent_orchestrator.orchestrator.subprocess.Popen")
    def test_initial_task_appended_with_separator(self, mock_popen, mock_thread):
        mock_popen.return_value = self._make_proc_mock()

        _run(
            self.orch._create_session(
                full_name="test-proj-lapis",
                agent_name="lapis",
                agent_type="coder",
                identity="lapis",
                initial_task="echo smoke test",
            )
        )

        self.assertTrue(mock_popen.called)
        cmd = mock_popen.call_args.args[0]
        # The "--" separator must come before the task to prevent argparse
        # from interpreting tasks that start with "-" as flags.
        self.assertIn("--", cmd)
        sep_idx = cmd.index("--")
        self.assertEqual(cmd[sep_idx + 1], "echo smoke test")

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("plugins.agent_orchestrator.orchestrator.subprocess.Popen")
    def test_no_initial_task_means_no_separator(self, mock_popen, mock_thread):
        mock_popen.return_value = self._make_proc_mock()

        _run(
            self.orch._create_session(
                full_name="test-proj-lapis",
                agent_name="lapis",
                agent_type="coder",
                identity="lapis",
                initial_task="",
            )
        )

        self.assertTrue(mock_popen.called)
        cmd = mock_popen.call_args.args[0]
        self.assertNotIn("--", cmd)

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("plugins.agent_orchestrator.orchestrator.subprocess.Popen")
    def test_dash_prefixed_task_survives_argparse(self, mock_popen, mock_thread):
        """Tasks starting with - must reach the child as a single positional."""
        mock_popen.return_value = self._make_proc_mock()

        _run(
            self.orch._create_session(
                full_name="test-proj-lapis",
                agent_name="lapis",
                initial_task="-not-a-flag really",
            )
        )

        cmd = mock_popen.call_args.args[0]
        sep_idx = cmd.index("--")
        self.assertEqual(cmd[sep_idx + 1], "-not-a-flag really")


if __name__ == "__main__":
    unittest.main()

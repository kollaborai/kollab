"""Unit tests for AgentOrchestrator._create_session cmd construction.

Regression: hub_spawn used _send_keys to write the initial task to
proc.stdin, but detached agents have stdin directed to /dev/null. The task
must be delivered as a positional arg. The orchestrator also starts that
detached child directly so its Popen PID is the PID that writes presence.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor.binary import kollab_argv
from kollabor.daemon import DETACHED_CHILD_ENV
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

    def test_find_command_is_this_kollab_not_the_first_on_path(self):
        """A child agent runs the code that spawned it, never another install's shim."""
        with patch("shutil.which", return_value="/usr/local/bin/kollab"):
            self.assertEqual(self.orch._find_kollab_command(), kollab_argv())

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("kollabor_config.proc_ctl.start_child")
    def test_spawn_cmd_uses_real_kollab_entrypoint(self, mock_popen, mock_thread):
        """Child agents must launch Kollab, not cwd/main.py."""
        mock_popen.return_value = self._make_proc_mock()

        with TemporaryDirectory() as project_dir:
            with patch("plugins.agent_orchestrator.orchestrator.Path.cwd") as mock_cwd:
                mock_cwd.return_value = Path(project_dir)
                _run(
                    self.orch._setup_session(
                        name="lapis",
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
        self.assertTrue(mock_popen.call_args.kwargs["detached"])
        self.assertEqual(mock_popen.call_args.kwargs["env"][DETACHED_CHILD_ENV], "1")
        # The tracked session uses this Popen directly, with no short-lived
        # CLI parent that forks into an untracked child.
        session = next(
            agent
            for agent in self.orch.agents.values()
            if agent.proc is mock_popen.return_value
        )
        self.assertEqual(session.pid, mock_popen.return_value.pid)
        permissions_idx = cmd.index("--permissions")
        self.assertEqual(cmd[permissions_idx + 1], "trust")
        self.assertEqual(mock_popen.call_args.kwargs["cwd"], project_dir)

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("kollabor_config.proc_ctl.start_child")
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
    @patch("kollabor_config.proc_ctl.start_child")
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
    @patch("kollabor_config.proc_ctl.start_child")
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

    @patch("plugins.agent_orchestrator.orchestrator.threading.Thread")
    @patch("kollabor_config.proc_ctl.start_child")
    def test_acp_role_spawn_uses_resolved_identity_without_bundle_flag(
        self, mock_popen, mock_thread
    ):
        """An ACP child launches as its assigned persona, not its bundle role."""
        mock_popen.return_value = self._make_proc_mock()

        _run(
            self.orch._create_session(
                full_name="test-proj-jasper",
                agent_name="coder",
                identity="jasper",
                profile="codex-cli",
                harness=True,
            )
        )

        cmd = mock_popen.call_args.args[0]
        self.assertIn("--llm", cmd)
        self.assertEqual(cmd[cmd.index("--llm") + 1], "codex-cli")
        self.assertIn("--as", cmd)
        self.assertEqual(cmd[cmd.index("--as") + 1], "jasper")
        self.assertNotIn("--agent", cmd)


if __name__ == "__main__":
    unittest.main()

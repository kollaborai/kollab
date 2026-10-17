"""Hub agents must survive an engine bounce (issue: P0-c engine-restart survival).

Incident 2026-10-16 20:46: a user request routed to the engine made the engine's
own conversation run ``pkill -f "--detached --as"``. Every hub agent on both
hubs matches that command-line substring, so all of them died at once. The
engine bounce alone should never have taken agents down either, because the
orchestrator armed each spawned agent's parent watchdog with
``KOLLAB_PARENT_PID=<engine pid>``: when the engine process exited, every agent
it had spawned shut itself down within ~10s.

Three couplings are fixed:

1. orchestrator no longer arms the child's parent watchdog for hub agents
   (KOLLAB_PARENT_PID / KOLLAB_PARENT_CREATE_TIME are stripped from spawn env).
2. _kill_session is documented as the only sanctioned kill path (no loose
   ``pkill -f`` sweeps over shared flag substrings).
3. attach-stream heartbeat failures inside a dying default executor no longer
   tear down a live attach stream (messenger hardening).

This module covers (1) and the spawn cmd shape; the attach-stream regression
is covered in test_attach_stream_survives_executor_death.py.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.agent_orchestrator.orchestrator import AgentOrchestrator


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSpawnEnvStripsParentIdentity(unittest.TestCase):
    def setUp(self):
        self.orch = object.__new__(AgentOrchestrator)
        self.orch.agents = {}
        self.orch.project_name = "test-proj"
        self.orch.session_init_delay = 0.0

    def _spawn(self, extra_env):
        """Run _create_session with a fake Popen and return the child env."""
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = dict(kwargs["env"])
            proc = MagicMock()
            proc.pid = 42424
            proc.stdin = MagicMock()
            proc.stdout = MagicMock()
            proc.stdout.readline = MagicMock(return_value=b"")
            return proc

        base_env = dict(os.environ)
        base_env["KOLLAB_PARENT_PID"] = "999"
        base_env["KOLLAB_PARENT_CREATE_TIME"] = "12345.6"
        # Control the root socket explicitly: the outer kollab process may
        # already export it, and spawn propagates it verbatim.
        base_env["KOLLAB_ROOT_SOCKET"] = "test-proj"
        with TemporaryDirectory() as tmp:
            with (
                patch(
                    "plugins.agent_orchestrator.orchestrator.subprocess.Popen",
                    side_effect=fake_popen,
                ),
                patch(
                    "plugins.agent_orchestrator.orchestrator.os.environ",
                    base_env,
                ),
                patch(
                    "plugins.agent_orchestrator.orchestrator.Path.cwd",
                    return_value=Path(tmp),
                ),
                patch(
                    "plugins.agent_orchestrator.orchestrator.AgentOrchestrator._find_kollab_command",
                    return_value=["kollab"],
                ),
            ):
                _run(self.orch._create_session("tester", "tester-id", ""))

        return captured

    def test_spawn_strips_parent_pid_from_child_env(self):
        """A hub agent spawn must NOT arm the child's parent watchdog.

        Regression: spawn used to set KOLLAB_PARENT_PID=<engine pid>, which
        made every spawned agent self-shutdown when the engine process died.
        Hub agents own their lifecycle; the engine must be restartable
        without taking the fleet down.
        """
        captured = self._spawn({})
        env = captured["env"]
        self.assertNotIn("KOLLAB_PARENT_PID", env)
        self.assertNotIn("KOLLAB_PARENT_CREATE_TIME", env)

    def test_spawn_strips_inherited_parent_create_time(self):
        """A stale KOLLAB_PARENT_CREATE_TIME must not leak into children either."""
        captured = self._spawn({})
        self.assertNotIn("KOLLAB_PARENT_CREATE_TIME", captured["env"])

    def test_spawn_still_sets_root_socket_and_agent_name(self):
        """Stripping parent identity must not remove legit spawn wiring."""
        captured = self._spawn({})
        env = captured["env"]
        self.assertEqual(env.get("KOLLAB_AGENT_NAME"), "tester-id")
        # Spawn propagates the ambient root socket (already "test-proj" in
        # the base env here) rather than inventing its own.
        self.assertEqual(env.get("KOLLAB_ROOT_SOCKET"), "test-proj")

    def test_spawn_cmd_keeps_detached_flag(self):
        """The daemon shape itself is unchanged: still --detached."""
        captured = self._spawn({})
        self.assertIn("--detached", captured["cmd"])


if __name__ == "__main__":
    unittest.main()

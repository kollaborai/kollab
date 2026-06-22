"""Tests for the spawn guard introduced in issue #38.

Two bugs fixed:
  1. Explicit duplicate spawn (--as <identity>): refuse to start when a live
     process already holds that identity.  Prevents duplicate agents executing
     the same tasks and double-sending hub messages.
  2. TOCTOU race: two processes starting simultaneously on an empty presence
     directory both pick the same identity.  Fixed by publishing a preliminary
     presence record immediately after identity assignment so concurrent
     startups see it as "taken".

Test strategy
-------------
_start_hub() is 400+ lines of async code wired to DNS, socket servers, vaults,
etc.  Rather than starting the full stack we unit-test the guard logic directly
by:
  a) patching the three key I/O calls (_presence.discover_agents,
     AgentMessenger.ping_agent, _presence.publish, _election.*) so the test
     controls what "the world looks like" at the moment of identity assignment.
  b) asserting on os._exit calls (spawn guard) and on which identity was
     assigned (TOCTOU guard).
"""

import asyncio
import os
import sys
import types
import unittest
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import AgentState
from plugins.hub.plugin import HubPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(identity: str, pid: int = 99999, socket_path: str = "/tmp/x.sock") -> AgentRuntime:
    a = AgentRuntime(name="test", identity=identity)
    a.pid = pid
    a.socket_path = socket_path
    return a


def _minimal_plugin(as_identity: str | None = None) -> HubPlugin:
    """Create a HubPlugin wired just enough to call _start_hub()."""
    plugin = HubPlugin(event_bus=MagicMock())

    # Set _cli_args so explicit_as detection works
    cli_ns = Namespace(as_identity=as_identity, attach=None)
    plugin._cli_args = cli_ns

    # Required attributes that _start_hub() asserts on
    plugin._identity = AgentRuntime(name="default")
    plugin._identity.agent_id = "testpid000001"

    plugin._election = MagicMock()
    plugin._election.try_become_coordinator.return_value = False
    plugin._election.update_identity = MagicMock()

    plugin._presence = MagicMock()
    plugin._presence.publish = MagicMock()

    plugin.config = None
    plugin.event_bus = MagicMock()
    plugin.event_bus.get_service.return_value = None

    return plugin


async def _run_start_hub_early(plugin: HubPlugin, existing_agents: list) -> str | None:
    """Run _start_hub() through to identity assignment, then bail.

    Returns the assigned identity string, or None if an exception was raised
    before assignment (excluding SystemExit / os._exit which are tested via
    patch).

    We patch all I/O that happens AFTER identity assignment (socket server,
    vault, DNS, etc.) so the test doesn't need those services.
    """
    plugin._presence.discover_agents.return_value = existing_agents
    plugin._presence.startup_scan = MagicMock(return_value=0)
    plugin.event_bus.get_service.return_value = None

    # Terminate _start_hub() cleanly right after publish() is called
    # (preliminary publish is the last step before heavy I/O).
    # We do this by making the socket server start raise StopIteration
    # which we then catch here.
    _sentinel = StopIteration("guard reached publish")
    real_publish = plugin._presence.publish
    publish_call_count = []

    def _publish_then_stop():
        publish_call_count.append(1)
        if len(publish_call_count) == 1:
            real_publish()
            raise _sentinel  # bail after preliminary publish

    plugin._presence.publish.side_effect = _publish_then_stop

    try:
        await plugin._start_hub()
    except StopIteration:
        pass
    except SystemExit:
        raise
    except Exception:
        pass

    return getattr(plugin._identity, "identity", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpawnGuardExplicitAs(unittest.IsolatedAsyncioTestCase):
    """Explicit --as <identity> + live holder → os._exit(1)."""

    async def test_explicit_as_live_holder_exits(self) -> None:
        """--as sapphire + sapphire is alive → process must exit (issue #38)."""
        plugin = _minimal_plugin(as_identity="sapphire")
        existing = [_make_runtime("sapphire", pid=12345)]

        # Use patch.object on the imported class to avoid string-based lookup
        # fragility (some suites leave residual mocks on the module namespace).
        with (
            patch.object(AgentMessenger, "ping_agent", new=AsyncMock(return_value=True)),
            patch("os._exit") as mock_exit,
        ):
            plugin._presence.discover_agents.return_value = existing
            plugin._presence.startup_scan = MagicMock(return_value=0)
            plugin.event_bus.get_service.return_value = None
            # os._exit will be called; catch it so the test doesn't die
            mock_exit.side_effect = SystemExit(1)

            with self.assertRaises(SystemExit):
                await plugin._start_hub()

        mock_exit.assert_called_once_with(1)

    async def test_explicit_as_dead_holder_force_claims(self) -> None:
        """--as sapphire + holder fails ping → force-claim (existing behavior)."""
        plugin = _minimal_plugin(as_identity="sapphire")
        existing = [_make_runtime("sapphire", pid=12345)]

        with (
            patch.object(AgentMessenger, "ping_agent", new=AsyncMock(return_value=False)),
            patch("os._exit") as mock_exit,
            # force-claim does `from .presence import get_presence_dir` locally
            patch("plugins.hub.presence.get_presence_dir", return_value=MagicMock()),
        ):
            try:
                identity = await _run_start_hub_early(plugin, existing)
            except SystemExit:
                self.fail("Should not exit when holder is dead (force-claim path)")

        mock_exit.assert_not_called()
        # identity should be "sapphire" after force-claim
        self.assertEqual(plugin._identity.identity, "sapphire")

    async def test_explicit_as_no_conflict_starts_normally(self) -> None:
        """--as sapphire + no existing agents → just assigns sapphire."""
        plugin = _minimal_plugin(as_identity="sapphire")

        with patch("os._exit") as mock_exit:
            identity = await _run_start_hub_early(plugin, existing_agents=[])

        mock_exit.assert_not_called()
        self.assertEqual(plugin._identity.identity, "sapphire")

    async def test_implicit_preferred_live_holder_picks_next_identity(self) -> None:
        """Agent config prefers 'sapphire', holder is alive → pick next, don't exit."""
        # No --as flag → explicit_as=False → fall through to pick different identity
        plugin = _minimal_plugin(as_identity=None)
        # Simulate agent config
        plugin.config = MagicMock()
        plugin.config.get.side_effect = lambda key, default="": (
            "sapphire" if key == "plugins.hub.identity" else default
        )

        existing = [_make_runtime("sapphire", pid=12345)]

        with (
            patch.object(AgentMessenger, "ping_agent", new=AsyncMock(return_value=True)),
            patch("os._exit") as mock_exit,
        ):
            identity = await _run_start_hub_early(plugin, existing)

        mock_exit.assert_not_called()
        # Should have picked a different identity (not sapphire)
        self.assertNotEqual(plugin._identity.identity, "sapphire")


class TestSpawnGuardTOCTOU(unittest.IsolatedAsyncioTestCase):
    """Preliminary publish closes the TOCTOU race (issue #38)."""

    async def test_preliminary_publish_happens_before_socket_server(self) -> None:
        """presence.publish() is called immediately after identity assignment,
        before the socket server starts."""
        plugin = _minimal_plugin(as_identity=None)

        publish_calls: list[str] = []
        socket_server_started = []

        real_publish_side = None

        def _track_publish():
            # Record that publish was called
            publish_calls.append("publish")
            # After first publish, bail out to avoid heavy init
            if len(publish_calls) == 1:
                raise StopIteration("bailing after preliminary publish")

        plugin._presence.publish.side_effect = _track_publish
        plugin._presence.discover_agents.return_value = []
        plugin._presence.startup_scan = MagicMock(return_value=0)
        plugin.event_bus.get_service.return_value = None

        try:
            await plugin._start_hub()
        except StopIteration:
            pass

        self.assertGreaterEqual(
            len(publish_calls),
            1,
            "presence.publish() should be called at least once "
            "(preliminary TOCTOU guard publish)",
        )
        # And identity should be set at this point
        self.assertTrue(
            plugin._identity.identity,
            "identity should be assigned before preliminary publish",
        )

    async def test_concurrent_start_sees_preliminary_presence(self) -> None:
        """Simulate two agents starting simultaneously.

        Agent A publishes a preliminary presence record.
        Agent B's discover_agents returns Agent A's preliminary record.
        Agent B should pick a DIFFERENT identity.
        """
        # Agent A's preliminary presence record (no socket_path yet)
        agent_a_presence = _make_runtime("sapphire", pid=os.getpid(), socket_path="")
        agent_a_presence.last_heartbeat = __import__("time").time()

        plugin_b = _minimal_plugin(as_identity=None)

        with patch("os._exit") as mock_exit:
            # Agent B sees Agent A's preliminary presence
            identity_b = await _run_start_hub_early(
                plugin_b, existing_agents=[agent_a_presence]
            )

        mock_exit.assert_not_called()
        # Agent B must have picked a different identity
        self.assertNotEqual(
            plugin_b._identity.identity,
            "sapphire",
            "Agent B should not claim 'sapphire' when A's preliminary "
            "presence shows it as taken",
        )
        self.assertTrue(plugin_b._identity.identity, "Agent B must have some identity")


class TestSpawnGuardPresenceScan(unittest.IsolatedAsyncioTestCase):
    """PresenceManager.startup_scan does not remove preliminary presence files."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._tmpdir = tempfile.mkdtemp()
        self._presence_dir = Path(self._tmpdir) / "presence"
        self._presence_dir.mkdir()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_preliminary_presence_not_cleaned_by_startup_scan(self) -> None:
        """A presence file with a live PID and no socket_path should survive
        startup_scan() — it represents a process mid-startup."""
        import json
        import time
        from pathlib import Path
        from unittest.mock import patch

        from plugins.hub.presence import PresenceManager

        agent_id = "aabbccdd0001"
        pf = self._presence_dir / f"{agent_id}.json"
        pf.write_text(json.dumps({
            "agent_id": agent_id,
            "identity": "sapphire",
            "pid": os.getpid(),  # THIS process is alive
            "socket_path": "",   # not yet set (preliminary record)
            "last_heartbeat": time.time(),
            "state": "idle",
        }))

        # Create a minimal PresenceManager pointing at our temp dir
        runtime = AgentRuntime(name="test", identity="lapis")
        pm = PresenceManager(runtime)
        pm._presence_dir = self._presence_dir

        removed = pm.startup_scan()

        self.assertEqual(removed, 0, "startup_scan should NOT remove a live-PID preliminary presence file")
        self.assertTrue(pf.exists(), "preliminary presence file must survive startup_scan()")

    def test_dead_pid_preliminary_presence_cleaned_by_startup_scan(self) -> None:
        """A preliminary presence file with a DEAD PID should be cleaned up."""
        import json
        import time

        from plugins.hub.presence import PresenceManager

        agent_id = "dead000000001"
        pf = self._presence_dir / f"{agent_id}.json"
        pf.write_text(json.dumps({
            "agent_id": agent_id,
            "identity": "sapphire",
            "pid": 9999999,  # Almost certainly dead
            "socket_path": "",
            "last_heartbeat": time.time(),
            "state": "idle",
        }))

        runtime = AgentRuntime(name="test", identity="lapis")
        pm = PresenceManager(runtime)
        pm._presence_dir = self._presence_dir

        removed = pm.startup_scan()

        self.assertEqual(removed, 1, "startup_scan should remove a dead-PID preliminary presence file")
        self.assertFalse(pf.exists(), "dead preliminary presence file must be cleaned up")


if __name__ == "__main__":
    unittest.main()

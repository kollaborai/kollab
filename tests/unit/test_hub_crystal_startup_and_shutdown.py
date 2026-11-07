"""The startup preload and the shutdown wait of #562, driven through the real methods.

``test_crystal_dream_off_loop.py`` tests the store and the dream. These tests pin
the two call sites in ``HubPlugin`` that it cannot reach: the load in
``_start_hub`` and the wait in ``shutdown``.
"""

import asyncio
import contextlib
import threading
import time
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

import plugins.hub.plugin as hub_plugin
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.crystal_store import CrystalStore
from plugins.hub.plugin import HubPlugin

INSIGHT = "Cobalt ternary spindle keeps the vault durable across every restart."


class _ReachedDns(BaseException):
    """Raised by the DNS stub: _start_hub got past the vault block."""


def _stop_at_dns(*args, **kwargs):
    raise _ReachedDns


class _Vault:
    """Stands in for AgentVault: two directories, nothing else _start_hub needs."""

    def __init__(self, root):
        self._vault_dir = root / "project"
        self.global_vault_dir = root / "global"

    def touch(self):
        pass


async def _start_hub_until_vault_is_published(plugin: HubPlugin) -> None:
    with contextlib.suppress(_ReachedDns):
        await plugin._start_hub()


def _startup_plugin() -> HubPlugin:
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._cli_args = Namespace(as_identity=None, attach=None)
    plugin._identity = AgentRuntime(name="default")
    plugin._identity.agent_id = "startup0000001"
    plugin._election = MagicMock()
    plugin._election.try_become_coordinator.return_value = False
    plugin._presence = MagicMock()
    plugin._presence.discover_agents.return_value = []
    plugin._display_tap = None
    plugin.config = None
    plugin.event_bus.get_service.return_value = None
    return plugin


@pytest.mark.asyncio
async def test_startup_parses_the_crystal_stores_off_the_loop_before_publishing_them(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(tmp_path / "project-root"))
    vault = _Vault(tmp_path)
    CrystalStore(vault._vault_dir).add_entry(INSIGHT)
    CrystalStore(vault.global_vault_dir).add_entry(INSIGHT)

    parses: list[bool] = []  # True when a parse ran on the event loop's thread
    original = CrystalStore._parse_file
    loop_thread = threading.get_ident()

    def slow_parse(self, *args, **kwargs):
        parses.append(threading.get_ident() == loop_thread)
        time.sleep(0.3)  # the first parse of a large vault takes this long
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CrystalStore, "_parse_file", slow_parse)
    monkeypatch.setattr(hub_plugin, "AgentVault", lambda *args: vault)
    monkeypatch.setattr(hub_plugin, "DNSStorage", _stop_at_dns)
    monkeypatch.setattr(hub_plugin, "_DNS_AVAILABLE", True)
    plugin = _startup_plugin()

    try:
        await _start_hub_until_vault_is_published(plugin)

        # Both stores were parsed during startup, in worker threads, and are
        # published loaded: a loop reader parses nothing and waits on nothing.
        assert parses == [False, False]
        before = len(parses)
        assert len(plugin._crystal_store.get_recent(3)) == 1
        assert len(plugin._global_crystal_store.get_recent(3)) == 1
        assert len(parses) == before, "a reader on the loop parsed a vault the preload skipped"
    finally:
        server = getattr(plugin, "_socket_server", None)
        if server is not None:
            await server.stop()


def _shutdown_plugin(vault_dir) -> HubPlugin:
    plugin = HubPlugin()
    plugin._identity = AgentRuntime(name="default")
    plugin._identity.agent_id = "shutdown000001"
    plugin._identity.identity = "test"
    plugin._crystal_store = CrystalStore(vault_dir)
    plugin._global_crystal_store = None
    plugin._vault = MagicMock()
    plugin._presence = None
    plugin._last_dream_at = 0.0
    plugin._build_dreaming_prompt = lambda stream_entries: "prompt"

    async def llm_call(prompt):
        return INSIGHT

    plugin._dreaming_llm_call = llm_call
    return plugin


@pytest.mark.asyncio
async def test_shutdown_waits_for_the_save_of_a_dream_it_cancels(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(tmp_path / "project-root"))
    writing, release = threading.Event(), threading.Event()
    original = CrystalStore._write_atomically

    def held(self, *args):
        writing.set()
        release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(CrystalStore, "_write_atomically", held)
    plugin = _shutdown_plugin(tmp_path)
    plugin._dreaming_task = asyncio.create_task(plugin._dream([]))
    assert await asyncio.to_thread(writing.wait, 10)  # the dream's save runs in its thread

    shutdown = asyncio.create_task(plugin.shutdown(exit_process=False))
    try:
        await asyncio.sleep(0.2)
        assert not shutdown.done(), "shutdown returned while the dream's save was still running"
    finally:
        release.set()
    await asyncio.wait_for(shutdown, 10)

    assert len(CrystalStore(tmp_path).load()) == 1  # the save landed before shutdown returned

"""Dreaming's crystal writes and the vault's first load stay off the event loop (#562)."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from plugins.hub.crystal_store import CrystalStore
from plugins.hub.plugin import HubPlugin

# Three paragraphs with no shared words: one dream writes three new entries.
INSIGHTS = (
    "Cobalt ternary spindle keeps the vault durable across every restart.\n\n"
    "Opal fractal meadow records each dreaming cycle timing in the stream.\n\n"
    "Saffron lattice orbit tracks nudge thresholds for each agent carefully."
)


def _slow(monkeypatch, name: str, seconds: float) -> None:
    """Make CrystalStore.<name> hold the calling thread for ``seconds``."""
    original = getattr(CrystalStore, name)

    def slow(self, *args, **kwargs):
        time.sleep(seconds)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CrystalStore, name, slow)


def _dreaming_plugin(vault: Path) -> HubPlugin:
    plugin = object.__new__(HubPlugin)
    plugin._crystal_store = CrystalStore(vault)
    plugin._global_crystal_store = None
    plugin._identity = SimpleNamespace(identity="test", state="idle", current_task=None)
    plugin._vault = MagicMock()
    plugin._presence = None
    plugin._last_dream_at = 0.0
    plugin._build_dreaming_prompt = lambda stream_entries: "prompt"

    async def llm_call(prompt):
        return INSIGHTS

    plugin._dreaming_llm_call = llm_call
    return plugin


async def _longest_loop_stall(work) -> float:
    """Run ``work`` while a heartbeat ticks on the loop; return the longest gap.

    The gaps run from the start and to the end too: a run that never yields to
    the loop leaves no heartbeat ticks at all, and that gap is the stall.
    """
    ticks = [time.monotonic()]

    async def beat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    heartbeat = asyncio.create_task(beat())
    try:
        await work
    finally:
        ticks.append(time.monotonic())
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    return max(later - earlier for earlier, later in zip(ticks, ticks[1:]))


@pytest.mark.asyncio
async def test_dream_insight_writes_do_not_stall_the_event_loop(tmp_path, monkeypatch):
    _slow(monkeypatch, "_write_atomically", 0.3)  # each save takes 0.3 s on a slow disk
    plugin = _dreaming_plugin(tmp_path)

    stall = await _longest_loop_stall(plugin._dream([]))

    assert plugin._crystal_store.count() == 3
    assert stall < 0.15, f"the event loop stalled for {stall:.2f}s during a dream's writes"


@pytest.mark.asyncio
async def test_dream_first_vault_load_does_not_stall_the_event_loop(tmp_path, monkeypatch):
    CrystalStore(tmp_path).add_entry("Seeded entry that the first load must parse.")
    _slow(monkeypatch, "_parse_file", 0.3)  # the first parse of a large vault takes 0.3 s
    plugin = _dreaming_plugin(tmp_path)

    stall = await _longest_loop_stall(plugin._dream([]))

    assert stall < 0.15, f"the event loop stalled for {stall:.2f}s on the vault's first load"


def test_merge_does_not_change_an_entry_a_reader_already_holds(tmp_path):
    store = CrystalStore(tmp_path)
    store.add_entry("Cobalt ternary spindle keeps the vault durable.", date="2026-01-01")
    held = store.get_by_id("crys-001")
    before = (list(held.keywords), held.date, held.summary, held.body)

    store.add_entry(
        "Cobalt ternary spindle keeps the vault durable across every restart.",
        date="2026-02-01",
    )

    assert len(store.get_all()) == 1  # the second add merged into the first
    assert (list(held.keywords), held.date, held.summary, held.body) == before

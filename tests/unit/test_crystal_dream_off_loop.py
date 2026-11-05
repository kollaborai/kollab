"""Dreaming's crystal writes and the vault's first load stay off the event loop (#562)."""

import asyncio
import contextlib
import os
import threading
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


def test_a_reader_is_not_blocked_by_a_write_in_progress(tmp_path, monkeypatch):
    store = CrystalStore(tmp_path)
    store.add_entry("Opal fractal meadow records each dreaming cycle timing.")
    writing, release = threading.Event(), threading.Event()
    original = CrystalStore._write_atomically

    def held(self, *args):
        writing.set()
        release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(CrystalStore, "_write_atomically", held)
    writer = threading.Thread(
        target=store.add_entry, args=("Saffron lattice orbit tracks nudge thresholds.",)
    )
    writer.start()
    try:
        assert writing.wait(10)
        read = []
        reader = threading.Thread(
            target=lambda: read.append(
                (store.get_all(), store.get_by_id("crys-001"), store.nudge("opal meadow"))
            )
        )
        reader.start()
        reader.join(timeout=2)
        assert not reader.is_alive(), "a reader waited for a write in progress"
        assert len(read) == 1
    finally:
        release.set()
        writer.join(10)


def test_a_save_in_progress_never_exposes_a_partial_vault_to_another_store(tmp_path, monkeypatch):
    store = CrystalStore(tmp_path)
    store.add_entry("Cobalt ternary spindle keeps the vault durable across every restart.")
    saved = (tmp_path / "crystallized.md").read_bytes()
    paused, release = threading.Event(), threading.Event()
    real_replace = os.replace

    def paused_replace(src, dst):
        paused.set()
        release.wait(10)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", paused_replace)
    writer = threading.Thread(
        target=store.add_entry, args=("Saffron lattice orbit tracks nudge thresholds.",)
    )
    writer.start()
    try:
        assert paused.wait(10)
        # The new bytes are in a temp file: another store reads the whole old vault.
        assert (tmp_path / "crystallized.md").read_bytes() == saved
        assert len(CrystalStore(tmp_path).load()) == 1
    finally:
        release.set()
        writer.join(10)
    assert len(CrystalStore(tmp_path).load()) == 2


@pytest.mark.asyncio
async def test_a_failed_dream_write_is_still_recorded_as_a_dreaming_error(tmp_path, monkeypatch):
    def disk_full(self, payload):
        raise OSError("disk full")

    monkeypatch.setattr(CrystalStore, "_write_atomically", disk_full)
    plugin = _dreaming_plugin(tmp_path)

    await plugin._dream([])

    recorded = [call.args[0] for call in plugin._vault.append_stream.call_args_list]
    assert "dreaming_error" in recorded
    assert "dreaming_complete" not in recorded


@pytest.mark.asyncio
async def test_shutdown_waits_for_a_dream_write_its_cancelled_dream_left_running(
    tmp_path, monkeypatch
):
    writing, release = threading.Event(), threading.Event()
    original = CrystalStore._write_atomically

    def held(self, *args):
        writing.set()
        release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(CrystalStore, "_write_atomically", held)
    plugin = _dreaming_plugin(tmp_path)
    dream = asyncio.create_task(plugin._dream([]))
    assert await asyncio.to_thread(writing.wait, 10)  # a save is running in its thread
    dream.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await dream

    settle = asyncio.create_task(plugin._settle_crystal_write())
    await asyncio.sleep(0.1)
    assert not settle.done(), "shutdown moved on while the dream's save was still running"
    release.set()
    await settle

    assert len(CrystalStore(tmp_path).load()) == 3  # the dream's saves all landed
    # A later save from another store is not written over by the dream's thread.
    CrystalStore(tmp_path).add_entry("Newer entry written after shutdown released the vault.")
    assert len(CrystalStore(tmp_path).load()) == 4

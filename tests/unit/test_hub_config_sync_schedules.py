"""Schedules as a config sync part, plus secondary edits (#612, PR #653, stage 3).

schedules.json travels with the sealed config sync: the primary pushes it as
its own bundle, a change on the primary re-pushes it, and an edit made on a
secondary is sent to the primary as a signed change request -- the primary
applies it and the normal push carries it to every device. Wherever the file
changes, the schedule timer wakes so the sleeping timer recomputes at once.
Runs on the same in-process two-machine wire as the config sync tests.
"""

# ruff: noqa: F811
# The `network` fixture is imported from the config sync service tests and
# pytest injects it by parameter name, so every test parameter redefines the
# import -- that is the framework's convention, not shadowing.
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from tests.unit.test_config_sync_service import network  # noqa: F401


def _row_dict(row_id="s1", **overrides):
    base = {
        "id": row_id,
        "agents": ["lapis"],
        "prompt": "check the queue",
        "interval_seconds": 600.0,
        "anchor": 1000.0,
    }
    base.update(overrides)
    return base


def _write_schedules(root: Path, rows) -> None:
    root.joinpath("schedules.json").write_text(
        json.dumps(rows), encoding="utf-8"
    )


def _read_schedules(root: Path):
    return json.loads(root.joinpath("schedules.json").read_text(encoding="utf-8"))


def _fake_timer():
    wakes = []
    return SimpleNamespace(wake=lambda: wakes.append(1)), wakes


async def sync_once(net):
    await net["primary"].tick()
    await net["primary"].drain()


@pytest.mark.asyncio
async def test_schedules_sync_to_the_secondary_and_wake_its_timer(network):
    net = network
    _write_schedules(net["mac"], [_row_dict()])
    timer, wakes = _fake_timer()
    net["right"].plugin._schedule_timer = timer

    await sync_once(net)

    rows = _read_schedules(net["server"])
    assert [r["id"] for r in rows] == ["s1"]
    assert rows[0]["prompt"] == "check the queue"
    assert wakes, "the schedule timer must wake when sync writes the file"


@pytest.mark.asyncio
async def test_a_schedules_change_repushes(network):
    net = network
    _write_schedules(net["mac"], [_row_dict()])
    await sync_once(net)
    assert [r["id"] for r in _read_schedules(net["server"])] == ["s1"]

    _write_schedules(net["mac"], [_row_dict(), _row_dict("s2", agents=["infra@box"])])
    await sync_once(net)

    assert [r["id"] for r in _read_schedules(net["server"])] == ["s1", "s2"]


@pytest.mark.asyncio
async def test_deleting_every_schedule_syncs_as_an_empty_list(network):
    net = network
    _write_schedules(net["mac"], [_row_dict()])
    await sync_once(net)

    _write_schedules(net["mac"], [])
    await sync_once(net)

    assert _read_schedules(net["server"]) == []


@pytest.mark.asyncio
async def test_secondary_edit_reaches_the_primary_and_pushes_back(network):
    net = network
    _write_schedules(net["mac"], [_row_dict()])
    await sync_once(net)

    # The secondary edits its copy and asks the primary to apply it.
    edited = [_row_dict(prompt="edited on the phone")]
    _write_schedules(net["server"], edited)
    reply = await net["secondary"].send_schedule_edit(edited)
    assert reply.get("ok") is True

    # The primary now holds the edit (stored in the canonical row form),
    # and its timer woke for the local change.
    assert [r["prompt"] for r in _read_schedules(net["mac"])] == [
        "edited on the phone"
    ]
    timer, wakes = _fake_timer()
    net["left"].plugin._schedule_timer = timer
    await net["secondary"].send_schedule_edit([_row_dict(prompt="second edit")])
    assert _read_schedules(net["mac"])[0]["prompt"] == "second edit"
    assert wakes, "the primary's timer must wake when it applies an edit"

    # The normal push carries the edit to every device (this one included).
    await sync_once(net)
    assert _read_schedules(net["server"])[0]["prompt"] == "second edit"


@pytest.mark.asyncio
async def test_secondary_edit_with_a_broken_row_is_refused(network):
    net = network
    _write_schedules(net["mac"], [_row_dict()])
    await sync_once(net)

    bad = [_row_dict(), {"id": "no-agents", "prompt": "p", "interval_seconds": 0}]
    reply = await net["secondary"].send_schedule_edit(bad)

    assert "error" in reply
    assert _read_schedules(net["mac"]) == [_row_dict()]


@pytest.mark.asyncio
async def test_schedule_edit_from_a_stranger_is_refused(network):
    net = network
    from nacl.signing import SigningKey

    from plugins.hub import config_sync as cs

    stranger = SigningKey.generate()
    sealed = cs.seal(
        "schedule_edit",
        {},
        cs.pack_json({"schedules": [_row_dict()]}),
        issuer_key=stranger,
        recipient_public_key=bytes.fromhex(net["left_key"]),
        revision=1,
    )
    import base64

    payload = {
        "v": 1,
        "op": "schedule_edit",
        "bundle": base64.b64encode(sealed).decode("ascii"),
    }
    reply = await net["primary"].receive(stranger.encode().hex()[:64], payload)
    assert "error" in reply

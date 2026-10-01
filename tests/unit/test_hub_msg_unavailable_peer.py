"""A hub_msg to an agent@device that the peer reports unavailable is refused.

The live proof sent to a handle whose agent had gone (a relaunch gave that
device a different agent) and got `sent to <handle>; its reply arrives by
itself`. Nobody was going to answer. The sender must read a plain refusal that
names the handle and says where to look.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_remote_target_helpers",
    Path(__file__).with_name("test_hub_msg_remote_target.py"),
)
_h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_h)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {"id": "a" * 32, "state": "rejected", "duplicate": False, "reason": "recipient_unavailable"},
        {"id": "a" * 32, "state": "unavailable", "duplicate": False},
    ],
)
async def test_an_unavailable_peer_is_a_plain_refusal_naming_the_handle(receipt):
    plugin, sent = _h._plugin(on_roster=[_h.PEER], receipt=receipt)

    result = await plugin._handle_hub_msg_tool(_h._call(_h.PEER))

    assert not result.success
    assert _h.PEER in result.output
    assert "/connect status" in result.output
    assert "sent to" not in result.output and "reply arrives" not in result.output
    assert _h.LEAK.search(result.output) is None

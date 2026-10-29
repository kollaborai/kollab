"""Kill switch round 14 (D12, first fix): a workspace whose network state is still in the v1
``~/.kollab/network/<digest>`` layout (the migration refuses to move it while its daemon runs,
issue #364) must still resolve folder -> network for the halt paths.

D11 (Docker, image ks11-img:dev on ff1bcfb) showed the failure live: a halt issued while the
folder's daemon was alive recorded ``folders[net] = []`` in halt.json on every machine, the
listener opened no link, and every resume form was refused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

NET = "c803f244ad84863c9b797f6d399b44b9c0567e3d6385f02d47f297c89b0c1929"
ROOM = "4f" * 32  # a room is an Ed25519 public key in hex


def _v1_layout(root: Path, folder: Path) -> Path:
    """A workspace folder whose only membership state is the v1 legacy dir (daemon still runs)."""
    digest = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()
    legacy = root / "network" / digest
    legacy.mkdir(parents=True)
    for name, data in (("state.json", {"room": ROOM}),
                       ("private/network_control.json", {"room": ROOM, "net": NET})):
        path = legacy / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        path.chmod(0o600)  # RelayState and HaltFile refuse group/other-readable state files
    return legacy


def test_v1_layout_still_lists_the_membership(tmp_path):
    from plugins.hub.membership_runtime import Memberships

    folder = tmp_path / "proj"
    folder.mkdir()
    legacy = _v1_layout(tmp_path, folder)
    memberships = Memberships(folder, root=tmp_path).list()
    assert [m.state_dir for m in memberships] == [legacy]


def test_v1_layout_resolves_the_network_for_the_halt_paths(tmp_path, monkeypatch):
    from plugins.hub.halt_runtime import HaltListener

    monkeypatch.setenv("HOME", str(tmp_path))  # _networks_of reads the machine's own root
    folder = tmp_path / "proj"
    folder.mkdir()
    _v1_layout(tmp_path / ".kollab", folder)
    listener = HaltListener.__new__(HaltListener)
    assert HaltListener._networks_of(listener, str(folder)) == [NET]


def test_take_halt_records_the_hand_off_folder_on_a_v1_machine(tmp_path, monkeypatch):
    """The D11 jam: the issuer's own halt left folders[net] empty, so no link ever opened."""
    import asyncio

    from plugins.hub import halt_runtime as hr

    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))  # the listener resolves folders against the machine root
    _v1_layout(tmp_path / ".kollab", folder)

    opened = []

    async def open_link(f, state_dir):
        opened.append((f, state_dir))
        return object()

    h = "ab" * 32
    from plugins.hub import network_halt as nh
    nh.HaltFile(tmp_path).write({
        "scopes": {}, "stopped": [],
        "networks": {NET: {"halts": {h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}},
    })
    listener = hr.HaltListener(tmp_path, open_link=open_link, grace=0, stop=_stop_ok,
                               processes=lambda: [], agents=lambda: [])
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": NET, "scope": "machine", "folder": str(folder)}))
    assert reply["ok"], reply
    data = hr.read_halt(tmp_path)
    assert data["folders"][NET] == [str(folder)]
    assert opened, "open_links found no folder, so no resume path would ever exist"


async def _stop_ok(rec):
    return True, "ok"

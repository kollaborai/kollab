"""Kill switch round 15 (D13): a workspace whose live network state sits under
``workspaces/<digest>/networks/unjoined`` — the founder's join-in-place shape, seen live on the
ks13 rig — must still resolve folder -> network for the halt paths.

The registry deliberately skips the ``unjoined`` slug (the runtime must not adopt a pre-join
placeholder), so until F18 the halt paths saw no membership: ``folders[net]`` stayed empty, the
halt listener's relay link never opened, and neither the halt nor its revocation records could
leave the machine (#365's transport).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

NET = "c803f244ad84863c9b797f6d399b44b9c0567e3d6385f02d47f297c89b0c1929"
ROOM = "4f" * 32  # a room is an Ed25519 public key in hex


def _unjoined_layout(root: Path, folder: Path) -> Path:
    """A workspace whose only network state is the unjoined dir (the founder's live shape)."""
    digest = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()
    unjoined = root / "workspaces" / digest / "networks" / "unjoined"
    for name, data in (("state.json", {"room": ROOM}),
                       ("private/network_control.json", {"room": ROOM, "net": NET})):
        path = unjoined / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        path.chmod(0o600)  # RelayState and HaltFile refuse group/other-readable state files
    return unjoined


def test_registry_still_skips_the_unjoined_slug(tmp_path):
    """The fix is halt-path-only: the runtime registry must not adopt the placeholder."""
    from plugins.hub.membership_runtime import Memberships

    folder = tmp_path / "proj"
    folder.mkdir()
    _unjoined_layout(tmp_path / ".kollab", folder)
    assert Memberships(folder, root=tmp_path / ".kollab").list() == []


def test_unjoined_layout_resolves_the_network_for_the_halt_paths(tmp_path, monkeypatch):
    from plugins.hub.halt_runtime import HaltListener

    monkeypatch.setenv("HOME", str(tmp_path))  # the halt paths resolve against the machine's root
    folder = tmp_path / "proj"
    folder.mkdir()
    _unjoined_layout(tmp_path / ".kollab", folder)
    listener = HaltListener.__new__(HaltListener)
    assert HaltListener._networks_of(listener, str(folder)) == [NET]


def test_unjoined_layout_yields_the_state_dir_for_the_link(tmp_path, monkeypatch):
    from plugins.hub.halt_runtime import _state_dir_for

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "proj"
    folder.mkdir()
    unjoined = _unjoined_layout(tmp_path / ".kollab", folder)
    assert _state_dir_for(str(folder), NET) == unjoined


def test_take_halt_records_folder_and_opens_link_on_an_unjoined_machine(tmp_path, monkeypatch):
    """The ks13 jam: the issuer's halt left folders[net] empty and the listener linkless."""
    import asyncio

    from plugins.hub import halt_runtime as hr
    from plugins.hub import network_halt as nh

    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    _unjoined_layout(tmp_path / ".kollab", folder)

    opened = []

    async def open_link(f, state_dir):
        opened.append((f, state_dir))
        return object()

    async def stop(rec):
        return True, "ok"

    h = "ab" * 32
    nh.HaltFile(tmp_path).write({
        "scopes": {}, "stopped": [],
        "networks": {NET: {"halts": {h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}},
    })
    listener = hr.HaltListener(tmp_path, open_link=open_link, grace=0, stop=stop,
                               processes=lambda: [], agents=lambda: [])
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": NET, "scope": "machine", "folder": str(folder)}))
    assert reply["ok"], reply
    data = hr.read_halt(tmp_path)
    assert data["folders"][NET] == [str(folder)]
    assert opened, "open_links found no folder, so neither halt nor revocations could leave"

"""Peer exchange pages must respect the complete frame's byte limit."""

from types import SimpleNamespace

import pytest

from plugins.hub.peer_records import peer_id_for_key
from plugins.hub.peer_transport import MAX_PEER_EXCHANGE_BYTES, PeerMeshRuntime, _encoded_size


class WireItem:
    def __init__(self, wire):
        self.wire = wire
        self.peer_id = wire.get("peer_id")
        self.left = wire.get("left")
        self.right = wire.get("right")

    def to_wire(self):
        return self.wire


def make_mesh(record_sizes, link_sizes):
    local_id = peer_id_for_key("01" * 32)
    peer_key = "02" * 32
    records = [
        WireItem({"peer_id": peer_id_for_key(f"{i:02x}" * 32), "data": "r" * size})
        for i, size in enumerate(record_sizes, 3)
    ]
    links = [
        WireItem(
            {
                "left": peer_id_for_key(f"{i:02x}" * 32),
                "right": peer_id_for_key("ff" * 32),
                "revision": 1,
                "data": "l" * size,
            }
        )
        for i, size in enumerate(link_sizes, 3)
    ]
    router = SimpleNamespace(
        record_snapshot=lambda: {r.peer_id: r for r in records},
        link_snapshot=lambda: {n: link for n, link in enumerate(links)},
    )
    mesh = object.__new__(PeerMeshRuntime)
    mesh.local_peer_id = local_id
    mesh._exchange_offsets = {}
    mesh._ensure_router = lambda: router
    local = WireItem({"peer_id": local_id, "data": "local" * 100})
    link = WireItem({"left": local_id, "right": peer_id_for_key(peer_key), "revision": 1, "data": "link" * 100})
    return mesh, peer_key, local, link, records, links


@pytest.mark.parametrize("kind", ["response", "request"])
def test_exchange_pages_fit_and_eventually_cover_both_kinds(kind):
    mesh, peer, local, link, records, links = make_mesh([500] * 4, [450] * 4)
    seen_records, seen_links = set(), set()
    for _ in range(12):
        if kind == "response":
            frame = mesh._exchange_response(peer, local, link)
        else:
            frame = mesh._exchange_request(peer, local, {"data": "proposal" * 50})
        assert _encoded_size(frame) <= MAX_PEER_EXCHANGE_BYTES
        seen_records.update(item["peer_id"] for item in frame["records"])
        seen_links.update(item["left"] for item in frame["links"])
    assert seen_records == {item.peer_id for item in records}
    assert seen_links == {item.left for item in links}


def test_oversized_items_are_skipped_and_both_kinds_make_progress(caplog):
    mesh, peer, local, link, records, links = make_mesh([5000, 1700], [5000, 1700])
    seen_records, seen_links = set(), set()
    with caplog.at_level("DEBUG", logger="plugins.hub.peer_transport"):
        for _ in range(8):
            frame = mesh._exchange_response(peer, local, link)
            assert _encoded_size(frame) <= MAX_PEER_EXCHANGE_BYTES
            seen_records.update(item["peer_id"] for item in frame["records"])
            seen_links.update(item["left"] for item in frame["links"])
    assert seen_records == {records[1].peer_id}
    assert seen_links == {links[1].left}
    assert "skip" in caplog.text.lower()

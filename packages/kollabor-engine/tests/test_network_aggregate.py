"""The multi-network aggregate GET /sessions attaches for the web UI (#158).

Fixtures cover both membership shapes slice 1 / the design docs have left on
disk (the device record + workspace networks layout, and the design-v1
index/memberships layout), plus the legacy single-state machine and the empty
cases a defensive reader must survive. Live daemon answers are folded into
the answering network's block; the flat fields the current UI reads pass
through untouched.
"""

import hashlib
import json
from pathlib import Path

from kollabor_engine.network_aggregate import (
    aggregate_network_snapshot,
    merge_view_and_memberships,
    read_membership_networks,
)

FIXTURES = Path(__file__).parent / "fixtures" / "network"
# Resolves to itself on Linux and macOS (where /home is /System/Volumes/Data/home),
# so the digests the fixture folders are named by match what the code computes.
WORKSPACE = "/Users/box/proj"
DIGEST = hashlib.sha256(WORKSPACE.encode()).hexdigest()


def _memberships(root: Path, links: list[str]) -> Path:
    """A design-v1 layout with one linked peer per link."""
    base = root / "network" / DIGEST
    base.mkdir(parents=True, exist_ok=True)
    state = {
        "origin": "https://relay.home.example",
        "network_name": "home-net",
        "trust": "open",
        "peer_devices": {item: f"box-{item[:4]}" for item in links},
        "peer_trust": {item: "agents" for item in links},
        "links": links,
    }
    (base / "memberships" / "aaaa").mkdir(parents=True, exist_ok=True)
    (base / "memberships" / "aaaa" / "state.json").write_text(json.dumps(state))
    (base / "index.json").write_text(
        json.dumps({"version": 2, "primary": "aaaa", "order": ["aaaa"]})
    )
    return base


def test_device_record_layout_lists_every_membership_primary_first():
    networks = read_membership_networks(FIXTURES / "devicerecord", WORKSPACE)

    assert [block["id"] for block in networks] == [
        "0123456789abcdef",
        "fedcba9876543210",
    ]
    assert [block["name"] for block in networks] == ["home-net", "lab-net"]
    assert networks[0]["trust"] == "open"


def test_device_record_contacts_come_from_links_and_peer_trust():
    networks = read_membership_networks(FIXTURES / "devicerecord", WORKSPACE)
    aggregate = aggregate_network_snapshot(
        home=FIXTURES / "devicerecord", workspace=WORKSPACE
    )

    contacts = aggregate["contacts"]
    assert contacts == [
        {
            "key": "aabb",
            "label": "server-box",
            "device": "server-box",
            "trust": "agents",
        }
    ]
    assert "_contacts" not in networks[0]  # merged out, never serialized


def test_v1_index_layout_orders_memberships_by_index_primary():
    networks = read_membership_networks(FIXTURES / "v1index", WORKSPACE)

    assert [block["id"] for block in networks] == [
        "aaaabbbbccccdddd",
        "eeeeffff00001111",
    ]
    assert [block["name"] for block in networks] == ["v1-one", "v1-two"]


def test_v1_contacts_name_the_named_and_key_the_unnamed():
    aggregate = aggregate_network_snapshot(
        home=FIXTURES / "v1index", workspace=WORKSPACE
    )

    # Rows sort by label: the unnamed contact's generated label first.
    assert [(row["key"], row["label"]) for row in aggregate["contacts"]] == [
        ("2222", "Contact 2222"),
        ("1111", "named-peer"),
    ]


def test_legacy_state_without_index_is_one_block(tmp_path):
    tmp = tmp_path
    base = tmp / "network" / DIGEST
    base.mkdir(parents=True, exist_ok=True)
    (base / "state.json").write_text(
        json.dumps(
            {
                "origin": "https://relay.legacy.example",
                "network_name": "legacy-net",
                "trust": "open",
                "links": [],
            }
        )
    )

    networks = read_membership_networks(tmp, WORKSPACE)

    assert len(networks) == 1
    assert networks[0]["name"] == "legacy-net"
    slug = hashlib.sha256(b"https://relay.legacy.example").hexdigest()[:16]
    assert networks[0]["id"] == slug


def test_missing_or_empty_index_means_legacy_block(tmp_path):
    # An index naming nothing but a memberships dir present: the reader
    # falls through to the legacy single-block shape, which needs the flat
    # state.json. Without it the answer is empty -- one network, not a crash.
    base = _memberships(tmp_path, links=["cc"])
    (base / "index.json").unlink()
    (base / "memberships" / "aaaa" / "state.json").write_text(
        json.dumps(
            {
                "origin": "https://relay.fallback.example",
                "network_name": "fallback-net",
                "trust": "open",
                "links": ["cc"],
            }
        )
    )
    # The flat legacy state is what the fallback reads.
    (base / "state.json").write_text(
        json.dumps(
            {
                "origin": "https://relay.fallback.example",
                "network_name": "fallback-net",
                "trust": "open",
                "links": ["cc"],
            }
        )
    )

    networks = read_membership_networks(tmp_path, WORKSPACE)

    assert len(networks) == 1 and networks[0]["name"] == "fallback-net"


def test_malformed_state_is_skipped_not_fatal(tmp_path):
    base = _memberships(tmp_path, links=[])
    (base / "memberships" / "aaaa" / "state.json").write_text("{not json")

    networks = read_membership_networks(tmp_path, WORKSPACE)

    # Broken membership state still lists the network (from the index), with
    # no name from state: slug stands in.
    assert [block["id"] for block in networks] == ["aaaa"]
    assert networks[0]["name"] == "aaaa"


def test_oversized_state_reads_as_missing(tmp_path):
    base = _memberships(tmp_path, links=[])
    (base / "memberships" / "aaaa" / "state.json").write_text(
        json.dumps({"origin": "https://x", "pad": "0" * 70000})
    )

    networks = read_membership_networks(tmp_path, WORKSPACE)

    assert [block["id"] for block in networks] == ["aaaa"]


def test_live_rows_fold_into_the_answering_network(tmp_path):
    _memberships(tmp_path, links=[])
    live = {
        "device": "zircon-box",
        "remote": [
            {
                "name": "lapis",
                "device": "server-box",
                "handle": "lapis@server-box",
                "state": "idle",
            },
            {
                "name": "ruby",
                "device": "server-box",
                "handle": "ruby@server-box",
                "state": "busy",
            },
            # This device's own agent: the sidebar's local rows cover it.
            {
                "name": "me",
                "device": "zircon-box",
                "handle": "me@zircon-box",
                "state": "idle",
            },
        ],
    }

    aggregate = aggregate_network_snapshot(
        home=tmp_path, workspace=WORKSPACE, live=live
    )

    [block] = aggregate["networks"]
    assert block["live"] is True
    [device_row] = block["devices"]
    assert device_row["device"] == "server-box"
    assert device_row["workspaces"][0]["agents"][0]["name"] == "lapis"


def test_live_answer_without_disk_membership_still_makes_one_block(tmp_path):
    live = {"device": "zircon-box", "remote": []}

    aggregate = aggregate_network_snapshot(
        home=tmp_path, workspace=WORKSPACE, live=live
    )

    [block] = aggregate["networks"]
    assert block["live"] is True
    assert block["devices"] == []


def test_merge_view_keeps_flat_fields_and_adds_the_aggregate(tmp_path):
    _memberships(tmp_path, links=["dd"])
    view = {
        "device": "zircon-box",
        "remote": [],
        "live": True,
        "folder": WORKSPACE,
    }

    merged = merge_view_and_memberships(view, home=tmp_path)

    assert merged["device"] == "zircon-box" and merged["live"] is True
    assert merged["folder"] == WORKSPACE and merged["remote"] == []
    assert [block["id"] for block in merged["networks"]] == ["aaaa"]
    assert [row["key"] for row in merged["contacts"]] == ["dd"]


def test_merge_view_on_no_network_at_all_is_empty_not_error(tmp_path):
    merged = merge_view_and_memberships(
        {"device": "zircon-box", "remote": [], "live": False}, home=tmp_path
    )

    assert merged["networks"] == [] and merged["contacts"] == []
    assert merged["device"] == "zircon-box"


def test_empty_machine_list_sessions_network_block(tmp_path, monkeypatch):
    # The route wiring: no membership data anywhere, live answer empty.

    monkeypatch.setattr(
        "kollabor_engine.network_aggregate._root", lambda home=None: tmp_path
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    merged = merge_view_and_memberships(
        {"device": "box", "remote": [], "live": False}, home=None
    )

    assert merged["networks"] == [] and isinstance(merged["contacts"], list)


def test_live_answer_for_exact_name_beats_prefix_overlap_primary(tmp_path):
    """A live answer naming "acme" must fold into the "acme" network, not the
    primary "acme-lab-net" whose name merely contains it (#163 fix-up)."""
    import hashlib

    root = tmp_path
    (root / "device").mkdir(parents=True, exist_ok=True)
    (root / "device" / "device.json").write_text(
        json.dumps(
            {
                "device_id": "d" * 32,
                "device_name": "fixup-box",
                "networks": {
                    "lab"
                    * 8: {
                        "origin": "https://relay.acmelab.example",
                        "network_name": "acme-lab-net",
                        "joined_at": 1,
                    },
                    "acm" * 8
                    + "e": {
                        "origin": "https://relay.acme.example",
                        "network_name": "acme",
                        "joined_at": 2,
                    },
                },
                "primary": "lab" * 8,
            }
        )
    )
    digest = hashlib.sha256(WORKSPACE.encode()).hexdigest()
    for slug in ("lab" * 8, "acm" * 8 + "e"):
        d = root / "workspaces" / digest / "networks" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(
            json.dumps(
                {"origin": "https://relay.example", "trust": "open", "links": []}
            )
        )

    aggregate = aggregate_network_snapshot(
        home=root,
        workspace=WORKSPACE,
        live={
            "device": "fixup-box",
            "network": "acme",
            "remote": [
                {
                    "name": "lapis",
                    "device": "peer",
                    "handle": "lapis@peer",
                    "state": "idle",
                }
            ],
        },
    )

    by_name = {block["name"]: block for block in aggregate["networks"]}
    assert by_name["acme"].get("live") is True
    assert by_name["acme"]["devices"], "the live row folded into the exact-name network"
    assert by_name["acme-lab-net"].get("live") is None
    assert by_name["acme-lab-net"]["devices"] == []


def test_each_network_answer_folds_into_its_own_block(tmp_path):
    # This computer's folders are on two networks and the disk knows only
    # home-net (a folder not moved to the device record yet): each roster
    # still lands under its own network, never the primary's.
    _memberships(tmp_path, links=[])
    opal = {"name": "opal", "device": "lab-box", "handle": "opal@lab-box", "state": "idle"}
    lapis = {"name": "lapis", "device": "server-box", "handle": "lapis@server-box", "state": "idle"}
    view = {
        "device": "mac-lab",
        "remote": [opal, lapis],
        "live": True,
        "networks": [
            {"network": "lab-net", "device": "mac-lab", "trust": "open", "folder": "/w/lab", "live": True,
             "remote": [opal]},
            {"network": "home-net", "device": "mac-box", "trust": "open", "folder": WORKSPACE, "live": True,
             "remote": [lapis]},
        ],
    }

    merged = merge_view_and_memberships(view, home=tmp_path, workspace=WORKSPACE)

    by_name = {block["name"]: block for block in merged["networks"]}
    assert set(by_name) == {"home-net", "lab-net"}
    assert [row["device"] for row in by_name["home-net"]["devices"]] == ["server-box"]
    assert [row["device"] for row in by_name["lab-net"]["devices"]] == ["lab-box"]
    assert by_name["lab-net"]["folder"] == "/w/lab" and by_name["lab-net"]["trust"] == "open"
    assert by_name["home-net"]["live"] is True and by_name["lab-net"]["live"] is True


def test_a_network_no_agent_here_carries_shows_its_last_roster(tmp_path):
    opal = {"name": "opal", "device": "lab-box", "handle": "opal@lab-box", "state": "offline", "last_seen": 900.0}
    view = {
        "device": "mac-lab",
        "remote": [opal],
        "live": False,
        "folder": "/w/lab",
        "networks": [
            {"network": "lab-net", "device": "mac-lab", "trust": "open", "folder": "/w/lab", "live": False,
             "remote": [opal]},
        ],
    }

    [block] = merge_view_and_memberships(view, home=tmp_path, workspace=WORKSPACE)["networks"]

    assert block["live"] is False and block["folder"] == "/w/lab"
    [agent] = block["devices"][0]["workspaces"][0]["agents"]
    assert agent["state"] == "offline" and agent["last_seen"] == 900.0

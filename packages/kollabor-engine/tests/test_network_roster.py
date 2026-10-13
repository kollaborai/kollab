"""The sidebar keeps every agent it has seen: offline gems here, each network's last roster."""

from types import SimpleNamespace

from kollabor_engine.routes import sessions as sessions_route

LAPIS = {"name": "lapis", "device": "prod-box", "handle": "lapis@prod-box", "state": "idle"}
RUBY = {"name": "ruby", "device": "prod-box", "handle": "ruby@prod-box", "state": "working"}
OPAL = {"name": "opal", "device": "lab-box", "handle": "opal@lab-box", "state": "idle"}
DAY = 86400.0


def home(*rows, device="mac-box"):
    return {"network": "home-net", "device": device, "trust": "open", "remote": list(rows), "folder": "/w/kollab"}


def lab(*rows):
    return {"network": "lab-net", "device": "mac-lab", "trust": "open", "remote": list(rows), "folder": "/w/lab"}


def live(*answers):
    """``_network_snapshot``'s answer for these networks; none answering is the bare fallback."""
    if not answers:
        return {"device": "mac-box", "remote": []}
    rows = [row for answer in answers for row in answer["remote"]]
    return {"device": answers[0]["device"], "remote": rows, "folder": answers[0]["folder"], "networks": list(answers)}


def states(network):
    return {row["handle"]: row["state"] for row in network["remote"]}


def test_an_agent_that_left_the_network_stays_listed_offline():
    sessions_route._network_view(live(home(LAPIS, RUBY)), now=1000.0)

    network = sessions_route._network_view(live(home(RUBY)), now=2000.0)

    assert network["live"] is True
    assert states(network) == {"ruby@prod-box": "working", "lapis@prod-box": "offline"}
    lapis = next(row for row in network["remote"] if row["name"] == "lapis")
    assert lapis["last_seen"] == 1000.0


def test_with_no_agent_here_on_the_network_the_last_one_seen_shows():
    sessions_route._network_view(live(home(LAPIS)), now=1000.0)

    network = sessions_route._network_view(live(), now=1500.0)

    assert network["live"] is False
    assert network["folder"] == "/w/kollab"  # opening one starts an agent here first
    assert [row["handle"] for row in network["remote"]] == ["lapis@prod-box"]
    assert [(block["network"], block["live"]) for block in network["networks"]] == [("home-net", False)]


def test_every_network_keeps_its_own_roster():
    first = sessions_route._network_view(live(home(LAPIS), lab(OPAL)), now=1000.0)
    assert [(block["network"], block["live"]) for block in first["networks"]] == [
        ("home-net", True), ("lab-net", True),
    ]
    assert [row["handle"] for row in first["remote"]] == ["lapis@prod-box", "opal@lab-box"]

    # The agent carrying home-net stopped: its roster stays, not live, with the folder to start one in.
    network = sessions_route._network_view(live(lab(OPAL)), now=2000.0)

    blocks = {block["network"]: block for block in network["networks"]}
    assert blocks["lab-net"]["live"] is True and blocks["home-net"]["live"] is False
    assert blocks["home-net"]["folder"] == "/w/kollab"
    assert [row["handle"] for row in blocks["home-net"]["remote"]] == ["lapis@prod-box"]
    assert network["live"] is True and "folder" not in network


def test_an_agent_on_two_networks_is_listed_once_up_front():
    network = sessions_route._network_view(live(home(LAPIS), lab(LAPIS, OPAL)), now=1000.0)

    assert [row["handle"] for row in network["remote"]] == ["lapis@prod-box", "opal@lab-box"]
    assert [len(block["remote"]) for block in network["networks"]] == [1, 2]


def test_a_month_unseen_is_forgotten_and_a_new_name_there_starts_over():
    sessions_route._network_view(live(home(LAPIS)), now=0.0)
    assert sessions_route._network_view(live(home(RUBY)), now=31 * DAY)["remote"] == [
        {**RUBY, "last_seen": 31 * DAY}
    ]

    # This computer joined home-net again under another name: what the old name saw is gone.
    assert sessions_route._network_view(live(home(device="mac-new")), now=32 * DAY)["remote"] == []


def test_the_roster_is_written_only_when_it_changes(no_real_network_roster):
    sessions_route._network_view(live(home(LAPIS)), now=1000.0)
    first = no_real_network_roster.stat().st_mtime_ns
    no_real_network_roster.touch()
    stamp = no_real_network_roster.stat().st_mtime_ns

    sessions_route._network_view(live(home(LAPIS)), now=1030.0)  # same roster, within the minute

    assert no_real_network_roster.stat().st_mtime_ns == stamp >= first


def test_no_network_ever_seen_shows_none():
    assert sessions_route._network_view(live()) == {"device": "mac-box", "remote": [], "live": False}


def test_agents_that_ran_here_and_are_not_running_are_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vaults = tmp_path / "hub" / "vaults"
    for name in ("lapis", "sapphire", "koordinator", "_shared"):
        (vaults / name).mkdir(parents=True)
        (vaults / name / "stream.jsonl").write_text("{}\n")
    (vaults / "ruby").mkdir()  # an empty vault never ran

    found = [{"identity": "sapphire", "workspace": str(tmp_path)}, {"identity": "lapis", "workspace": "/elsewhere"}]
    registry = {"s": SimpleNamespace(workspace=None, daemon=SimpleNamespace(identity="koordinator"))}

    offline = sessions_route._offline_agents(found, registry)

    assert [row["identity"] for row in offline] == ["lapis"]
    assert offline[0]["workspace"] == str(tmp_path) and offline[0]["last_ran"]

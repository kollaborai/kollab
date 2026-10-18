"""Hidden sidebar rows live in the engine, so every client shares them: the
store (hidden_agents.py), POST /sidebar/hide|unhide, GET /sidebar/hidden and
the `instance` and `hidden` GET /sessions adds. The conftest points the store
at a temp file."""

import json
from types import SimpleNamespace

import kollabor_engine.hidden_agents as hidden_agents  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.hub_bridge import HubBridge  # type: ignore[import-not-found]
from kollabor_engine.routes import sessions as sessions_route  # type: ignore[import-not-found]
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

# The real path, before the conftest swaps it for a temp file.
REAL_PATH = hidden_agents.hidden_path

SAPPHIRE = {"key": '["agent","a1b2c3d4e5f6"]', "label": "Sapphire", "detail": "/w/one", "instance": "a1b2c3d4e5f6"}
OFFLINE = {"key": '["offline","/w/one","lapis"]', "label": "Lapis", "detail": "/w/one"}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


def present(monkeypatch, *rows):
    """Hub presence lists these daemons: (agent id, pid) pairs."""
    found = [
        {"session_id": agent_id, "daemon_pid": pid, "identity": "sapphire", "workspace": "/w/one",
         "discovered": True, "external": True}
        for agent_id, pid in rows
    ]
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: found)
    return found


class TestParseEntry:
    def test_keeps_the_fields_it_knows(self):
        assert hidden_agents.parse_entry({**SAPPHIRE, "extra": 1}) == SAPPHIRE
        assert hidden_agents.parse_entry(OFFLINE) == OFFLINE  # no process, no instance
        assert hidden_agents.parse_entry({"key": "k", "label": "L"}) == {"key": "k", "label": "L", "detail": ""}

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            ["key"],
            {},
            {"key": "k"},
            {"label": "L"},
            {"key": 3, "label": "L"},
            {"key": "k", "label": ["L"]},
            {"key": "k", "label": "L", "detail": 4},
            {"key": "x" * 1025, "label": "L"},
            {"key": "k", "label": "L" * 257},
            {"key": "k\n", "label": "L"},
            {"key": "k", "label": "L", "instance": "a\x00b"},
        ],
    )
    def test_refuses_the_rest(self, raw):
        assert hidden_agents.parse_entry(raw) is None


@pytest.mark.asyncio
async def test_hide_and_unhide_round_trip(client, no_real_hidden_agents):
    assert (await client.get("/sidebar/hidden")).json() == {"hidden": []}

    # Offline rows have no process to be gone, so they always stay listed.
    first = await client.post("/sidebar/hide", json=OFFLINE)
    assert first.status_code == 200 and first.json() == {"hidden": [OFFLINE]}
    other = {**OFFLINE, "key": '["offline","/w/two","ruby"]', "label": "Ruby"}
    await client.post("/sidebar/hide", json=other)
    # Hiding one again moves it to the end and never duplicates it.
    again = await client.post("/sidebar/hide", json=OFFLINE)
    assert again.json()["hidden"] == [other, OFFLINE]
    assert json.loads(no_real_hidden_agents.read_text())["hidden"] == [other, OFFLINE]
    assert (await client.get("/sidebar/hidden")).json() == again.json()

    shown = await client.post("/sidebar/unhide", json={"key": other["key"]})
    assert shown.json() == {"hidden": [OFFLINE]}
    assert (await client.post("/sidebar/unhide", json={"key": "never hidden"})).json() == {"hidden": [OFFLINE]}
    assert (await client.post("/sidebar/unhide", json={"all": True})).json() == {"hidden": []}
    assert json.loads(no_real_hidden_agents.read_text()) == {"hidden": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, body",
    [
        ("/sidebar/hide", {}),
        ("/sidebar/hide", {"key": "k"}),
        ("/sidebar/hide", {"key": 7, "label": "L"}),
        ("/sidebar/hide", {"key": "x" * 2000, "label": "L"}),
        ("/sidebar/unhide", {}),
        ("/sidebar/unhide", {"key": ""}),
        ("/sidebar/unhide", {"key": "k", "all": True}),
        ("/sidebar/unhide", {"all": False}),
        ("/sidebar/unhide", {"key": ["k"]}),
    ],
)
async def test_a_bad_payload_is_a_400_and_changes_nothing(client, no_real_hidden_agents, path, body):
    await client.post("/sidebar/hide", json=OFFLINE)

    response = await client.post(path, json=body)

    assert response.status_code == 400
    assert json.loads(no_real_hidden_agents.read_text())["hidden"] == [OFFLINE]


@pytest.mark.asyncio
async def test_the_file_lands_under_home(client, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(hidden_agents, "hidden_path", REAL_PATH)

    await client.post("/sidebar/hide", json=OFFLINE)

    stored = home / ".kollab" / "hub" / "hidden-agents.json"
    assert json.loads(stored.read_text()) == {"hidden": [OFFLINE]}


def test_an_unreadable_file_is_an_empty_list(no_real_hidden_agents):
    no_real_hidden_agents.parent.mkdir(parents=True)
    for text in ("not json", "[]", '{"hidden": "no"}', '{"hidden": [3, {"key": "k"}]}'):
        no_real_hidden_agents.write_text(text)
        assert hidden_agents.visible([]) == []
    assert hidden_agents.hide(hidden_agents.parse_entry(OFFLINE)) == [OFFLINE]  # and the next hide mends it


def test_the_list_is_capped_to_the_newest(no_real_hidden_agents):
    for number in range(hidden_agents.MAX_HIDDEN + 5):
        hidden_agents.hide({"key": f"k{number}", "label": "L", "detail": ""})

    keys = [row["key"] for row in hidden_agents.visible([])]
    assert len(keys) == hidden_agents.MAX_HIDDEN and keys[-1] == f"k{hidden_agents.MAX_HIDDEN + 4}" and "k0" not in keys


def test_an_entry_lasts_as_long_as_its_daemon(no_real_hidden_agents):
    hidden_agents.hide(SAPPHIRE)
    hidden_agents.hide(OFFLINE)

    assert hidden_agents.visible({SAPPHIRE["instance"]}) == [SAPPHIRE, OFFLINE]
    # The daemon is gone (or a new one took the name: another instance): only the offline row is left.
    assert hidden_agents.visible({"ffffffffffff"}) == [OFFLINE]
    assert hidden_agents.visible([]) == [OFFLINE]


@pytest.mark.asyncio
async def test_get_leaves_out_the_hidden_agents_that_are_gone(client, monkeypatch):
    await client.post("/sidebar/hide", json=SAPPHIRE)
    await client.post("/sidebar/hide", json=OFFLINE)

    present(monkeypatch, ("a1b2c3d4e5f6", 41))
    assert (await client.get("/sidebar/hidden")).json() == {"hidden": [SAPPHIRE, OFFLINE]}

    present(monkeypatch, ("0000000000aa", 99))  # a new sapphire took over
    assert (await client.get("/sidebar/hidden")).json() == {"hidden": [OFFLINE]}


class TestStampInstances:
    def test_an_engine_session_and_its_rediscovery_are_one_instance(self):
        found = [{"session_id": "a1b2c3d4e5f6", "daemon_pid": 41, "discovered": True}]
        launched = {"session_id": "wolf-reign", "daemon_pid": 41}
        rediscovered = {"session_id": "a1b2c3d4e5f6", "daemon_pid": 41, "discovered": True}

        hidden_agents.stamp_instances([launched], found)
        hidden_agents.stamp_instances([rediscovered], found)  # the engine restarted

        assert launched["instance"] == rediscovered["instance"] == "a1b2c3d4e5f6"

    def test_a_relaunched_agent_is_another_instance(self):
        old = {"session_id": "wolf-reign", "daemon_pid": 41}
        new = {"session_id": "mist-glade", "daemon_pid": 77}
        found = [
            {"session_id": "a1b2c3d4e5f6", "daemon_pid": 41, "discovered": True},
            {"session_id": "0000000000aa", "daemon_pid": 77, "discovered": True},
        ]

        hidden_agents.stamp_instances([old, new], found)

        assert (old["instance"], new["instance"]) == ("a1b2c3d4e5f6", "0000000000aa")

    def test_with_no_daemon_in_presence_the_session_id_stands_in(self):
        rows = [{"session_id": "acp-1", "daemon_pid": 0}, {"session_id": "acp-2"}]

        hidden_agents.stamp_instances(rows, [])

        assert [row["instance"] for row in rows] == ["acp-1", "acp-2"]

    def test_presence_going_stale_does_not_change_an_engine_sessions_instance(self):
        found = [{"session_id": "a1b2c3d4e5f6", "daemon_pid": 41, "discovered": True}]
        row = {"session_id": "wolf-reign", "daemon_pid": 41}
        hidden_agents.stamp_instances([row], found)

        again = {"session_id": "wolf-reign", "daemon_pid": 41}
        hidden_agents.stamp_instances([again], [])  # the machine just woke: no heartbeat is fresh yet

        assert again["instance"] == "a1b2c3d4e5f6"

    def test_a_session_that_is_gone_is_forgotten(self):
        found = [{"session_id": "a1b2c3d4e5f6", "daemon_pid": 41, "discovered": True}]
        hidden_agents.stamp_instances([{"session_id": "wolf-reign", "daemon_pid": 41}], found)

        hidden_agents.stamp_instances([], found)

        assert hidden_agents._learned == {}


@pytest.mark.asyncio
async def test_list_sessions_names_each_rows_instance_and_the_hidden_list(client, monkeypatch):
    launched = SimpleNamespace(
        session_id="wolf-reign",
        alive=True,
        to_dict=lambda: {"session_id": "wolf-reign", "identity": "sapphire", "daemon_pid": 41, "external": False},
    )
    registry = sessions_route.get_session_registry()
    registry["wolf-reign"] = launched
    present(monkeypatch, ("a1b2c3d4e5f6", 41), ("0000000000aa", 77))
    try:
        await client.post("/sidebar/hide", json=SAPPHIRE)  # hidden through the instance, not the session name
        await client.post("/sidebar/hide", json=OFFLINE)

        body = (await client.get("/sessions")).json()
    finally:
        registry.pop("wolf-reign", None)

    assert {row["session_id"]: row["instance"] for row in body["sessions"]} == {
        "wolf-reign": "a1b2c3d4e5f6",
        "0000000000aa": "0000000000aa",
    }
    assert body["hidden"] == [SAPPHIRE, OFFLINE]

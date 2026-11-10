"""The PR-chain read routes show an external runner's state folder (#1008)."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A scratch runner folder. The real one is never read."""
    folder = tmp_path / "pr-chain-root"
    folder.mkdir()
    monkeypatch.setenv("KOLLAB_PR_CHAIN_ROOT", str(folder))
    return folder


def write_state(root, kind, number, **fields):
    folder = root / kind / str(number)
    folder.mkdir(parents=True)
    (folder / "STATE.json").write_text(json.dumps(fields))


@pytest.mark.asyncio
async def test_missing_root_is_an_empty_list(client, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_PR_CHAIN_ROOT", str(tmp_path / "nope"))
    body = (await client.get("/pr-chain")).json()
    assert body == {"items": [], "events": [], "root_found": False}


@pytest.mark.asyncio
async def test_lists_prs_and_issues_newest_first(client, root):
    write_state(
        root,
        "prs",
        766,
        status="AWAIT-HUMAN",
        updated="2026-11-09T16:35:29",
        round=2,
        head="7666ec53db73",
        last_verdict="PASS",
        why="screenshots posted",
    )
    write_state(root, "issues", 893, status="BUILDING", updated="2026-11-09T17:19:02")
    body = (await client.get("/pr-chain")).json()
    assert body["root_found"] is True
    assert [(i["kind"], i["number"]) for i in body["items"]] == [("issue", 893), ("pr", 766)]
    pr = body["items"][1]
    assert pr["status"] == "AWAIT-HUMAN" and pr["round"] == 2 and pr["why"] == "screenshots posted"


@pytest.mark.asyncio
async def test_a_half_written_last_ledger_line_is_skipped(client, root):
    lines = [json.dumps({"pr": 7, "stage": "verdict", "verdict": "PASS", "ts": "t1"}), '{"pr": 7, "sta']
    (root / "ledger.jsonl").write_text("\n".join(lines))
    body = (await client.get("/pr-chain")).json()
    assert [e["stage"] for e in body["events"]] == ["verdict"]


@pytest.mark.asyncio
async def test_a_broken_state_file_does_not_hide_the_others(client, root):
    write_state(root, "prs", 1, status="READY", updated="2026-11-09T10:00:00")
    (root / "prs" / "2").mkdir()
    (root / "prs" / "2" / "STATE.json").write_text("{not json")
    body = (await client.get("/pr-chain")).json()
    assert [i["number"] for i in body["items"]] == [1]


@pytest.mark.asyncio
async def test_detail_has_the_items_events_only(client, root):
    write_state(root, "prs", 5, status="READY", updated="x")
    rows = [{"pr": 5, "stage": "fold", "ts": "a"}, {"pr": 6, "stage": "fold", "ts": "b"}]
    (root / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    body = (await client.get("/pr-chain/pr/5")).json()
    assert body["item"]["status"] == "READY"
    assert [e["pr"] for e in body["events"]] == [5]


@pytest.mark.asyncio
async def test_detail_of_a_stranger_is_404_and_a_bad_kind_is_422(client, root):
    assert (await client.get("/pr-chain/pr/404")).status_code == 404
    assert (await client.get("/pr-chain/../x/1")).status_code in (404, 422)
    assert (await client.get("/pr-chain/foo/1")).status_code == 422

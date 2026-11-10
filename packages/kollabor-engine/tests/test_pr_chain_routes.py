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


@pytest.mark.asyncio
async def test_state_fields_of_the_wrong_type_come_out_as_plain_values(client, root):
    write_state(root, "prs", 3, status=["x"], updated=12, round="two", last_verdict={"a": 1}, why=None, pr="3")
    item = (await client.get("/pr-chain")).json()["items"][0]
    assert item["status"] == "['x']" and item["updated"] == "12" and item["last_verdict"] == "{'a': 1}"
    assert "round" not in item and "why" not in item and item["pr"] == 3


@pytest.mark.asyncio
async def test_events_of_the_wrong_type_come_out_as_plain_values(client, root):
    rows = [{"pr": 8, "stage": 5, "status": ["a"], "ts": None, "round": "x"}, {"issue": "9", "verdict": 1}]
    (root / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    events = (await client.get("/pr-chain")).json()["events"]
    assert events[0] == {"pr": 8, "stage": "5", "status": "['a']"}
    assert events[1] == {"issue": 9, "verdict": "1"}


@pytest.mark.asyncio
async def test_a_folder_named_with_non_ascii_digits_does_not_break_the_list(client, root):
    write_state(root, "prs", 1, status="READY", updated="a")
    (root / "prs" / "²").mkdir()
    (root / "prs" / "١٢").mkdir()
    body = (await client.get("/pr-chain")).json()
    assert [i["number"] for i in body["items"]] == [1]


@pytest.mark.asyncio
async def test_claimed_means_a_live_runner_process(client, root):
    import os

    write_state(root, "prs", 1, status="REVIEWING", updated="a")
    write_state(root, "issues", 2, status="BUILDING", updated="b")
    (root / "claims" / "1").mkdir(parents=True)
    (root / "claims" / "1" / "pid").write_text(str(os.getpid()))
    (root / "claims" / "i2").mkdir(parents=True)
    (root / "claims" / "i2" / "pid").write_text("999999999")
    items = {(i["kind"], i["number"]): i["claimed"] for i in (await client.get("/pr-chain")).json()["items"]}
    assert items == {("pr", 1): True, ("issue", 2): False}


@pytest.mark.asyncio
async def test_an_oversized_state_file_is_skipped(client, root):
    write_state(root, "prs", 1, status="READY", updated="a")
    write_state(root, "prs", 2, status="READY", updated="b", why="x" * (1024 * 1024))
    assert [i["number"] for i in (await client.get("/pr-chain")).json()["items"]] == [1]


@pytest.mark.asyncio
async def test_detail_matches_events_whose_pr_is_a_string(client, root):
    write_state(root, "prs", 5, status="READY", updated="x")
    (root / "ledger.jsonl").write_text(json.dumps({"pr": "5", "stage": "fold"}) + "\n")
    assert [e["stage"] for e in (await client.get("/pr-chain/pr/5")).json()["events"]] == ["fold"]


@pytest.mark.asyncio
async def test_an_agent_event_logged_under_an_issue_tag_belongs_to_that_issue(client, root):
    write_state(root, "issues", 893, status="BUILDING", updated="x")
    write_state(root, "prs", 893, status="READY", updated="x")
    row = {"stage": "agent", "pr": "i893", "role": "build", "rc": 124}
    (root / "ledger.jsonl").write_text(json.dumps(row) + "\n")
    assert [e["stage"] for e in (await client.get("/pr-chain/issue/893")).json()["events"]] == ["agent"]
    assert (await client.get("/pr-chain/pr/893")).json()["events"] == []
    event = (await client.get("/pr-chain")).json()["events"][0]
    assert event.get("issue") == 893 and "pr" not in event


@pytest.mark.asyncio
async def test_issue_detail_and_the_event_cap(client, root):
    write_state(root, "issues", 7, status="BUILDING", updated="x")
    rows = [{"issue": 7, "stage": f"s{i}"} for i in range(250)] + [{"pr": 7, "stage": "other"}]
    (root / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    detail = (await client.get("/pr-chain/issue/7")).json()
    assert detail["item"]["number"] == 7
    assert len(detail["events"]) == 200 and detail["events"][-1]["stage"] == "s249"
    assert len((await client.get("/pr-chain")).json()["events"]) == 200


@pytest.mark.asyncio
async def test_only_the_ledger_tail_is_read(client, root):
    filler = json.dumps({"pr": 1, "stage": "old", "why": "x" * 200}) + "\n"
    (root / "ledger.jsonl").write_text(filler * 3000 + json.dumps({"pr": 2, "stage": "new"}) + "\n")
    events = (await client.get("/pr-chain")).json()["events"]
    assert events[-1]["stage"] == "new" and len(events) <= 200


@pytest.mark.asyncio
async def test_a_huge_digit_string_is_dropped_not_a_500(client, root):
    huge = "9" * 5000
    write_state(root, "prs", 1, status="READY", updated="a", round=huge, pr=huge)
    (root / "ledger.jsonl").write_text(json.dumps({"pr": huge, "issue": huge, "stage": "x"}) + "\n")
    body = (await client.get("/pr-chain")).json()
    assert body["items"][0]["number"] == 1 and "round" not in body["items"][0]
    assert body["events"] == [{"stage": "x"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", ["0", "1", "-1", "9" * 30, "abc", ""])
async def test_a_bad_claim_pid_is_not_a_live_runner(client, root, pid):
    write_state(root, "prs", 1, status="REVIEWING", updated="a")
    (root / "claims" / "1").mkdir(parents=True)
    (root / "claims" / "1" / "pid").write_text(pid)
    response = await client.get("/pr-chain")
    assert response.status_code == 200 and response.json()["items"][0]["claimed"] is False


@pytest.mark.asyncio
async def test_deeply_nested_json_hides_only_itself(client, root):
    write_state(root, "prs", 1, status="READY", updated="a")
    (root / "prs" / "2").mkdir()
    (root / "prs" / "2" / "STATE.json").write_text("[" * 100_000 + "]" * 100_000)
    good = json.dumps({"pr": 1, "stage": "ok"})
    (root / "ledger.jsonl").write_text(good + "\n" + "[" * 100_000 + "\n" + good + "\n")
    body = (await client.get("/pr-chain")).json()
    assert [i["number"] for i in body["items"]] == [1]
    assert [e["stage"] for e in body["events"]] == ["ok", "ok"]


@pytest.mark.asyncio
async def test_a_lone_surrogate_in_runner_text_is_not_a_500(client, root):
    folder = root / "prs" / "1"
    folder.mkdir(parents=True)
    (folder / "STATE.json").write_text('{"status": "A\\ud800B", "why": "x"}')
    (root / "ledger.jsonl").write_text('{"pr": 1, "stage": "s\\udfffx"}\n')
    response = await client.get("/pr-chain")
    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "A?B"
    assert response.json()["events"][0]["stage"] == "s?x"


@pytest.mark.asyncio
async def test_an_unreadable_claim_pid_is_not_a_live_runner(client, root):
    write_state(root, "prs", 1, status="REVIEWING", updated="a")
    pid_file = root / "claims" / "1" / "pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("1")
    pid_file.chmod(0)
    try:
        response = await client.get("/pr-chain")
    finally:
        pid_file.chmod(0o600)
    assert response.status_code == 200 and response.json()["items"][0]["claimed"] is False


@pytest.mark.asyncio
async def test_a_unicode_line_separator_inside_a_ledger_line_keeps_the_event(client, root):
    line = json.dumps({"pr": 1, "stage": "review complete"}, ensure_ascii=False)
    (root / "ledger.jsonl").write_text(line + "\n", encoding="utf-8")
    events = (await client.get("/pr-chain")).json()["events"]
    assert [e["stage"] for e in events] == ["review complete"]


def release_fifo_later(path, after=1.5, give_up=4.0):
    """Frees a reader stuck on a named pipe, so a broken build fails the test instead of hanging it."""
    import os
    import threading
    import time

    def run():
        time.sleep(after)
        deadline = time.monotonic() + give_up
        while time.monotonic() < deadline:
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
                return
            except OSError:
                time.sleep(0.1)

    threading.Thread(target=run, daemon=True).start()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["prs/7/STATE.json", "claims/7/pid", "ledger.jsonl"])
async def test_a_named_pipe_in_the_runner_folder_never_blocks_the_read(client, root, where):
    import os
    import time

    write_state(root, "prs", 1, status="READY", updated="a")
    (root / "claims" / "7").mkdir(parents=True)
    if where == "prs/7/STATE.json":
        (root / "prs" / "7").mkdir()
    else:
        write_state(root, "prs", 7, status="READY", updated="b")
    pipe = root / where
    os.mkfifo(pipe)
    release_fifo_later(pipe)
    started = time.monotonic()
    response = await client.get("/pr-chain")
    assert time.monotonic() - started < 1.0
    assert response.status_code == 200
    assert 1 in [i["number"] for i in response.json()["items"]]


@pytest.mark.asyncio
async def test_a_zero_padded_folder_name_is_not_a_second_copy_of_the_item(client, root):
    write_state(root, "prs", 7, status="READY", updated="a")
    write_state(root, "prs", "007", status="FAILED", updated="b")
    write_state(root, "issues", "08", status="BUILDING", updated="c")
    items = (await client.get("/pr-chain")).json()["items"]
    assert [(i["kind"], i["number"], i["status"]) for i in items] == [("pr", 7, "READY")]


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [990, 20_000, 60_000, 90_000])
async def test_a_value_nested_just_inside_the_parser_limit_is_not_a_500(client, root, depth):
    deep = "[" * depth + "]" * depth
    write_state(root, "prs", 1, status="READY", updated="a")
    folder = root / "prs" / "2"
    folder.mkdir()
    (folder / "STATE.json").write_text('{"status": ' + deep + ', "updated": "b"}')
    good = json.dumps({"pr": 1, "stage": "ok"})
    (root / "ledger.jsonl").write_text(good + "\n" + '{"pr": 2, "stage": ' + deep + "}\n" + good + "\n")
    response = await client.get("/pr-chain")
    assert response.status_code == 200
    body = response.json()
    assert 1 in [i["number"] for i in body["items"]]
    assert [e["stage"] for e in body["events"] if "stage" in e].count("ok") == 2


def make_symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


@pytest.mark.asyncio
async def test_a_symlinked_state_file_is_not_followed(client, root, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"status": "LEAKED", "updated": "z"}))
    write_state(root, "prs", 1, status="READY", updated="a")
    folder = root / "prs" / "7"
    folder.mkdir()
    make_symlink(folder / "STATE.json", outside)
    body = (await client.get("/pr-chain")).json()
    assert [i["number"] for i in body["items"]] == [1]
    assert "LEAKED" not in json.dumps(body)
    assert (await client.get("/pr-chain/pr/7")).status_code == 404


@pytest.mark.asyncio
async def test_a_symlinked_item_folder_is_not_followed(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "STATE.json").write_text(json.dumps({"status": "LEAKED", "updated": "z"}))
    write_state(root, "prs", 1, status="READY", updated="a")
    make_symlink(root / "prs" / "7", elsewhere)
    body = (await client.get("/pr-chain")).json()
    assert [i["number"] for i in body["items"]] == [1]
    assert "LEAKED" not in json.dumps(body)
    assert (await client.get("/pr-chain/pr/7")).status_code == 404


@pytest.mark.asyncio
async def test_a_symlinked_kind_folder_is_not_followed(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "5").mkdir(parents=True)
    (elsewhere / "5" / "STATE.json").write_text(json.dumps({"status": "LEAKED", "updated": "z"}))
    make_symlink(root / "prs", elsewhere)
    body = (await client.get("/pr-chain")).json()
    assert body["items"] == []
    assert "LEAKED" not in json.dumps(body)


@pytest.mark.asyncio
async def test_a_symlinked_ledger_and_claim_pid_are_not_followed(client, root, tmp_path):
    import os

    outside = tmp_path / "outside.txt"
    outside.write_text(json.dumps({"pr": 1, "stage": "LEAKED"}) + "\n")
    make_symlink(root / "ledger.jsonl", outside)
    write_state(root, "prs", 1, status="READY", updated="a")
    (root / "claims" / "1").mkdir(parents=True)
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    make_symlink(root / "claims" / "1" / "pid", pid_file)
    body = (await client.get("/pr-chain")).json()
    assert body["events"] == []
    assert body["items"][0]["claimed"] is False


@pytest.mark.asyncio
async def test_the_list_returns_only_the_newest_hundred_items_of_each_kind(client, root):
    for number in range(1, 131):
        write_state(root, "prs", number, status="READY", updated=f"2026-11-09T10:{number % 60:02d}:00")
    write_state(root, "issues", 5, status="BUILDING", updated="2026-11-09T09:00:00")
    items = (await client.get("/pr-chain")).json()["items"]
    prs = sorted(i["number"] for i in items if i["kind"] == "pr")
    assert prs == list(range(31, 131))
    assert [i["number"] for i in items if i["kind"] == "issue"] == [5]


@pytest.mark.asyncio
async def test_a_folder_with_a_huge_number_of_entries_is_only_partly_examined(client, root, monkeypatch):
    from kollabor_engine import pr_chain

    monkeypatch.setattr(pr_chain, "MAX_SCAN", 30, raising=False)
    for number in range(1, 131):
        write_state(root, "prs", number, status="READY", updated="a")
    items = (await client.get("/pr-chain")).json()["items"]
    assert 0 < len(items) <= 30

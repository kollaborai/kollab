"""Revisioned sidebar order must survive restarts and reject stale writes."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from kollabor_engine import sidebar_order  # type: ignore[import-not-found]
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

KEYS = ['["pos","/w/one","lapis"]', '["pos","/w/two","ruby"]', '["pos","/w/one","peridot"]']


def new_engine():
    """A fresh app instance, as when the engine starts."""
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


def _subprocess_env(home: Path) -> tuple[Path, dict[str, str]]:
    repo_root = Path(__file__).resolve().parents[3]
    source_paths = [repo_root, *sorted((repo_root / "packages").glob("*/src"), key=str)]
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["KOLLAB_ENGINE_BYPASS_AUTH"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([*(str(path) for path in source_paths), env.get("PYTHONPATH", "")])
    return repo_root, env


@pytest.mark.asyncio
async def test_order_survives_an_engine_process_restart(no_real_sidebar_order, tmp_path):
    async with new_engine() as client:
        initial = (await client.get("/sidebar/order")).json()
        assert initial == {"order": [], "revision": 0, "warning": None}
        saved = await client.post("/sidebar/order", json={"order": KEYS, "revision": 0})
        assert saved.json() == {"order": KEYS, "revision": 1, "warning": None}
    assert json.loads(no_real_sidebar_order.read_text()) == {"order": KEYS, "revision": 1}

    # A second OS process reconstructs the engine and reads the order through
    # the same /sessions payload the Web UI uses on page reload.
    repo_root, env = _subprocess_env(tmp_path)
    program = """
import asyncio
import json
import pytest
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app

async def main():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/sessions")
        response.raise_for_status()
        body = response.json()
        print(json.dumps({
            "order": body["order"],
            "revision": body["order_revision"],
            "warning": body["order_warning"],
        }))

asyncio.run(main())
"""
    restarted = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    output = [line for line in restarted.stdout.splitlines() if line.strip()]
    assert json.loads(output[-1]) == {"order": KEYS, "revision": 1, "warning": None}


@pytest.mark.asyncio
async def test_two_clients_reject_a_stale_whole_list(no_real_sidebar_order):
    async with new_engine() as first_client, new_engine() as second_client:
        first_snapshot = (await first_client.get("/sidebar/order")).json()
        second_snapshot = (await second_client.get("/sidebar/order")).json()
        first, second = await asyncio.gather(
            first_client.post(
                "/sidebar/order",
                json={"order": [KEYS[0]], "revision": first_snapshot["revision"]},
            ),
            second_client.post(
                "/sidebar/order",
                json={"order": [KEYS[1]], "revision": second_snapshot["revision"]},
            ),
        )
        assert sorted((first.status_code, second.status_code)) == [200, 409]
        saved = first if first.status_code == 200 else second
        rejected = second if first.status_code == 200 else first
        assert "another client" in rejected.json()["detail"].lower()
        current = (await first_client.get("/sidebar/order")).json()
        assert current["order"] == saved.json()["order"]
        assert current["revision"] == 1


def test_simultaneous_process_writes_are_check_and_replace_atomic(no_real_sidebar_order, tmp_path):
    repo_root, env = _subprocess_env(tmp_path)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    gate = tmp_path / "go"
    program = """
import json
import sys
import time
from pathlib import Path
from kollabor_engine import sidebar_order

ready = Path(sys.argv[1])
gate = Path(sys.argv[2])
keys = json.loads(sys.argv[3])
ready.write_text("ready")
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("timed out waiting for process barrier")
    time.sleep(0.01)
try:
    saved = sidebar_order.write_order(keys, expected_revision=0)
    print(json.dumps({"result": "saved", **saved.to_dict()}))
except sidebar_order.SidebarOrderConflict as exc:
    print(json.dumps({"result": "conflict", **exc.state.to_dict()}))
"""
    processes: list[subprocess.Popen[str]] = []
    try:
        for index, key in enumerate(KEYS[:2]):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        program,
                        str(ready_dir / str(index)),
                        str(gate),
                        json.dumps([key]),
                    ],
                    cwd=repo_root,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 10
        while not all((ready_dir / str(index)).exists() for index in range(2)):
            if time.monotonic() >= deadline:
                pytest.fail("writer processes did not reach the test barrier")
            time.sleep(0.01)
        gate.touch()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout.splitlines()[-1]))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    assert sorted(result["result"] for result in results) == ["conflict", "saved"]
    state = sidebar_order.read_order_state()
    assert state.revision == 1
    assert state.order in ([KEYS[0]], [KEYS[1]])


@pytest.mark.asyncio
async def test_corrupt_order_is_warned_and_never_overwritten(no_real_sidebar_order):
    no_real_sidebar_order.parent.mkdir(parents=True)
    damaged = b'{"order": [broken'
    no_real_sidebar_order.write_bytes(damaged)
    async with new_engine() as client:
        state = (await client.get("/sidebar/order")).json()
        assert state["order"] == []
        assert state["revision"] == 0
        assert "preserved" in state["warning"].lower()
        assert (await client.get("/sessions")).json()["order_warning"] == state["warning"]
        response = await client.post("/sidebar/order", json={"order": KEYS, "revision": 0})
        assert response.status_code == 422
        assert "preserved" in response.json()["detail"].lower()
    assert no_real_sidebar_order.read_bytes() == damaged
    assert "preserved" in sidebar_order.read_order_state().warning.lower()


@pytest.mark.asyncio
async def test_missing_file_is_normal_and_legacy_file_starts_at_revision_zero(no_real_sidebar_order):
    async with new_engine() as client:
        missing = (await client.get("/sidebar/order")).json()
        assert missing == {"order": [], "revision": 0, "warning": None}
        no_real_sidebar_order.parent.mkdir(parents=True)
        no_real_sidebar_order.write_text(json.dumps({"order": KEYS}), encoding="utf-8")
        legacy = (await client.get("/sidebar/order")).json()
        assert legacy == {"order": KEYS, "revision": 0, "warning": None}
        saved = await client.post("/sidebar/order", json={"order": KEYS[::-1], "revision": 0})
        assert saved.json() == {"order": KEYS[::-1], "revision": 1, "warning": None}


@pytest.mark.asyncio
async def test_rejects_malformed_order_and_revision(no_real_sidebar_order):
    async with new_engine() as client:
        for bad in ("lapis", [3], [""], ["a\nb"], ["x" * 1025]):
            response = await client.post("/sidebar/order", json={"order": bad, "revision": 0})
            assert response.status_code == 400
        for bad_revision in (None, -1, True, "0"):
            response = await client.post("/sidebar/order", json={"order": KEYS, "revision": bad_revision})
            assert response.status_code == 400
        assert (await client.get("/sidebar/order")).json() == {"order": [], "revision": 0, "warning": None}


def test_duplicates_collapse():
    assert sidebar_order.parse_order(["a", "b", "a"]) == ["a", "b"]

"""The sidebar order is engine-owned and must survive a real process restart."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from kollabor_engine import sidebar_order  # type: ignore[import-not-found]
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

KEYS = ['["pos","/w/one","lapis"]', '["pos","/w/two","ruby"]', '["pos","/w/one","peridot"]']


def new_engine():
    """A fresh app instance, as when the engine starts."""
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


@pytest.mark.asyncio
async def test_order_survives_an_engine_process_restart(no_real_sidebar_order, tmp_path):
    async with new_engine() as client:
        assert (await client.get("/sidebar/order")).json() == {"order": []}
        saved = await client.post("/sidebar/order", json={"order": KEYS})
        assert saved.json() == {"order": KEYS}
    assert json.loads(no_real_sidebar_order.read_text()) == {"order": KEYS}

    # A second OS process reconstructs the engine and reads the order through
    # the same /sessions payload the Web UI uses on page reload.
    repo_root = Path(__file__).resolve().parents[3]
    source_paths = [repo_root, *sorted((repo_root / "packages").glob("*/src"), key=str)]
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["KOLLAB_ENGINE_BYPASS_AUTH"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([*(str(path) for path in source_paths), env.get("PYTHONPATH", "")])
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
        print(json.dumps(response.json()["order"]))

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
    assert json.loads(output[-1]) == KEYS


@pytest.mark.asyncio
async def test_rejects_malformed_order():
    async with new_engine() as client:
        for bad in ("lapis", [3], [""], ["a\nb"], ["x" * 1025]):
            assert (await client.post("/sidebar/order", json={"order": bad})).status_code == 400
        assert (await client.get("/sidebar/order")).json() == {"order": []}


def test_duplicates_collapse_and_unreadable_file_is_empty(no_real_sidebar_order):
    assert sidebar_order.parse_order(["a", "b", "a"]) == ["a", "b"]
    no_real_sidebar_order.parent.mkdir(parents=True)
    no_real_sidebar_order.write_text("{nope")
    assert sidebar_order.read_order() == []

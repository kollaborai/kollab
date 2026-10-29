"""Tests for gem headshots: GET/PUT /agents/headshot keeps one PNG per look key
under the user's home folder, the picture a browser without WebGL shows."""

import hashlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

URL = "/agents/headshot"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
LIMIT = 256 * 1024


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_round_trip(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    put = await client.put(URL, params={"key": "lapis"}, content=PNG)
    assert put.status_code == 204
    got = await client.get(URL, params={"key": "lapis"})
    assert got.status_code == 200
    assert got.content == PNG
    assert got.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_unknown_key_is_not_found(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    got = await client.get(URL, params={"key": "nobody"})
    assert got.status_code == 404


@pytest.mark.asyncio
async def test_key_with_folders_stays_inside_headshots(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    put = await client.put(URL, params={"key": "../../evil"}, content=PNG)
    assert put.status_code == 204
    headshots = tmp_path / ".kollab" / "hub" / "headshots"
    stored = headshots / (hashlib.sha256("../../evil".encode()).hexdigest() + ".png")
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files == [stored]
    assert all(headshots in p.parents for p in files)


@pytest.mark.asyncio
async def test_rejects_what_is_not_a_png(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    put = await client.put(URL, params={"key": "lapis"}, content=b"GIF89a...")
    assert put.status_code == 400


@pytest.mark.asyncio
async def test_size_limit_is_256_kib(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    full = PNG + b"x" * (LIMIT - len(PNG))
    put = await client.put(URL, params={"key": "lapis"}, content=full)
    assert put.status_code == 204
    put = await client.put(URL, params={"key": "lapis"}, content=full + b"x")
    assert put.status_code == 413


@pytest.mark.asyncio
async def test_rejects_a_key_over_4096_bytes(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    put = await client.put(URL, params={"key": "k" * 4097}, content=PNG)
    assert put.status_code == 400

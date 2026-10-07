"""Tests for the Gem Studio's saved looks (GET/PUT /agents/appearance)."""

import json

import kollabor_engine.gem_appearance as gem_appearance  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

EMPTY = {"season": "auto", "defaults": {}, "gems": {}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "hub" / "appearance.json"
    monkeypatch.setattr(gem_appearance, "appearance_path", lambda: path)
    return path


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


class TestNormalize:
    def test_keeps_well_formed_looks(self):
        doc = {
            "season": "halloween",
            "defaults": {"face": "disney", "hat": "auto"},
            "gems": {"lapis": {"face": "kawaii", "hat": "crown", "color": [30, 90, 180]}},
        }
        assert gem_appearance.normalize(doc) == doc

    def test_drops_malformed_fields(self):
        doc = {
            "season": "Not A Slug",
            "defaults": {"face": "pill", "color": [1, 2, 3]},
            "gems": {
                "lapis": {"face": 7, "hat": "crown", "color": [256, 0, 0]},
                "ruby": {"color": [True, 0, 0]},
                "Bad Name": {"face": "pill"},
                "opal": "not a look",
            },
        }
        assert gem_appearance.normalize(doc) == {
            "season": "auto",
            "defaults": {"face": "pill"},
            "gems": {"lapis": {"hat": "crown"}},
        }

    def test_non_object_is_empty(self):
        assert gem_appearance.normalize(["nope"]) == EMPTY


@pytest.mark.asyncio
class TestAppearanceRoutes:
    async def test_missing_file_reads_as_empty(self, client, store):
        assert (await client.get("/agents/appearance")).json() == EMPTY

    async def test_put_saves_and_get_reads_back(self, client, store):
        doc = {
            "season": "none",
            "defaults": {"face": "disney"},
            "gems": {"lapis": {"hat": "crown", "color": [10, 20, 30]}},
        }
        saved = (await client.put("/agents/appearance", json=doc)).json()

        assert saved == doc
        assert json.loads(store.read_text()) == doc
        assert (await client.get("/agents/appearance")).json() == doc

    async def test_put_drops_bad_fields_before_writing(self, client, store):
        saved = (
            await client.put(
                "/agents/appearance",
                json={"gems": {"lapis": {"color": "blue", "face": "kawaii"}}},
            )
        ).json()

        assert saved["gems"] == {"lapis": {"face": "kawaii"}}
        assert json.loads(store.read_text())["gems"] == {"lapis": {"face": "kawaii"}}

    async def test_unreadable_file_reads_as_empty(self, client, store):
        store.parent.mkdir(parents=True)
        store.write_text("{not json")

        assert (await client.get("/agents/appearance")).json() == EMPTY

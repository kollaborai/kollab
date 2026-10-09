"""Tests for gem looks: births (record_births) and the Gem Studio's
GET/PUT /agents/appearance. The conftest points the store at a temp file."""

import json
import os
import re
from pathlib import Path

import kollabor_engine.gem_appearance as gem_appearance  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

EMPTY = {"season": "auto", "born": {}, "gems": {}}
# The routes also say which folder a bare gem name means: this engine's.
HOME = {"home": os.path.realpath(os.getcwd())}
GEM_FACE_TS = (
    Path(__file__).resolve().parents[3]
    / "packages/kollabor-webui/frontend/src/components/gems/gem-face.ts"
)


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
            "born": {"lapis": {"face": "kawaii", "hat": "beret"}},
            "gems": {"lapis": {"face": "disney", "hat": "crown", "color": [30, 90, 180]}},
        }
        assert gem_appearance.normalize(doc) == doc

    def test_drops_malformed_fields(self):
        doc = {
            "season": "Not A Slug",
            "born": {"ruby": {"face": "pill", "color": [1, 2, 3]}, "Bad Name": {"face": "pill"}},
            "gems": {
                "lapis": {"face": 7, "hat": "crown", "color": [256, 0, 0]},
                "ruby": {"color": [True, 0, 0]},
                "Bad Name": {"face": "pill"},
                "opal": "not a look",
            },
            "defaults": {"face": "pill"},
        }
        assert gem_appearance.normalize(doc) == {
            "season": "auto",
            "born": {"ruby": {"face": "pill"}},
            "gems": {"lapis": {"hat": "crown"}},
        }

    def test_non_object_is_empty(self):
        assert gem_appearance.normalize(["nope"]) == EMPTY

    def test_an_agent_in_another_folder_or_on_another_computer_keeps_its_own_picks(self):
        looks = {
            "koordinator": {"face": "visor"},
            "koordinator@/Users/me/dev/webapp": {"face": "kawaii", "color": [30, 90, 180]},
            "koordinator@home-server": {"hat": "crown"},
        }
        doc = {"born": dict(looks), "gems": {
            **looks,
            "koordinator@": {"face": "pill"},
            "koordinator@bad\nline": {"face": "pill"},
            "Koordinator@home-server": {"face": "pill"},
            "koordinator@" + "x" * 1025: {"face": "pill"},
            "lapis\n": {"face": "pill"},
        }}
        clean = gem_appearance.normalize(doc)
        assert clean["gems"] == looks
        # Only the engine rolls born looks, and only for a gem's bare name.
        assert clean["born"] == {"koordinator": {"face": "visor"}}


class TestBirths:
    def test_a_gem_is_born_once_and_keeps_its_look(self, appearance_store):
        gem_appearance.record_births(["lapis"])
        look = gem_appearance.load_appearance()["born"]["lapis"]
        assert look["face"] in gem_appearance.BIRTH_FACES
        assert look["hat"] in gem_appearance.BIRTH_HATS

        for _ in range(20):
            gem_appearance.record_births(["lapis", "ruby"])

        born = json.loads(appearance_store.read_text())["born"]
        assert born["lapis"] == look
        assert set(born) == {"lapis", "ruby"}

    def test_unreadable_file_is_left_alone(self, appearance_store):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text("{not json")

        gem_appearance.record_births(["lapis"])

        assert appearance_store.read_text() == "{not json"

    @pytest.mark.skipif(not GEM_FACE_TS.exists(), reason="web UI source not in this checkout")
    def test_birth_styles_match_the_web_ui(self):
        text = GEM_FACE_TS.read_text(encoding="utf-8")
        entry = re.compile(r'\{ id: "([a-z0-9_-]+)", label: "[^"]*", group: "(\w*)" \}')

        def everyday(const):
            block = text.split(f"export const {const}", 1)[1].split("export const", 1)[0]
            return {id_ for id_, group in entry.findall(block) if group not in ("Halloween", "Christmas")}

        assert everyday("EYE_STYLES") == set(gem_appearance.BIRTH_FACES)
        assert everyday("HAT_STYLES") == set(gem_appearance.BIRTH_HATS)


@pytest.mark.asyncio
class TestAppearanceRoutes:
    async def test_missing_file_reads_as_empty(self, client):
        assert (await client.get("/agents/appearance")).json() == {**EMPTY, **HOME}

    async def test_put_saves_and_get_reads_back(self, client, appearance_store):
        doc = {
            "season": "none",
            "born": {},
            "gems": {"lapis": {"hat": "crown", "color": [10, 20, 30]}},
        }
        saved = (await client.put("/agents/appearance", json={**doc, "home": "/forged"})).json()

        assert saved == {**doc, **HOME}
        assert json.loads(appearance_store.read_text()) == doc
        assert (await client.get("/agents/appearance")).json() == {**doc, **HOME}

    async def test_put_keeps_born_looks_from_disk(self, client):
        gem_appearance.record_births(["lapis"])
        born = gem_appearance.load_appearance()["born"]

        # A studio opened before the birth sends none; a forged body sends its own.
        for body in ({"gems": {}}, {"born": {"lapis": {"face": "lantern"}}}):
            saved = (await client.put("/agents/appearance", json=body)).json()
            assert saved["born"] == born

    async def test_put_drops_bad_fields_before_writing(self, client, appearance_store):
        saved = (
            await client.put(
                "/agents/appearance",
                json={"gems": {"lapis": {"color": "blue", "face": "kawaii"}}},
            )
        ).json()

        assert saved["gems"] == {"lapis": {"face": "kawaii"}}
        assert json.loads(appearance_store.read_text())["gems"] == {"lapis": {"face": "kawaii"}}

    async def test_unreadable_file_reads_as_empty(self, client, appearance_store):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text("{not json")

        assert (await client.get("/agents/appearance")).json() == {**EMPTY, **HOME}

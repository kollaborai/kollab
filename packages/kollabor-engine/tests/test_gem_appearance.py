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
            "gems": {
                "lapis": {"face": "disney", "hat": "crown", "color": [30, 90, 180]}
            },
        }
        assert gem_appearance.normalize(doc) == doc

    def test_drops_malformed_fields(self):
        doc = {
            "season": "Not A Slug",
            "born": {
                "ruby": {"face": "pill", "color": [1, 2, 3]},
                "Bad Name": {"face": "pill"},
            },
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

    def test_keeps_a_picked_avatar_and_shape_and_drops_a_bad_one(self):
        picture = "data:image/webp;base64,UklGRg=="
        doc = {
            "born": {"lapis": {"avatar": {"kind": "initials"}, "cut": "heart"}},
            "gems": {
                "omar": {"avatar": {"kind": "emoji", "value": " 🦊 "}, "cut": "hex"},
                "maya": {"avatar": {"kind": "image", "url": picture}},
                "lapis": {"avatar": {"kind": "initials"}, "color": [1, 2, 3]},
                "theo": {"avatar": {"kind": "gem", "extra": 1}},
                "svg": {
                    "avatar": {
                        "kind": "image",
                        "url": "data:image/svg+xml;base64,PHN2Zz4=",
                    }
                },
                "web": {
                    "avatar": {"kind": "image", "url": "https://example.com/a.png"}
                },
                "big": {
                    "avatar": {
                        "kind": "image",
                        "url": "data:image/png;base64," + "A" * 200_000,
                    }
                },
                "empty": {"avatar": {"kind": "emoji", "value": "  "}},
                "odd": {"avatar": {"kind": "sticker"}},
            },
        }
        clean = gem_appearance.normalize(doc)
        assert clean["born"] == {}
        assert clean["gems"] == {
            "omar": {"avatar": {"kind": "emoji", "value": "🦊"}, "cut": "hex"},
            "maya": {"avatar": {"kind": "image", "url": picture}},
            "lapis": {"avatar": {"kind": "initials"}, "color": [1, 2, 3]},
            "theo": {"avatar": {"kind": "gem"}},
        }

    def test_keeps_hair_fur_and_auras_and_drops_bad_ones(self):
        doc = {
            "born": {
                "lapis": {
                    "hair": "long",
                    "hairColor": [95, 60, 35],
                    "auras": ["glow"],
                    "sparkles": "lots",
                }
            },
            "gems": {
                "lapis": {
                    "hair": "mohawk",
                    "hairColor": "gem",
                    "fur": "shaggy",
                    "furColor": [225, 190, 120],
                },
                "ruby": {
                    "auras": ["glow", "orbit", "glow", "Bad", 3],
                    "sparkles": "few",
                    "auraColor": [200, 30, 50],
                },
                "opal": {
                    "hair": "Not A Slug",
                    "hairColor": "blue",
                    "furColor": [1, 2],
                    "auras": "glow",
                    "auraColor": "pink",
                },
            },
        }
        clean = gem_appearance.normalize(doc)
        # A born look keeps its coat, never an aura.
        assert clean["born"] == {"lapis": {"hair": "long", "hairColor": [95, 60, 35]}}
        assert clean["gems"] == {
            "lapis": {
                "hair": "mohawk",
                "hairColor": "gem",
                "fur": "shaggy",
                "furColor": [225, 190, 120],
            },
            "ruby": {
                "auras": ["glow", "orbit"],
                "sparkles": "few",
                "auraColor": [200, 30, 50],
            },
        }

    def test_keeps_coat_knobs_and_drops_bad_ones(self):
        doc = {
            "born": {"lapis": {"hair": "long", "hairKnobs": {"length": 0.7}}},
            "gems": {
                "lapis": {
                    "hair": "long",
                    "hairKnobs": {
                        "length": 0.7,
                        "lie": 2,
                        "fall": True,
                        "sway": "far",
                        "Bad Name": 1,
                        "clump": float("nan"),
                    },
                    "furKnobs": {"length": 99},
                },
                "ruby": {"hairKnobs": [0.3], "furKnobs": {"gloss": "high"}},
            },
        }
        clean = gem_appearance.normalize(doc)
        # Nothing is born with knob settings.
        assert clean["born"] == {"lapis": {"hair": "long"}}
        assert clean["gems"] == {
            "lapis": {
                "hair": "long",
                "hairKnobs": {"length": 0.7, "lie": 2.0},
                "furKnobs": {"length": 10.0},
            },
        }

    def test_non_object_is_empty(self):
        assert gem_appearance.normalize(["nope"]) == EMPTY

    def test_an_agent_in_another_folder_or_on_another_computer_keeps_its_own_picks(
        self,
    ):
        looks = {
            "koordinator": {"face": "visor"},
            "koordinator@/Users/me/dev/webapp": {
                "face": "kawaii",
                "color": [30, 90, 180],
            },
            "koordinator@home-server": {"hat": "crown"},
        }
        doc = {
            "born": dict(looks),
            "gems": {
                **looks,
                "koordinator@": {"face": "pill"},
                "koordinator@bad\nline": {"face": "pill"},
                "Koordinator@home-server": {"face": "pill"},
                "koordinator@" + "x" * 1025: {"face": "pill"},
                "lapis\n": {"face": "pill"},
            },
        }
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

    def test_most_gems_are_born_furry(self):
        coats = [gem_appearance._birth_coat(f"gem{i}") for i in range(1000)]
        furry = [coat for coat in coats if "fur" in coat]
        assert 800 <= len(furry) <= 900
        for coat in furry:
            assert coat["fur"] in gem_appearance.BIRTH_FURS
            assert coat["furColor"] in gem_appearance.BIRTH_FUR_COLORS
        for coat in coats:
            if coat.get("hair") not in (None, "none"):
                assert coat["hair"] in gem_appearance.BIRTH_HAIRS
                assert tuple(coat["hairColor"]) in gem_appearance.BIRTH_HAIR_COLORS

    def test_the_roll_matches_the_web_uis_draw_for_the_same_name(
        self, appearance_store
    ):
        # Computed by the web UI's own resolveGemLook over the same vocabulary
        # (gem-appearance.tsx GEM_LOOK_VOCABULARY): the engine's roll and the
        # client's fallback draw must never diverge.
        expected = {
            "lapis": {
                "face": "drowsy",
                "hat": "wizard",
                "hair": "long",
                "hairColor": [170, 170, 175],
            },
            "ruby": {
                "face": "bean",
                "hat": "bucket",
                "fur": "long",
                "furColor": [150, 60, 35],
            },
            "opal": {
                "face": "drowsy",
                "hat": "bucket",
                "hair": "bob",
                "hairColor": [170, 170, 175],
            },
            "peridot": {
                "face": "drowsy",
                "hat": "wizard",
                "fur": "velvet",
                "furColor": [235, 232, 225],
            },
            "sapphire": {
                "face": "smile",
                "hat": "crown",
                "fur": "velvet",
                "furColor": "gem",
            },
            "garnet": {
                "face": "kawaii",
                "hat": "cap",
                "fur": "short",
                "furColor": [95, 60, 35],
            },
            "bismuth": {
                "face": "bean",
                "hat": "sailor",
                "fur": "velvet",
                "furColor": "gem",
            },
        }
        gem_appearance.record_births(list(expected))
        assert gem_appearance.load_appearance()["born"] == expected

    def test_a_redrawn_birth_is_the_same_look(self, appearance_store):
        gem_appearance.record_births(["lapis", "ruby"])
        first = gem_appearance.load_appearance()["born"]
        appearance_store.unlink()
        gem_appearance.record_births(["lapis", "ruby"])
        assert gem_appearance.load_appearance()["born"] == first

    def test_a_gem_born_before_coats_gets_its_coat_drawn_once(self, appearance_store):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text(
            json.dumps({"born": {"lapis": {"face": "pill", "hat": "cap"}}})
        )
        gem_appearance.record_births([])
        lapis = json.loads(appearance_store.read_text())["born"]["lapis"]
        assert lapis["face"] == "pill" and lapis["hat"] == "cap"
        assert "hair" in lapis or "fur" in lapis
        gem_appearance.record_births([])
        assert json.loads(appearance_store.read_text())["born"]["lapis"] == lapis

    def test_a_coat_recorded_bare_before_fur_was_common_is_drawn_again(
        self, appearance_store
    ):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text(
            json.dumps(
                {
                    "born": {
                        # An old bare roll (fur was rare then) draws its coat again.
                        "lapis": {"face": "pill", "hat": "cap", "hair": "none"},
                        # A drawn coat sticks: this hair is never rolled over.
                        "ruby": {
                            "face": "dot",
                            "hat": "top",
                            "hair": "short",
                            "hairColor": [30, 26, 24],
                        },
                    }
                }
            )
        )
        gem_appearance.record_births([])
        born = gem_appearance.load_appearance()["born"]
        # Stored colors are JSON lists, the draw's tuples.
        assert born["lapis"] == json.loads(
            json.dumps(
                {"face": "pill", "hat": "cap", **gem_appearance._birth_coat("lapis")}
            )
        )
        assert born["ruby"] == {
            "face": "dot",
            "hat": "top",
            "hair": "short",
            "hairColor": [30, 26, 24],
        }

    def test_unreadable_file_is_left_alone(self, appearance_store):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text("{not json")

        gem_appearance.record_births(["lapis"])

        assert appearance_store.read_text() == "{not json"

    @pytest.mark.skipif(
        not GEM_FACE_TS.exists(), reason="web UI source not in this checkout"
    )
    def test_birth_styles_match_the_web_ui(self):
        text = GEM_FACE_TS.read_text(encoding="utf-8")
        entry = re.compile(r'\{ id: "([a-z0-9_-]+)", label: "[^"]*", group: "(\w*)" \}')

        def block(const):
            return text.split(f"export const {const}", 1)[1].split("export const", 1)[0]

        def everyday(const):
            return {
                id_
                for id_, group in entry.findall(block(const))
                if group not in ("Halloween", "Christmas")
            }

        appearance_ts = GEM_FACE_TS.with_name("gem-appearance.tsx").read_text(encoding="utf-8")
        faces = re.search(r"export const EVERYDAY_FACES: EyeStyle\[\] = \[(.*?)\]", appearance_ts)
        assert faces is not None
        assert re.findall(r'"([a-z0-9_-]+)"', faces.group(1)) == list(gem_appearance.BIRTH_FACES)
        assert set(gem_appearance.BIRTH_FACES) <= {id_ for id_, _ in entry.findall(block("EYE_STYLES"))}
        assert everyday("HAT_STYLES") == set(gem_appearance.BIRTH_HATS)
        assert re.findall(r'id: "([a-z]+)"', block("HAIR_STYLES")) == list(
            gem_appearance.BIRTH_HAIRS
        )
        assert re.findall(r'id: "([a-z]+)"', block("FUR_STYLES")) == list(
            gem_appearance.BIRTH_FURS
        )
        shades = re.findall(r"rgb: \[(\d+), (\d+), (\d+)\]", block("HAIR_COLORS"))
        assert [tuple(map(int, shade)) for shade in shades] == list(
            gem_appearance.BIRTH_HAIR_COLORS
        )
        # Fur is born in the body's own color or a natural shade.
        assert gem_appearance.BIRTH_FUR_COLORS == ("gem",) + tuple(
            gem_appearance.BIRTH_HAIR_COLORS
        )


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
        saved = (
            await client.put("/agents/appearance", json={**doc, "home": "/forged"})
        ).json()

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
        assert json.loads(appearance_store.read_text())["gems"] == {
            "lapis": {"face": "kawaii"}
        }

    async def test_unreadable_file_reads_as_empty(self, client, appearance_store):
        appearance_store.parent.mkdir(parents=True)
        appearance_store.write_text("{not json")

        assert (await client.get("/agents/appearance")).json() == {**EMPTY, **HOME}

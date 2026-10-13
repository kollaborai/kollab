"""Tests for the Apps catalog: the shipped file, user overrides, and every schema rule."""

import copy
import json
from pathlib import Path

import pytest

from kollabor.apps.catalog import load_catalog, validate_catalog

BUNDLED = Path(__file__).resolve().parents[2] / "bundles" / "data" / "apps.json"


def _good():
    return {
        "schema_version": 1,
        "updated": "2026-10-08",
        "recommended": ["mcp:demo", "cli:tool", "skill:demo-skill"],
        "featured": [],
        "apps": [
            {
                "id": "demo",
                "kind": "mcp",
                "name": "Demo",
                "description": "Demo server.",
                "publisher": "Test",
                "homepage": "https://example.com/demo",
                "official": False,
                "tags": ["demo"],
                "requires": ["npx"],
                "mcp": {
                    "command": "npx",
                    "args": ["-y", "demo-server", "{ROOT}"],
                    "inputs": [
                        {"name": "ROOT", "label": "Folder", "to": "arg", "required": True, "default": "{workspace}"}
                    ],
                },
            },
            {
                "id": "tool",
                "kind": "cli",
                "name": "Tool",
                "description": "A tool.",
                "publisher": "Test",
                "homepage": "https://example.com/tool",
                "official": False,
                "cli": {
                    "bin": "tool",
                    "install": [{"manager": "brew", "package": "tool"}],
                    "post_install": "tool login",
                },
            },
            {
                "id": "demo-skill",
                "kind": "skill",
                "name": "Demo Skill",
                "description": "A skill.",
                "publisher": "Test",
                "homepage": "https://example.com/skill",
                "official": False,
                "skill": {"repo": "owner/repo", "path": "skills/demo-skill", "ref": "main"},
            },
        ],
    }


def _shipped():
    return json.loads(BUNDLED.read_text())


def test_shipped_catalog_is_valid():
    assert validate_catalog(_shipped()) == []


def test_fixture_is_valid():
    assert validate_catalog(_good()) == []


MUTATIONS = {
    "bad id": lambda c: c["apps"][0].update(id="Bad_ID"),
    "unknown kind": lambda c: c["apps"][0].update(kind="tool"),
    "long description": lambda c: c["apps"][0].update(description="x" * 91),
    "http homepage": lambda c: c["apps"][0].update(homepage="http://example.com"),
    "duplicate kind and id": lambda c: c["apps"].append(copy.deepcopy(c["apps"][0])),
    "undeclared token": lambda c: c["apps"][0]["mcp"]["args"].append("{NOPE}"),
    "secret arg": lambda c: c["apps"][0]["mcp"]["inputs"][0].update(secret=True),
    "lowercase input name": lambda c: c["apps"][0]["mcp"]["inputs"][0].update(name="root"),
    "bad manager": lambda c: c["apps"][1]["cli"]["install"][0].update(manager="apt"),
    "cask on npm": lambda c: c["apps"][1]["cli"]["install"][0].update(manager="npm", cask=True),
    "bad package": lambda c: c["apps"][1]["cli"]["install"][0].update(package="x; rm -rf"),
    "skill path escapes": lambda c: c["apps"][2]["skill"].update(path="../etc"),
    "skill repo malformed": lambda c: c["apps"][2]["skill"].update(repo="notarepo"),
    "recommended unknown": lambda c: c["recommended"].append("mcp:missing"),
    "featured bad shape": lambda c: c["featured"].append("demo"),
    "recommended and featured": lambda c: c["featured"].append("mcp:demo"),
    "icon hex": lambda c: c["apps"][0].update(icon={"path": "M0 0L1 1", "hex": "zzzzzz"}),
    "schema version": lambda c: c.update(schema_version=2),
    "uppercase tag": lambda c: c["apps"][0].update(tags=["Upper"]),
    "missing kind object": lambda c: c["apps"][1].pop("cli"),
    "other kind object": lambda c: c["apps"][1].update(mcp={"command": "npx"}),
    "official not bool": lambda c: c["apps"][0].update(official="yes"),
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_each_rule_rejects_a_bad_catalog(name):
    catalog = _good()
    MUTATIONS[name](catalog)
    assert validate_catalog(catalog), name


def _user_file(tmp_path, data):
    path = tmp_path / "apps.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def test_user_entry_replaces_same_kind_and_id_and_new_entries_append(tmp_path):
    shipped = _shipped()
    gh = next(app for app in shipped["apps"] if app["id"] == "gh")
    extra = {
        "id": "extra",
        "kind": "cli",
        "name": "Extra",
        "description": "Extra tool.",
        "publisher": "Team",
        "homepage": "https://example.com/extra",
        "official": False,
        "cli": {"bin": "extra", "install": [{"manager": "brew", "package": "extra"}]},
    }
    path = _user_file(tmp_path, {"apps": [{**gh, "name": "Team GitHub"}, extra]})

    merged = load_catalog(path)

    ids = [f"{app['kind']}:{app['id']}" for app in merged["apps"]]
    assert ids[: len(shipped["apps"])] == [f"{app['kind']}:{app['id']}" for app in shipped["apps"]]
    assert ids[-1] == "cli:extra"
    assert merged["apps"][ids.index("cli:gh")]["name"] == "Team GitHub"


@pytest.mark.parametrize("content", ["{not json", "[]", '{"apps": "nope"}', '{"apps": ["x"]}'])
def test_broken_user_file_is_ignored(tmp_path, content):
    assert load_catalog(_user_file(tmp_path, content)) == _shipped()


def test_invalid_user_entry_drops_the_whole_user_file(tmp_path):
    bad = {"apps": [{"id": "Bad", "kind": "cli", "name": "Bad"}]}
    assert load_catalog(_user_file(tmp_path, bad)) == _shipped()


def test_missing_user_file_gives_the_shipped_catalog(tmp_path):
    assert load_catalog(tmp_path / "none.json") == _shipped()

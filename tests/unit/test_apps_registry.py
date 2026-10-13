"""Tests for MCP Registry search in Apps: conversion, search, ranking, rows, Add, the panels and /apps search.

The registry is a fake ``get`` serving canned pages. No network, no real home.
"""

import asyncio
import json
import shutil
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

import kollabor.commands.system_commands.handlers.apps as apps_command
import kollabor.panels.apps as apps_panel
from kollabor.apps import registry
from kollabor.apps.service import AppsService
from kollabor.commands.registry import SlashCommandRegistry
from kollabor_agent import MCPManager
from kollabor_events.models import SlashCommand


def _server(name, **fields):
    return {"name": name, "description": f"The {name} server.", "version": "1.2.0", **fields}


NOTES = _server(
    "io.github.acme/notes-mcp",
    title="Acme Notes",
    repository={"url": "https://github.com/acme/notes-mcp", "source": "github"},
    packages=[
        {
            "registryType": "npm",
            "identifier": "@acme/notes-mcp",
            "version": "1.2.0",
            "transport": {"type": "stdio"},
            "environmentVariables": [
                {"name": "NOTES_TOKEN", "description": "API token", "isRequired": True, "isSecret": True},
                {"name": "NOTES_REGION", "description": "Region", "default": "us"},
                {"name": "NOTES_DEBUG", "description": "Debug logs"},
            ],
            "packageArguments": [{"type": "named", "name": "--readonly", "value": ""}],
        }
    ],
)
HOSTED = _server(
    "com.acme/mcp",
    remotes=[
        {
            "type": "streamable-http",
            "url": "https://mcp.acme.com/mcp",
            "headers": [{"name": "Authorization", "description": "Bearer <key>", "isRequired": True, "isSecret": True}],
        }
    ],
)
WEATHER = _server(
    "io.github.jo/weather", packages=[{"registryType": "pypi", "identifier": "weather-mcp", "version": "0.3.1"}]
)
NUGET = _server(
    "com.microsoft/azure", packages=[{"registryType": "nuget", "identifier": "Azure.Mcp", "version": "1.0.0"}]
)
DUPLICATE = _server(
    "io.github.upstash/context7",
    packages=[{"registryType": "npm", "identifier": "@upstash/context7-mcp", "version": "1.0.0"}],
)


def _fp(server):
    """The confirm code for what Add would save for server (every runner on PATH)."""
    return registry.fingerprint(registry.to_app(server, lambda exe: True))


def _item(server, status="active"):
    return {"server": server, "_meta": {registry.STATUS_KEY: {"status": status, "isLatest": True}}}


class FakeRegistry:
    """Serves pages by search term and single servers by name. Records every path."""

    def __init__(self, servers, statuses=None, overflow=False, fail=None):
        self.servers, self.statuses, self.overflow, self.fail = servers, statuses or {}, overflow, fail
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        if self.fail:
            raise self.fail
        parts = urllib.parse.urlsplit(path)
        if parts.path.startswith("/servers/"):
            name = urllib.parse.unquote(parts.path.split("/")[2])
            found = next((s for s in self.servers if s["name"] == name), None)
            if found is None:
                raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
            return _item(found, self.statuses.get(name, "active"))
        term = urllib.parse.parse_qs(parts.query)["search"][0].casefold()
        hits = [_item(s, self.statuses.get(s["name"], "active")) for s in self.servers if term in s["name"].casefold()]
        plain = "/" not in term
        return {"servers": hits, "metadata": {"nextCursor": "next" if self.overflow and plain else None}}


def _catalog():
    return {
        "recommended": [],
        "featured": [],
        "apps": [
            {
                "id": "context7",
                "kind": "mcp",
                "name": "Context7",
                "description": "Library docs.",
                "publisher": "Upstash",
                "homepage": "https://context7.com",
                "official": True,
                "requires": ["npx"],
                "mcp": {"command": "npx", "args": ["-y", "@upstash/context7-mcp@latest"], "inputs": []},
            },
            {
                "id": "weather",
                "kind": "mcp",
                "name": "Weather",
                "description": "A catalog server named weather.",
                "publisher": "Test",
                "homepage": "https://example.com/weather",
                "official": False,
                "requires": ["uvx"],
                "mcp": {"command": "uvx", "args": ["other-weather"], "inputs": []},
            },
        ],
    }


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    registry.clear_cache()
    # The MCP settings validator checks the command is on PATH; CI may have no npx.
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    yield
    registry.clear_cache()


def _service(tmp_path, fake, on_path=("npx", "uvx", "docker")):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return AppsService(
        catalog=_catalog(),
        mcp_manager=MCPManager(mcp_dir=home / ".kollab" / "mcp"),
        home=home,
        workspace=tmp_path,
        which=lambda name: f"/bin/{name}" if name in on_path else None,
        registry_get=fake,
    )


def _servers(svc):
    return (svc.mcp_manager.load_config() or {}).get("servers", {})


# --- one server as an entry ---------------------------------------------------


def test_npm_package_pins_its_version_and_keeps_secrets_in_env():
    app = registry.to_app(NOTES, lambda exe: True)

    assert app["mcp"]["command"] == "npx"
    assert app["mcp"]["args"] == ["-y", "@acme/notes-mcp@1.2.0", "--readonly"]
    assert [(i["name"], i["secret"], i["required"], i.get("default")) for i in app["mcp"]["inputs"]] == [
        ("NOTES_TOKEN", True, True, None),
        ("NOTES_REGION", False, False, "us"),
        ("NOTES_DEBUG", False, False, None),
    ]
    assert (app["id"], app["name"], app["publisher"], app["requires"]) == (
        "notes-mcp",
        "Acme Notes",
        "io.github.acme",
        ["npx"],
    )
    assert app["homepage"] == "https://github.com/acme/notes-mcp" and app["unsupported"] == ""


def test_hosted_server_runs_through_mcp_remote_and_its_secret_header_stays_in_env():
    app = registry.to_app(HOSTED, lambda exe: True)

    assert app["mcp"]["args"] == [
        "-y",
        "mcp-remote",
        "https://mcp.acme.com/mcp",
        "--header",
        "Authorization:${MCP_HEADER_AUTHORIZATION}",
    ]
    assert app["mcp"]["inputs"] == [
        {
            "name": "MCP_HEADER_AUTHORIZATION",
            "label": "Authorization",
            "help": "Bearer <key>",
            "secret": True,
            "required": True,
        }
    ]
    assert app["id"] == "acme-mcp"  # "mcp" alone says nothing, so the vendor goes first


def test_header_template_variables_become_env_inputs():
    server = _server(
        "com.acme/docs",
        remotes=[
            {
                "type": "sse",
                "url": "https://docs.acme.com/sse",
                "headers": [
                    {
                        "name": "Authorization",
                        "value": "Bearer {token}",
                        "variables": {"token": {"isSecret": True, "isRequired": True, "description": "Key"}},
                    },
                    {"name": "X-Client", "value": "kollab"},
                    {"name": "X-Optional", "description": "Not needed"},
                ],
            }
        ],
    )
    app = registry.to_app(server, lambda exe: True)

    assert app["mcp"]["args"][3:] == [
        "--header",
        "Authorization:Bearer ${MCP_HEADER_AUTHORIZATION_TOKEN}",
        "--header",
        "X-Client:kollab",
    ]
    assert [(i["name"], i["to"], i["secret"]) for i in app["mcp"]["inputs"]] == [
        ("MCP_HEADER_AUTHORIZATION_TOKEN", "env", True)
    ]


def test_address_variables_are_filled_in_and_a_secret_or_undeclared_one_refuses():
    def hosted(url, variables):
        return _server("com.acme/tenant", remotes=[{"type": "streamable-http", "url": url, "variables": variables}])

    app = registry.to_app(hosted("https://{tenant}.acme.com/mcp", {"tenant": {"isRequired": True}}), lambda e: True)
    assert app["mcp"]["args"][2] == "https://{TENANT}.acme.com/mcp"
    assert app["mcp"]["inputs"][0]["to"] == "arg"

    secret = registry.to_app(hosted("https://{key}.acme.com/mcp", {"key": {"isSecret": True}}), lambda e: True)
    assert secret["unsupported"] == "Apps can't put a secret on a command line"
    undeclared = registry.to_app(hosted("https://{env}.acme.com/mcp", {}), lambda e: True)
    assert undeclared["unsupported"] == "Apps can't fill in part of this server's address"
    plain_http = registry.to_app(hosted("http://acme.com/mcp", {}), lambda e: True)
    assert plain_http["unsupported"] == "This server's address is not https"


def test_pypi_and_oci_packages():
    weather = registry.to_app(WEATHER, lambda exe: True)
    assert (weather["mcp"]["command"], weather["mcp"]["args"]) == ("uvx", ["weather-mcp@0.3.1"])

    database = _server(
        "io.github.ops/db",
        packages=[
            {
                "registryType": "oci",
                "identifier": "ghcr.io/ops/db-mcp:2.0.0",
                "environmentVariables": [{"name": "DB_URL", "isRequired": True}],
                "runtimeArguments": [{"type": "named", "name": "--init"}],
                "packageArguments": [{"type": "positional", "valueHint": "schema", "isRequired": True}],
            }
        ],
    )
    app = registry.to_app(database, lambda exe: True)
    assert app["mcp"]["args"] == [
        "run",
        "-i",
        "--rm",
        "--init",
        "-e",
        "DB_URL",
        "ghcr.io/ops/db-mcp:2.0.0",
        "{SCHEMA}",
    ]
    assert [(i["name"], i.get("to", "env")) for i in app["mcp"]["inputs"]] == [("DB_URL", "env"), ("SCHEMA", "arg")]


def test_a_server_apps_cannot_run_says_why():
    def reason(server):
        return registry.to_app(server, lambda exe: True)["unsupported"]

    assert reason(NUGET) == "Apps can't run nuget packages yet"
    assert reason(_server("com.acme/empty")) == "This server lists nothing to install"
    secret_arg = {"type": "named", "name": "--key", "isRequired": True, "isSecret": True}
    npm = {"registryType": "npm", "identifier": "x-mcp", "version": "1.0.0"}
    assert reason(_server("com.acme/a", packages=[{**npm, "packageArguments": [secret_arg]}])) == (
        "Apps can't put a secret on a command line"
    )
    bad_env = [{"name": "BAD NAME"}]
    assert reason(_server("com.acme/b", packages=[{**npm, "environmentVariables": bad_env}])) == (
        "Apps can't set this server's environment variables"
    )
    assert reason(_server("com.acme/c", packages=[{**npm, "identifier": "x; rm -rf ~"}])) == (
        "Apps can't use this package name"
    )
    http_package = {**npm, "transport": {"type": "streamable-http"}}
    assert reason(_server("com.acme/d", packages=[http_package])) == (
        "Apps can't run a package that serves over HTTP yet"
    )


def test_hosted_comes_first_unless_only_a_package_runner_is_on_path():
    both = {**WEATHER, "remotes": HOSTED["remotes"]}

    assert registry.to_app(both, lambda exe: True)["mcp"]["command"] == "npx"
    assert registry.to_app(both, lambda exe: exe == "uvx")["mcp"]["command"] == "uvx"
    none_on_path = registry.to_app(both, lambda exe: False)
    assert none_on_path["requires"] == ["npx"]  # the first way: the row says to install npx


def test_registry_text_is_one_clean_line_and_links_must_be_https():
    server = _server(
        "com.acme/clean",
        title="Evil\x1b[31m Title‮",
        description="Line one\nline two\x07",
        repository={"url": "javascript:alert(1)"},
        websiteUrl="https://acme.com",
        remotes=HOSTED["remotes"],
    )
    app = registry.to_app(server, lambda exe: True)

    assert app["name"] == "Evil [31m Title"
    assert app["description"] == "Line one line two"
    assert app["homepage"] == "https://acme.com"


def test_candidate_ids():
    assert registry.candidate_ids("io.github.github/github-mcp-server") == [
        "github-mcp-server",
        "github-mcp-server-github",
        "github-mcp-server-io-github-github",
    ]
    assert registry.candidate_ids("com.notion/mcp")[0] == "notion-mcp"


def test_a_server_with_no_title_gets_a_readable_name():
    assert registry.to_app(HOSTED, lambda exe: True)["name"] == "Acme MCP"
    github = _server("io.github.someone/github-api-mcp-server", remotes=HOSTED["remotes"])
    assert registry.to_app(github, lambda exe: True)["name"] == "GitHub API MCP Server"


# --- search and rank ----------------------------------------------------------


def test_search_asks_three_ways_and_keeps_active_servers_once():
    fake = FakeRegistry(
        [NOTES, HOSTED, _server("io.github.x/notes-old")], statuses={"io.github.x/notes-old": "deleted"}
    )

    servers, overflow, partial = asyncio.run(registry.search("notes", fake))

    assert sorted(urllib.parse.parse_qs(urllib.parse.urlsplit(p).query)["search"][0] for p in fake.paths) == [
        "/notes",
        "notes",
        "notes/",
    ]
    assert [s["name"] for s in servers] == ["io.github.acme/notes-mcp"] and (overflow, partial) == (False, [])


def test_search_keeps_every_word_and_reports_overflow():
    fake = FakeRegistry([NOTES, _server("io.github.b/notes-lite")], overflow=True)

    servers, overflow, partial = asyncio.run(registry.search("notes acme", fake))

    assert [s["name"] for s in servers] == ["io.github.acme/notes-mcp"] and overflow is True


def test_a_failed_call_keeps_the_others_and_says_the_list_may_be_short(tmp_path):
    class SlowPlainSearch(FakeRegistry):
        def __call__(self, path):
            if path.endswith("search=notes"):
                raise TimeoutError()
            return super().__call__(path)

    servers, overflow, partial = asyncio.run(registry.search("notes", SlowPlainSearch([NOTES])))
    assert [s["name"] for s in servers] == ["io.github.acme/notes-mcp"] and len(partial) == 1

    registry.clear_cache()
    found = asyncio.run(_service(tmp_path, SlowPlainSearch([NOTES])).registry_rows("notes"))
    assert found["note"] == "Part of the search failed (timed out), so this list may be short."
    assert [row["id"] for row in found["rows"]] == ["registry:io.github.acme/notes-mcp"]


def test_a_slow_plain_search_is_cut_short_and_finishes_into_the_cache(monkeypatch):
    import threading

    release = threading.Event()

    class SlowPlain(FakeRegistry):
        def __call__(self, path):
            if path.endswith("search=notes"):
                release.wait(5)
            return super().__call__(path)

    fake = SlowPlain([NOTES, _server("io.github.z/my-notes")])  # only the plain search finds my-notes
    monkeypatch.setattr(registry, "PLAIN_GRACE_SECONDS", 0.05)

    async def two_searches():
        first = await registry.search("notes", fake)
        release.set()
        await asyncio.sleep(0.2)  # the plain call finishes in its thread and lands in the cache
        return first, await registry.search("notes", fake)

    (servers, _, partial), (later, _, later_partial) = asyncio.run(two_searches())
    assert [s["name"] for s in servers] == ["io.github.acme/notes-mcp"] and len(partial) == 1
    assert sorted(s["name"] for s in later) == ["io.github.acme/notes-mcp", "io.github.z/my-notes"]
    assert later_partial == []


def test_a_stalled_call_gets_a_second_try_beside_it_and_a_4xx_is_final(monkeypatch):
    import threading

    monkeypatch.setattr(registry, "HEDGE_SECONDS", 0.05)
    stall = threading.Event()
    tries = []

    def flaky(path):
        tries.append(path)
        if len(tries) == 1:
            stall.wait(2)  # the first try never answers in time
        return {"servers": [_item(NOTES)]}

    data = asyncio.run(registry._get_cached("/servers?search=x", flaky))
    stall.set()
    assert len(tries) == 2 and data["servers"][0]["server"]["name"] == NOTES["name"]

    def missing(path):
        tries.append(path)
        raise urllib.error.HTTPError(path, 404, "Not Found", None, None)

    tries.clear()
    with pytest.raises(urllib.error.HTTPError):
        asyncio.run(registry._get_cached("/servers/x%2Fy/versions/latest", missing))
    assert len(tries) == 1

    def busy(path):
        tries.append(path)
        raise urllib.error.HTTPError(path, 503, "Busy", None, None)

    tries.clear()
    with pytest.raises(urllib.error.HTTPError, match="Busy"):
        asyncio.run(registry._get_cached("/servers?search=y", busy))
    assert len(tries) == registry.TRIES  # a 5xx is replaced at once, up to TRIES


def test_rank_puts_the_vendors_own_namespace_first():
    names = [
        "ai.smithery/smithery-github",
        "io.github.someone/tools",
        "com.x/github",
        "io.github.github/github-mcp-server",
    ]
    ranked = registry.rank([_server(name) for name in names], "github")

    assert [s["name"] for s in ranked] == [
        "io.github.github/github-mcp-server",
        "com.x/github",
        "ai.smithery/smithery-github",
        "io.github.someone/tools",
    ]


def test_search_answers_from_the_cache_for_ten_minutes():
    fake = FakeRegistry([NOTES])
    asyncio.run(registry.search("notes", fake))
    asyncio.run(registry.search("notes", fake))

    assert len(fake.paths) == 3


# --- rows -------------------------------------------------------------------


def test_registry_rows_hide_catalog_servers_and_put_unusable_ones_last(tmp_path):
    nuget = {**NUGET, "name": "com.microsoft/notes-sync"}
    duplicate = {**DUPLICATE, "name": "io.github.upstash/notes7"}  # a catalog package under another name
    svc = _service(tmp_path, FakeRegistry([nuget, NOTES, duplicate]))

    found = asyncio.run(svc.registry_rows("n"))
    assert found["rows"] == [] and found["note"] == "Type two or more letters to search the MCP Registry."

    rows = asyncio.run(svc.registry_rows("notes"))["rows"]
    assert [row["id"] for row in rows] == ["registry:io.github.acme/notes-mcp", "registry:com.microsoft/notes-sync"]
    notes, azure = rows
    assert (notes["state"], notes["actions"], notes["badges"]) == ("available", ["add"], ["Registry"])
    assert notes["note"] == registry.NOT_REVIEWED
    assert notes["preview"] == "npx -y @acme/notes-mcp@1.2.0 --readonly"
    assert (azure["state"], azure["note"], azure["actions"]) == ("unavailable", "Apps can't run nuget packages yet", [])


def test_registry_rows_say_what_is_missing_or_unreachable(tmp_path):
    rows = asyncio.run(_service(tmp_path, FakeRegistry([NOTES]), on_path=()).registry_rows("notes"))["rows"]
    assert (rows[0]["state"], rows[0]["note"]) == ("unavailable", "Needs npx on PATH")

    registry.clear_cache()
    down = FakeRegistry([], fail=urllib.error.URLError("nodename nor servname provided"))
    found = asyncio.run(_service(tmp_path, down).registry_rows("notes"))
    assert found == {
        "query": "notes",
        "rows": [],
        "note": "Could not search the MCP Registry (nodename nor servname provided).",
    }


def test_registry_rows_say_when_there_are_more(tmp_path):
    many = [_server(f"io.github.n{i}/notes-{i}", packages=NOTES["packages"]) for i in range(25)]
    found = asyncio.run(_service(tmp_path, FakeRegistry(many)).registry_rows("notes"))

    assert len(found["rows"]) == registry.SHOWN
    assert found["note"] == "The top 20 of 25 matches. Add a word to narrow them."


# --- add, list, remove, turn on -------------------------------------------------


def _add(svc, name, values=None):
    """Add the way the confirm step does: with the code for what it showed."""
    return asyncio.run(svc.add(f"registry:{name}", values, _fp(next(s for s in svc_servers(svc) if s["name"] == name))))


def svc_servers(svc):
    return svc.registry_get.servers


def test_add_saves_the_server_with_its_registry_name_and_lists_it(tmp_path):
    svc = _service(tmp_path, FakeRegistry([NOTES]))

    result = _add(svc, "io.github.acme/notes-mcp", {"NOTES_TOKEN": "sk-1"})

    assert result == {"ok": True, "message": "Added Acme Notes.", "mcp_changed": True, "skills_changed": False}
    saved = _servers(svc)["notes-mcp"]
    assert saved["command"] == "npx" and saved["args"] == ["-y", "@acme/notes-mcp@1.2.0", "--readonly"]
    assert saved["env"] == {"NOTES_TOKEN": "sk-1", "NOTES_REGION": "us"}  # NOTES_DEBUG left empty stays unset
    assert (saved["registry"], saved["title"], saved["version"], saved["added_by"], saved["enabled"]) == (
        "io.github.acme/notes-mcp",
        "Acme Notes",
        "1.2.0",
        "apps",
        True,
    )
    row = next(row for row in svc.rows() if row["id"] == "registry:io.github.acme/notes-mcp")
    assert (row["state"], row["actions"], row["server"], row["label"]) == (
        "added",
        ["remove"],
        "notes-mcp",
        "Acme Notes",
    )
    assert asyncio.run(svc.registry_rows("notes"))["rows"] == []  # listed once: the directory row has it

    removed = asyncio.run(svc.remove("registry:io.github.acme/notes-mcp"))
    assert removed["ok"] and "notes-mcp" not in _servers(svc)


def test_add_without_its_secret_saves_it_off_and_turn_on_waits_for_the_key(tmp_path):
    svc = _service(tmp_path, FakeRegistry([NOTES]))

    result = _add(svc, "io.github.acme/notes-mcp")

    assert result["message"] == "Added Acme Notes off. Set NOTES_TOKEN with /mcp setup, then turn it on."
    assert _servers(svc)["notes-mcp"]["enabled"] is False
    with pytest.raises(ValueError, match="Set NOTES_TOKEN with /mcp setup"):
        asyncio.run(svc.enable("registry:io.github.acme/notes-mcp"))
    saved = _servers(svc)["notes-mcp"]
    svc.mcp_manager.upsert_server("notes-mcp", {**saved, "env": {**saved["env"], "NOTES_TOKEN": "sk-2"}})
    assert asyncio.run(svc.enable("registry:io.github.acme/notes-mcp"))["ok"]


def test_names_taken_by_another_server_or_a_catalog_entry_move_aside(tmp_path):
    svc = _service(tmp_path, FakeRegistry([NOTES, WEATHER]))
    svc.mcp_manager.upsert_server("notes-mcp", {"type": "stdio", "command": "npx", "args": ["mine"]})

    _add(svc, "io.github.acme/notes-mcp", {"NOTES_TOKEN": "sk"})
    _add(svc, "io.github.jo/weather")

    servers = _servers(svc)
    assert servers["notes-mcp"]["args"] == ["mine"]  # the hand-made one is untouched
    assert servers["notes-mcp-acme"]["registry"] == "io.github.acme/notes-mcp"
    assert servers["weather-jo"]["registry"] == "io.github.jo/weather"  # "weather" is the catalog's


def test_add_refuses_what_is_unusable_gone_or_unreachable(tmp_path):
    svc = _service(tmp_path, FakeRegistry([NUGET], statuses={}))
    assert asyncio.run(svc.add("registry:com.microsoft/azure")) == {
        "ok": False,
        "message": "Apps can't run nuget packages yet",
        "mcp_changed": False,
        "skills_changed": False,
    }
    gone = asyncio.run(svc.add("registry:com.acme/missing"))
    assert gone["message"] == "Could not load com.acme/missing from the MCP Registry: HTTP 404"
    deleted = _service(tmp_path, FakeRegistry([NOTES], statuses={"io.github.acme/notes-mcp": "deleted"}))
    result = asyncio.run(deleted.add("registry:io.github.acme/notes-mcp"))
    assert result["message"].endswith("from the MCP Registry: io.github.acme/notes-mcp is deleted in the MCP Registry")
    bad_name = asyncio.run(svc.add("registry:../../etc"))
    assert bad_name["ok"] is False and "is not an MCP Registry server name" in bad_name["message"]


# --- panels -------------------------------------------------------------------


@pytest.fixture
def panel_service(tmp_path, monkeypatch):
    svc = _service(tmp_path, FakeRegistry([NOTES, HOSTED]))
    monkeypatch.setattr(apps_panel, "_service", lambda: svc)
    return svc


def test_registry_panel_lists_hits_for_the_query(panel_service):
    panel = asyncio.run(apps_panel.AppsRegistryPanel().describe(None, {"query": "notes"}))

    assert panel["panel"] == "apps-registry" and panel["kind"] == "picker"
    assert [row["id"] for row in panel["rows"]] == ["registry:io.github.acme/notes-mcp"]
    assert panel["sections"] == [
        {"id": "registry", "title": "MCP Registry", "ids": ["registry:io.github.acme/notes-mcp"]}
    ]


def test_add_on_a_registry_row_always_opens_the_confirm_form(panel_service):
    result = asyncio.run(apps_panel.AppsPanel().act(None, "add", {"id": "registry:com.acme/mcp"}))

    form = result["open"]
    assert [(section["id"], section["title"]) for section in form["sections"]] == [
        ("command", "Command"),
        ("s0", "Settings"),
    ]
    runs = form["sections"][0]["fields"][0]
    assert (runs["path"], runs["type"], runs["value"]) == (
        "_runs",
        "label",
        "npx -y mcp-remote https://mcp.acme.com/mcp --header 'Authorization:${MCP_HEADER_AUTHORIZATION}'",
    )
    assert registry.NOT_REVIEWED in runs["help"]
    assert form["context"] == {"id": "registry:com.acme/mcp", "fingerprint": _fp(HOSTED)}
    assert [field["path"] for field in form["sections"][1]["fields"]] == ["MCP_HEADER_AUTHORIZATION"]
    assert _servers(panel_service) == {}  # nothing saved or run before the confirm


def test_install_form_adds_and_never_echoes_the_secret(panel_service):
    payload = {
        "context": {"id": "registry:com.acme/mcp", "fingerprint": _fp(HOSTED)},
        "changes": {"MCP_HEADER_AUTHORIZATION": "Bearer sk-SECRET", "PATH": "/evil"},
    }
    result = asyncio.run(apps_panel.AppsInstallPanel().act(None, "install", payload))

    assert result["ok"] is True and "sk-SECRET" not in json.dumps(result)
    saved = _servers(panel_service)["acme-mcp"]
    assert saved["env"] == {"MCP_HEADER_AUTHORIZATION": "Bearer sk-SECRET"}  # PATH is not an input: ignored


def test_install_form_refuses_when_the_registry_entry_changed(panel_service):
    payload = {
        "context": {"id": "registry:com.acme/mcp", "fingerprint": "0" * 16},
        "changes": {"MCP_HEADER_AUTHORIZATION": "x"},
    }
    result = asyncio.run(apps_panel.AppsInstallPanel().act(None, "install", payload))

    assert result["ok"] is False and "changed in the MCP Registry" in result["message"]
    assert _servers(panel_service) == {}


def test_install_form_still_asks_for_required_fields(panel_service):
    payload = {"context": {"id": "registry:com.acme/mcp", "fingerprint": _fp(HOSTED)}, "changes": {}}
    result = asyncio.run(apps_panel.AppsInstallPanel().act(None, "install", payload))

    assert result["ok"] is False and result["errors"] == {"MCP_HEADER_AUTHORIZATION": "Authorization is required"}


# --- terminal -----------------------------------------------------------------


def _apps(svc, monkeypatch, *args):
    monkeypatch.setattr(apps_command, "_service", lambda: svc)
    handler = apps_command.AppsCommandHandler(SlashCommandRegistry(), SimpleNamespace(get_service=lambda name: None))
    command = SlashCommand(name="apps", args=list(args), raw_input="/apps " + " ".join(args))
    return asyncio.run(handler.handle_apps(command))


def _search(svc, monkeypatch, *words):
    return _apps(svc, monkeypatch, "search", *words).message


def test_terminal_search_lists_registry_servers_after_the_directory(tmp_path, monkeypatch):
    message = _search(_service(tmp_path, FakeRegistry([NOTES, NUGET])), monkeypatch, "acme")

    assert message.splitlines() == [
        "No apps in the directory match 'acme'.",
        "MCP Registry",
        "  [available] Acme Notes (registry:io.github.acme/notes-mcp)",
        "  Kollab has not reviewed these servers. /apps add <id> shows what one runs.",
    ]


def test_terminal_search_says_when_the_registry_is_down(tmp_path, monkeypatch):
    down = FakeRegistry([], fail=TimeoutError())
    message = _search(_service(tmp_path, down), monkeypatch, "context7")

    assert "[available] Context7 (mcp:context7)" in message
    assert message.endswith("MCP Registry\n  Could not search the MCP Registry (timed out).")


# --- trust boundaries -----------------------------------------------------------

NPM = {"registryType": "npm", "identifier": "@x/tool", "version": "1.0.0"}
OCI = {"registryType": "oci", "identifier": "ghcr.io/x/box:1.0.0"}


def _reason(server):
    return registry.to_app(server, lambda exe: True)["unsupported"]


def _hosted(url="https://mcp.acme.com/mcp", headers=()):
    return _server("com.acme/h", remotes=[{"type": "streamable-http", "url": url, "headers": list(headers)}])


def test_a_hosted_server_gets_only_its_own_inputs_never_this_computers_env_or_paths():
    # mcp-remote fills ${NAME} from this computer's environment, and Add fills {workspace} and {home}.
    assert _reason(_hosted(headers=[{"name": "X-Client", "value": "${OPENAI_API_KEY}"}])) == (
        "Apps can't send this server's headers"
    )
    # mcp-remote reads any ${...}, even a name {token} rules would not match
    assert _reason(_hosted(headers=[{"name": "X-Client", "value": "${MY-SECRET}"}])) == (
        "Apps can't send this server's headers"
    )
    sneaky = {"name": "Authorization", "value": "Bearer {t} ${AWS_SECRET_ACCESS_KEY}", "variables": {"t": {}}}
    assert _reason(_hosted(headers=[sneaky])) == "Apps can't send this server's headers"
    assert _reason(_hosted(headers=[{"name": "X-Home", "value": "{home}"}])) == "Apps can't send this server's headers"
    named_like_home = {"type": "streamable-http", "url": "https://acme.com/{HOME}/mcp", "variables": {"HOME": {}}}
    named_like_home["headers"] = [{"name": "X-H", "value": "${HOME}"}]  # the address variable can't vouch for it
    assert _reason(_server("com.acme/s", remotes=[named_like_home])) == "Apps can't send this server's headers"
    assert _reason(_hosted("https://acme.com/mcp?w={workspace}")) == "Apps can't fill in part of this server's address"
    assert _reason(_hosted("https://acme.com/mcp?k=${HOME}")) == "Apps can't fill in part of this server's address"
    braces = _server("com.x/d", packages=[{**NPM, "environmentVariables": [{"name": "ROOT", "default": "{home}/d"}]}])
    assert "default" not in registry.to_app(braces, lambda exe: True)["mcp"]["inputs"][0]


def test_variables_and_flags_that_change_what_a_runner_installs_refuse():
    for name in ("npm_config_registry", "NODE_OPTIONS", "UV_INDEX_URL", "PIP_INDEX_URL", "DOCKER_HOST", "PATH"):
        envy = _server("com.x/envy", packages=[{**NPM, "environmentVariables": [{"name": name, "value": "x"}]}])
        assert _reason(envy) == f"Apps won't set {name}: npx, uvx and docker read it themselves"
    flag = {"type": "named", "name": "--registry", "value": "https://evil.example/"}
    assert _reason(_server("com.x/a", packages=[{**NPM, "runtimeArguments": [flag]}])) == (
        "Apps won't pass --registry to npx"
    )
    for name, value in (("--privileged", None), ("-v", "/:/host"), ("--network", "host")):
        arg = {"type": "named", "name": name, **({"value": value} if value else {})}
        assert _reason(_server("com.x/b", packages=[{**OCI, "runtimeArguments": [arg]}])) == (
            f"Apps won't pass {name} to docker"
        )
    host_env = {"type": "named", "name": "-e", "value": "AWS_SECRET_ACCESS_KEY"}
    assert _reason(_server("com.x/c", packages=[{**OCI, "runtimeArguments": [host_env]}])) == (
        "Apps won't pass this computer's environment into a container"
    )
    y_with_a_package = {"type": "named", "name": "-y", "value": "evil-pkg"}
    app = registry.to_app(
        _server("com.x/y", packages=[{**NPM, "runtimeArguments": [y_with_a_package]}]), lambda e: True
    )
    assert app["mcp"]["args"] == ["-y", "@x/tool@1.0.0"]


def test_add_saves_only_the_command_the_confirm_step_showed(tmp_path):
    long_version = "1.0.0-build." + "0" * 40
    v1 = _server("io.github.x/tool", version=long_version + "1", packages=[{**NPM, "identifier": "@x/good"}])
    fake = FakeRegistry([v1])
    svc = _service(tmp_path, fake)
    shown = _fp(v1)
    # The publisher swaps the package; the shown version text, cut to 40 characters, looks the same.
    fake.servers = [{**v1, "version": long_version + "2", "packages": [{**NPM, "identifier": "@x/evil"}]}]
    registry.clear_cache()

    refused = asyncio.run(svc.add("registry:io.github.x/tool", None, shown))
    assert refused["ok"] is False and "changed in the MCP Registry" in refused["message"]
    no_code = asyncio.run(svc.add("registry:io.github.x/tool"))
    assert no_code["ok"] is False and "/apps add registry:io.github.x/tool shows the command" in no_code["message"]
    assert _servers(svc) == {}


def test_terminal_add_shows_the_command_and_adds_only_with_its_code(tmp_path, monkeypatch):
    svc = _service(tmp_path, FakeRegistry([NOTES]))
    code = _fp(NOTES)

    shown = _apps(svc, monkeypatch, "add", "registry:io.github.acme/notes-mcp")
    assert shown.message.splitlines() == [
        "Acme Notes (registry:io.github.acme/notes-mcp)",
        f"  {registry.NOT_REVIEWED}",
        "  Runs: npx -y @acme/notes-mcp@1.2.0 --readonly",
        "  With: NOTES_REGION=us",
        "  Add saves it off. Set NOTES_TOKEN with /mcp setup, then turn it on.",
        f"  To add it: /apps add registry:io.github.acme/notes-mcp {code}",
    ]
    assert _servers(svc) == {}  # showing it saves nothing
    wrong = _apps(svc, monkeypatch, "add", "registry:io.github.acme/notes-mcp", "0" * 16)
    assert wrong.success is False and _servers(svc) == {}
    added = _apps(svc, monkeypatch, "add", "registry:io.github.acme/notes-mcp", code)
    assert added.success and _servers(svc)["notes-mcp"]["enabled"] is False


def test_a_cleared_field_stays_cleared(panel_service):
    payload = {
        "context": {"id": "registry:io.github.acme/notes-mcp", "fingerprint": _fp(NOTES)},
        "changes": {"NOTES_TOKEN": "sk", "NOTES_REGION": ""},  # NOTES_REGION's default is "us"
    }
    result = asyncio.run(apps_panel.AppsInstallPanel().act(None, "install", payload))

    assert result["ok"] is True
    assert _servers(panel_service)["notes-mcp"]["env"] == {"NOTES_TOKEN": "sk"}


def test_an_empty_optional_variable_is_not_copied_into_a_container(tmp_path):
    env = [{"name": "API_KEY", "isRequired": True, "isSecret": True}, {"name": "AWS_SECRET_ACCESS_KEY"}]
    box = _server("io.github.x/box", packages=[{**OCI, "environmentVariables": env}])
    svc = _service(tmp_path, FakeRegistry([box]))

    assert _add(svc, "io.github.x/box", {"API_KEY": "k"})["ok"]

    saved = _servers(svc)["box"]
    # "-e AWS_SECRET_ACCESS_KEY" with no value would copy this computer's key into the container.
    assert saved["args"] == ["run", "-i", "--rm", "-e", "API_KEY", "ghcr.io/x/box:1.0.0"]
    assert saved["env"] == {"API_KEY": "k"}


def test_an_optional_argument_left_empty_is_left_out(tmp_path):
    port = {"type": "named", "name": "--port", "value": "{port}", "variables": {"port": {}}}
    host = {
        "type": "named",
        "name": "--host",
        "value": "{host}",
        "isRequired": True,
        "variables": {"host": {"default": "lo"}},
    }
    porty = _server("io.github.x/porty", packages=[{**NPM, "identifier": "porty", "packageArguments": [port, host]}])
    svc = _service(tmp_path, FakeRegistry([porty]))

    assert _add(svc, "io.github.x/porty")["ok"]

    assert _servers(svc)["porty"]["args"] == ["-y", "porty@1.0.0", "--host", "lo"]


def test_a_malformed_link_is_dropped_not_raised(tmp_path):
    odd = _server(
        "io.github.x/odd-notes",
        websiteUrl="https://[x]/",
        repository={"url": "https://[x]/"},
        remotes=[{"type": "streamable-http", "url": "https://[x]/mcp"}],
        packages=NOTES["packages"],
    )
    found = asyncio.run(_service(tmp_path, FakeRegistry([odd])).registry_rows("notes"))

    assert [(row["id"], row["state"], row["homepage"]) for row in found["rows"]] == [
        ("registry:io.github.x/odd-notes", "available", "")
    ]


def test_a_reply_that_breaks_off_is_a_failed_call(monkeypatch):
    import http.client

    class BrokenReply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size):
            raise http.client.IncompleteRead(b"{", 10)

    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda request, timeout: BrokenReply())
    with pytest.raises(ConnectionError, match="the reply broke off"):
        registry.http_get("/servers?search=x")


def test_notes_say_only_what_is_true(tmp_path):
    found = asyncio.run(_service(tmp_path, FakeRegistry([NOTES], overflow=True)).registry_rows("notes zzz"))
    assert found["note"] == (
        'None of the MCP Registry servers read match notes zzz. "notes" matches too many: try a rarer word.'
    )

    registry.clear_cache()
    found = asyncio.run(_service(tmp_path, FakeRegistry([DUPLICATE])).registry_rows("context7"))
    assert (found["rows"], found["note"]) == ([], "Every MCP Registry match is in the directory already.")

    class Busy(FakeRegistry):
        def __call__(self, path):
            if path.endswith("search=notes"):
                raise urllib.error.HTTPError(path, 503, "Busy", None, None)
            return super().__call__(path)

    registry.clear_cache()
    found = asyncio.run(_service(tmp_path, Busy([NOTES])).registry_rows("notes"))
    assert found["note"] == "Part of the search failed (HTTP 503), so this list may be short."


def test_a_catalog_id_taken_by_a_registry_server_keeps_both_rows_honest(tmp_path):
    svc = _service(tmp_path, FakeRegistry([]))
    other = {"type": "stdio", "command": "npx", "args": ["-y", "other"], "registry": "io.github.y/context7"}
    svc.mcp_manager.upsert_server("context7", {**other, "title": "Other", "added_by": "apps", "enabled": True})

    rows = {row["id"]: row for row in svc.rows()}

    assert (rows["registry:io.github.y/context7"]["state"], rows["registry:io.github.y/context7"]["actions"]) == (
        "added",
        ["remove"],
    )
    assert (rows["mcp:context7"]["state"], rows["mcp:context7"]["note"]) == (
        "unavailable",
        "A server from the MCP Registry is saved as context7",
    )


def test_terminal_search_prints_without_waiting_for_a_slow_registry(tmp_path, monkeypatch):
    import threading

    release = threading.Event()

    class Slow(FakeRegistry):
        def __call__(self, path):
            release.wait(5)
            return super().__call__(path)

    monkeypatch.setattr(apps_command, "REGISTRY_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(apps_command, "_searches", set())  # only this test's searches
    svc = _service(tmp_path, Slow([NOTES]))

    async def search_twice():
        handler = apps_command.AppsCommandHandler(SlashCommandRegistry(), SimpleNamespace(get_service=lambda n: None))
        command = SlashCommand(name="apps", args=["search", "acme"], raw_input="/apps search acme")
        monkeypatch.setattr(apps_command, "_service", lambda: svc)
        first = (await handler.handle_apps(command)).message
        release.set()
        while apps_command._searches:  # the slow search finishes into the registry cache
            await asyncio.sleep(0.01)
        return first, (await handler.handle_apps(command)).message

    first, again = asyncio.run(search_twice())
    assert first.endswith(
        "MCP Registry\n  Still searching: the MCP Registry is slow right now. Run this search again soon."
    )
    assert "[available] Acme Notes (registry:io.github.acme/notes-mcp)" in again

"""Tests for kollabor.apps.service: row states, actions and the install paths.

No network and no real installs. which, run and fetch are fakes; home, workspace
and the bundled skill folder are temp dirs.
"""

import asyncio
import io
import json
import sys
import tarfile

import pytest

import kollabor.apps.service as service_module
from kollabor.apps.service import AppsService
from kollabor_agent import MCPManager

PY = sys.executable  # absolute path: the MCP validator checks the command exists

ROOT_INPUT = {"name": "ROOT", "label": "Folder", "to": "arg", "required": True, "default": "{workspace}"}
KEY_INPUT = {"name": "KEY", "label": "API Key", "to": "env", "secret": True, "required": True}
FOLDER_INPUT = {"name": "FOLDER", "label": "Folder", "to": "arg", "required": True}


def _mcp(app_id, requires=(), args=("-m", "server"), inputs=()):
    return {
        "id": app_id,
        "kind": "mcp",
        "name": app_id.title(),
        "description": f"{app_id} server.",
        "publisher": "Test",
        "homepage": "https://example.com/mcp",
        "official": False,
        "requires": list(requires),
        "mcp": {"command": PY, "args": list(args), "inputs": list(inputs)},
    }


def _cli(cli_id, manager, package, post_install=None):
    cli = {"bin": cli_id, "install": [{"manager": manager, "package": package}]}
    if post_install:
        cli["post_install"] = post_install
    return {
        "id": cli_id,
        "kind": "cli",
        "name": cli_id.title(),
        "description": f"{cli_id} tool.",
        "publisher": "Test",
        "homepage": "https://example.com/cli",
        "official": False,
        "cli": cli,
    }


def _catalog():
    return {
        "schema_version": 1,
        "updated": "2026-10-08",
        "recommended": [
            "mcp:demo",
            "mcp:broken",
            "cli:tool",
            "cli:gone",
            "mcp:needs-folder",
            "mcp:secret",
            "skill:demo-skill",
        ],
        "featured": ["mcp:secret", "cli:tool"],
        "apps": [
            _mcp("demo", requires=["npx"], args=("-m", "demo", "{ROOT}"), inputs=[ROOT_INPUT]),
            _mcp("secret", requires=["npx"], inputs=[KEY_INPUT]),
            _mcp("needs-folder", args=("-m", "folder", "{FOLDER}"), inputs=[FOLDER_INPUT]),
            _mcp("broken", requires=["missingbin"]),
            _cli("tool", "brew", "tool-formula", post_install="tool login"),
            _cli("gone", "uv", "gone"),
            {
                "id": "demo-skill",
                "kind": "skill",
                "name": "Demo Skill",
                "description": "A demo skill.",
                "publisher": "Test",
                "homepage": "https://example.com/skill",
                "official": False,
                "skill": {"repo": "owner/repo", "path": "skills/demo-skill", "ref": "main"},
            },
        ],
    }


class FakeRun:
    def __init__(self, code=0, output="done", after=None):
        self.calls, self.code, self.output, self.after = [], code, output, after

    async def __call__(self, argv):
        self.calls.append(argv)
        if self.code == 0 and self.after:
            self.after()
        return self.code, self.output


class FakeFetch:
    def __init__(self, data):
        self.data, self.urls = data, []

    async def __call__(self, url):
        self.urls.append(url)
        return self.data


def _skill_md(name):
    return f"---\nname: {name}\ndescription: A demo skill.\n---\n\nDo the thing.\n"


def _write_skill(parent, name):
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(_skill_md(name))
    return folder


def _tarball(files=None, links=None):
    """A codeload-shaped archive: every name sits under one top folder, like skills-main/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (files or {}).items():
            info = tarfile.TarInfo(f"skills-main/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"skills-main/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


def _good_archive():
    return _tarball(
        {
            "README.md": b"ignored",
            "skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode(),
            "skills/demo-skill/scripts/run.sh": b"echo hi\n",
            "skills/other/SKILL.md": _skill_md("other").encode(),
        }
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "builtin-skill")
    monkeypatch.setattr(service_module, "get_bundled_skills_dir", lambda: bundled)
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {"home": home, "workspace": workspace}


def _service(env, available=("npx", "brew"), run=None, fetch=None, catalog=None):
    return AppsService(
        catalog=catalog if catalog is not None else _catalog(),
        mcp_manager=MCPManager(mcp_dir=env["home"] / ".kollab" / "mcp"),
        home=env["home"],
        workspace=env["workspace"],
        which=lambda name: f"/bin/{name}" if name in available else None,
        run=run or FakeRun(),
        fetch=fetch or FakeFetch(b""),
    )


def _rows(svc):
    return {row["id"]: row for row in svc.rows()}


def _skills_dir(env):
    return env["home"] / ".kollab" / "skills"


def test_states_on_a_fresh_machine(env):
    rows = _rows(_service(env))

    assert rows["mcp:demo"]["state"] == "available"
    assert rows["mcp:broken"]["state"] == "unavailable"
    assert rows["mcp:broken"]["note"] == "Needs missingbin on PATH"
    assert rows["mcp:broken"]["actions"] == []
    assert rows["cli:tool"]["state"] == "available"
    assert rows["cli:tool"]["preview"] == "brew install tool-formula"
    assert rows["cli:gone"]["state"] == "unavailable"
    assert rows["cli:gone"]["note"] == "Needs uv on PATH"
    assert rows["skill:demo-skill"]["state"] == "available"
    assert rows["skill:builtin-skill"]["state"] == "added"
    assert rows["skill:builtin-skill"]["badges"] == ["Skill", "Built-in"]
    assert rows["skill:builtin-skill"]["actions"] == []


def test_hand_written_skill_is_local_and_not_removable(env):
    _write_skill(_skills_dir(env), "hand-written")

    row = _rows(_service(env))["skill:hand-written"]

    assert row["badges"] == ["Skill", "Local"]
    assert row["actions"] == []


def test_mcp_moves_between_added_off_and_back(env):
    svc = _service(env)

    asyncio.run(svc.add("mcp:demo"))
    assert _rows(svc)["mcp:demo"]["state"] == "added"
    assert _rows(svc)["mcp:demo"]["actions"] == ["remove"]

    svc.mcp_manager.disable_server("demo")
    assert _rows(svc)["mcp:demo"]["state"] == "off"
    assert _rows(svc)["mcp:demo"]["actions"] == ["enable", "remove"]

    asyncio.run(svc.enable("demo"))
    assert _rows(svc)["mcp:demo"]["state"] == "added"


def test_runtime_note_comes_from_the_session(env):
    svc = _service(env)
    asyncio.run(svc.add("demo"))

    connected = {row["id"]: row for row in svc.rows({"demo": {"connected": True, "tool_count": 2}})}
    failed = {row["id"]: row for row in svc.rows({"demo": {"error": "boom"}})}

    assert connected["mcp:demo"]["note"] == "connected · 2 tools"
    assert failed["mcp:demo"]["note"] == "error: boom"


def test_add_mcp_writes_the_entry_with_the_workspace_expanded(env):
    svc = _service(env)

    result = asyncio.run(svc.add("mcp:demo"))

    assert result == {"ok": True, "message": "Added Demo.", "mcp_changed": True, "skills_changed": False}
    entry = svc.mcp_manager.load_config()["servers"]["demo"]
    assert entry["args"] == ["-m", "demo", str(env["workspace"])]
    assert entry["type"] == "stdio"
    assert entry["enabled"] is True
    assert entry["added_by"] == "apps"


@pytest.mark.parametrize("enabled, state, actions", [(True, "added", []), (False, "off", ["enable"])])
def test_a_server_set_up_by_hand_under_a_catalog_id_is_never_removed(env, enabled, state, actions):
    svc = _service(env)
    own = {"type": "stdio", "command": "python3", "args": ["my_server.py"], "env": {"TOKEN": "mine"}}
    svc.mcp_manager.upsert_server("demo", {**own, "enabled": enabled})

    row = _rows(svc)["mcp:demo"]

    assert (row["state"], row["actions"]) == (state, actions)
    with pytest.raises(ValueError, match="set up outside Apps"):
        asyncio.run(svc.remove("demo"))
    assert svc.mcp_manager.load_config()["servers"]["demo"]["env"] == {"TOKEN": "mine"}


def test_missing_secret_adds_the_server_off(env):
    svc = _service(env)
    assert svc.missing_inputs("mcp:secret") == [KEY_INPUT]

    result = asyncio.run(svc.add("mcp:secret"))

    assert result["ok"] and result["mcp_changed"]
    assert "KEY" in result["message"] and "/mcp" in result["message"]
    entry = svc.mcp_manager.load_config()["servers"]["secret"]
    assert entry["enabled"] is False
    assert entry["env"] == {"KEY": ""}


def test_secret_value_never_appears_in_the_reply(env):
    svc = _service(env)

    result = asyncio.run(svc.add("mcp:secret", {"KEY": "sk-live-123"}))

    assert result["ok"]
    assert "sk-live-123" not in json.dumps(result)
    assert svc.mcp_manager.load_config()["servers"]["secret"]["env"] == {"KEY": "sk-live-123"}
    assert svc.missing_inputs("mcp:secret", {"KEY": "x"}) == []


def test_enable_refuses_an_unset_secret_then_allows_it_once_set(env):
    svc = _service(env)
    asyncio.run(svc.add("secret"))

    with pytest.raises(ValueError, match="Set KEY with /mcp"):
        asyncio.run(svc.enable("secret"))

    definition = svc.mcp_manager.load_config()["servers"]["secret"]
    svc.mcp_manager.upsert_server("secret", {**definition, "env": {"KEY": "sk"}})
    result = asyncio.run(svc.enable("secret"))

    assert result["ok"]
    assert svc.mcp_manager.load_config()["servers"]["secret"]["enabled"] is True


def test_missing_folder_without_default_is_refused_and_nothing_is_written(env):
    svc = _service(env)

    result = asyncio.run(svc.add("mcp:needs-folder"))

    assert result == {"ok": False, "message": "Needs Folder", "mcp_changed": False, "skills_changed": False}
    assert svc.mcp_manager.load_config() is None


def test_unavailable_mcp_cannot_be_added(env):
    result = asyncio.run(_service(env).add("mcp:broken"))

    assert result == {"ok": False, "message": "Needs missingbin on PATH", "mcp_changed": False, "skills_changed": False}


def test_remove_mcp_then_refuse_a_second_remove(env):
    svc = _service(env)
    asyncio.run(svc.add("demo"))

    result = asyncio.run(svc.remove("demo"))

    assert result == {"ok": True, "message": "Removed Demo.", "mcp_changed": True, "skills_changed": False}
    assert "demo" not in svc.mcp_manager.load_config()["servers"]
    with pytest.raises(ValueError):
        asyncio.run(svc.remove("demo"))


def test_enable_only_applies_to_off_servers(env):
    svc = _service(env)
    asyncio.run(svc.add("demo"))

    with pytest.raises(ValueError, match="not off"):
        asyncio.run(svc.enable("demo"))


def test_cli_add_runs_the_first_manager_and_shows_post_install(env):
    available = {"npx", "brew"}
    run = FakeRun(after=lambda: available.add("tool"))
    svc = _service(env, available=available, run=run)

    result = asyncio.run(svc.add("cli:tool"))

    assert run.calls == [["brew", "install", "tool-formula"]]
    assert result == {
        "ok": True,
        "message": "Installed Tool. Next, run `tool login` in your terminal.",
        "mcp_changed": False,
        "skills_changed": False,
    }


def test_cli_failure_reports_the_output_tail(env):
    svc = _service(env, run=FakeRun(code=1, output="Error: no formula"))

    result = asyncio.run(svc.add("cli:tool"))

    assert result["ok"] is False
    assert "exit 1" in result["message"] and "Error: no formula" in result["message"]
    assert _rows(svc)["cli:tool"]["state"] == "available"


def test_cli_with_no_manager_on_path_is_unavailable(env):
    result = asyncio.run(_service(env).add("cli:gone"))

    assert result == {"ok": False, "message": "Needs uv on PATH", "mcp_changed": False, "skills_changed": False}


def test_cli_is_never_removed_from_apps(env):
    with pytest.raises(ValueError):
        asyncio.run(_service(env).remove("cli:tool"))


def test_skill_add_extracts_only_its_folder_and_writes_the_marker(env):
    fetch = FakeFetch(_good_archive())
    svc = _service(env, fetch=fetch)

    result = asyncio.run(svc.add("skill:demo-skill"))

    assert result == {
        "ok": True,
        "message": "Added Demo Skill to your skills.",
        "mcp_changed": False,
        "skills_changed": True,
    }
    assert fetch.urls == ["https://codeload.github.com/owner/repo/tar.gz/main"]
    folder = _skills_dir(env) / "demo-skill"
    assert (folder / "scripts" / "run.sh").read_text() == "echo hi\n"
    assert not (folder / "README.md").exists()
    assert not (_skills_dir(env) / "other").exists()
    marker = json.loads((folder / ".kollab-app.json").read_text())
    assert marker["repo"] == "owner/repo" and marker["path"] == "skills/demo-skill"
    assert marker["ref"] == "main" and marker["id"] == "demo-skill" and "installed_at" in marker
    assert _rows(svc)["skill:demo-skill"]["state"] == "added"
    assert _rows(svc)["skill:demo-skill"]["actions"] == ["remove"]


@pytest.mark.parametrize(
    "archive",
    [
        _tarball(
            {
                "skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode(),
                "skills/demo-skill/../../evil": b"x",
            }
        ),
        _tarball(
            {"skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode()},
            links={"skills/demo-skill/link": "/etc/passwd"},
        ),
        _tarball({"skills/demo-skill/SKILL.md": _skill_md("wrong-name").encode()}),
        _tarball(
            {
                "skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode(),
                "skills/demo-skill/big.bin": b"0" * (5 * 1024 * 1024 + 1),
            }
        ),
        _tarball({"skills/other/SKILL.md": _skill_md("other").encode()}),
    ],
    ids=["escaping-member", "symlink", "wrong-name", "over-5mb", "path-missing"],
)
def test_skill_add_rejects_unsafe_or_wrong_archives(env, archive):
    svc = _service(env, fetch=FakeFetch(archive))

    result = asyncio.run(svc.add("skill:demo-skill"))

    assert result["ok"] is False
    assert not (_skills_dir(env) / "demo-skill").exists()
    assert list(_skills_dir(env).iterdir()) == []


def test_skill_add_refuses_a_member_whose_path_turns_absolute(env):
    outside = env["workspace"] / "outside.txt"  # writable, so only the check can stop the write
    archive = _tarball(
        {
            "skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode(),
            f"skills/demo-skill/{outside}": b"x",  # "demo-skill//Users/..." leaves an absolute inner path
        }
    )

    result = asyncio.run(_service(env, fetch=FakeFetch(archive)).add("skill:demo-skill"))

    assert result["ok"] is False
    assert not outside.exists()
    assert list(_skills_dir(env).iterdir()) == []


def test_skill_add_ignores_links_outside_the_skill(env):
    archive = _tarball(
        {"skills/demo-skill/SKILL.md": _skill_md("demo-skill").encode()},
        links={"docs/shared": "../skills", "../outside": "/etc/passwd"},
    )
    svc = _service(env, fetch=FakeFetch(archive))

    result = asyncio.run(svc.add("skill:demo-skill"))

    assert result["ok"] is True
    assert sorted(p.name for p in (_skills_dir(env) / "demo-skill").iterdir()) == [".kollab-app.json", "SKILL.md"]


def test_download_only_talks_to_codeload():
    with pytest.raises(ValueError, match="codeload"):
        service_module._download("https://example.com/x.tar.gz")


def test_remove_app_installed_skill(env):
    svc = _service(env, fetch=FakeFetch(_good_archive()))
    asyncio.run(svc.add("skill:demo-skill"))

    asyncio.run(svc.remove("skill:demo-skill"))

    assert not (_skills_dir(env) / "demo-skill").exists()


def test_built_in_and_hand_written_skills_cannot_be_removed(env):
    _write_skill(_skills_dir(env), "hand-written")
    svc = _service(env)

    for row_id in ("skill:builtin-skill", "skill:hand-written"):
        with pytest.raises(ValueError):
            asyncio.run(svc.remove(row_id))
    assert (_skills_dir(env) / "hand-written").exists()


def test_row_id_without_prefix_resolves_when_unambiguous(env):
    result = asyncio.run(_service(env).add("demo"))

    assert result["ok"] is True


def test_ambiguous_row_id_is_refused(env):
    catalog = _catalog()
    catalog["apps"].append(_cli("demo", "brew", "demo"))
    svc = _service(env, catalog=catalog)

    with pytest.raises(ValueError, match="more than one"):
        svc.missing_inputs("demo")


def test_unknown_row_id_is_refused(env):
    with pytest.raises(ValueError, match="No app named"):
        _service(env).missing_inputs("nope")


def test_missing_inputs_respects_defaults_and_values(env):
    svc = _service(env)

    assert svc.missing_inputs("mcp:demo") == []
    assert svc.missing_inputs("mcp:needs-folder") == [FOLDER_INPUT]
    assert svc.missing_inputs("mcp:needs-folder", {"FOLDER": "/x"}) == []
    assert svc.missing_inputs("cli:tool") == []


def test_for_you_skips_added_and_unavailable_and_caps_at_four(env):
    svc = _service(env)

    sections = {section["id"]: section["ids"] for section in svc.sections(svc.rows())}

    assert sections["for-you"] == ["mcp:demo", "cli:tool", "mcp:needs-folder", "mcp:secret"]
    assert sections["featured"] == ["mcp:secret", "cli:tool"]


def test_for_you_is_left_out_when_nothing_qualifies(env):
    catalog = _catalog()
    catalog["recommended"] = ["mcp:broken"]
    svc = _service(env, catalog=catalog)

    assert "for-you" not in {section["id"] for section in svc.sections(svc.rows())}


def test_kind_sections_list_added_first_then_by_name(env):
    svc = _service(env)
    asyncio.run(svc.add("demo"))

    sections = {section["id"]: section["ids"] for section in svc.sections(svc.rows())}

    assert sections["mcp"] == ["mcp:demo", "mcp:broken", "mcp:needs-folder", "mcp:secret"]


def test_installed_cli_is_added_with_no_actions(env):
    svc = _service(env, available=("npx", "brew", "tool"))

    row = _rows(svc)["cli:tool"]

    assert row["state"] == "added" and row["actions"] == []
    assert asyncio.run(svc.add("cli:tool")) == {
        "ok": False,
        "message": "Tool is already added",
        "mcp_changed": False,
        "skills_changed": False,
    }


def _catalog_with_skill_needing(exe):
    catalog = _catalog()
    next(app for app in catalog["apps"] if app["id"] == "demo-skill")["requires"] = [exe]
    return catalog


def test_skill_needing_a_tool_no_catalog_entry_installs_is_unavailable(env):
    svc = _service(env, catalog=_catalog_with_skill_needing("officecli"))

    row = _rows(svc)["skill:demo-skill"]

    assert (row["state"], row["note"], row["actions"]) == ("unavailable", "Needs officecli on PATH", [])
    assert asyncio.run(svc.add("skill:demo-skill"))["message"] == "Needs officecli on PATH"


def test_skill_add_installs_its_tool_first(env):
    available = ["npx", "brew"]
    run = FakeRun(after=lambda: available.append("tool"))
    svc = _service(
        env, available=available, run=run, fetch=FakeFetch(_good_archive()), catalog=_catalog_with_skill_needing("tool")
    )

    row = _rows(svc)["skill:demo-skill"]
    assert (row["state"], row["note"], row["actions"]) == ("available", "Installs Tool first", ["add"])
    assert row["preview"] == "brew install tool-formula; owner/repo@main:skills/demo-skill"

    result = asyncio.run(svc.add("skill:demo-skill"))

    assert result["ok"] and result["skills_changed"]
    assert result["message"] == (
        "Installed Tool. Next, run `tool login` in your terminal. Added Demo Skill to your skills."
    )
    assert run.calls == [["brew", "install", "tool-formula"]]
    assert (_skills_dir(env) / "demo-skill" / "SKILL.md").is_file()


def test_failed_tool_install_adds_no_skill(env):
    svc = _service(
        env,
        run=FakeRun(code=1, output="boom"),
        fetch=FakeFetch(_good_archive()),
        catalog=_catalog_with_skill_needing("tool"),
    )

    result = asyncio.run(svc.add("skill:demo-skill"))

    assert result["ok"] is False and "Tool install failed (exit 1)" in result["message"]
    assert not (_skills_dir(env) / "demo-skill").exists()

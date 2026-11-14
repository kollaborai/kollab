"""The file and git handlers honor the Code settings (#987, spec docs/specs/code-shell-settings.md).

The handlers are called with a CodeSettings the way ``kollabor.code_service.call`` passes one. Each setting that
changes files or git has its test here; the existing test files are unchanged.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.code_service import files as code_files
from kollabor.code_service import git as code_git
from kollabor.code_service.settings import CodeSettings

KB = 1024
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def ws(tmp_path):
    """A workspace folder and a folder beside it that lies outside the workspace (both under a resolved home)."""
    home = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    root = home / "ws"
    outside = home / "outside"
    root.mkdir()
    outside.mkdir()
    return SimpleNamespace(home=home, root=root, outside=outside)


@pytest.fixture
def repo(ws, monkeypatch):
    """The workspace as a git repository with an identity for commits."""
    for key, value in (
        ("GIT_AUTHOR_NAME", "Test"),
        ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
        ("GIT_COMMITTER_NAME", "Test"),
        ("GIT_COMMITTER_EMAIL", "test@example.invalid"),
    ):
        monkeypatch.setenv(key, value)
    _cli(ws.root, "init", "-q")
    return ws.root


def _write(path: Path, data: str | bytes = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def _link(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this system cannot make symlinks")


def _cli(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    return done.stdout


def _fs(op: str, root: Path, params: dict | None = None, settings: CodeSettings | None = None):
    return asyncio.run(code_files.serve(op, str(root), dict(params or {}), settings))


def _git(root: Path, body: dict, settings: CodeSettings | None = None):
    return asyncio.run(code_git.serve(str(root), body, settings))


def _tree(root: Path, settings: CodeSettings | None = None) -> list[dict]:
    reply = _fs("tree", root, None, settings)
    assert reply.status_code == 200
    return reply.content["data"]["tree"]


def _tree_paths(nodes: list[dict], root: Path) -> set[str]:
    """Every path in a tree answer, relative to the workspace, folders included."""
    found: set[str] = set()
    for node in nodes:
        found.add(os.path.relpath(node["path"], root))
        found |= _tree_paths(node.get("children", []), root)
    return found


def _search_hits(root: Path, settings: CodeSettings | None = None, query: str = "needle") -> list[dict]:
    reply = _fs("search", root, {"query": query}, settings)
    assert reply.status_code == 200
    return reply.content["data"]["results"]


def _search_paths(root: Path, settings: CodeSettings | None = None) -> set[str]:
    return {hit["path"] for hit in _search_hits(root, settings)}


def _mtime_ms(path: Path) -> int:
    return os.stat(path).st_mtime_ns // 1_000_000


def _move_mtime_on_disk(path: Path, ms: int) -> None:
    """Pretend another program saved the file ``ms`` milliseconds after it was last written."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + ms * 1_000_000))


# --- max_file_kb ------------------------------------------------------------------------------------------


def test_max_file_kb_sets_the_read_limit_and_the_refusal_names_the_setting(ws):
    settings = CodeSettings(max_file_kb=16)
    _write(ws.root / "edge.txt", "a" * (16 * KB))
    _write(ws.root / "big.txt", "a" * (16 * KB + 1))

    edge = _fs("file", ws.root, {"path": "edge.txt"}, settings)
    big = _fs("file", ws.root, {"path": "big.txt"}, settings)

    assert edge.status_code == 200 and edge.content["data"]["size"] == 16 * KB
    assert big.status_code == 400
    assert big.content["error"]["code"] == "file_too_large"
    assert big.content["error"]["message"].endswith(" Setting: kollabor.code.files.max_file_kb")


def test_max_file_kb_sets_the_write_limit(ws):
    settings = CodeSettings(max_file_kb=16)

    fits = _fs("write_file", ws.root, {"path": "a.txt", "content": "a" * (16 * KB)}, settings)
    too_big = _fs("write_file", ws.root, {"path": "b.txt", "content": "a" * (16 * KB + 1)}, settings)

    assert fits.status_code == 200
    assert too_big.status_code == 400 and too_big.content["error"]["code"] == "file_too_large"
    assert too_big.content["error"]["message"].endswith(" Setting: kollabor.code.files.max_file_kb")
    assert not (ws.root / "b.txt").exists()


# --- show_hidden ------------------------------------------------------------------------------------------


def test_show_hidden_tree_lists_dot_skipped_and_unknown_extension_entries_but_never_dot_git(ws):
    for rel in (".env", "node_modules/pkg/index.js", "dist/bundle.js", "image.bin", "readme.md", ".git/config"):
        _write(ws.root / rel)

    assert _tree_paths(_tree(ws.root), ws.root) == {"readme.md"}
    shown = _tree_paths(_tree(ws.root, CodeSettings(show_hidden=True)), ws.root)
    assert shown == {
        ".env",
        "node_modules",
        os.path.join("node_modules", "pkg"),
        os.path.join("node_modules", "pkg", "index.js"),
        "dist",
        os.path.join("dist", "bundle.js"),
        "image.bin",
        "readme.md",
    }


def test_show_hidden_search_reads_hidden_and_skipped_folders_but_never_git_or_binary(ws):
    _write(ws.root / "src" / "app.py", "needle")
    _write(ws.root / "node_modules" / "lib.js", "needle")
    _write(ws.root / ".secret.txt", "needle")
    _write(ws.root / ".git" / "config", "needle")
    (ws.root / "pic.png").write_bytes(b"needle")

    assert _search_paths(ws.root) == {os.path.join("src", "app.py")}
    assert _search_paths(ws.root, CodeSettings(show_hidden=True)) == {
        os.path.join("src", "app.py"),
        os.path.join("node_modules", "lib.js"),
        ".secret.txt",
    }


# --- search_max_files -------------------------------------------------------------------------------------


def test_search_max_files_sets_how_many_files_one_search_scans(ws):
    for index in range(150):
        _write(ws.root / f"f{index:03}.txt", "needle")

    assert len(_search_hits(ws.root)) == 150
    assert len(_search_hits(ws.root, CodeSettings(search_max_files=100))) == 100


def test_search_reports_when_file_limit_is_reached(ws):
    for index in range(150):
        _write(ws.root / f"f{index:03}.txt", "needle")
    result = _fs("search", ws.root, {"query": "needle"}, CodeSettings(search_max_files=100))
    assert len(result.content["data"]["results"]) == 100
    assert result.content["data"]["truncated"] is True


def test_search_reports_unreadable_directories_and_files(ws, monkeypatch):
    _write(ws.root / "sub" / "inside.txt", "needle")
    _write(ws.root / "other.txt", "needle")
    original_scandir = os.scandir
    with monkeypatch.context() as scoped:
        def fail_directory(path):
            if os.fspath(path) == str(ws.root / "sub"):
                raise PermissionError("denied")
            return original_scandir(path)

        scoped.setattr(code_files.os, "scandir", fail_directory)
        assert _fs("search", ws.root, {"query": "needle"}).content["data"]["truncated"] is True
    original_open = os.open
    with monkeypatch.context() as scoped:
        def fail_file(path, flags, *args, **kwargs):
            if os.fspath(path) == str(ws.root / "other.txt"):
                raise PermissionError("denied")
            return original_open(path, flags, *args, **kwargs)

        scoped.setattr(code_files.os, "open", fail_file)
        assert _fs("search", ws.root, {"query": "needle"}).content["data"]["truncated"] is True


# --- extra_roots ------------------------------------------------------------------------------------------


def test_extra_roots_let_read_and_write_reach_a_folder_outside_the_workspace(ws):
    _write(ws.outside / "notes.txt", "shared")
    settings = CodeSettings(extra_roots=(str(ws.outside),))

    read_absolute = _fs("file", ws.root, {"path": str(ws.outside / "notes.txt")}, settings)
    read_relative = _fs("file", ws.root, {"path": "../outside/notes.txt"}, settings)
    wrote = _fs("write_file", ws.root, {"path": str(ws.outside / "new.txt"), "content": "made"}, settings)

    assert read_absolute.content["data"]["content"] == "shared"
    assert read_relative.content["data"]["content"] == "shared"
    assert wrote.status_code == 200
    assert (ws.outside / "new.txt").read_text() == "made"
    assert _fs("file", ws.root, {"path": str(ws.outside / "notes.txt")}).status_code == 403


def test_extra_roots_cover_create_mkdir_rename_and_delete_on_both_ends(ws):
    settings = CodeSettings(extra_roots=(str(ws.outside),))

    created = _fs("create", ws.root, {"path": str(ws.outside / "made.txt"), "type": "file"}, settings)
    folder = _fs("mkdir", ws.root, {"parent": str(ws.outside), "name": "sub"}, settings)
    pulled = _fs("rename", ws.root, {"oldPath": str(ws.outside / "made.txt"), "newPath": "inside.txt"}, settings)
    pushed = _fs("rename", ws.root, {"oldPath": "inside.txt", "newPath": str(ws.outside / "back.txt")}, settings)
    removed = _fs("delete", ws.root, {"path": str(ws.outside / "back.txt")}, settings)

    assert [created.status_code, folder.status_code, pulled.status_code, pushed.status_code, removed.status_code] == [
        200,
        200,
        200,
        200,
        200,
    ]
    assert (ws.outside / "sub").is_dir()
    assert not (ws.outside / "back.txt").exists()
    assert not (ws.root / "inside.txt").exists()


def test_without_extra_roots_create_mkdir_and_rename_outside_are_refused(ws):
    assert _fs("create", ws.root, {"path": str(ws.outside / "x.txt"), "type": "file"}).status_code == 403
    assert _fs("mkdir", ws.root, {"parent": str(ws.outside), "name": "sub"}).status_code == 403
    _write(ws.root / "a.txt")
    assert _fs("rename", ws.root, {"oldPath": "a.txt", "newPath": str(ws.outside / "a.txt")}).status_code == 403


def test_outside_refusal_names_the_extra_roots_setting(ws):
    _write(ws.outside / "secret.txt", "classified")

    refused = _fs("file", ws.root, {"path": str(ws.outside / "secret.txt")})

    assert refused.status_code == 403
    assert refused.content["error"]["code"] == "outside_workspace"
    assert refused.content["error"]["message"] == (
        "That path is outside the workspace. Setting: kollabor.code.files.extra_roots"
    )


def test_tree_and_search_still_cover_only_the_workspace_with_extra_roots(ws):
    _write(ws.outside / "other.md", "needle")
    settings = CodeSettings(extra_roots=(str(ws.outside),))

    assert _tree(ws.root, settings) == []
    assert _search_hits(ws.root, settings) == []


# --- follow_symlinks --------------------------------------------------------------------------------------


def test_follow_symlinks_reads_and_writes_a_link_whose_target_is_outside(ws):
    _write(ws.outside / "secret.txt", "classified")
    _link(ws.root / "peek.txt", ws.outside / "secret.txt")
    settings = CodeSettings(follow_symlinks=True)

    read = _fs("file", ws.root, {"path": "peek.txt"}, settings)
    wrote = _fs("write_file", ws.root, {"path": "peek.txt", "content": "changed"}, settings)

    assert read.content["data"]["content"] == "classified"
    assert wrote.status_code == 200
    assert (ws.outside / "secret.txt").read_text() == "changed"
    assert os.path.islink(ws.root / "peek.txt")


def test_without_follow_symlinks_a_link_whose_target_is_outside_is_still_refused(ws):
    _write(ws.outside / "secret.txt", "classified")
    _link(ws.root / "peek.txt", ws.outside / "secret.txt")

    refused = _fs("file", ws.root, {"path": "peek.txt"})

    assert refused.status_code == 403 and refused.content["error"]["code"] == "outside_workspace"


def test_follow_symlinks_lists_linked_folders_and_files_in_tree_and_search(ws):
    _write(ws.outside / "tree" / "in.md", "needle")
    _link(ws.root / "linkdir", ws.outside / "tree")
    _link(ws.root / "linkfile.md", ws.outside / "tree" / "in.md")
    settings = CodeSettings(follow_symlinks=True)

    by_name = {node["name"]: node for node in _tree(ws.root, settings)}

    assert by_name["linkdir"]["type"] == "dir"
    assert [child["name"] for child in by_name["linkdir"]["children"]] == ["in.md"]
    assert by_name["linkfile.md"] == {
        "name": "linkfile.md",
        "path": str(ws.root / "linkfile.md"),
        "type": "file",
        "ext": ".md",
    }
    assert _tree(ws.root) == []
    assert _search_paths(ws.root, settings) == {os.path.join("linkdir", "in.md"), "linkfile.md"}


def test_paged_listing_honors_follow_symlinks(ws):
    _write(ws.outside / "tree" / "in.md", "needle")
    _link(ws.root / "linkdir", ws.outside / "tree")
    params = {"path": str(ws.root / "linkdir")}
    refused = _fs("list", ws.root, params)
    listed = _fs("list", ws.root, params, CodeSettings(follow_symlinks=True))
    assert refused.status_code == 403
    assert [entry["name"] for entry in listed.content["data"]["entries"]] == ["in.md"]


def test_follow_symlinks_stops_at_a_link_back_to_an_ancestor(ws):
    _write(ws.root / "a.txt", "needle")
    _link(ws.root / "loop", ws.root)
    settings = CodeSettings(follow_symlinks=True)

    assert "loop" not in {node["name"] for node in _tree(ws.root, settings)}
    assert len(_search_hits(ws.root, settings)) == 1


# --- UTF-8 safe save --------------------------------------------------------------------------------------


def test_read_reports_latin1_for_a_file_that_is_not_utf8_and_utf8_otherwise(ws):
    (ws.root / "legacy.txt").write_bytes("café\n".encode("latin-1"))
    (ws.root / "modern.txt").write_text("café\n", encoding="utf-8")

    legacy = _fs("file", ws.root, {"path": "legacy.txt"}).content["data"]
    modern = _fs("file", ws.root, {"path": "modern.txt"}).content["data"]

    assert legacy["content"] == "café\n" and legacy["encoding"] == "latin-1"
    assert modern["content"] == "café\n" and modern["encoding"] == "utf-8"


def test_a_latin1_file_read_and_written_back_unchanged_keeps_its_exact_bytes(ws):
    original = "café\n".encode("latin-1")
    (ws.root / "legacy.txt").write_bytes(original)
    text = _fs("file", ws.root, {"path": "legacy.txt"}).content["data"]["content"]

    wrote = _fs("write_file", ws.root, {"path": "legacy.txt", "content": text})

    assert wrote.status_code == 200
    assert wrote.content["data"]["encoding"] == "latin-1"
    assert (ws.root / "legacy.txt").read_bytes() == original


def test_writing_a_character_past_latin1_to_a_latin1_file_is_refused_and_keeps_the_file(ws):
    original = "caf\u00e9 It\u2019s".encode("cp1252", errors="ignore") + b"\x92\n"
    (ws.root / "legacy.txt").write_bytes(original)

    wrote = _fs("write_file", ws.root, {"path": "legacy.txt", "content": "price \u20ac5\n"})

    assert wrote.status_code == 409
    assert wrote.content["error"]["code"] == "encoding_unsafe"
    assert (ws.root / "legacy.txt").read_bytes() == original


def test_a_new_file_is_written_as_utf8_and_the_result_says_so(ws):
    wrote = _fs("write_file", ws.root, {"path": "new.txt", "content": "café"})

    assert wrote.content["data"]["encoding"] == "utf-8"
    assert (ws.root / "new.txt").read_bytes() == "café".encode("utf-8")


# --- stale write check ------------------------------------------------------------------------------------


def test_read_returns_the_mtime_in_whole_milliseconds(ws):
    _write(ws.root / "a.txt", "hello")

    data = _fs("file", ws.root, {"path": "a.txt"}).content["data"]

    assert isinstance(data["mtime"], int)
    assert data["mtime"] == _mtime_ms(ws.root / "a.txt")


def test_write_with_a_stale_expected_mtime_is_refused_with_409_and_keeps_the_file(ws):
    _write(ws.root / "a.txt", "old")
    opened = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["mtime"]
    _move_mtime_on_disk(ws.root / "a.txt", 5000)

    refused = _fs("write_file", ws.root, {"path": "a.txt", "content": "mine", "expectedMtime": opened})

    assert refused.status_code == 409
    assert refused.content["error"]["code"] == "stale_write"
    assert refused.content["error"]["message"] == "The file changed on disk since you opened it."
    assert (ws.root / "a.txt").read_text() == "old"


def test_write_with_a_stale_expected_mtime_and_force_saves(ws):
    _write(ws.root / "a.txt", "old")
    opened = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["mtime"]
    _move_mtime_on_disk(ws.root / "a.txt", 5000)

    forced = _fs("write_file", ws.root, {"path": "a.txt", "content": "mine", "expectedMtime": opened, "force": True})

    assert forced.status_code == 200
    assert (ws.root / "a.txt").read_text() == "mine"


def test_write_with_the_current_expected_mtime_saves_and_returns_the_new_mtime(ws):
    _write(ws.root / "a.txt", "old")
    opened = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["mtime"]

    wrote = _fs("write_file", ws.root, {"path": "a.txt", "content": "new", "expectedMtime": opened})

    assert wrote.status_code == 200
    assert wrote.content["data"]["mtime"] == _mtime_ms(ws.root / "a.txt")


def test_write_without_an_expected_mtime_does_no_check(ws):
    _write(ws.root / "a.txt", "old")
    _move_mtime_on_disk(ws.root / "a.txt", 5000)

    wrote = _fs("write_file", ws.root, {"path": "a.txt", "content": "new"})

    assert wrote.status_code == 200
    assert (ws.root / "a.txt").read_text() == "new"


# --- git status with -z -----------------------------------------------------------------------------------


@needs_git
def test_git_status_returns_names_with_spaces_quotes_and_non_ascii_exact(repo):
    names = ["a b.txt", '"q".txt', "héllo.txt"]
    for name in names:
        _write(repo / name)

    files = _git(repo, {"action": "status"}).content["data"]["files"]

    assert {file["path"]: file["statusCode"] for file in files} == {name: "??" for name in names}


@needs_git
def test_git_status_reads_a_staged_rename_as_one_entry_under_its_new_name(repo):
    _write(repo / "old name.txt", "x")
    _cli(repo, "add", "old name.txt")
    _cli(repo, "commit", "-q", "-m", "init")
    _cli(repo, "mv", "old name.txt", "new é.txt")

    files = _git(repo, {"action": "status"}).content["data"]["files"]

    assert [(f["path"], f["statusCode"], f["staged"], f["unstaged"]) for f in files] == [
        ("new é.txt", "R ", True, False),
    ]


# --- git.allow_outside_workspace --------------------------------------------------------------------------


def _stage_outside_and_inside(repo: Path) -> Path:
    """A workspace at repo/app with one file staged from outside it (repo/top.txt) and one inside it."""
    app = repo / "app"
    _write(repo / "top.txt")
    _write(app / "inside.txt")
    _cli(repo, "add", "top.txt")
    staged = _git(app, {"action": "stage", "paths": ["inside.txt"]})
    assert staged.status_code == 200
    return app


@needs_git
def test_commit_refuses_files_staged_outside_the_workspace_and_names_the_setting(repo):
    app = _stage_outside_and_inside(repo)

    refused = _git(app, {"action": "commit", "message": "work"})

    assert refused.status_code == 409
    assert refused.content["error"]["code"] == "staged_outside_workspace"
    assert refused.content["error"]["message"].endswith(" Setting: kollabor.code.git.allow_outside_workspace")
    assert set(refused.content["error"]) == {"code", "message"}


@needs_git
def test_commit_goes_through_when_git_allow_outside_workspace_is_on(repo):
    app = _stage_outside_and_inside(repo)

    committed = _git(app, {"action": "commit", "message": "work"}, CodeSettings(git_allow_outside_workspace=True))

    assert committed.status_code == 200
    assert committed.content["data"]["ok"] is True
    assert sorted(_cli(repo, "show", "--name-only", "--format=", "HEAD").split()) == ["app/inside.txt", "top.txt"]


# ---------------------------------------------------------------------------------------------- review fixes


def test_delete_of_a_symlink_removes_the_link_and_never_its_target(ws):
    _write(ws.root / "docs" / "README.md", "keep me")
    _link(ws.root / "README.md", ws.root / "docs" / "README.md")

    reply = _fs("delete", ws.root, {"path": "README.md"})

    assert reply.status_code == 200
    assert not os.path.lexists(ws.root / "README.md")
    assert (ws.root / "docs" / "README.md").read_text() == "keep me"


def test_force_delete_of_a_link_to_a_folder_leaves_the_folder(ws):
    _write(ws.root / "releases" / "v2" / "app.txt")
    _link(ws.root / "current", ws.root / "releases" / "v2")

    reply = _fs("delete", ws.root, {"path": "current", "force": True})

    assert reply.status_code == 200
    assert not os.path.lexists(ws.root / "current")
    assert (ws.root / "releases" / "v2" / "app.txt").exists()


def test_rename_of_a_symlink_moves_the_link_and_not_its_target(ws):
    _write(ws.root / "real.txt", "data")
    _link(ws.root / "alias", ws.root / "real.txt")

    reply = _fs("rename", ws.root, {"oldPath": "alias", "newPath": "x.txt"})

    assert reply.status_code == 200
    assert (ws.root / "real.txt").read_text() == "data"
    assert os.path.islink(ws.root / "x.txt") and not os.path.lexists(ws.root / "alias")


def test_a_non_integer_expected_mtime_is_refused_not_ignored(ws):
    _write(ws.root / "a.txt", "one")

    reply = _fs("write_file", ws.root, {"path": "a.txt", "content": "two", "expectedMtime": "123"})

    assert reply.status_code == 400
    assert (ws.root / "a.txt").read_text() == "one"


def test_two_saves_with_the_same_expected_mtime_cannot_both_win(ws):
    _write(ws.root / "a.txt", "one")
    mtime = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["mtime"]

    async def both():
        params = {"path": "a.txt", "expectedMtime": mtime}
        return await asyncio.gather(
            code_files.serve("write_file", str(ws.root), {**params, "content": "A"}, None),
            code_files.serve("write_file", str(ws.root), {**params, "content": "B"}, None),
        )

    first, second = asyncio.run(both())

    assert sorted([first.status_code, second.status_code]) == [200, 409]


def test_a_write_to_a_fifo_path_does_not_hang(ws):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs here")
    os.mkfifo(ws.root / "pipe")

    attempt = code_files.serve("write_file", str(ws.root), {"path": "pipe", "content": "x"}, None)
    asyncio.run(asyncio.wait_for(attempt, 5))


def test_status_marks_every_unmerged_pair_as_a_conflict():
    assert {code_files._status_code(xy) for xy in ("UU", "DU", "UD", "AU", "UA", "AA", "DD")} == {"U"}


@needs_git
def test_a_rename_that_crosses_into_the_workspace_still_counts_as_outside(repo):
    app = repo / "app"
    _write(repo / "top.txt", "same\n" * 20)
    _write(app / "keep.txt")
    _cli(repo, "add", "-A")
    _cli(repo, "commit", "-q", "-m", "base")
    _cli(repo, "mv", "top.txt", "app/top.txt")

    refused = _git(app, {"action": "commit", "message": "move"})

    assert refused.status_code == 409
    assert refused.content["error"]["code"] == "staged_outside_workspace"


@needs_git
def test_a_stash_from_a_subfolder_leaves_the_rest_of_the_repository_alone(repo):
    app = repo / "app"
    _write(repo / "top.txt", "one\n")
    _write(app / "in.txt", "one\n")
    _cli(repo, "add", "-A")
    _cli(repo, "commit", "-q", "-m", "base")
    _write(repo / "top.txt", "two\n")
    _write(app / "in.txt", "two\n")

    stashed = _git(app, {"action": "create_stash"})

    assert stashed.status_code == 200
    assert (app / "in.txt").read_text() == "one\n"
    assert (repo / "top.txt").read_text() == "two\n"


def test_version_rejects_deleted_file_and_same_millisecond_replacement(ws):
    path = ws.root / "a.txt"
    _write(path, "old")
    version = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["version"]
    path.unlink()
    reply = _fs("write_file", ws.root, {"path": "a.txt", "content": "mine", "expectedVersion": version})
    assert reply.status_code == 409
    assert not path.exists()
    _write(path, "old")
    version = _fs("file", ws.root, {"path": "a.txt"}).content["data"]["version"]
    first = _fs("write_file", ws.root, {"path": "a.txt", "content": "one", "expectedVersion": version})
    second = _fs("write_file", ws.root, {"path": "a.txt", "content": "two", "expectedVersion": version})
    assert first.status_code == 200
    assert second.status_code == 409
    assert path.read_text() == "one"


@needs_git
def test_applying_stash_cannot_modify_sibling_folder(repo):
    app = repo / "app"
    _write(repo / "outside.txt", "base")
    _write(app / "inside.txt", "base")
    _cli(repo, "add", "-A")
    _cli(repo, "commit", "-q", "-m", "base")
    _write(repo / "outside.txt", "changed")
    _write(app / "inside.txt", "changed")
    _write(repo / "untracked.txt", "untracked")
    _cli(repo, "stash", "push", "-u")
    reply = _git(app, {"action": "apply_stash", "stashId": "0"})
    assert reply.status_code == 409
    assert (repo / "outside.txt").read_text() == "base"
    assert not (repo / "untracked.txt").exists()


@needs_git
def test_subfolder_commit_refuses_when_scope_cannot_be_guaranteed(repo):
    app = repo / "app"
    _write(repo / "outside.txt", "base")
    _write(app / "inside.txt", "base")
    _cli(repo, "add", "-A")
    _cli(repo, "commit", "-q", "-m", "base")
    before = _cli(repo, "rev-parse", "HEAD").strip()
    _write(app / "inside.txt", "change")
    _cli(repo, "add", "app/inside.txt")
    result = _git(app, {"action": "commit", "message": "inside"})
    assert result.status_code == 409
    assert result.content["error"]["code"] == "subfolder_commit_unavailable"
    assert _cli(repo, "rev-parse", "HEAD").strip() == before
    assert _cli(repo, "diff", "--cached", "--name-only").splitlines() == ["app/inside.txt"]


def test_rename_does_not_replace_destination_created_after_precheck(ws, monkeypatch):
    _write(ws.root / "source.txt", "source")
    destination = ws.root / "target.txt"
    real_lexists = os.path.lexists
    def race(path):
        exists = real_lexists(path)
        if str(path) == str(destination) and not exists:
            _write(destination, "other writer")
            return False
        return exists
    monkeypatch.setattr(os.path, "lexists", race)
    result = _fs("rename", ws.root, {"oldPath": "source.txt", "newPath": "target.txt"})
    assert result.status_code == 409
    assert destination.read_text() == "other writer"
    assert (ws.root / "source.txt").read_text() == "source"

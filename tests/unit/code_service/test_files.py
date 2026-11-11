"""The daemon's file API (kollabor/code_service/files.py): the mentiko /api/fs/* contract (#987)."""

import asyncio
import os
import shutil
import stat
import subprocess
from types import SimpleNamespace

import pytest

from kollabor.code_service import files as code_files
from tests.unit.code_service.daemon_client import DaemonClient

MB = 1024 * 1024
TWO_MB = 2 * MB


@pytest.fixture
def client():
    return DaemonClient()


@pytest.fixture
def ws(tmp_path):
    """A workspace folder under home, and a folder beside it that lies outside the workspace."""
    home = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    root = home / "ws"
    outside = home / "outside"
    root.mkdir()
    outside.mkdir()
    return SimpleNamespace(home=home, root=root, outside=outside)


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _link(path, target):
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this system cannot make symlinks")


def _get(client, route, root, **params):
    return client.get(f"/code/fs/{route}", params={"workspace": str(root), **params})


def _post(client, route, root, **body):
    return client.post(f"/code/fs/{route}", json={"workspace": str(root), **body})


def _delete(client, root, **body):
    return client.request("DELETE", "/code/fs/delete", json={"workspace": str(root), **body})


def _put(client, root, path, content):
    return client.put("/code/fs/file", params={"path": path}, json={"workspace": str(root), "content": content})


def _refused(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == code


# --- tree --------------------------------------------------------------------------------------------


def test_tree_lists_folders_then_files_and_skips_hidden_and_build_folders(client, ws):
    for rel in (
        "b.py", "a.md", "Makefile", ".env", "adir/x.exe", "zdir/inner.ts",
        "node_modules/pkg/index.js", "dist/bundle.js", ".venv/lib.py",
    ):
        _write(ws.root / rel)

    response = _get(client, "tree", ws.root)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["path"] == str(ws.root)
    tree = data["tree"]
    assert [(node["name"], node["type"]) for node in tree] == [
        ("adir", "dir"),
        ("zdir", "dir"),
        ("a.md", "file"),
        ("b.py", "file"),
        ("Makefile", "file"),
    ]
    assert tree[1]["children"] == [
        {"name": "inner.ts", "path": str(ws.root / "zdir" / "inner.ts"), "type": "file", "ext": ".ts"}
    ]
    assert tree[4]["ext"] == ""


def test_tree_neither_lists_nor_follows_a_symlink(client, ws):
    _write(ws.outside / "secret.txt")
    _link(ws.root / "peek", ws.outside)
    _link(ws.root / "secret.txt", ws.outside / "secret.txt")

    tree = _get(client, "tree", ws.root).json()["data"]["tree"]

    assert tree == []


# --- read --------------------------------------------------------------------------------------------


def test_read_returns_the_content_path_and_size(client, ws):
    _write(ws.root / "notes" / "a.txt", "hello")

    response = _get(client, "file", ws.root, path="notes/a.txt")

    data = response.json()["data"]
    assert response.json()["success"] is True
    assert {k: data[k] for k in ("path", "content", "size", "encoding")} == {
        "path": str(ws.root / "notes" / "a.txt"),
        "content": "hello",
        "size": 5,
        "encoding": "utf-8",
    }
    assert isinstance(data["mtime"], int)


def test_read_refuses_a_binary_file(client, ws):
    (ws.root / "blob.bin").write_bytes(b"\x00\x01\x02")

    _refused(_get(client, "file", ws.root, path="blob.bin"), 400, "not_text")


def test_read_serves_exactly_two_megabytes_and_refuses_one_byte_more(client, ws):
    _write(ws.root / "edge.txt", "a" * TWO_MB)
    _write(ws.root / "big.txt", "a" * (TWO_MB + 1))

    assert _get(client, "file", ws.root, path="edge.txt").json()["data"]["size"] == TWO_MB
    _refused(_get(client, "file", ws.root, path="big.txt"), 400, "file_too_large")


def test_read_a_missing_file_is_404(client, ws):
    _refused(_get(client, "file", ws.root, path="nope.txt"), 404, "not_found")


# --- write -------------------------------------------------------------------------------------------


def test_write_replaces_the_content(client, ws):
    _write(ws.root / "a.txt", "old")

    response = _put(client, ws.root, "a.txt", "new")

    data = response.json()["data"]
    assert response.json()["success"] is True and data["success"] is True
    assert data["path"] == str(ws.root / "a.txt") and data["encoding"] == "utf-8" and isinstance(data["mtime"], int)
    assert (ws.root / "a.txt").read_text() == "new"


def test_a_write_leaves_no_temp_file_behind(client, ws):
    _write(ws.root / "a.txt", "old")

    _put(client, ws.root, "a.txt", "new")

    assert sorted(os.listdir(ws.root)) == ["a.txt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_write_keeps_the_file_mode(client, ws):
    _write(ws.root / "run.sh", "old")
    os.chmod(ws.root / "run.sh", 0o640)

    _put(client, ws.root, "run.sh", "new")

    assert stat.S_IMODE(os.stat(ws.root / "run.sh").st_mode) == 0o640


def test_a_failed_write_keeps_the_old_content_and_leaves_no_temp_file(client, ws, monkeypatch):
    _write(ws.root / "a.txt", "old")

    def refuse(source, target):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(code_files.os, "replace", refuse)

    _refused(_put(client, ws.root, "a.txt", "new"), 500, "io_error")
    assert (ws.root / "a.txt").read_text() == "old"
    assert sorted(os.listdir(ws.root)) == ["a.txt"]


def test_a_write_over_two_megabytes_is_refused(client, ws):
    _refused(_put(client, ws.root, "big.txt", "a" * (TWO_MB + 1)), 400, "file_too_large")
    assert not (ws.root / "big.txt").exists()


def test_tree_refuses_large_directories_without_returning_partial_results(client, ws, monkeypatch):
    monkeypatch.setattr(code_files, "TREE_MAX_ENTRIES", 2)
    for name in ("a.txt", "b.txt", "c.txt"):
        _write(ws.root / name, "x")
    _refused(_get(client, "tree", ws.root), 413, "tree_too_large")
    search = _get(client, "search", ws.root, query="test")
    assert search.status_code == 200
    assert search.json()["data"]["truncated"] is False


def test_large_directory_can_be_listed_in_bounded_pages(client, ws, monkeypatch):
    monkeypatch.setattr(code_files, "DIRECTORY_PAGE_SIZE", 2)
    (ws.root / "folder").mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        _write(ws.root / name, "x")
    first = _get(client, "list", ws.root, path=str(ws.root)).json()["data"]
    second = _get(client, "list", ws.root, path=str(ws.root), after=first["next"]).json()["data"]
    assert [item["name"] for item in first["entries"] + second["entries"]] == [
        "folder", "a.txt", "b.txt", "c.txt"
    ]
    assert second["next"] is None
    _refused(_get(client, "list", ws.root, path=str(ws.outside)), 403, "outside_workspace")


def test_write_does_not_replace_an_oversized_existing_file(client, ws):
    path = ws.root / "big.txt"
    path.write_bytes(b"a" * (TWO_MB + 1))
    _refused(_put(client, ws.root, "big.txt", "small"), 400, "file_too_large")
    assert path.stat().st_size == TWO_MB + 1


# --- create, mkdir, rename, delete -------------------------------------------------------------------


def test_create_makes_a_file_and_never_truncates_an_existing_one(client, ws):
    _write(ws.root / "keep.txt", "precious")

    created = _post(client, "create", ws.root, path="new/deep.txt", type="file")
    again = _post(client, "create", ws.root, path="keep.txt", type="file")

    assert created.json() == {
        "success": True,
        "data": {"success": True, "path": str(ws.root / "new" / "deep.txt")},
    }
    assert (ws.root / "new" / "deep.txt").read_text() == ""
    _refused(again, 409, "already_exists")
    assert (ws.root / "keep.txt").read_text() == "precious"


def test_create_refuses_an_existing_folder_and_an_unknown_type(client, ws):
    (ws.root / "dir").mkdir()

    _refused(_post(client, "create", ws.root, path="dir", type="dir"), 409, "already_exists")
    _refused(_post(client, "create", ws.root, path="x", type="link"), 400, "invalid_type")


def test_mkdir_makes_a_folder_and_refuses_bad_or_existing_names(client, ws):
    response = _post(client, "mkdir", ws.root, parent=".", name="sub")

    assert response.json() == {"success": True, "data": {"path": str(ws.root / "sub")}}
    assert (ws.root / "sub").is_dir()
    _refused(_post(client, "mkdir", ws.root, parent=".", name="sub"), 409, "already_exists")
    _refused(_post(client, "mkdir", ws.root, parent=".", name="a/b"), 400, "invalid_name")
    _refused(_post(client, "mkdir", ws.root, parent=".", name=".."), 400, "invalid_name")


def test_rename_moves_a_file_and_refuses_an_existing_destination(client, ws):
    _write(ws.root / "a.txt", "one")
    _write(ws.root / "b.txt", "two")

    _refused(_post(client, "rename", ws.root, oldPath="a.txt", newPath="b.txt"), 409, "already_exists")
    moved = _post(client, "rename", ws.root, oldPath="a.txt", newPath="c.txt")

    assert moved.json() == {"success": True, "data": {"success": True, "path": str(ws.root / "c.txt")}}
    assert (ws.root / "c.txt").read_text() == "one"
    assert not (ws.root / "a.txt").exists()


def test_rename_refuses_the_workspace_root_and_a_destination_outside(client, ws):
    _write(ws.root / "a.txt")

    _refused(_post(client, "rename", ws.root, oldPath=".", newPath="moved"), 403, "workspace_root")
    _refused(
        _post(client, "rename", ws.root, oldPath="a.txt", newPath="../outside/a.txt"),
        403,
        "outside_workspace",
    )
    assert (ws.root / "a.txt").exists()
    assert not (ws.outside / "a.txt").exists()


def test_delete_removes_a_file_and_a_folder_only_when_empty_or_forced(client, ws):
    _write(ws.root / "dir" / "inner.txt")
    _write(ws.root / "f.txt")

    _refused(_delete(client, ws.root, path="dir"), 400, "directory_not_empty")
    assert (ws.root / "dir" / "inner.txt").exists()

    forced = _delete(client, ws.root, path="dir", force=True)
    assert forced.json() == {"success": True, "data": {"success": True}}
    assert not (ws.root / "dir").exists()
    assert _delete(client, ws.root, path="f.txt").status_code == 200
    assert not (ws.root / "f.txt").exists()


def test_delete_refuses_the_workspace_root_and_reports_a_missing_path(client, ws):
    _refused(_delete(client, ws.root, path="."), 403, "workspace_root")
    _refused(_delete(client, ws.root, path="gone.txt"), 404, "not_found")
    assert ws.root.exists()


def test_delete_also_answers_post_like_mentiko(client, ws):
    _write(ws.root / "f.txt")

    assert _post(client, "delete", ws.root, path="f.txt").status_code == 200
    assert not (ws.root / "f.txt").exists()


# --- search ------------------------------------------------------------------------------------------


def test_search_returns_hits_and_skips_hidden_skipped_binary_and_big_files(client, ws):
    _write(ws.root / "src" / "app.py", "def hello():\n    return 'needle'\n")
    _write(ws.root / "node_modules" / "lib.js", "needle")
    _write(ws.root / ".secret.txt", "needle")
    (ws.root / "image.png").write_bytes(b"needle")
    (ws.root / "blob.txt").write_bytes(b"\x00needle")
    _write(ws.root / "big.txt", "needle" + "a" * MB)

    results = _get(client, "search", ws.root, query="needle").json()["data"]["results"]

    assert results == [
        {
            "path": os.path.join("src", "app.py"),
            "name": "app.py",
            "line": 2,
            "column": 1,
            "text": "return 'needle'",
            "context": "",
        }
    ]


def test_search_stops_at_two_hundred_results(client, ws):
    _write(ws.root / "many.txt", "needle\n" * 250)

    results = _get(client, "search", ws.root, query="needle").json()["data"]["results"]

    assert len(results) == 200


def test_search_reads_the_query_as_plain_text_even_when_it_looks_like_a_regex(client, ws):
    _write(ws.root / "a.txt", "needle42\n(a+)+ here\n")

    plain = _get(client, "search", ws.root, query="(a+)+", regex="true").json()["data"]["results"]
    assert [(hit["line"], hit["text"]) for hit in plain] == [(2, "(a+)+ here")]
    assert _get(client, "search", ws.root, query=r"needle\d+", regex="true").json()["data"]["results"] == []
    _refused(_get(client, "search", ws.root, query="x" * 201), 400, "invalid_query")


def test_search_needs_a_query_of_at_least_two_characters(client, ws):
    _refused(_get(client, "search", ws.root, query="a"), 400, "invalid_query")
    _refused(_get(client, "search", ws.root), 400, "missing_param")


def test_search_never_reads_through_a_symlink(client, ws):
    _write(ws.outside / "secret.txt", "needle")
    _link(ws.root / "peek.txt", ws.outside / "secret.txt")

    results = _get(client, "search", ws.root, query="needle").json()["data"]["results"]

    assert results == []


# --- git status --------------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_status_maps_porcelain_codes_inside_a_repository(client, ws):
    subprocess.run(["git", "-C", str(ws.root), "init", "-q"], check=True)
    _write(ws.root / "a.txt")
    subprocess.run(["git", "-C", str(ws.root), "add", "a.txt"], check=True)
    _write(ws.root / "b.txt")

    data = _get(client, "git-status", ws.root).json()["data"]

    assert data == {"status": {"a.txt": "A", "b.txt": "?"}}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_status_in_a_subfolder_keys_workspace_paths_and_skips_the_rest(client, ws):
    repo = ws.root
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _write(repo / "top.txt")
    _write(repo / "app" / "inside.txt")
    subprocess.run(["git", "-C", str(repo), "add", "app/inside.txt"], check=True)
    # the workspace is the subfolder: top.txt is outside it and must not show, inside.txt keys as "inside.txt"
    data = _get(client, "git-status", repo / "app").json()["data"]

    assert data == {"status": {"inside.txt": "A"}}


def test_git_status_outside_a_repository_says_so(client, ws):
    response = _get(client, "git-status", ws.root)

    assert response.json() == {"success": True, "data": {"error": "Not a git repository", "status": {}}}


# --- the workspace boundary --------------------------------------------------------------------------


def test_a_path_that_climbs_out_of_the_workspace_is_403(client, ws):
    _write(ws.outside / "secret.txt", "classified")

    response = _get(client, "file", ws.root, path="../outside/secret.txt")

    _refused(response, 403, "outside_workspace")
    assert "classified" not in response.text


def test_an_absolute_path_outside_the_workspace_is_403(client, ws):
    _write(ws.outside / "secret.txt", "classified")

    response = _get(client, "file", ws.root, path=str(ws.outside / "secret.txt"))

    _refused(response, 403, "outside_workspace")
    assert "classified" not in response.text


def test_writes_creates_and_deletes_cannot_leave_the_workspace(client, ws):
    _write(ws.outside / "keep.txt", "keep")

    _refused(_put(client, ws.root, "../outside/new.txt", "x"), 403, "outside_workspace")
    _refused(_post(client, "create", ws.root, path="../outside/made.txt", type="file"), 403, "outside_workspace")
    _refused(_post(client, "mkdir", ws.root, parent="../outside", name="made"), 403, "outside_workspace")
    _refused(_delete(client, ws.root, path="../outside/keep.txt"), 403, "outside_workspace")
    assert sorted(os.listdir(ws.outside)) == ["keep.txt"]


def test_a_symlink_pointing_outside_is_403_for_every_route(client, ws):
    _write(ws.outside / "secret.txt", "classified")
    _link(ws.root / "link", ws.outside)
    _link(ws.root / "peek.txt", ws.outside / "secret.txt")

    _refused(_get(client, "file", ws.root, path="link/secret.txt"), 403, "outside_workspace")
    _refused(_get(client, "file", ws.root, path="peek.txt"), 403, "outside_workspace")
    _refused(_put(client, ws.root, "link/new.txt", "x"), 403, "outside_workspace")
    _refused(_post(client, "create", ws.root, path="link/made.txt", type="file"), 403, "outside_workspace")
    _refused(_post(client, "mkdir", ws.root, parent="link", name="made"), 403, "outside_workspace")
    # deleting the link itself is allowed (it lives in the workspace); the file it points to is never touched
    assert _delete(client, ws.root, path="peek.txt").status_code == 200
    assert not os.path.lexists(ws.root / "peek.txt")
    assert sorted(os.listdir(ws.outside)) == ["secret.txt"]
    assert (ws.outside / "secret.txt").read_text() == "classified"


def test_a_folder_that_is_gone_is_refused_with_400(client, ws):
    missing = _get(client, "tree", ws.root / "nope")

    _refused(missing, 400, "bad_workspace")
    _refused(_post(client, "create", ws.root, type="file"), 400, "missing_param")


def test_the_daemon_serves_its_own_folder_and_a_request_cannot_name_another(client, ws, monkeypatch):
    # the engine proxy passes request fields on as params: a "workspace" there must never move the root
    _write(ws.root / "mine.txt")
    _write(ws.outside / "theirs.txt")
    monkeypatch.chdir(ws.root)

    from kollabor import code_service

    answer = asyncio.run(code_service.call("fs.tree", {"workspace": str(ws.outside)}))

    names = [entry["name"] for entry in answer["body"]["data"]["tree"]]
    assert names == ["mine.txt"]

"""Files for the code editor view: the engine's port of mentiko's /api/fs/* routes.

Spec: docs/specs/floating-code-shell.md. Request and response shapes are mentiko's, inside the spec envelope:
success is {"success": true, "data": ...}; a failure is a non-2xx {"success": false, "error": {"code", "message"}}.

Every route takes the folder as ``workspace`` (query for GET, JSON body for POST, PUT and DELETE) and runs it through
folder_or_400 first; only the folder that returns is used. Every path a request names is resolved with
os.path.realpath and must sit inside that resolved workspace. The check is written inline in each route, in the
closed-prefix form (CodeQL's path-injection idiom). tree and search never follow a symlink. Request bodies and file
contents are never logged.
"""

import asyncio
import contextlib
import os
import secrets
import shutil
import stat
import subprocess
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from ..workspace_folder import folder_or_400

router = APIRouter(prefix="/code/fs", tags=["code-files"])

MAX_FILE_BYTES = 2 * 1024 * 1024
TREE_MAX_DEPTH = 8
SEARCH_MAX_RESULTS = 200
SEARCH_MAX_FILE_BYTES = 1024 * 1024
SEARCH_MAX_FILES = 20_000
SEARCH_MAX_QUERY = 200
SEARCH_LINE_CHARS = 500
OUTSIDE = "That path is outside the workspace."

# mentiko's lists, unchanged, plus .venv (dot names are skipped anyway).
ALLOWED_EXTENSIONS = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".jsonl", ".yaml", ".yml",
        ".sh", ".bash", ".zsh", ".md", ".mdx", ".txt", ".css", ".scss", ".env", ".env.example",
        ".env.local", ".py", ".go", ".rs", ".rb", ".html", ".svg", ".xml", ".toml", ".ini", ".conf",
        ".graphql", ".gql", ".sql", ".dockerfile",
    }
)
SKIP_DIRS = frozenset(
    {
        "node_modules", ".git", ".next", "dist", "build", "__pycache__", ".cache", ".turbo",
        "coverage", ".claude", ".vscode", ".gitlab", ".venv",
    }
)
# search's binary skips (mentiko's list): never read as text.
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".woff", ".woff2", ".ttf", ".eot",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3",
        ".mov", ".avi", ".pyc", ".so", ".dylib", ".dll", ".exe",
    }
)

def _ok(data: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"success": True, "data": data})


def _fail(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": {"code": code, "message": message}})


def _disk_failure(exc: OSError) -> JSONResponse:
    """A disk error in the envelope: the status follows the kind of error, the words are fixed, and the system's
    own text (which can name paths) is never sent."""
    if isinstance(exc, FileNotFoundError):
        return _fail(404, "not_found", "Path not found.")
    if isinstance(exc, FileExistsError):
        return _fail(409, "already_exists", "Path already exists.")
    if isinstance(exc, PermissionError):
        return _fail(403, "permission_denied", "Permission denied.")
    if isinstance(exc, (NotADirectoryError, IsADirectoryError)):
        return _fail(400, "invalid_path", "That path cannot be used for this change.")
    return _fail(500, "io_error", "The file system refused the change.")


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    """The JSON object a POST, PUT or DELETE sends. Its text is never logged."""
    try:
        body = await request.json()
    except ValueError:
        return _fail(400, "invalid_json", "The request body must be JSON.")
    if not isinstance(body, dict):
        return _fail(400, "invalid_json", "The request body must be a JSON object.")
    return body


def _checked_workspace(workspace: object) -> str | JSONResponse:
    """The folder a request names, through folder_or_400. A refusal is a 400 in the envelope. ``detail`` repeats
    folder_or_400's words, the shape the folder-rule tests read (test_workspace_folder_routes.py)."""
    if not isinstance(workspace, str) or not workspace:
        return _fail(400, "missing_param", "workspace param required")
    try:
        return folder_or_400(workspace)
    except HTTPException as exc:
        message = str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": "bad_workspace", "message": message}, "detail": message},
        )


def _extension(name: str) -> str:
    """mentiko's getExtension: the text from the last dot, or "" for no dot or a leading one."""
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _entries(folder: str) -> list[os.DirEntry[str]]:
    """The entries of ``folder`` in name order; none when the folder cannot be read."""
    try:
        with os.scandir(folder) as scan:
            return sorted(scan, key=lambda entry: (entry.name.casefold(), entry.name))
    except OSError:
        return []


def _kind(entry: os.DirEntry[str]) -> str:
    """"dir" or "file" for an entry that is one. A symlink is neither: it is never followed or listed."""
    with contextlib.suppress(OSError):
        if entry.is_dir(follow_symlinks=False):
            return "dir"
        if entry.is_file(follow_symlinks=False):
            return "file"
    return ""


def _folder_listed(name: str) -> bool:
    return not name.startswith(".") and name not in SKIP_DIRS


def _tree_file_listed(name: str) -> bool:
    ext = _extension(name)
    return not name.startswith(".") and (ext == "" or ext in ALLOWED_EXTENSIONS)


def _search_file_listed(name: str) -> bool:
    return not name.startswith(".") and _extension(name) not in BINARY_EXTENSIONS


def _tree(folder: str, depth: int) -> list[dict[str, Any]]:
    """mentiko's buildTree: folders first, then files, each in name order, down to TREE_MAX_DEPTH.

    ``folder`` lies inside a workspace the route has already checked, and no symlink is followed.
    """
    if depth > TREE_MAX_DEPTH:
        return []
    listed = [(entry, _kind(entry)) for entry in _entries(folder)]
    nodes: list[dict[str, Any]] = []
    for entry, kind in listed:
        if kind == "dir" and _folder_listed(entry.name):
            children = _tree(entry.path, depth + 1)
            nodes.append({"name": entry.name, "path": entry.path, "type": "dir", "children": children})
    for entry, kind in listed:
        if kind == "file" and _tree_file_listed(entry.name):
            nodes.append({"name": entry.name, "path": entry.path, "type": "file", "ext": _extension(entry.name)})
    return nodes


def _search_files(root: str) -> Iterator[str]:
    """The files search may read under ``root``: folder by folder, names in order, no symlink followed, and at most
    SEARCH_MAX_FILES of them. ``root`` is a checked workspace."""
    pending = [root]
    scanned = 0
    while pending:
        folders: list[str] = []
        for entry in _entries(pending.pop()):
            kind = _kind(entry)
            if kind == "dir" and _folder_listed(entry.name):
                folders.append(entry.path)
            elif kind == "file" and _search_file_listed(entry.name):
                scanned += 1
                if scanned > SEARCH_MAX_FILES:
                    return
                yield entry.path
        pending.extend(reversed(folders))


def _search_text(path: str) -> str | None:
    """A file's text when search may read it: at most 1 MB, no NUL byte, and never through a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(SEARCH_MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(data) > SEARCH_MAX_FILE_BYTES or b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _write_atomic(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temp file in the same folder, then os.replace it over ``path``.

    A reader sees the old bytes or the new, never half a file. The existing file's permission bits carry over, and
    the temp file is removed if anything fails.
    """
    folder, name = os.path.split(path)
    try:
        mode: int | None = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = None
    temp = os.path.join(folder, f".{name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def _status_code(xy: str) -> str:
    """mentiko's parseStatusCode: one letter per path, checked in the order R, A, D, M."""
    x, y = xy[0], xy[1]
    if x == "?" and y == "?":
        return "?"
    for code in ("R", "A", "D", "M"):
        if code in (x, y):
            return code
    return x.strip() or y.strip() or "M"


def _parse_status(output: bytes) -> dict[str, str]:
    """``git status --porcelain=v1 -z`` as mentiko's {path: code} map.

    A rename record is followed by its old path as its own NUL-ended field, which is skipped.
    """
    fields = output.decode("utf-8", errors="replace").split("\0")
    status: dict[str, str] = {}
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        xy = entry[:2]
        status[entry[3:]] = _status_code(xy)
        if "R" in xy:
            index += 1
    return status


@router.get("/tree")
def tree(workspace: str = Query("")) -> JSONResponse:
    """The workspace as a tree of files and folders, mentiko's /api/fs/tree."""
    folder = _checked_workspace(workspace)
    if isinstance(folder, JSONResponse):
        return folder
    root = os.path.realpath(folder)
    return _ok({"path": root, "tree": _tree(root, 0)})


@router.get("/file")
def read_file(workspace: str = Query(""), path: str = Query("")) -> JSONResponse:
    """One text file in the workspace, at most 2 MB, mentiko's /api/fs/file."""
    folder = _checked_workspace(workspace)
    if isinstance(folder, JSONResponse):
        return folder
    if not path:
        return _fail(400, "missing_param", "path param required")
    base = os.path.join(os.path.realpath(folder), "")
    try:
        full = os.path.realpath(os.path.join(base, path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", OUTSIDE)
    if not full.startswith(base):
        return _fail(403, "outside_workspace", OUTSIDE)
    if not os.path.exists(full):
        return _fail(404, "not_found", "File not found.")
    if not os.path.isfile(full):
        return _fail(400, "not_a_file", "Not a file.")
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return _fail(400, "file_too_large", "File too large (max 2MB).")
        with open(full, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        return _disk_failure(exc)
    if len(data) > MAX_FILE_BYTES:
        return _fail(400, "file_too_large", "File too large (max 2MB).")
    if b"\0" in data:
        return _fail(400, "not_text", "Not a text file.")
    return _ok({"path": full, "content": data.decode("utf-8", errors="replace"), "size": len(data)})


@router.put("/file")
async def write_file(request: Request, path: str = Query("")) -> JSONResponse:
    """Replace one file's content, atomically, mentiko's PUT /api/fs/file. The path is the query; the body
    carries ``workspace`` and ``content``."""
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    folder = _checked_workspace(body.get("workspace"))
    if isinstance(folder, JSONResponse):
        return folder
    if not path:
        return _fail(400, "missing_param", "path param required")
    content = body.get("content")
    if not isinstance(content, str):
        return _fail(400, "invalid_content", "content must be a string")
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError:
        return _fail(400, "invalid_content", "content must be valid text")
    if len(data) > MAX_FILE_BYTES:
        return _fail(400, "file_too_large", "Content too large (max 2MB).")
    base = os.path.join(os.path.realpath(folder), "")
    try:
        full = os.path.realpath(os.path.join(base, path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", OUTSIDE)
    if not full.startswith(base):
        return _fail(403, "outside_workspace", OUTSIDE)
    if os.path.isdir(full):
        return _fail(400, "not_a_file", "Not a file.")
    try:
        await asyncio.to_thread(_write_atomic, full, data)
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"success": True, "path": full})


@router.post("/create")
async def create_path(request: Request) -> JSONResponse:
    """A new empty file or a new folder, mentiko's /api/fs/create. An existing path is a 409, never overwritten."""
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    folder = _checked_workspace(body.get("workspace"))
    if isinstance(folder, JSONResponse):
        return folder
    path = body.get("path")
    kind = body.get("type")
    if not path or not isinstance(path, str):
        return _fail(400, "missing_param", "path is required")
    if kind not in ("file", "dir"):
        return _fail(400, "invalid_type", 'type must be "file" or "dir"')
    base = os.path.join(os.path.realpath(folder), "")
    try:
        full = os.path.realpath(os.path.join(base, path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", OUTSIDE)
    if not full.startswith(base):
        return _fail(403, "outside_workspace", OUTSIDE)
    if os.path.lexists(full):
        return _fail(409, "already_exists", "Path already exists.")
    try:
        if kind == "dir":
            os.makedirs(full)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "x", encoding="utf-8"):
                pass
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"success": True, "path": full})


@router.post("/mkdir")
async def make_folder(request: Request) -> JSONResponse:
    """A new folder inside ``parent``, mentiko's /api/fs/mkdir."""
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    folder = _checked_workspace(body.get("workspace"))
    if isinstance(folder, JSONResponse):
        return folder
    parent = body.get("parent")
    name = body.get("name")
    if not parent or not name or not isinstance(parent, str) or not isinstance(name, str):
        return _fail(400, "missing_param", "parent and name required")
    if name in (".", "..") or "/" in name or "\\" in name or ".." in name or "\0" in name:
        return _fail(400, "invalid_name", "invalid folder name")
    base = os.path.join(os.path.realpath(folder), "")
    try:
        full = os.path.realpath(os.path.join(base, parent, name))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", OUTSIDE)
    if not full.startswith(base):
        return _fail(403, "outside_workspace", OUTSIDE)
    if os.path.lexists(full):
        return _fail(409, "already_exists", "Folder already exists.")
    try:
        os.mkdir(full)
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"path": full})


@router.post("/rename")
async def rename_path(request: Request) -> JSONResponse:
    """Move or rename one path inside the workspace, mentiko's /api/fs/rename. The workspace root cannot move."""
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    folder = _checked_workspace(body.get("workspace"))
    if isinstance(folder, JSONResponse):
        return folder
    old_path = body.get("oldPath")
    new_path = body.get("newPath")
    if not old_path or not isinstance(old_path, str):
        return _fail(400, "missing_param", "oldPath is required")
    if not new_path or not isinstance(new_path, str):
        return _fail(400, "missing_param", "newPath is required")
    base = os.path.join(os.path.realpath(folder), "")
    try:
        old_full = os.path.realpath(os.path.join(base, old_path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", "The source path is outside the workspace.")
    if not old_full.startswith(base):
        if old_full + os.sep == base:
            return _fail(403, "workspace_root", "Cannot rename the workspace root.")
        return _fail(403, "outside_workspace", "The source path is outside the workspace.")
    if not os.path.lexists(old_full):
        return _fail(404, "not_found", "Source path not found.")
    try:
        new_full = os.path.realpath(os.path.join(base, new_path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", "The destination path is outside the workspace.")
    if not new_full.startswith(base):
        return _fail(403, "outside_workspace", "The destination path is outside the workspace.")
    if os.path.lexists(new_full):
        return _fail(409, "already_exists", "Destination path already exists.")
    try:
        os.rename(old_full, new_full)
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"success": True, "path": new_full})


@router.delete("/delete")
@router.post("/delete")
async def delete_path(request: Request) -> JSONResponse:
    """Remove one file or folder, mentiko's /api/fs/delete. A folder must be empty unless ``force`` is true.

    mentiko's route is a POST; the engine answers both verbs with this one handler so the ported callers keep working.
    """
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    folder = _checked_workspace(body.get("workspace"))
    if isinstance(folder, JSONResponse):
        return folder
    path = body.get("path")
    if not path or not isinstance(path, str):
        return _fail(400, "missing_param", "path is required")
    base = os.path.join(os.path.realpath(folder), "")
    try:
        full = os.path.realpath(os.path.join(base, path))
    except OSError, ValueError:
        return _fail(403, "outside_workspace", OUTSIDE)
    if not full.startswith(base):
        if full + os.sep == base:
            return _fail(403, "workspace_root", "Cannot delete a workspace root.")
        return _fail(403, "outside_workspace", OUTSIDE)
    if not os.path.lexists(full):
        return _fail(404, "not_found", "File not found.")
    try:
        if not os.path.isdir(full):
            os.unlink(full)
        elif not os.listdir(full):
            os.rmdir(full)
        elif body.get("force") is True:
            await asyncio.to_thread(shutil.rmtree, full)
        else:
            return _fail(400, "directory_not_empty", "Directory is not empty. Remove contents first.")
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"success": True})


@router.get("/search")
def search_files(workspace: str = Query(""), query: str = Query("")) -> JSONResponse:
    """Lines of text files that contain ``query`` as plain text, mentiko's /api/fs/search without its regex mode.

    A user-supplied regular expression is not run: Python's ``re`` has no time limit, so a pattern could hold the
    engine for good. At most 200 results. Hidden, skipped and binary files are not read; neither are files over
    1 MB or symlinks.
    """
    folder = _checked_workspace(workspace)
    if isinstance(folder, JSONResponse):
        return folder
    if not query:
        return _fail(400, "missing_param", "query param required")
    if len(query) < 2:
        return _fail(400, "invalid_query", "query too short (min 2 chars)")
    if len(query) > SEARCH_MAX_QUERY:
        return _fail(400, "invalid_query", "query too long (max 200 chars)")
    root = os.path.realpath(folder)
    results: list[dict[str, Any]] = []
    for path in _search_files(root):
        text = _search_text(path)
        if text is None:
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if query not in line:
                continue
            results.append(
                {
                    "path": os.path.relpath(path, root),
                    "name": os.path.basename(path),
                    "line": number,
                    "column": 1,
                    "text": line.strip()[:SEARCH_LINE_CHARS],
                    "context": "",
                }
            )
            if len(results) >= SEARCH_MAX_RESULTS:
                return _ok({"results": results})
    return _ok({"results": results})


@router.get("/git-status")
def git_status(workspace: str = Query("")) -> JSONResponse:
    """The git status of the workspace as {path: code}, mentiko's /api/fs/git-status."""
    folder = _checked_workspace(workspace)
    if isinstance(folder, JSONResponse):
        return folder
    root = os.path.realpath(folder)
    try:
        done = subprocess.run(["git", "-C", root, "status", "--porcelain=v1", "-z"], timeout=10, capture_output=True)
    except OSError, subprocess.SubprocessError:
        return _fail(500, "git_failed", "Failed to get git status.")
    if done.returncode != 0:
        if b"not a git repository" in done.stderr.lower():
            return _ok({"error": "Not a git repository", "status": {}})
        return _fail(500, "git_failed", "Failed to get git status.")
    return _ok({"status": _parse_status(done.stdout)})

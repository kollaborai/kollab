"""Files for the code editor view, served by the daemon of the session being used.

Mentiko's /api/fs/* contract inside the spec envelope (docs/specs/floating-code-shell.md): success is
{"success": true, "data": ...}; a failure carries a non-2xx status and {"success": false, "error": {"code", "message"}}.

The daemon passes the selected, validated folder as the workspace; a request's ``workspace`` is not read.
Every path a request names is resolved with os.path.realpath and must sit inside that resolved folder or one of the
``extra_roots`` the settings name. The check is written inline in each operation, in the closed-prefix form (CodeQL's
path-injection idiom). Symlinks are not followed unless ``follow_symlinks`` is on. Request bodies and file contents are
never logged.

The limits and switches come from ``settings.CodeSettings`` (docs/specs/code-shell-settings.md, #987); a handler
called without settings uses ``settings.DEFAULTS``.
"""

import asyncio
import contextlib
import ctypes
import errno
import heapq
import os
import secrets
import shutil
import stat
import sys
import threading
from collections.abc import Iterator
from typing import Any

from . import git
from .reply import Reply
from .settings import DEFAULTS, PREFIX, CodeSettings

TREE_MAX_DEPTH = 8
TREE_MAX_ENTRIES = 10000
TREE_MAX_PATH_BYTES = 1024 * 1024
DIRECTORY_PAGE_SIZE = 200
SEARCH_MAX_RESULTS = 200
SEARCH_MAX_FILE_BYTES = 1024 * 1024
SEARCH_MAX_QUERY = 200
SEARCH_LINE_CHARS = 500
OUTSIDE = "That path is outside the workspace."
# Never listed, read or searched, whatever the settings say.
ALWAYS_HIDDEN = ".git"

# mentiko's lists, unchanged, plus .venv (dot names are skipped anyway).
ALLOWED_EXTENSIONS = frozenset(
    {
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".sh",
        ".bash",
        ".zsh",
        ".md",
        ".mdx",
        ".txt",
        ".css",
        ".scss",
        ".env",
        ".env.example",
        ".env.local",
        ".py",
        ".go",
        ".rs",
        ".rb",
        ".html",
        ".svg",
        ".xml",
        ".toml",
        ".ini",
        ".conf",
        ".graphql",
        ".gql",
        ".sql",
        ".dockerfile",
    }
)
SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".next",
        "dist",
        "build",
        "__pycache__",
        ".cache",
        ".turbo",
        "coverage",
        ".claude",
        ".vscode",
        ".gitlab",
        ".venv",
    }
)
# search's binary skips (mentiko's list): never read as text.
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".mp4",
        ".mp3",
        ".mov",
        ".avi",
        ".pyc",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
    }
)


def _ok(data: dict[str, Any]) -> Reply:
    return Reply({"success": True, "data": data})


def _fail(status: int, code: str, message: str) -> Reply:
    return Reply(status_code=status, content={"success": False, "error": {"code": code, "message": message}})


def _setting(message: str, name: str) -> str:
    """``message`` with the setting that controls it named at the end, worded as settings.refusal words it."""
    return f"{message} Setting: {PREFIX}{name}"


def _outside(message: str = OUTSIDE) -> Reply:
    return _fail(403, "outside_workspace", _setting(message, "files.extra_roots"))


def _size_text(kb: int) -> str:
    return f"{kb // 1024}MB" if kb % 1024 == 0 else f"{kb}KB"


def _size_refusal(word: str, kb: int) -> Reply:
    return _fail(400, "file_too_large", _setting(f"{word} too large (max {_size_text(kb)}).", "files.max_file_kb"))


def _disk_failure(exc: OSError) -> Reply:
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


def _checked_workspace(workspace: str) -> str | Reply:
    """The daemon's own folder; a refusal when it cannot be used as one."""
    if not workspace or not os.path.isdir(workspace):
        return _fail(400, "bad_workspace", "The session's folder is not available.")
    return workspace


def _config(settings: CodeSettings | None) -> CodeSettings:
    return DEFAULTS if settings is None else settings


def _bases(folder: str, settings: CodeSettings) -> tuple[str, ...]:
    """Where a request path may resolve: the folder, then each absolute extra root, each as realpath + separator.

    Compared after os.path.normcase, on both sides (case-folded on Windows, unchanged elsewhere). The first entry is
    always the workspace.
    """
    roots = [folder, *(root for root in settings.extra_roots if os.path.isabs(root))]
    return tuple(os.path.normcase(os.path.join(os.path.realpath(root), "")) for root in roots)


def _start_chain(root: str, settings: CodeSettings) -> frozenset[str]:
    """The real folders on the way down from the workspace. Kept only when links are followed, to cut a loop."""
    return frozenset({root}) if settings.follow_symlinks else frozenset()


def _descend(entry: os.DirEntry[str], chain: frozenset[str], follow: bool) -> frozenset[str] | None:
    """The chain to pass into the folder ``entry`` names, or None when it is a link back to a folder already on the
    way down (a loop, which is not descended into)."""
    if not follow:
        return chain
    real = os.path.realpath(entry.path)
    return None if real in chain else chain | {real}


def _text(params: dict[str, Any], key: str) -> str:
    """A request field that must be text; anything else reads as missing."""
    value = params.get(key)
    return value if isinstance(value, str) else ""


def _extension(name: str) -> str:
    """mentiko's getExtension: the text from the last dot, or "" for no dot or a leading one."""
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


class _TreeTooLarge(Exception):
    pass


def _entries(folder: str, budget: list[int] | None = None) -> list[os.DirEntry[str]]:
    """The entries of ``folder`` in name order; none when the folder cannot be read."""
    try:
        with os.scandir(folder) as scan:
            entries = []
            for entry in scan:
                if budget is not None:
                    budget[0] -= 1
                    budget[1] -= len(entry.path.encode("utf-8", errors="replace"))
                    if budget[0] < 0 or budget[1] < 0:
                        raise _TreeTooLarge
                entries.append(entry)
            return sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
    except OSError:
        return []


def _kind(entry: os.DirEntry[str], follow: bool = False) -> str:
    """ "dir" or "file" for an entry that is one. A symlink is neither unless ``follow`` is set, then it counts as what
    it points at; a dangling link is neither."""
    with contextlib.suppress(OSError):
        if entry.is_dir(follow_symlinks=follow):
            return "dir"
        if entry.is_file(follow_symlinks=follow):
            return "file"
    return ""


def _folder_listed(name: str, show_hidden: bool) -> bool:
    if name == ALWAYS_HIDDEN:
        return False
    return show_hidden or (not name.startswith(".") and name not in SKIP_DIRS)


def _tree_file_listed(name: str, show_hidden: bool) -> bool:
    if name == ALWAYS_HIDDEN:
        return False
    if show_hidden:
        return True
    ext = _extension(name)
    return not name.startswith(".") and (ext == "" or ext in ALLOWED_EXTENSIONS)


def _search_file_listed(name: str, show_hidden: bool) -> bool:
    if name == ALWAYS_HIDDEN:
        return False
    return (show_hidden or not name.startswith(".")) and _extension(name) not in BINARY_EXTENSIONS


def _tree(
    folder: str, depth: int, settings: CodeSettings, chain: frozenset[str], budget: list[int]
) -> list[dict[str, Any]]:
    """mentiko's buildTree: folders first, then files, each in name order, down to TREE_MAX_DEPTH.

    ``folder`` lies inside a workspace the route has already checked. Symlinks are listed and followed only when
    ``follow_symlinks`` is on, and then a folder already on the way down is not entered again.
    """
    if depth > TREE_MAX_DEPTH:
        return []
    follow = settings.follow_symlinks
    listed = [(entry, _kind(entry, follow)) for entry in _entries(folder, budget)]
    nodes: list[dict[str, Any]] = []
    for entry, kind in listed:
        if kind == "dir" and _folder_listed(entry.name, settings.show_hidden):
            below = _descend(entry, chain, follow)
            if below is not None:
                children = _tree(entry.path, depth + 1, settings, below, budget)
                nodes.append({"name": entry.name, "path": entry.path, "type": "dir", "children": children})
    for entry, kind in listed:
        if kind == "file" and _tree_file_listed(entry.name, settings.show_hidden):
            nodes.append({"name": entry.name, "path": entry.path, "type": "file", "ext": _extension(entry.name)})
    return nodes


def _search_files(root: str, settings: CodeSettings, state: dict[str, Any]) -> Iterator[str]:
    """Stream files with bounded open directories and no materialized directory listings."""
    follow = settings.follow_symlinks
    scanned = 0
    visited = 0
    max_entries = max(10000, settings.search_max_files * 5)
    stack: list[tuple[Any, frozenset[str]]] = []
    try:
        stack.append((os.scandir(root), _start_chain(root, settings)))
        while stack:
            iterator, chain = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                stack.pop()
                continue
            visited += 1
            if visited > max_entries:
                state["truncated"] = True
                return
            kind = _kind(entry, follow)
            if kind == "dir" and _folder_listed(entry.name, settings.show_hidden):
                below = _descend(entry, chain, follow)
                if below is not None:
                    if len(stack) >= 64:
                        state["truncated"] = True
                        continue
                    try:
                        stack.append((os.scandir(entry.path), below))
                    except OSError:
                        state["truncated"] = True
                        continue
            elif kind == "file" and _search_file_listed(entry.name, settings.show_hidden):
                scanned += 1
                if scanned > settings.search_max_files:
                    state["truncated"] = True
                    return
                yield entry.path
    except OSError:
        state["truncated"] = True
    finally:
        for iterator, _ in stack:
            iterator.close()


def _search_text(path: str, follow: bool, state: dict[str, Any] | None = None) -> str | None:
    """A file's text when search may read it: at most 1 MB and no NUL byte. Never through a symlink unless follow."""
    flags = os.O_RDONLY | (0 if follow else getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(SEARCH_MAX_FILE_BYTES + 1)
    except OSError:
        if state is not None:
            state["truncated"] = True
        return None
    if len(data) > SEARCH_MAX_FILE_BYTES or b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _leaf_path(base: str, path: str) -> str:
    """``path`` under ``base`` with the folders resolved but the last name left alone, so a symlink is the thing
    renamed or deleted and never the file it points to."""
    joined = os.path.normpath(os.path.join(base, path))
    if os.path.normcase(joined) + os.sep == os.path.normcase(base):
        return os.path.realpath(joined)  # the workspace root itself
    return os.path.join(os.path.realpath(os.path.dirname(joined)), os.path.basename(joined))



def _rename_without_replacement(source: str, destination: str) -> None:
    """Move a path atomically only if the destination is absent; never fall back to replacing rename."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename refuses an existing destination.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes, dest_bytes = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(source_bytes, dest_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, source_bytes, -100, dest_bytes, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "Atomic non-replacing rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)

def _decode(data: bytes) -> tuple[str, str]:
    """The text of ``data`` and its encoding: utf-8 when the bytes are valid utf-8, else latin-1 (every byte is one
    character, so nothing is lost)."""
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


class _EncodingUnsafe(Exception):
    """The file on disk is not utf-8 and the new text cannot be written the way it is stored."""


def _encode(content: str, existing: bytes | None) -> tuple[bytes, str]:
    """``content`` as bytes and the encoding used. An existing file that is not valid utf-8 stays latin-1, so the
    bytes the editor did not change come back exactly. A character past U+00FF cannot be latin-1, and writing the
    whole file as utf-8 would turn every other byte from 0x80 up into two, so that save is refused."""
    if existing is not None and not _is_utf8(existing):
        try:
            return content.encode("latin-1"), "latin-1"
        except UnicodeEncodeError as exc:
            raise _EncodingUnsafe from exc
    return content.encode("utf-8"), "utf-8"


def _existing_bytes(path: str, limit: int) -> bytes | None:
    """The bytes of the file at ``path``, or None when there is no such file."""
    try:
        if not os.path.isfile(path):  # a folder, a FIFO or a device must not be opened for reading
            return None
        with open(path, "rb") as handle:
            return handle.read(limit + 1)
    except FileNotFoundError:
        return None


def _version(st: os.stat_result) -> str:
    """Opaque identity for a file revision, including sub-millisecond and replacement changes."""
    return f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}"


def _mtime_ms(path: str) -> int:
    """A file's modification time in whole milliseconds, the form the editor sends back to check for a stale write."""
    return os.stat(path).st_mtime_ns // 1_000_000


class _StaleWrite(Exception):
    """The file's modification time is not the one the editor read."""


_WRITE_LOCK = threading.Lock()


def _checked_write(path: str, data: bytes, expected: int | None, version: str | None) -> tuple[int, str]:
    """Check ``expected`` against the file's mtime and write, as one step, so two saves with the same ``expectedMtime``
    cannot both pass. Returns the new mtime in ms."""
    with _WRITE_LOCK:
        if version is not None:
            try:
                if _version(os.stat(path)) != version:
                    raise _StaleWrite
            except FileNotFoundError:
                raise _StaleWrite from None
        if expected is not None:
            try:
                current: int | None = _mtime_ms(path)
            except FileNotFoundError:
                current = None
            if current != expected:
                raise _StaleWrite
        _write_atomic(path, data)
        st = os.stat(path)
        return st.st_mtime_ns // 1_000_000, _version(st)


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
    if "U" in xy or xy in ("AA", "DD"):  # an unmerged path is a conflict whatever side added or deleted it
        return "U"
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
        if "R" in xy or "C" in xy:
            index += 1
    return status


def tree(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """The workspace as a tree of files and folders, mentiko's /api/fs/tree."""
    cfg = _config(settings)
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    root = os.path.realpath(folder)
    try:
        nodes = _tree(root, 0, cfg, _start_chain(root, cfg), [TREE_MAX_ENTRIES, TREE_MAX_PATH_BYTES])
    except _TreeTooLarge:
        return _fail(413, "tree_too_large", "This folder is too large to show. Choose a smaller subfolder.")
    return _ok({"path": root, "tree": nodes})


def list_directory(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """List a single workspace directory in bounded, ordered pages."""
    cfg = _config(settings)
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    root = os.path.realpath(folder)
    path = params.get("path", root)
    after = params.get("after", "")
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        return _fail(400, "invalid_path", "The folder path is invalid.")
    if not isinstance(after, str) or "\0" in after or len(after) > 4098 or (after and after[:2] not in ("0:", "1:")):
        return _fail(400, "invalid_cursor", "The folder cursor is invalid.")
    lexical = os.path.normpath(path)
    real = os.path.realpath(lexical)
    try:
        if os.path.commonpath((root, lexical)) != root:
            return _outside()
    except ValueError:
        return _outside()
    if not cfg.follow_symlinks and real != lexical:
        return _outside()
    cursor = (after[0], after[2:].casefold(), after[2:]) if after else None
    try:
        with os.scandir(lexical) as scan:
            def entries():
                for entry in scan:
                    kind = _kind(entry, cfg.follow_symlinks)
                    if kind == "dir" and _folder_listed(entry.name, cfg.show_hidden):
                        rank = "0"
                    elif kind == "file" and _tree_file_listed(entry.name, cfg.show_hidden):
                        rank = "1"
                    else:
                        continue
                    key = (rank, entry.name.casefold(), entry.name)
                    if cursor is None or key > cursor:
                        yield key

            keys = heapq.nsmallest(DIRECTORY_PAGE_SIZE + 1, entries())
    except OSError as exc:
        return _disk_failure(exc)
    more = len(keys) > DIRECTORY_PAGE_SIZE
    keys = keys[:DIRECTORY_PAGE_SIZE]
    nodes = [{"name": name, "path": os.path.join(lexical, name), "type": "dir" if rank == "0" else "file",
              **({"ext": _extension(name)} if rank == "1" else {})} for rank, _, name in keys]
    next_cursor = f"{keys[-1][0]}:{keys[-1][2]}" if more else None
    return _ok({"path": lexical, "parent": os.path.dirname(lexical), "entries": nodes, "next": next_cursor})


def read_file(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """One text file in the workspace, at most ``max_file_kb`` (2 MB by default), mentiko's /api/fs/file."""
    cfg = _config(settings)
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    path = _text(params, "path")
    if not path:
        return _fail(400, "missing_param", "path param required")
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    try:
        full = os.path.realpath(os.path.join(base, path))
        lexical = os.path.normpath(os.path.join(base, path))
    except OSError, ValueError:
        return _outside()
    if not os.path.normcase(full).startswith(bases) and not (
        cfg.follow_symlinks and os.path.normcase(lexical).startswith(bases)
    ):
        return _outside()
    if not os.path.exists(full):
        return _fail(404, "not_found", "File not found.")
    if not os.path.isfile(full):
        return _fail(400, "not_a_file", "Not a file.")
    limit = cfg.max_file_kb * 1024
    try:
        if os.path.getsize(full) > limit:
            return _size_refusal("File", cfg.max_file_kb)
        with open(full, "rb") as handle:
            st = os.fstat(handle.fileno())
            mtime = st.st_mtime_ns // 1_000_000
            version = _version(st)
            data = handle.read(limit + 1)
    except OSError as exc:
        return _disk_failure(exc)
    if len(data) > limit:
        return _size_refusal("File", cfg.max_file_kb)
    if b"\0" in data:
        return _fail(400, "not_text", "Not a text file.")
    text, encoding = _decode(data)
    return _ok(
        {"path": full, "content": text, "size": len(data), "encoding": encoding, "mtime": mtime, "version": version}
    )


async def write_file(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """Replace one file's content, atomically, mentiko's PUT /api/fs/file. The path is the query; the body
    carries ``workspace`` and ``content``.

    With an integer ``expectedMtime`` (the ``mtime`` the editor read, in ms) the write is refused with 409 when the file
    changed since, unless ``force`` is true. Without it there is no check.
    """
    cfg = _config(settings)
    body = params
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    path = _text(params, "path")
    if not path:
        return _fail(400, "missing_param", "path param required")
    content = body.get("content")
    if not isinstance(content, str):
        return _fail(400, "invalid_content", "content must be a string")
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    try:
        full = os.path.realpath(os.path.join(base, path))
        lexical = os.path.normpath(os.path.join(base, path))
    except OSError, ValueError:
        return _outside()
    if not os.path.normcase(full).startswith(bases) and not (
        cfg.follow_symlinks and os.path.normcase(lexical).startswith(bases)
    ):
        return _outside()
    if os.path.isdir(full):
        return _fail(400, "not_a_file", "Not a file.")
    try:
        existing = await asyncio.to_thread(_existing_bytes, full, cfg.max_file_kb * 1024)
        if existing is not None and len(existing) > cfg.max_file_kb * 1024:
            return _size_refusal("File", cfg.max_file_kb)
        data, encoding = _encode(content, existing)
    except _EncodingUnsafe:
        return _fail(
            409,
            "encoding_unsafe",
            "This file is not UTF-8 and the new text has characters it cannot hold. Save it under another name.",
        )
    except UnicodeEncodeError:
        return _fail(400, "invalid_content", "content must be valid text")
    except OSError as exc:
        return _disk_failure(exc)
    if len(data) > cfg.max_file_kb * 1024:
        return _size_refusal("Content", cfg.max_file_kb)
    expected = body.get("expectedMtime")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        return _fail(400, "invalid_param", "expectedMtime must be an integer (milliseconds).")
    version = body.get("expectedVersion")
    if version is not None and (not isinstance(version, str) or not version or len(version) > 256):
        return _fail(400, "invalid_param", "expectedVersion must be a version token.")
    check = expected if body.get("force") is not True else None
    version = version if body.get("force") is not True else None
    try:
        mtime, version = await asyncio.to_thread(_checked_write, full, data, check, version)
    except _StaleWrite:
        return _fail(409, "stale_write", "The file changed on disk since you opened it.")
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"success": True, "path": full, "encoding": encoding, "mtime": mtime, "version": version})


async def create_path(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """A new empty file or a new folder, mentiko's /api/fs/create. An existing path is a 409, never overwritten."""
    cfg = _config(settings)
    body = params
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    path = body.get("path")
    kind = body.get("type")
    if not path or not isinstance(path, str):
        return _fail(400, "missing_param", "path is required")
    if kind not in ("file", "dir"):
        return _fail(400, "invalid_type", 'type must be "file" or "dir"')
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    try:
        full = os.path.realpath(os.path.join(base, path))
    except OSError, ValueError:
        return _outside()
    if not os.path.normcase(full).startswith(bases):
        return _outside()
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


async def make_folder(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """A new folder inside ``parent``, mentiko's /api/fs/mkdir."""
    cfg = _config(settings)
    body = params
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    parent = body.get("parent")
    name = body.get("name")
    if not parent or not name or not isinstance(parent, str) or not isinstance(name, str):
        return _fail(400, "missing_param", "parent and name required")
    if name in (".", "..") or "/" in name or "\\" in name or ".." in name or "\0" in name:
        return _fail(400, "invalid_name", "invalid folder name")
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    try:
        full = os.path.realpath(os.path.join(base, parent, name))
    except OSError, ValueError:
        return _outside()
    if not os.path.normcase(full).startswith(bases):
        return _outside()
    if os.path.lexists(full):
        return _fail(409, "already_exists", "Folder already exists.")
    try:
        os.mkdir(full)
    except OSError as exc:
        return _disk_failure(exc)
    return _ok({"path": full})


async def rename_path(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """Move or rename one path inside the workspace or an extra root, mentiko's /api/fs/rename. The workspace root
    cannot move."""
    cfg = _config(settings)
    body = params
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    old_path = body.get("oldPath")
    new_path = body.get("newPath")
    if not old_path or not isinstance(old_path, str):
        return _fail(400, "missing_param", "oldPath is required")
    if not new_path or not isinstance(new_path, str):
        return _fail(400, "missing_param", "newPath is required")
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    source_outside = "The source path is outside the workspace."
    try:
        old_full = _leaf_path(base, old_path)
    except OSError, ValueError:
        return _outside(source_outside)
    if not os.path.normcase(old_full).startswith(bases):
        if os.path.normcase(old_full) + os.sep == bases[0]:
            return _fail(403, "workspace_root", "Cannot rename the workspace root.")
        return _outside(source_outside)
    if not os.path.lexists(old_full):
        return _fail(404, "not_found", "Source path not found.")
    try:
        new_full = _leaf_path(base, new_path)
    except OSError, ValueError:
        return _outside("The destination path is outside the workspace.")
    if not os.path.normcase(new_full).startswith(bases):
        return _outside("The destination path is outside the workspace.")
    if os.path.lexists(new_full):
        return _fail(409, "already_exists", "Destination path already exists.")
    try:
        _rename_without_replacement(old_full, new_full)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.ENOSYS, errno.EINVAL):
            return _fail(
                409, "rename_unavailable", "This file system cannot safely rename without replacing a destination."
            )
        return _disk_failure(exc)
    return _ok({"success": True, "path": new_full})


async def delete_path(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """Remove one file or folder, mentiko's /api/fs/delete. A folder must be empty unless ``force`` is true.

    mentiko's route is a POST; the engine answers both verbs with this one handler so the ported callers keep working.
    """
    cfg = _config(settings)
    body = params
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    path = body.get("path")
    if not path or not isinstance(path, str):
        return _fail(400, "missing_param", "path is required")
    base = os.path.join(os.path.realpath(folder), "")
    bases = _bases(folder, cfg)
    try:
        full = _leaf_path(base, path)
    except OSError, ValueError:
        return _outside()
    if not os.path.normcase(full).startswith(bases):
        if os.path.normcase(full) + os.sep == bases[0]:
            return _fail(403, "workspace_root", "Cannot delete a workspace root.")
        return _outside()
    if not os.path.lexists(full):
        return _fail(404, "not_found", "File not found.")
    try:
        if os.path.islink(full) or not os.path.isdir(full):
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


def search_files(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """Lines of text files that contain ``query`` as plain text, mentiko's /api/fs/search without its regex mode.

    A user-supplied regular expression is not run: Python's ``re`` has no time limit, so a pattern could hold the
    engine for good. At most 200 results. Binary files and files over 1 MB are not read, and neither is ``.git``;
    hidden and skipped folders are read only when ``show_hidden`` is on, and symlinks only when ``follow_symlinks`` is.
    """
    cfg = _config(settings)
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    query = _text(params, "query")
    if not query:
        return _fail(400, "missing_param", "query param required")
    if len(query) < 2:
        return _fail(400, "invalid_query", "query too short (min 2 chars)")
    if len(query) > SEARCH_MAX_QUERY:
        return _fail(400, "invalid_query", "query too long (max 200 chars)")
    root = os.path.realpath(folder)
    results: list[dict[str, Any]] = []
    state: dict[str, Any] = {"truncated": False}
    paths = _search_files(root, cfg, state)
    for path in paths:
        text = _search_text(path, cfg.follow_symlinks, state)
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
                paths.close()
                return _ok({"results": results, "truncated": True})
    return _ok({"results": results, "truncated": state["truncated"]})


def git_status(workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """The git status of the workspace as {path: code}, mentiko's /api/fs/git-status."""
    folder = _checked_workspace(workspace)
    if isinstance(folder, Reply):
        return folder
    root = os.path.realpath(folder)
    # A repository's own GIT_* variables would send git elsewhere; the workspace may also sit below the repository's
    # top, where porcelain paths are relative to the top: scope the status to the workspace and key it relative to it.
    try:
        done = git._run_git(root, ["status", "--porcelain=v1", "-z", "--", "."], True, timeout=10)
        top = git._run_git(root, ["rev-parse", "--show-prefix"], True, timeout=10)
    except git.GitRequestError as exc:
        if exc.code == "git_output_too_large":
            return _fail(exc.status, exc.code, exc.message)
        return _fail(500, "git_failed", "Failed to get git status.")
    if done.returncode != 0:
        if "not a git repository" in done.stderr.lower():
            return _ok({"error": "Not a git repository", "status": {}})
        return _fail(500, "git_failed", "Failed to get git status.")
    if top.returncode != 0:
        return _fail(500, "git_failed", "Failed to get the Git workspace prefix.")
    prefix = top.stdout.removesuffix("\n")
    status = _parse_status(done.stdout.encode("utf-8"))
    if len(status) > git.MAX_STATUS_ENTRIES:
        return _fail(413, "git_output_too_large", "Git status has too many entries to display.")
    if prefix:
        status = {(key[len(prefix) :] if key.startswith(prefix) else key): code for key, code in status.items()}
    return _ok({"status": status})


OPS: dict[str, Any] = {
    "tree": tree,
    "list": list_directory,
    "file": read_file,
    "write_file": write_file,
    "create": create_path,
    "mkdir": make_folder,
    "rename": rename_path,
    "delete": delete_path,
    "search": search_files,
    "git-status": git_status,
}


async def serve(op: str, workspace: str, params: dict[str, Any], settings: CodeSettings | None = None) -> Reply:
    """Run one file operation in the daemon's folder with ``settings`` (None = defaults). Blocking ones run in a
    thread, off the daemon's event loop."""
    handler = OPS.get(op)
    if handler is None:
        return _fail(404, "unknown_op", "That file operation is not available.")
    cfg = _config(settings)
    if asyncio.iscoroutinefunction(handler):
        return await handler(workspace, params, cfg)
    return await asyncio.to_thread(handler, workspace, params, cfg)

"""Git for the code editor view: the engine's git API, part 1 (the read actions).

One POST route takes ``{"action": ..., "workspace": ..., ...}`` and dispatches to a handler in ``ACTIONS``, so later
actions only add entries there. A GET route answers the three read actions that take no body. Request and response
shapes are mentiko's (``app/api/git/route.ts``) inside the envelope of ``docs/specs/floating-code-shell.md``.

Git runs only as an argv list, never through a shell, in the workspace folder the engine's folder rule accepted.
Every value that reaches git is checked first: no option-like text, no NUL or line break, a commit as hex, a stash as
its number, and a file path by the realpath closed-prefix rule (symlinks followed) inside the workspace.
"""

import asyncio
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from ..workspace_folder import MAX_PATH, folder_or_400

router = APIRouter(prefix="/code/git", tags=["code-git"])

Handler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

GIT_TIMEOUT = 30  # seconds for one git call
LOG_LIMIT = 20
LOG_SEP = "||GITSEP||"
LOG_FORMAT = f"%H{LOG_SEP}%h{LOG_SEP}%s{LOG_SEP}%an{LOG_SEP}%ar{LOG_SEP}%D"
STASH_SEP = "||STASHSEP||"
# git-log placeholders: `stash list` runs over the stash reflog, and for-each-ref ones would print literally.
STASH_FORMAT = f"%gd{STASH_SEP}%s{STASH_SEP}%cr{STASH_SEP}%h"
BRANCH_FORMAT = "%(refname:short)|%(HEAD)|%(upstream:short)|%(objectname:short)|%(committerdate:relative)"
REMOTE_FORMAT = "%(refname:short)|%(HEAD)|%(objectname:short)|%(committerdate:relative)"
COMMIT_ID = re.compile(r"[0-9a-fA-F]{4,40}")
STASH_ID = re.compile(r"stash@\{(\d{1,6})\}|(\d{1,6})")
# Variables that would point git at a repository other than the workspace's own.
REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
# What GET /code/git?action= answers: read-only, no body.
GET_ACTIONS = ("status", "current_branch", "list_branches")


class GitRequestError(Exception):
    """A request git cannot serve: the envelope's HTTP status, snake_case code and one-sentence message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _fail(status: int, code: str, message: str) -> JSONResponse:
    # `detail` repeats the message so a folder refusal reads like the engine's other routes' 400s.
    body = {"success": False, "error": {"code": code, "message": message}, "detail": message}
    return JSONResponse(body, status_code=status)


def _run_git(root: str, args: list[str], read: bool, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in REPO_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if read:
        env["GIT_OPTIONAL_LOCKS"] = "0"  # a read never takes an index lock
    try:
        return subprocess.run(
            ["git", "-C", root, *args],
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitRequestError(504, "git_timeout", "git did not finish in time.") from exc
    except OSError as exc:
        raise GitRequestError(500, "git_unavailable", "git could not be started.") from exc


async def _git(
    root: str, args: list[str], *, read: bool = True, timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run git in ``root`` on a worker thread. ``args`` is an argv list, never a shell string."""
    return await asyncio.to_thread(_run_git, root, args, read, timeout)


async def _text(root: str, args: list[str]) -> str:
    """Stdout of a read that may fail (no commits yet, no such file, not a repository): empty when it fails."""
    proc = await _git(root, args)
    return proc.stdout if proc.returncode == 0 else ""


async def _head_name(root: str) -> str:
    return (await _text(root, ["rev-parse", "--abbrev-ref", "HEAD"])).strip() or "HEAD"


def _fields(line: str, sep: str, count: int) -> list[str]:
    """``line`` split on ``sep`` into exactly ``count`` fields, missing ones empty."""
    return (line.split(sep) + [""] * count)[:count]


def _stash_branch(message: str) -> str:
    """The branch a stash was made on, read the way mentiko reads it: ``WIP on main: abc123 subject`` gives ``main``."""
    match = re.search(r"on (.+?):", message)
    return match.group(1) if match else "unknown"


def _plain(value: object) -> str | None:
    """``value`` if git can take it as a plain argument: text, not an option, no NUL or line break."""
    if not isinstance(value, str) or not value or value.startswith("-"):
        return None
    if "\0" in value or "\n" in value or "\r" in value:
        return None
    return value


def _commit_id(value: object) -> str:
    text = _plain(value)
    if text is None or not COMMIT_ID.fullmatch(text):
        raise GitRequestError(400, "invalid_commit", "commitHash is not a valid commit id.")
    return text


def _stash_index(value: object) -> str:
    """The number of a stash given as ``stash@{N}`` or as ``N``."""
    text = _plain(value)
    match = STASH_ID.fullmatch(text) if text is not None else None
    if match is None:
        raise GitRequestError(400, "invalid_stash", "stashId is not a valid stash id.")
    return match.group(1) or match.group(2)


def _rel_path(value: object) -> str:
    """A file path a request names, relative to its workspace."""
    if not isinstance(value, str) or not value.strip():
        raise GitRequestError(400, "path_required", "A file path is required.")
    text = _plain(value)
    if text is None or len(text) > MAX_PATH:
        raise GitRequestError(400, "invalid_path", "That file path is not valid.")
    return text


def _workspace(value: object) -> str:
    """The folder git runs in. The engine's folder rule decides, and only its return is used."""
    if not isinstance(value, str) or not value.strip():
        raise GitRequestError(400, "workspace_required", "workspace is required: the folder git runs in.")
    try:
        return folder_or_400(value)
    except HTTPException as exc:
        raise GitRequestError(400, "bad_workspace", str(exc.detail)) from exc


async def _status(root: str, body: dict[str, Any]) -> dict[str, Any]:
    branch = await _head_name(root)
    upstream = (await _text(root, ["rev-parse", "--abbrev-ref", "@{upstream}"])).strip() or None
    ahead = behind = 0
    counts = (await _text(root, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])).split()
    if len(counts) == 2 and all(count.isdigit() for count in counts):
        ahead, behind = int(counts[0]), int(counts[1])
    files: list[dict[str, Any]] = []
    # Porcelain paths are relative to the repository's top; the workspace may be a folder inside it. Scope the
    # status to the workspace and give back workspace-relative paths, the form stage, unstage and diff take.
    prefix = (await _text(root, ["rev-parse", "--show-prefix"])).strip()
    for line in (await _text(root, ["status", "--porcelain", "-u", "--", "."])).split("\n"):
        if not line:
            continue
        code = line[:2]
        index, worktree = code[:1], code[1:2]
        raw_path = line[3:]
        path = raw_path.split(" -> ")[1] if " -> " in raw_path else raw_path  # a rename reads "old -> new"
        if prefix and path.startswith(prefix):
            path = path[len(prefix) :]
        files.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "staged": index not in (" ", "?"),
                "unstaged": worktree not in (" ", "?"),
                "untracked": index == "?" and worktree == "?",
                "statusCode": code,
            }
        )
    return {"branch": branch, "upstream": upstream, "ahead": ahead, "behind": behind, "files": files}


async def _log(root: str, body: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    for line in (await _text(root, ["log", f"--format={LOG_FORMAT}", "-n", str(LOG_LIMIT)])).split("\n"):
        if not line:
            continue
        full, short, subject, author, date, refs = _fields(line, LOG_SEP, 6)
        entries.append(
            {"hash": full, "shortHash": short, "message": subject, "author": author, "date": date, "refs": refs}
        )
    return {"entries": entries}


async def _show(root: str, body: dict[str, Any]) -> dict[str, Any]:
    """A file's content at HEAD; empty for a file HEAD does not have (a new file)."""
    rel = _rel_path(body.get("path"))
    base = os.path.realpath(root)
    # The closed-prefix check is written here, in the handler that uses the path, not in a helper.
    try:
        full = os.path.realpath(os.path.join(base, rel))
    except OSError, ValueError:
        full = ""
    if full != base and not full.startswith(os.path.join(base, "")):
        raise GitRequestError(403, "path_outside_workspace", "That path is outside the workspace.")
    # ./ makes the path relative to the workspace folder rather than to the top of the repository.
    proc = await _git(base, ["show", f"HEAD:./{rel}"])
    return {"content": proc.stdout if proc.returncode == 0 else ""}


async def _diff(root: str, body: dict[str, Any]) -> dict[str, Any]:
    """The unstaged diff of one file, or the staged one when ``staged`` is true."""
    rel = _rel_path(body.get("path"))
    base = os.path.realpath(root)
    # The closed-prefix check is written here, in the handler that uses the path, not in a helper.
    try:
        full = os.path.realpath(os.path.join(base, rel))
    except OSError, ValueError:
        full = ""
    if full != base and not full.startswith(os.path.join(base, "")):
        raise GitRequestError(403, "path_outside_workspace", "That path is outside the workspace.")
    # --literal-pathspecs: the path names one file, so pathspec magic or glob syntax cannot reach past it.
    if body.get("staged") is True:
        args = ["--literal-pathspecs", "diff", "--cached", "--", rel]
    else:
        args = ["--literal-pathspecs", "diff", "--", rel]
    return {"diff": await _text(base, args)}


async def _show_commit(root: str, body: dict[str, Any]) -> dict[str, Any]:
    proc = await _git(root, ["show", "--no-color", _commit_id(body.get("commitHash"))])
    if proc.returncode != 0:
        raise GitRequestError(404, "commit_not_found", "That commit is not in this repository.")
    return {"content": proc.stdout}


async def _list_branches(root: str, body: dict[str, Any]) -> dict[str, Any]:
    current = await _head_name(root)
    default_ref = (await _text(root, ["symbolic-ref", "refs/remotes/origin/HEAD"])).strip()
    branches: list[dict[str, Any]] = []
    for line in (await _text(root, ["branch", f"--format={BRANCH_FORMAT}"])).split("\n"):
        parts = line.split("|")
        if len(parts) < 5:
            continue
        name, marker, tracking, short, date = parts[:5]
        branches.append(
            {
                "name": name,
                "isCurrent": marker == "*",
                "isRemote": False,
                "tracking": tracking or None,
                "lastCommit": short,
                "lastCommitDate": date,
            }
        )
    for line in (await _text(root, ["branch", "--remote", f"--format={REMOTE_FORMAT}"])).split("\n"):
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, _marker, short, date = parts[:4]
        branches.append(
            {
                "name": name,
                "isCurrent": False,
                "isRemote": True,
                "tracking": None,
                "lastCommit": short,
                "lastCommitDate": date,
            }
        )
    default_branch = default_ref.removeprefix("refs/remotes/origin/") or None
    return {"branches": branches, "current": current, "defaultBranch": default_branch}


async def _current_branch(root: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "current": await _head_name(root)}


async def _list_stashes(root: str, body: dict[str, Any]) -> dict[str, Any]:
    stashes: list[dict[str, Any]] = []
    for line in (await _text(root, ["stash", "list", f"--format={STASH_FORMAT}"])).split("\n"):
        if not line:
            continue
        ref, subject, date, commit = _fields(line, STASH_SEP, 4)
        stashes.append(
            {
                "id": ref.removeprefix("stash@{").removesuffix("}"),
                "branch": _stash_branch(subject),
                "message": subject,
                "date": date,
                "commitHash": commit or None,
            }
        )
    return {"ok": True, "stashes": stashes}


async def _show_stash(root: str, body: dict[str, Any]) -> dict[str, Any]:
    index = _stash_index(body.get("stashId"))
    proc = await _git(root, ["stash", "show", "-p", f"stash@{{{index}}}"])
    if proc.returncode != 0:
        raise GitRequestError(404, "stash_not_found", "That stash does not exist; it may be applied or dropped.")
    return {"ok": True, "diff": proc.stdout, "stashId": index}


ACTIONS: dict[str, Handler] = {
    "status": _status,
    "log": _log,
    "show": _show,
    "show_commit": _show_commit,
    "diff": _diff,
    "list_branches": _list_branches,
    "current_branch": _current_branch,
    "list_stashes": _list_stashes,
    "show_stash": _show_stash,
}


async def _serve(body: dict[str, Any], actions: Iterable[str]) -> JSONResponse | dict[str, Any]:
    """Check the folder first, then run the named action. Failures answer the envelope; success is {success, data}."""
    try:
        root = _workspace(body.get("workspace"))
        action = body.get("action")
        if not isinstance(action, str) or action not in actions:
            raise GitRequestError(400, "unknown_action", "That git action is not available here.")
        data = await ACTIONS[action](root, body)
    except GitRequestError as exc:
        return _fail(exc.status, exc.code, exc.message)
    return {"success": True, "data": data}


@router.post("", response_model=None)
async def git_action(request: Request) -> JSONResponse | dict[str, Any]:
    """Run one git action: ``{"action": ..., "workspace": ..., ...}``; the other fields belong to the action."""
    try:
        body = await request.json()
    except ValueError:
        return _fail(400, "invalid_body", "The request body is not JSON.")
    if not isinstance(body, dict):
        return _fail(400, "invalid_body", "The request body must be a JSON object.")
    return await _serve(body, ACTIONS)


@router.get("", response_model=None)
async def git_read(
    workspace: str | None = Query(default=None, description="The folder git runs in."),
    action: str = Query(default="status", description="status, current_branch or list_branches."),
) -> JSONResponse | dict[str, Any]:
    """The read actions that take no body."""
    return await _serve({"workspace": workspace, "action": action}, GET_ACTIONS)


# --- part 2: write actions ---
# Each write runs one argv list through `_write` (`read=False`: it takes the index lock, as git does), after the checks
# the reads use: a ref by `_ref_name`, a message by `_message`, a path after `--` with the realpath closed-prefix check
# written inline in the handler that uses it. A git refusal answers 409 with git's one-line reason. A merge conflict on
# apply is a result, not a failure: it answers `ok: false` with the conflicting files, as mentiko does.

MAX_REF = 255  # characters in a branch or remote name
MAX_MESSAGE = 10_000  # characters in a commit or stash message
PUSH_TIMEOUT = 60  # seconds for one push: a slow remote may take a while, a hung one must not hold the request
REASON_MAX = 300  # characters of git's reason in a refusal
# Characters no ref name may hold: control characters, space, and the operators git reads in refspecs and globs.
REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def _ref_name(value: object, code: str, message: str) -> str:
    """A branch or remote name git reads as a plain ref: no option, no refspec or glob syntax, no ``..`` or ``@{``."""
    text = _plain(value)
    if (
        text is None
        or len(text) > MAX_REF
        or text.startswith("+")  # a leading + turns a push into a force push
        or REF_FORBIDDEN.search(text)
        or ".." in text
        or "@{" in text
        or text == "@"
        or text.endswith((".", "/"))
        or not all(part and not part.startswith(".") and not part.endswith(".lock") for part in text.split("/"))
    ):
        raise GitRequestError(400, code, message)
    return text


def _branch_name(body: dict[str, Any]) -> str:
    """The ``branchName`` a request names, trimmed and checked as a ref."""
    value = body.get("branchName")
    text = value.strip() if isinstance(value, str) else value
    if text in (None, ""):
        raise GitRequestError(400, "branch_required", "branchName is required.")
    return _ref_name(text, "invalid_branch", "branchName is not a valid branch name.")


def _message(value: object) -> str:
    """A commit or stash message, trimmed: text with no NUL and at most MAX_MESSAGE characters; empty when absent."""
    if value is None:
        return ""
    if not isinstance(value, str) or "\0" in value or len(value) > MAX_MESSAGE:
        raise GitRequestError(400, "invalid_message", "The message is not valid text.")
    return value.strip()


def _rel_paths(value: object) -> list[str]:
    """The file paths a stage or unstage request names: a non-empty list, each one checked as a relative path."""
    if not isinstance(value, list) or not value:
        raise GitRequestError(400, "paths_required", "At least one file path is required.")
    return [_rel_path(item) for item in value]


def _reason(proc: subprocess.CompletedProcess[str]) -> str:
    """Why git refused, as one line: its first ``fatal:`` or ``error:`` line, else its last line of output."""
    lines = [line.strip() for line in f"{proc.stderr}\n{proc.stdout}".splitlines() if line.strip()]
    reasons = [line.partition(":")[2].strip() for line in lines if line.startswith(("fatal:", "error:"))]
    if reasons and reasons[0]:
        return reasons[0][:REASON_MAX]
    return (lines[-1] if lines else "git refused the change.")[:REASON_MAX]


async def _write(root: str, args: list[str], timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """One write git call. A refusal raises a 409 that carries git's one-line reason."""
    proc = await _git(root, args, read=False, timeout=timeout)
    if proc.returncode != 0:
        raise GitRequestError(409, "git_failed", _reason(proc))
    return proc


async def _change_index(root: str, body: dict[str, Any], command: list[str]) -> dict[str, Any]:
    """Run ``command`` over the files a request names. Every path is checked before git runs."""
    rels = _rel_paths(body.get("paths"))
    base = os.path.realpath(root)
    for rel in rels:
        # The closed-prefix check is written here, in the handler that uses the paths, not in a helper.
        try:
            full = os.path.realpath(os.path.join(base, rel))
        except OSError, ValueError:
            full = ""
        if full != base and not full.startswith(os.path.join(base, "")):
            raise GitRequestError(403, "path_outside_workspace", "That path is outside the workspace.")
    await _write(base, ["--literal-pathspecs", *command, "--", *rels])
    return {"ok": True, "status": await _status(base, {})}


async def _stage(root: str, body: dict[str, Any]) -> dict[str, Any]:
    return await _change_index(root, body, ["add"])


async def _unstage(root: str, body: dict[str, Any]) -> dict[str, Any]:
    return await _change_index(root, body, ["restore", "--staged"])


async def _stage_all(root: str, body: dict[str, Any]) -> dict[str, Any]:
    await _write(root, ["add", "-A", "--", "."])  # the workspace only, not the whole repository
    return {"ok": True, "status": await _status(root, {})}


async def _unstage_all(root: str, body: dict[str, Any]) -> dict[str, Any]:
    await _write(root, ["restore", "--staged", "--", "."])
    return {"ok": True, "status": await _status(root, {})}


async def _commit(root: str, body: dict[str, Any]) -> dict[str, Any]:
    message = _message(body.get("message"))
    if not message:
        raise GitRequestError(400, "message_required", "A commit message is required.")
    # The panel lists only the workspace's changes. A commit takes the whole index, so refuse one that would
    # carry staged files from outside the workspace the panel cannot show.
    everything = (await _text(root, ["diff", "--cached", "--name-only", "-z", "--no-relative"])).split("\0")
    inside = (await _text(root, ["diff", "--cached", "--name-only", "-z", "--relative"])).split("\0")
    outside = len([name for name in everything if name]) - len([name for name in inside if name])
    if outside > 0:
        raise GitRequestError(
            409, "staged_outside_workspace", f"{outside} staged file(s) lie outside this folder; unstage them first."
        )
    proc = await _write(root, ["commit", "-m", message])
    return {"ok": True, "output": proc.stdout, "status": await _status(root, {})}


async def _push(root: str, body: dict[str, Any]) -> dict[str, Any]:
    """Push to the branch's remote, or to a named remote and branch. Names only: never a URL or an option."""
    remote, branch = body.get("remote"), body.get("branch")
    args = ["push"]
    if remote is not None:
        name = _ref_name(remote, "invalid_remote", "remote is not a valid remote name.")
        if name not in (await _text(root, ["remote"])).split():
            raise GitRequestError(400, "unknown_remote", "That remote is not configured in this repository.")
        args.append(name)
        if branch is not None:
            args.append(_ref_name(branch, "invalid_branch", "branch is not a valid branch name."))
    elif branch is not None:
        raise GitRequestError(400, "remote_required", "A branch can only be pushed to a named remote.")
    proc = await _write(root, args, PUSH_TIMEOUT)
    return {"ok": True, "output": f"{proc.stderr}{proc.stdout}", "status": await _status(root, {})}


async def _create_branch(root: str, body: dict[str, Any]) -> dict[str, Any]:
    name = _branch_name(body)
    await _write(root, ["branch", name])
    return {"ok": True, "branch": name, "current": await _head_name(root)}


async def _switch_branch(root: str, body: dict[str, Any]) -> dict[str, Any]:
    name = _branch_name(body)
    previous = await _head_name(root)
    proc = await _git(root, ["switch", name], read=False)
    carried = False
    if proc.returncode != 0:
        if not any(word in f"{proc.stderr}{proc.stdout}" for word in ("uncommitted", "working tree", "changes")):
            raise GitRequestError(409, "git_failed", _reason(proc))
        # mentiko's fallback: carry the uncommitted changes over (not a stash). A conflict still refuses, in `_write`.
        await _write(root, ["switch", "-m", name])
        carried = True
    return {"ok": True, "current": await _head_name(root), "previous": previous, "hasUncommittedChanges": carried}


async def _delete_branch(root: str, body: dict[str, Any]) -> dict[str, Any]:
    """Delete a local branch with ``-d``, or ``-D`` when ``force`` is true. The checked-out branch is never deleted."""
    name = _branch_name(body)
    if name == await _head_name(root):
        raise GitRequestError(409, "current_branch", "Cannot delete the checked-out branch; switch to another first.")
    force = body.get("force") is True
    proc = await _git(root, ["branch", "-D" if force else "-d", name], read=False)
    if proc.returncode != 0:
        if not force and "not fully merged" in f"{proc.stderr}{proc.stdout}":
            raise GitRequestError(
                409, "not_fully_merged", "Branch has unmerged commits. Use force delete to remove it anyway."
            )
        raise GitRequestError(409, "git_failed", _reason(proc))
    return {"ok": True, "deleted": name, "forceUsed": force}


async def _create_stash(root: str, body: dict[str, Any]) -> dict[str, Any]:
    message = _message(body.get("stashMessage")) or None
    args = ["stash", "push"]
    if body.get("includeUntracked") is True:
        args.append("-u")
    if message:
        args += ["-m", message]
    # With nothing to save, `git stash push` exits 0 and makes no stash: compare the top of refs/stash.
    before = (await _text(root, ["rev-parse", "--verify", "-q", "refs/stash"])).strip()
    await _write(root, args)
    after = (await _text(root, ["rev-parse", "--verify", "-q", "refs/stash"])).strip()
    if after == before:
        raise GitRequestError(409, "nothing_to_stash", "There are no local changes to stash.")
    top: dict[str, Any] = (await _list_stashes(root, {}))["stashes"][0]
    return {"ok": True, "stashId": f"stash@{{{top['id']}}}", "message": message or top["message"]}


async def _named_stash(root: str, body: dict[str, Any]) -> str:
    """The position of the stash a request names (it must exist). A stashCommit wins and never falls back."""
    stashes = (await _list_stashes(root, {}))["stashes"]
    commit = body.get("stashCommit")
    if commit not in (None, ""):
        wanted = _commit_id(commit).lower()
        hits = [
            stash["id"]
            for stash in stashes
            if stash["commitHash"] and wanted.startswith(stash["commitHash"].lower())
        ]
    elif body.get("stashId") not in (None, ""):
        position = str(int(_stash_index(body.get("stashId"))))
        hits = [position] if any(stash["id"] == position for stash in stashes) else []
    else:
        raise GitRequestError(400, "stash_required", "stashId or stashCommit is required.")
    if not hits:
        raise GitRequestError(404, "stash_not_found", "That stash does not exist; it may have been applied or dropped.")
    return hits[0]


async def _apply_stash(root: str, body: dict[str, Any]) -> dict[str, Any]:
    position = await _named_stash(root, body)
    ref = f"stash@{{{position}}}"
    proc = await _git(root, ["stash", "apply", ref], read=False)
    status = await _status(root, {})
    if proc.returncode == 0:
        return {"ok": True, "appliedStashId": ref, "status": status}
    conflicts = [
        entry["path"] for entry in status["files"] if "U" in entry["statusCode"] or entry["statusCode"] in ("AA", "DD")
    ]
    if not conflicts:
        raise GitRequestError(409, "git_failed", _reason(proc))
    return {
        "ok": False,
        "appliedStashId": ref,
        "conflicts": conflicts,
        "conflictCount": len(conflicts),
        "hasUnmergedPaths": True,
        "error": "Merge conflicts during stash apply. Resolve conflicts and commit.",
        "status": status,
    }


async def _drop_stash(root: str, body: dict[str, Any]) -> dict[str, Any]:
    position = await _named_stash(root, body)
    ref = f"stash@{{{position}}}"
    await _write(root, ["stash", "drop", ref])
    return {"ok": True, "droppedId": ref}


ACTIONS.update(
    {
        "stage": _stage,
        "unstage": _unstage,
        "stage_all": _stage_all,
        "unstage_all": _unstage_all,
        "commit": _commit,
        "push": _push,
        "create_branch": _create_branch,
        "switch_branch": _switch_branch,
        "delete_branch": _delete_branch,
        "create_stash": _create_stash,
        "apply_stash": _apply_stash,
        "drop_stash": _drop_stash,
    }
)

"""The code editor's files, git and terminal, served by the daemon of the session being used (#987).

The web UI's Code Editor, Git and Terminal views reach this through the engine's ``/code/*`` routes, which proxy to
``state.code_call`` on the session's daemon. A daemon on another computer answers over the same device channel as the
rest of its state, so a remote session shows that computer's files, git and shell. Code and Git can select any
existing folder on that machine; linked Git worktrees are discovered automatically.

Spec: ``docs/specs/floating-code-shell.md``.
"""

import asyncio
import dataclasses
import os
from typing import Any

from . import files, folders, git, terminal
from .reply import Reply
from .settings import DEFAULTS, CodeSettings, refusal

__all__ = ["call", "shutdown", "OPS", "CodeSettings"]

# What a request may name: "fs.<operation>", "git", "settings" and "terminal.<operation>".
TERMINAL_OPS = ("message", "poll", "detach", "env_report")
OPS = (
    tuple(f"fs.{name}" for name in files.OPS)
    + ("git", "settings", "folders", "folders.browse")
    + tuple(f"terminal.{name}" for name in TERMINAL_OPS)
)
# The operations that change something. ``read_only`` refuses them; ``git.read_only`` refuses the git ones.
FILE_WRITE_OPS = ("write_file", "create", "mkdir", "rename", "delete")
GIT_READ_ACTIONS = (
    "status", "log", "show", "show_commit", "diff", "list_branches", "current_branch", "list_stashes", "show_stash",
)


def _bad(status: int, code: str, message: str) -> Reply:
    return Reply({"success": False, "error": {"code": code, "message": message}, "detail": message}, status)


def _refused(status: int, code: str, message: str, setting: str) -> dict[str, Any]:
    return Reply(refusal(code, message, setting), status).as_wire()


def settings_report(settings: CodeSettings) -> dict[str, Any]:
    """The settings in effect, for the UI. ``env_set`` shows names only: its values may be secrets."""
    data = dataclasses.asdict(settings)
    data["env_set"] = [name for name, _ in settings.env_set]
    return data


def _gate(kind: str, name: str, params: dict[str, Any], settings: CodeSettings) -> dict[str, Any] | None:
    """The refusal for an operation a setting closes, or None."""
    if not settings.enabled and kind != "settings" and not (kind == "terminal" and name == "detach"):
        return _refused(404, "code_disabled", "Code, Git and Terminal are turned off for this agent.", "enabled")
    if kind == "terminal" and not settings.terminal_enabled and name != "detach":
        return _refused(403, "terminal_disabled", "Terminals are turned off for this agent.", "terminal.enabled")
    writes_file = kind == "fs" and name in FILE_WRITE_OPS
    action = params.get("action")
    writes_git = kind == "git" and not (isinstance(action, str) and action in GIT_READ_ACTIONS)
    if (writes_file or writes_git) and settings.read_only:
        return _refused(403, "read_only", "Read only is on for this agent.", "read_only")
    if writes_git and settings.git_read_only:
        return _refused(403, "git_read_only", "Git is read only for this agent.", "git.read_only")
    return None


async def call(
    op: object, params: object, root: str | None = None, settings: CodeSettings | None = None
) -> dict[str, Any]:
    """Run one operation in the daemon's folder and return ``{"status": int, "body": dict}``.

    ``root`` is the folder to serve; the daemon passes none and gets its working directory, realpath'd. A test passes
    its own. ``settings`` are the resolved Code settings (``settings.resolve``); None means the defaults. A setting
    that closes an operation answers a refusal that names it. Shells already running keep the environment they
    started with.
    """
    if not isinstance(op, str) or op not in OPS or not isinstance(params, dict):
        return _bad(400, "bad_request", "That code request is not valid.").as_wire()
    settings = settings or DEFAULTS
    kind, _, name = op.partition(".")
    refusal_wire = _gate(kind, name, params, settings)
    if refusal_wire is not None:
        return refusal_wire
    if kind == "settings":
        return Reply({"success": True, "data": settings_report(settings)}).as_wire()
    folder = os.path.realpath(root or os.getcwd())
    if op == "folders.browse":
        path = params.get("path", folder)
        if not isinstance(path, str) or "\0" in path or not os.path.isabs(path):
            return _bad(400, "invalid_folder", "The folder path must be absolute.").as_wire()
        path = os.path.realpath(path)
        after = params.get("after", "")
        if not isinstance(after, str) or "\0" in after or len(after) > 4096:
            return _bad(400, "invalid_cursor", "The folder cursor is invalid.").as_wire()
        if not os.path.isdir(path):
            return _bad(404, "folder_not_found", "That folder is not available.").as_wire()
        try:
            entries, next_cursor = await asyncio.to_thread(folders.browse, path, after)
        except OSError:
            return _bad(403, "folder_unreadable", "That folder cannot be read.").as_wire()
        data = {"path": path, "parent": os.path.dirname(path), "folders": entries, "next": next_cursor}
        return Reply({"success": True, "data": data}).as_wire()
    if kind == "folders":
        try:
            entries = await asyncio.to_thread(folders.linked_folders, folder)
        except git.GitRequestError as exc:
            return _bad(exc.status, exc.code, exc.message).as_wire()
        return Reply({"success": True, "data": {"folders": entries, "default": folder}}).as_wire()
    selected = params.get("codeRoot")
    if kind in ("fs", "git") and selected is not None:
        if not isinstance(selected, str) or "\0" in selected or not os.path.isabs(selected):
            return _bad(400, "invalid_folder", "The selected folder is invalid.").as_wire()
        selected = os.path.realpath(selected)
        if not os.path.isdir(selected):
            return _bad(404, "folder_not_found", "That folder is not available.").as_wire()
        folder = selected
    if kind == "fs":
        reply = await files.serve(name, folder, params, settings)
    elif kind == "git":
        reply = await git.serve(folder, params, settings)
    else:
        reply = await terminal.serve(name, folder, params, settings)
    return reply.as_wire()


async def shutdown() -> None:
    """The daemon is stopping: end every terminal shell."""
    await terminal.shutdown_terminals()

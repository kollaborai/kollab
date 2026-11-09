"""The code editor's files, git and terminal, served by the daemon of the session being used (#987).

The web UI's Code Editor, Git and Terminal views reach this through the engine's ``/code/*`` routes, which proxy to
``state.code_call`` on the session's daemon. A daemon on another computer answers over the same device channel as the
rest of its state, so a remote session shows that computer's files, git and shell. The daemon serves its own folder:
it never reads a folder from a request.

Spec: ``docs/specs/floating-code-shell.md``.
"""

import os
from typing import Any

from . import files, git, terminal
from .reply import Reply

__all__ = ["call", "shutdown", "OPS"]

# What a request may name: "fs.<operation>", "git" and "terminal.<operation>".
TERMINAL_OPS = ("message", "poll", "detach")
OPS = (
    tuple(f"fs.{name}" for name in files.OPS)
    + ("git",)
    + tuple(f"terminal.{name}" for name in TERMINAL_OPS)
)


def _bad(status: int, code: str, message: str) -> Reply:
    return Reply({"success": False, "error": {"code": code, "message": message}, "detail": message}, status)


async def call(op: object, params: object, root: str | None = None) -> dict[str, Any]:
    """Run one operation in the daemon's folder and return ``{"status": int, "body": dict}``.

    ``root`` is the folder to serve; the daemon passes none and gets its working directory, realpath'd. A test passes
    its own.
    """
    if not isinstance(op, str) or op not in OPS or not isinstance(params, dict):
        return _bad(400, "bad_request", "That code request is not valid.").as_wire()
    folder = os.path.realpath(root or os.getcwd())
    kind, _, name = op.partition(".")
    if kind == "fs":
        reply = await files.serve(name, folder, params)
    elif kind == "git":
        reply = await git.serve(folder, params)
    else:
        reply = await terminal.serve(name, folder, params)
    return reply.as_wire()


async def shutdown() -> None:
    """The daemon is stopping: end every terminal shell."""
    await terminal.shutdown_terminals()

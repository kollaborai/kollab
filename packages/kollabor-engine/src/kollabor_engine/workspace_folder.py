"""The one rule for a folder a request names (#980, docs/specs/engine-folder-rule.md).

Several engine routes take a folder on purpose (New Session in any folder, #957): ``workspace``, ``folder``
or the picker's ``path``. Each calls :func:`folder_or_400` first and uses what it returns, never the
request's own text. A folder is accepted when, once ``~``, relative parts, ``..`` and symlinks are resolved,
it exists, is a directory, and sits inside one of:

1. this engine's own folder,
2. the user's home folder,
3. a folder an agent on this computer runs in right now (hub presence, or a live web session),
4. a folder a web session started in recently (the New Session recent list).

Everything else is refused, whatever the way in: ``/etc``, ``~/../other-user``, a symlink in home that
points out, a sibling that only shares a name prefix with a root.
"""

import os
from typing import Iterator, Optional

from fastapi import HTTPException  # type: ignore[import-not-found]

# Longer than any path an operating system accepts; also keeps an error message short.
MAX_PATH = 4096


class FolderError(ValueError):
    """A folder the rule refuses. The message is written for the person who typed the folder."""


def _closed(folder: str) -> str:
    """``folder`` as a real path with a closing separator, so a root matches itself and its contents only.

    Without the separator ``/home/me/proj`` would also admit ``/home/me/project2``.
    """
    return os.path.join(os.path.realpath(folder), "")


def _known_folders() -> Iterator[str]:
    """Folders kollab already runs in, or ran a web session in. A lookup that fails adds none."""
    try:
        from .hub_bridge import HubBridge

        for agent in HubBridge().get_agents(use_cache=True):
            if isinstance(agent, dict) and agent.get("project"):
                yield str(agent["project"])
    except Exception:  # noqa: BLE001 - fewer roots only narrows the rule
        pass
    try:
        from .server import get_session_registry

        for session in list(get_session_registry().values()):
            if getattr(session, "alive", False) and getattr(session, "workspace", None):
                yield str(session.workspace)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .routes.workspaces import _stored

        yield from _stored()
    except Exception:  # noqa: BLE001
        pass


def _roots() -> Iterator[str]:
    """The folders a request may name, cheapest first: most requests end at the first two."""
    yield _closed(os.getcwd())
    yield _closed(os.path.expanduser("~"))
    for folder in _known_folders():
        yield _closed(folder)


def _shown(text: str) -> str:
    """What the person typed, cut short; never the resolved path, which could name where a symlink points."""
    return text if len(text) <= 120 else text[:117] + "..."


def checked_folder(path: Optional[str]) -> str:
    """The real path of the folder ``path`` names, if the rule allows it; else :class:`FolderError`.

    No folder (``None`` or empty) means this engine's own.
    """
    text = path or ""
    if "\0" in text or len(text) > MAX_PATH:
        raise FolderError("That is not a folder path.")
    folder = os.path.realpath(os.path.expanduser(text)) if text else os.path.realpath(os.getcwd())
    # The separator on the end is part of the check: a root is its own folder and what is inside it.
    probe = os.path.join(folder, "")
    for root in _roots():
        if probe.startswith(root):
            checked = os.path.normpath(probe)
            if not os.path.exists(checked):
                raise FolderError(f"Folder does not exist: {_shown(text)}")
            if not os.path.isdir(checked):
                raise FolderError(f"Not a folder: {_shown(text)}")
            return checked
    raise FolderError(f"Can't use {_shown(text)}: it is outside your home folder and no kollab agent runs there.")


def folder_or_400(path: Optional[str]) -> str:
    """:func:`checked_folder` for a route: a refused folder answers 400 with the same words."""
    try:
        return checked_folder(path)
    except FolderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parent_folder(folder: str) -> Optional[str]:
    """``folder``'s parent when the rule lets a request name it, else None (the picker's Up button)."""
    parent = os.path.dirname(folder)
    if parent == folder:
        return None
    try:
        return checked_folder(parent)
    except FolderError:
        return None

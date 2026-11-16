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

Two details keep the rule honest. A remembered folder (3 and 4) counts only while its pathname still leads to
itself: it was recorded as a real path, so a symlink put in its place, or in one of its parents, makes it grant
nothing, not even what it leads to now. And on a volume that ignores case (macOS and Windows by default)
``/Users/me/Dev`` and ``/users/me/dev`` are one folder, so a request meets the same roots in either spelling; a
volume that tells them apart keeps them apart. The folder comes back spelled the way that volume stores it, because
a daemon started there reports that spelling and the engine finds the agent it launched by comparing folder strings.
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


def _pinned(folder: str) -> Optional[str]:
    """A remembered ``folder`` as a closed real path, or None once its pathname no longer leads to itself.

    Every source of remembered folders records a real path: a recent folder is saved as one, a session runs in
    the checked one, an agent reports its resolved project folder. If the real path of that text is not the text
    any more, a symlink now stands in the folder's place or in one of its parents. The folder that was
    remembered is not the one the pathname reaches, so the entry grants nothing, and neither does its new target.
    An entry that cannot be a path at all (a NUL byte in a hand-edited list) grants nothing either.
    """
    try:
        real = os.path.realpath(folder)
    except OSError, ValueError:
        return None
    if os.path.normcase(real) != os.path.normcase(os.path.normpath(folder)):
        return None
    return os.path.join(real, "")


def _ignores_case(root: str) -> bool:
    """Whether the volume ``root`` is on treats names that differ only in case as one name.

    Asked of a root, which kollab knows, and never of the request, and only when a request spelled a root in
    other letters. A volume that does not ignore case has no folder of the other spelling, so the same path with
    its case swapped is missing there, or is a different folder.
    """
    other = root.swapcase()
    if other == root:
        return False
    try:
        return os.path.samefile(root, other)
    except OSError:
        return False


def _respelled(probe: str, root: str) -> str:
    """``probe`` with the start that names ``root`` spelled as ``root`` is, when only the case differs and the
    volume ignores it; otherwise ``probe`` as it was, for the prefix check to decide."""
    head = probe[: len(root)]
    if head != root and head.casefold() == root.casefold() and _ignores_case(root):
        return root + probe[len(root) :]
    return probe


def _stored_name(folder: str, name: str) -> str:
    """``name`` as ``folder`` stores it.

    ``name`` itself when it is an entry, else the entry the volume calls the same one. Only a name that is no entry
    is looked up by case, and only an entry that ``samefile`` says is that name is taken.
    """
    try:
        entries = os.listdir(folder)
    except OSError:
        return name
    if name in entries:
        return name
    wanted = name.casefold()
    for entry in entries:
        if entry.casefold() != wanted:
            continue
        try:
            if os.path.samefile(os.path.join(folder, entry), os.path.join(folder, name)):
                return entry
        except OSError:  # the volume knows no such name: it tells the cases apart, so this is another name
            return name
    return name


def _on_disk(checked: str, root: str) -> str:
    """``checked`` with every part below ``root`` spelled the way its folder stores it.

    A volume that ignores case opens ``cASEpROJECT`` for ``CaseProject``, but a daemon started there reports the stored
    spelling, and the engine finds the agent it launched by comparing folder strings
    (``HubBridge.get_agent_by_identity``): with the typed spelling New Session waited 45 s for a daemon it could not
    find. A part is replaced only by the entry the volume itself calls the same one, so a mount below the root that
    tells the cases apart is left as typed.
    """
    if not checked.startswith(root):
        return checked
    built = root
    for name in filter(None, checked[len(root) :].split(os.sep)):
        built = os.path.join(built, _stored_name(built, name))
    return os.path.normpath(built)


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
        root = _pinned(folder)
        if root is not None:
            yield root


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
        spelled = _respelled(probe, root)
        if spelled.startswith(root):
            checked = os.path.normpath(spelled)
            if not os.path.exists(checked):
                raise FolderError(f"Folder does not exist: {_shown(text)}")
            if not os.path.isdir(checked):
                raise FolderError(f"Not a folder: {_shown(text)}")
            return _on_disk(checked, root) if _ignores_case(root) else checked
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

"""Settings of the Code Editor, Git and Terminal views: every limit has a switch (#987).

Spec: ``docs/specs/code-shell-settings.md``. A setting is looked up in the agent's own ``agent.json`` ``"code"`` object
first, then in the merged config (project over global) under ``kollabor.code.``, then the default here. The daemon of
the session resolves them, so a session on another computer follows that computer's config and agent.

A value of the wrong type falls back to the default: a typo in a config file must not close the door on a developer
who needs the shell. Numbers are clamped to a range that still works.
"""

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PREFIX = "kollabor.code."
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class CodeSettings:
    """One resolved set of settings. The defaults are the safe ones; nothing here is locked."""

    enabled: bool = True
    read_only: bool = False
    terminal_enabled: bool = True
    pass_secret_env: bool = False
    pass_kollab_env: bool = False
    env_allow: tuple[str, ...] = ()
    env_deny: tuple[str, ...] = ()
    env_set: tuple[tuple[str, str], ...] = ()
    shell: str = ""
    login: bool = True
    start_command: str = ""
    max_shells: int = 8
    idle_reap_minutes: float = 10.0
    scrollback_kb: int = 256
    max_file_kb: int = 2048
    show_hidden: bool = False
    search_max_files: int = 20000
    extra_roots: tuple[str, ...] = ()
    follow_symlinks: bool = False
    git_allow_outside_workspace: bool = False
    git_read_only: bool = False

    @property
    def git_writes_refused(self) -> bool:
        return self.read_only or self.git_read_only


DEFAULTS = CodeSettings()

# key (without the prefix) -> (field, kind). Kinds: bool, int:<lo>:<hi>, float:<lo>:<hi>, str, names, paths, env_set.
SCHEMA: dict[str, tuple[str, str]] = {
    "enabled": ("enabled", "bool"),
    "read_only": ("read_only", "bool"),
    "terminal.enabled": ("terminal_enabled", "bool"),
    "terminal.pass_secret_env": ("pass_secret_env", "bool"),
    "terminal.pass_kollab_env": ("pass_kollab_env", "bool"),
    "terminal.env_allow": ("env_allow", "names"),
    "terminal.env_deny": ("env_deny", "names"),
    "terminal.env_set": ("env_set", "env_set"),
    "terminal.shell": ("shell", "str"),
    "terminal.login": ("login", "bool"),
    "terminal.start_command": ("start_command", "str"),
    "terminal.max_shells": ("max_shells", "int:1:64"),
    "terminal.idle_reap_minutes": ("idle_reap_minutes", "float:0:100000"),
    "terminal.scrollback_kb": ("scrollback_kb", "int:16:65536"),
    "files.max_file_kb": ("max_file_kb", "int:16:1048576"),
    "files.show_hidden": ("show_hidden", "bool"),
    "files.search_max_files": ("search_max_files", "int:100:1000000"),
    "files.extra_roots": ("extra_roots", "paths"),
    "files.follow_symlinks": ("follow_symlinks", "bool"),
    "git.allow_outside_workspace": ("git_allow_outside_workspace", "bool"),
    "git.read_only": ("git_read_only", "bool"),
}


def _number(value: object, kind: str) -> int | float | None:
    _, lo, hi = kind.split(":")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value:  # NaN
        return None
    try:
        number: int | float = int(value) if kind.startswith("int") else float(value)
    except ValueError, OverflowError:  # infinity, which Python's json accepts
        return None
    if number in (float("inf"), float("-inf")):
        return None
    return max(type(number)(lo), min(type(number)(hi), number))


def _names(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list | tuple):
        return None
    return tuple(dict.fromkeys(item for item in value if isinstance(item, str) and _ENV_NAME.fullmatch(item)))


def _paths(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list | tuple):
        return None
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        expanded = os.path.expanduser(item.strip())
        if os.path.isabs(expanded):  # a relative entry would mean "relative to what?": dropped
            paths.append(expanded)
    return tuple(dict.fromkeys(paths))


def _env_set(value: object) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(value, Mapping):
        return None
    return tuple(
        (name, str(item))
        for name, item in value.items()
        if isinstance(name, str) and _ENV_NAME.fullmatch(name) and isinstance(item, str | int | float | bool)
    )


def coerce(value: object, kind: str) -> Any:
    """``value`` as the setting's type, or None when it is the wrong type (the caller then uses a lower tier)."""
    if kind == "bool":
        return value if isinstance(value, bool) else None
    if kind == "str":
        return value if isinstance(value, str) else None
    if kind == "names":
        return _names(value)
    if kind == "paths":
        return _paths(value)
    if kind == "env_set":
        return _env_set(value)
    return _number(value, kind)


def _flatten(agent_code: object) -> dict[str, object]:
    """The agent's ``code`` object as dotted keys; nested (``{"terminal": {"shell": ...}}``) and dotted both work."""
    flat: dict[str, object] = {}
    if not isinstance(agent_code, Mapping):
        return flat
    for key, value in agent_code.items():
        if not isinstance(key, str):
            continue
        if key in ("terminal", "files", "git") and isinstance(value, Mapping):
            for inner, item in value.items():
                if isinstance(inner, str):
                    flat[f"{key}.{inner}"] = item
        else:
            flat[key] = value
    return flat


_MISSING = object()


def resolve(get: Callable[[str, Any], Any] | None, agent_code: object = None) -> CodeSettings:
    """The settings for one request: agent.json ``code``, then ``get("kollabor.code.<key>", default)``, then defaults.

    ``get`` is the merged config's ``get`` (project over global); None or a failing ``get`` means defaults.
    """
    agent = _flatten(agent_code)
    values: dict[str, Any] = {}
    for key, (field, kind) in SCHEMA.items():
        chosen: Any = None
        if key in agent:
            try:
                chosen = coerce(agent[key], kind)
            except Exception:
                chosen = None
        if chosen is None and get is not None:
            try:
                chosen = coerce(get(PREFIX + key, _MISSING), kind)
            except Exception:
                chosen = None
        if chosen is not None:
            values[field] = chosen
    return CodeSettings(**values)


def read_agent_code(directory: object) -> dict[str, Any] | None:
    """The ``"code"`` object of ``<directory>/agent.json``, or None when there is none or the file cannot be read."""
    if not directory:
        return None
    try:
        data = json.loads((Path(str(directory)) / "agent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, dict) else None


def refusal(code: str, message: str, setting: str) -> dict[str, Any]:
    """The wire body for a refusal that names the setting behind it."""
    full = f"{message} Setting: {PREFIX}{setting}"
    return {"success": False, "error": {"code": code, "message": full, "setting": PREFIX + setting}, "detail": full}

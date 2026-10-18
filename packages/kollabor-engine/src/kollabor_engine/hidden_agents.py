"""The sidebar rows the user hid, shared by every client of this engine.

One JSON document at ``~/.kollab/hub/hidden-agents.json``, so the app, every
browser and the phone pointed at this engine hide the same rows::

    {"hidden": [{"key": "[\\"agent\\",\\"a1b2c3d4e5f6\\"]", "label": "Sapphire",
                 "detail": "/home/user/project", "instance": "a1b2c3d4e5f6"}]}

The web UI builds ``key`` (hidden-agents.ts) and finds a row's entry through
it; the engine keeps it opaque. The row of a running agent also names its
``instance``: the hub presence ``agent_id`` of its daemon, new for every
process, so an agent launched later under the same name is a different row and
shows. Such an entry lasts as long as that daemon (``visible`` leaves out the
ones whose daemon is gone). Offline and remote rows have no process to name and
stay hidden until shown.

``stamp_instances`` gives every row of ``GET /sessions`` its ``instance``.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

HIDDEN_FILE = "hidden-agents.json"
MAX_HIDDEN = 256
_FIELD_LIMITS = {"key": 1024, "label": 256, "detail": 1024, "instance": 64}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# The instance of each engine-launched session, learned while its daemon is in
# presence: presence goes stale for a minute after the machine wakes, and a row
# must not unhide itself meanwhile.
_learned: Dict[str, str] = {}


def hidden_path() -> Path:
    return get_config_directory() / "hub" / HIDDEN_FILE


def parse_entry(raw: Any) -> Optional[Dict[str, str]]:
    """A well-formed entry, or None: ``key`` and ``label`` are required text."""
    if not isinstance(raw, dict):
        return None
    entry: Dict[str, str] = {}
    for field, limit in _FIELD_LIMITS.items():
        value = raw.get(field) or ""
        if not isinstance(value, str) or len(value) > limit or _CONTROL.search(value):
            return None
        if value or field == "detail":
            entry[field] = value
    return entry if entry.get("key") and entry.get("label") else None


def _read() -> List[Dict[str, str]]:
    path = hidden_path()
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("hidden")
    except FileNotFoundError:
        return []
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("Ignoring unreadable hidden agents %s: %s", path, exc)
        return []
    entries: Dict[str, Dict[str, str]] = {}
    for raw in rows if isinstance(rows, list) else []:
        entry = parse_entry(raw)
        if entry:
            entries[entry["key"]] = entry
    return list(entries.values())


def _write(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    entries = entries[-MAX_HIDDEN:]
    path = hidden_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hidden-agents-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"hidden": entries}, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return entries


# ponytail: no file lock. Each change reads then writes without yielding, so one
# engine cannot interleave itself; two engines on one machine changing it in the
# same instant can lose one change.
def hide(entry: Dict[str, str]) -> List[Dict[str, str]]:
    """Hide a row (again: it moves to the end). Returns every stored entry."""
    return _write([row for row in _read() if row["key"] != entry["key"]] + [entry])


def show(key: str) -> List[Dict[str, str]]:
    return _write([row for row in _read() if row["key"] != key])


def show_all() -> List[Dict[str, str]]:
    return _write([])


def visible(live: Iterable[str]) -> List[Dict[str, str]]:
    """The entries to show: all but those of an agent process that is gone.

    ``live`` is the instances of the daemons running now.
    """
    running = set(live)
    return [row for row in _read() if "instance" not in row or row["instance"] in running]


def stamp_instances(rows: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> None:
    """Give each session row its ``instance``: the presence agent id of its daemon.

    ``found`` is HubBridge.discover_sessions, whose ``session_id`` is that id. An
    engine-launched session has its own name for ``session_id`` (and loses it with
    the engine), and a rediscovered one has the agent id, so the daemon's pid
    ties both to the same instance. With no daemon in presence (an ACP session,
    one still starting) the session id stands in.
    """
    by_pid = {int(row["daemon_pid"]): row["session_id"] for row in found if row.get("daemon_pid")}
    for row in rows:
        session_id = row["session_id"]
        instance = by_pid.get(int(row.get("daemon_pid") or 0))
        if instance and not row.get("discovered"):
            _learned[session_id] = instance
        row["instance"] = instance or _learned.get(session_id) or session_id
    for session_id in set(_learned) - {row["session_id"] for row in rows}:
        del _learned[session_id]

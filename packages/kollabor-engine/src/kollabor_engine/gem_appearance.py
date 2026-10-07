"""How the user dressed their gems in the web UI's Gem Studio.

One JSON document at ``~/.kollab/hub/appearance.json``, so every browser
pointed at this engine shows the same gems::

    {"season": "auto",
     "seed": 0,
     "defaults": {"face": "pill", "hat": "auto"},
     "gems": {"lapis": {"face": "disney", "hat": "crown", "color": [30, 90, 180]}}}

A gem with no eyes picked (its own or the defaults') draws a pair from the
web UI's mix by its name and ``seed``; Shuffle Eyes saves a new seed.

The web UI owns the style vocabulary (eye styles, hats and seasons in
``gem-face.ts``), so the engine checks shape only: short slug ids, colors as
three 0-255 ints, a bounded number of gems. The renderer skips ids it does not
know, and malformed fields are dropped rather than stored.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

APPEARANCE_FILE = "appearance.json"
MAX_GEMS = 256
MAX_SEED = 2**31
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def appearance_path() -> Path:
    return get_config_directory() / "hub" / APPEARANCE_FILE


def _slug(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _SLUG.match(value) else None


def _color(value: Any) -> Optional[List[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    # bool is an int subclass; True is not a color channel.
    if not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value):
        return None
    return list(value)


def _seed(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < MAX_SEED:
        return value
    return 0


def _look(value: Any, allow_color: bool) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    look: Dict[str, Any] = {}
    for key in ("face", "hat"):
        slug = _slug(value.get(key))
        if slug:
            look[key] = slug
    color = _color(value.get("color")) if allow_color else None
    if color:
        look["color"] = color
    return look


def normalize(doc: Any) -> Dict[str, Any]:
    """Keep the well-formed fields of ``doc``; drop everything else."""
    if not isinstance(doc, dict):
        doc = {}
    gems: Dict[str, Any] = {}
    raw_gems = doc.get("gems")
    if isinstance(raw_gems, dict):
        for name, value in list(raw_gems.items())[:MAX_GEMS]:
            look = _look(value, allow_color=True) if _slug(name) else {}
            if look:
                gems[name] = look
    return {
        "season": _slug(doc.get("season")) or "auto",
        "seed": _seed(doc.get("seed")),
        # A default color would repaint every gem one color: per gem only.
        "defaults": _look(doc.get("defaults"), allow_color=False),
        "gems": gems,
    }


def load_appearance() -> Dict[str, Any]:
    path = appearance_path()
    try:
        return normalize(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return normalize({})
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable gem appearance %s: %s", path, exc)
        return normalize({})


def save_appearance(doc: Any) -> Dict[str, Any]:
    """Write the normalized document atomically and return what was stored."""
    clean = normalize(doc)
    path = appearance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".appearance-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return clean

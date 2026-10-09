"""How the web UI dresses its gems: the look each agent is born with, and the
user's picks from the Gem Studio.

One JSON document at ``~/.kollab/hub/appearance.json``, so every browser
pointed at this engine shows the same gems::

    {"season": "auto",
     "born": {"lapis": {"face": "kawaii", "hat": "beret"}},
     "gems": {"lapis": {"face": "disney", "hat": "crown", "color": [30, 90, 180]}}}

``born`` is the random eyes and hat a gem gets the first time the engine sees
it alive (``record_births``, from ``GET /agents``). It sticks: nothing rolls it
again, and the studio's save keeps it from disk whatever the request says.
``gems`` holds the user's picks, drawn over the born look.

A name repeats across folders and computers (a koordinator per project), and
each of those agents dresses on its own. A bare name is the agent in the
engine's own folder (``home`` in the routes' answer); the web UI saves picks
for any other under ``name@folder`` or ``name@device`` and draws its born
look from that key (``gem-look.ts`` ``lookKey``).

The web UI owns the style vocabulary (``gem-face.ts``), so the engine checks
shape only: short slug ids, colors as three 0-255 ints, a bounded number of
gems. The renderer skips ids it does not know, and malformed fields are
dropped rather than stored.
"""

import json
import logging
import os
import random
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

APPEARANCE_FILE = "appearance.json"
MAX_GEMS = 256
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# A pick's key: a gem name, alone or @ the folder or device the agent runs in.
_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,31}(@[^\x00-\x1f\x7f]{1,1024})?$")

# What a gem can be born with: gem-face.ts's EYE_STYLES and HAT_STYLES without
# the Halloween and Christmas groups (a test keeps the two in step).
BIRTH_FACES = ("pill", "dot", "disney", "kawaii", "diamond", "heart", "triclops", "shades", "visor", "googly", "pixel")
BIRTH_HATS = ("none", "party", "top", "beanie", "crown", "wizard", "cap", "headphones", "hardhat", "beret")


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


def _looks(value: Any, allow_color: bool, key: "re.Pattern[str]" = _SLUG) -> Dict[str, Dict[str, Any]]:
    looks: Dict[str, Dict[str, Any]] = {}
    if isinstance(value, dict):
        for name, raw in list(value.items())[:MAX_GEMS]:
            look = _look(raw, allow_color) if isinstance(name, str) and key.fullmatch(name) else {}
            if look:
                looks[name] = look
    return looks


def normalize(doc: Any) -> Dict[str, Any]:
    """Keep the well-formed fields of ``doc``; drop everything else."""
    if not isinstance(doc, dict):
        doc = {}
    return {
        "season": _slug(doc.get("season")) or "auto",
        # Born looks keep a gem's own color: the gem names are colors.
        "born": _looks(doc.get("born"), allow_color=False),
        "gems": _looks(doc.get("gems"), allow_color=True, key=_KEY),
    }


def _read() -> Optional[Dict[str, Any]]:
    """The stored document, or None when the file exists but cannot be read."""
    path = appearance_path()
    try:
        return normalize(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return normalize({})
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable gem appearance %s: %s", path, exc)
        return None


def _write(clean: Dict[str, Any]) -> None:
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


def load_appearance() -> Dict[str, Any]:
    return _read() or normalize({})


def save_appearance(doc: Any) -> Dict[str, Any]:
    """Write the studio's document atomically and return what was stored.

    Born looks come from disk, never from the request, so a studio opened
    before a gem was born cannot drop the look it was born with.
    """
    clean = normalize(doc)
    clean["born"] = load_appearance()["born"]
    _write(clean)
    return clean


def record_births(names: Iterable[str]) -> None:
    """Give each gem in ``names`` without a born look a random one, for good.

    Called with the gems seen alive. A file that exists but cannot be read is
    left alone for the user to recover.
    """
    # ponytail: no file lock. The engine does this read-modify-write without
    # yielding, but two engines on one machine writing at once can lose a birth.
    doc = _read()
    if doc is None:
        return
    born = doc["born"]
    newborn = [name for name in dict.fromkeys(names) if _slug(name) and name not in born]
    newborn = newborn[: max(0, MAX_GEMS - len(born))]
    for name in newborn:
        born[name] = {"face": random.choice(BIRTH_FACES), "hat": random.choice(BIRTH_HATS)}
    if newborn:
        try:
            _write(doc)
        except OSError as exc:
            logger.warning("Could not record gem births %s: %s", newborn, exc)

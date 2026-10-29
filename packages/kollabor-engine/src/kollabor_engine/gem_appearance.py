"""How the web UI dresses its gems: the look each agent is born with, and the
user's picks from the Gem Studio.

One JSON document at ``~/.kollab/hub/appearance.json``, so every browser
pointed at this engine shows the same gems::

    {"season": "auto",
     "born": {"lapis": {"face": "kawaii", "hat": "beret"}},
     "gems": {"lapis": {"face": "disney", "hat": "crown", "color": [30, 90, 180]}}}

``born`` is the eyes, hat and coat a gem draws the first time the engine sees
it alive (``record_births``, from ``GET /agents``): a fixed draw from its name
— furry 85% of the time — the same one the web UI draws for a gem it never
rolled (``gem-look.ts`` ``bornCoat``), so a gem is born looking the same on
every device. It sticks: nothing rolls it again but a coat recorded bare
(hair before fur was made the common birth), and the studio's save keeps it
from disk whatever the request says.
``gems`` holds the user's picks, drawn over the born look. A pick may also
carry an ``avatar`` that replaces the roster's for that agent everywhere: a
gem, initials, an emoji, or a small picture stored inline as a data URL.
Hair and fur (``hair``, ``fur``) take a color each (``hairColor``,
``furColor``: three ints, or ``"gem"`` for the body's own) and the user's
settings of their styles' knobs (``hairKnobs``, ``furKnobs``: numbers by
knob name); auras (``auras``, ``sparkles``) take one color (``auraColor``).

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
import math
import os
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
# A picture the studio uploads: shrunk in the browser, stored inline. No SVG (it can carry script).
_PICTURE = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*$")
MAX_PICTURE = 200_000
MAX_EMOJI = 16
_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,31}(@[^\x00-\x1f\x7f]{1,1024})?$")

# What a gem can be born with: gem-face.ts's EYE_STYLES and HAT_STYLES without
# the Halloween and Christmas groups, its HAIR_STYLES in a HAIR_COLORS shade,
# and FUR_STYLES in the gem's own color or a natural shade (a test keeps the
# two in step). Every birth is a fixed draw from the gem's name (_drawn, the
# same scoring draw the web UI's fallback makes), never a fresh random one.
BIRTH_FACES = (
    "pill",
    "dot",
    "disney",
    "kawaii",
    "diamond",
    "heart",
    "triclops",
    "shades",
    "visor",
    "googly",
    "pixel",
)
BIRTH_HATS = (
    "none",
    "party",
    "top",
    "beanie",
    "crown",
    "wizard",
    "cap",
    "headphones",
    "hardhat",
    "beret",
)
BIRTH_HAIRS = ("short", "long", "spiky", "mohawk")
BIRTH_HAIR_COLORS = (
    (30, 26, 24),
    (95, 60, 35),
    (150, 60, 35),
    (225, 190, 120),
    (170, 170, 175),
    (235, 232, 225),
)
BIRTH_FURS = ("short", "shaggy")
BIRTH_FUR_COLORS = ("gem",) + BIRTH_HAIR_COLORS
# The share of new gems born with fur, and with hair; the rest are born bare.
# gem-look.ts (BORN_FUR_SHARE, BORN_HAIR_SHARE) draws the same shares.
FUR_SHARE = 0.85
HAIR_SHARE = 0.1
MAX_AURAS = 8
# A coat's knob settings: how many, and how far out (the web UI holds each to its knob's range).
MAX_KNOBS = 16
KNOB_LIMIT = 10.0


def appearance_path() -> Path:
    return get_config_directory() / "hub" / APPEARANCE_FILE


def _slug(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _SLUG.match(value) else None


def _color(value: Any) -> Optional[List[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    # bool is an int subclass; True is not a color channel.
    if not all(
        isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value
    ):
        return None
    return list(value)


def _look_color(value: Any) -> Any:
    """A coat or aura color: three 0-255 ints, or "gem" for the body's own."""
    return "gem" if value == "gem" else _color(value)


def _knobs(value: Any) -> Optional[Dict[str, float]]:
    """A coat's knob settings (gem-face.ts COAT_KNOBS): finite numbers by knob name."""
    if not isinstance(value, dict):
        return None
    knobs: Dict[str, float] = {}
    for name, setting in list(value.items())[:MAX_KNOBS]:
        number = isinstance(setting, (int, float)) and not isinstance(setting, bool)
        if _slug(name) and number and math.isfinite(setting):
            knobs[name] = max(-KNOB_LIMIT, min(KNOB_LIMIT, float(setting)))
    return knobs or None


def _avatar(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind in ("gem", "initials"):
        return {"kind": kind}
    emoji = value.get("value")
    if (
        kind == "emoji"
        and isinstance(emoji, str)
        and 0 < len(emoji.strip()) <= MAX_EMOJI
    ):
        return {"kind": "emoji", "value": emoji.strip()}
    url = value.get("url")
    if (
        kind == "image"
        and isinstance(url, str)
        and len(url) <= MAX_PICTURE
        and _PICTURE.match(url)
    ):
        return {"kind": "image", "url": url}
    return None


def _look(value: Any, allow_color: bool) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    look: Dict[str, Any] = {}
    for key in ("face", "hat", "hair", "fur"):
        slug = _slug(value.get(key))
        if slug:
            look[key] = slug
    # A born coat has its color too (hair in a natural shade).
    for key in ("hairColor", "furColor"):
        color = _look_color(value.get(key))
        if color:
            look[key] = color
    # The user's settings of a coat's knobs; never born.
    for key in ("hairKnobs", "furKnobs"):
        knobs = _knobs(value.get(key)) if allow_color else None
        if knobs:
            look[key] = knobs
    # A picked body shape (gem-specs.ts GEM_CUTS); like color, never born.
    cut = _slug(value.get("cut")) if allow_color else None
    if cut:
        look["cut"] = cut
    color = _color(value.get("color")) if allow_color else None
    if color:
        look["color"] = color
    # The user's picks only, like color: nothing is born with an avatar.
    avatar = _avatar(value.get("avatar")) if allow_color else None
    if avatar:
        look["avatar"] = avatar
    # Auras are the user's picks only.
    auras = value.get("auras") if allow_color else None
    if isinstance(auras, list):
        kept = list(
            dict.fromkeys(slug for slug in map(_slug, auras[:MAX_AURAS]) if slug)
        )
        if kept:
            look["auras"] = kept
    sparkles = _slug(value.get("sparkles")) if allow_color else None
    if sparkles:
        look["sparkles"] = sparkles
    aura_color = _look_color(value.get("auraColor")) if allow_color else None
    if aura_color:
        look["auraColor"] = aura_color
    return look


def _looks(
    value: Any, allow_color: bool, key: "re.Pattern[str]" = _SLUG
) -> Dict[str, Dict[str, Any]]:
    looks: Dict[str, Dict[str, Any]] = {}
    if isinstance(value, dict):
        for name, raw in list(value.items())[:MAX_GEMS]:
            look = (
                _look(raw, allow_color)
                if isinstance(name, str) and key.fullmatch(name)
                else {}
            )
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


def _fnv(text: str) -> int:
    """FNV-1a over a key's UTF-16 code units, exactly as gem-look.ts ``fnv``."""
    units = text.encode("utf-16-le")
    hash_ = 2166136261
    for i in range(0, len(units), 2):
        hash_ ^= int.from_bytes(units[i : i + 2], "little")
        hash_ = (hash_ * 16777619) & 0xFFFFFFFF
    return hash_


def _drawn(name: str, salt: str, pool: Iterable[Any]) -> Any:
    """The pool option that scores highest for ``name`` — gem-look.ts ``drawn``,
    so the engine's roll and the web UI's fallback draw agree exactly."""
    best = None
    best_score = -1
    for option in pool:
        text = (
            option
            if isinstance(option, str)
            else ",".join(str(part) for part in option)
        )
        score = _fnv(f"{salt}:{name}:{text}")
        if score > best_score:
            best, best_score = option, score
    return best


def _birth_coat(name: str) -> Dict[str, Any]:
    """Fur, hair or neither, drawn from the name (gem-look.ts ``bornCoat``).

    Furry FUR_SHARE of names, then hair HAIR_SHARE, bare the rest; "none"
    records that the coat was drawn and came up bare.
    """
    share = _fnv(f"coat:{name}") / 2**32
    if share < FUR_SHARE:
        return {
            "fur": _drawn(name, "fur", BIRTH_FURS),
            "furColor": _drawn(name, "furColor", BIRTH_FUR_COLORS),
        }
    if share < FUR_SHARE + HAIR_SHARE:
        hair = _drawn(name, "hair", BIRTH_HAIRS)
        color = _drawn(name, "hairColor", BIRTH_HAIR_COLORS)
        look: Dict[str, Any] = {"hair": hair}
        if color is not None:
            look["hairColor"] = color
        return look
    return {"hair": "none"}


def record_births(names: Iterable[str]) -> None:
    """Give each gem in ``names`` without a born look the draw of its name, for good.

    Called with the gems seen alive. A file that exists but cannot be read is
    left alone for the user to recover.
    """
    # ponytail: no file lock. The engine does this read-modify-write without
    # yielding, but two engines on one machine writing at once can lose a birth.
    doc = _read()
    if doc is None:
        return
    born = doc["born"]
    newborn = [
        name for name in dict.fromkeys(names) if _slug(name) and name not in born
    ]
    newborn = newborn[: max(0, MAX_GEMS - len(born))]
    for name in newborn:
        born[name] = {
            "face": _drawn(name, "face", BIRTH_FACES),
            "hat": _drawn(name, "hat", BIRTH_HATS),
            **_birth_coat(name),
        }
    # A gem whose coat was never drawn, or drawn bare before fur was made the
    # common birth, draws it once more; a drawn coat sticks for good.
    bare = [
        name
        for name, look in born.items()
        if look.get("hair", "none") == "none" and "fur" not in look
    ]
    wrote = bool(newborn)
    for name in bare:
        coat = _birth_coat(name)
        if {**born[name], **coat} != born[name]:
            born[name].update(coat)
            wrote = True
    if wrote:
        try:
            _write(doc)
        except OSError as exc:
            logger.warning("Could not record gem births %s: %s", newborn, exc)

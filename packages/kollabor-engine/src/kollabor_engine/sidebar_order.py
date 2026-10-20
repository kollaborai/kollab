"""The order the user put sidebar agent rows in, shared by every client.

One JSON document at ``~/.kollab/hub/sidebar-order.json``::

    {"order": ["[\\"pos\\",\\"/home/user/project\\",\\"lapis\\"]", ...]}

The web UI builds each opaque key from its folder and identity; remote rows add
network/device scope. The engine keeps the keys across process restarts.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

ORDER_FILE = "sidebar-order.json"
MAX_KEYS = 512
MAX_KEY_LEN = 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def order_path() -> Path:
    return get_config_directory() / "hub" / ORDER_FILE


def parse_order(raw: Any) -> List[str] | None:
    """The deduplicated key list, or None when ``raw`` is not a list of bounded text."""
    if not isinstance(raw, list) or len(raw) > MAX_KEYS:
        return None
    keys: List[str] = []
    for key in raw:
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LEN or _CONTROL.search(key):
            return None
        if key not in keys:
            keys.append(key)
    return keys


def read_order() -> List[str]:
    path = order_path()
    try:
        keys = parse_order(json.loads(path.read_text(encoding="utf-8")).get("order"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("Ignoring unreadable sidebar order %s: %s", path, exc)
        return []
    return keys or []


def write_order(keys: List[str]) -> List[str]:
    path = order_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sidebar-order-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"order": keys}, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return keys

"""What each ACP harness offers (models, effort levels), remembered from its sessions.

An ACP agent reports its options (``configOptions``) only inside a live
session. The web UI's model picker lists them for a new session from here,
without starting the harness: ``~/.kollab/acp/options.json``, next to the
adapters' ``resolved.json``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _path() -> Path:
    return Path(os.environ.get("KOLLAB_CONFIG_DIR", "~/.kollab")).expanduser() / "acp" / "options.json"


def find_option(options: List[Dict[str, Any]], category: str) -> Optional[Dict[str, Any]]:
    """The option of one category ("model", "thought_level", "effort"), by category or id."""
    category = category.lower()
    for option in options:
        if str(option.get("category") or "").lower() == category:
            return option
        if str(option.get("id") or "").lower() in {category, f"{category}_level"}:
            return option
    return None


def option_values(option: Dict[str, Any]) -> List[str]:
    """Every value an option offers, grouped ones included."""
    values: List[str] = []
    for candidate in option.get("options") or []:
        if not isinstance(candidate, dict):
            continue
        if "value" in candidate:
            values.append(str(candidate["value"]))
        for nested in candidate.get("options") or []:
            if isinstance(nested, dict) and "value" in nested:
                values.append(str(nested["value"]))
    return values


def load() -> Dict[str, Dict[str, List[str]]]:
    """``{harness profile name: {"models": [...], "efforts": [...]}}``; empty when unknown."""
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember(harness: str, options: List[Dict[str, Any]]) -> None:
    """Keep what a harness's session offered. Never raises: listing is a convenience."""
    model = find_option(options, "model")
    effort = find_option(options, "thought_level") or find_option(options, "effort")
    entry = {
        "models": option_values(model) if model else [],
        "efforts": option_values(effort) if effort else [],
    }
    if not harness or not (entry["models"] or entry["efforts"]):
        return
    data = load()
    if data.get(harness) == entry:
        return
    data[harness] = entry
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_suffix(".tmp")
        scratch.write_text(json.dumps(data, indent=1))
        os.replace(scratch, path)
    except OSError as exc:
        logger.debug("acp options for %s not saved: %s", harness, exc)

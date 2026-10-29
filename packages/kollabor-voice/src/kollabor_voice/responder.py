"""Fast voice responder: model choice and the UI activity snapshot it reads.

The snapshot is the summarized view the human sees ("ran Bash", "edited
foo.py"), never raw command text, file contents or tool output.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

RESPONDER_KEY = "kollabor.voice.responder_model"
MAX_EVENTS = 8


def responder_model(config: Any) -> Optional[str]:
    """Quick-model loadout name for voice replies, or None to use the session model.

    A loadout name carries its own provider profile (endpoint and credentials),
    so the quick model may run on a different provider than the chat — an
    OpenRouter fast model while the chat itself stays where it is. Resolution
    is the /llm one: an explicit loadout, else an implicit per-provider model.
    No kollabor.llm.fast_model fallback: that key holds a bare model id for
    compaction and dreaming, which is not a loadout name.
    """
    if config is None:
        return None
    value = config.get(RESPONDER_KEY, "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def activity_snapshot(events: Iterable[dict], limit: int = MAX_EVENTS) -> str:
    """One line per recent event: tool name, short target basename, status.

    Only these whitelisted fields are read; `command`, `args`, `input` and
    `output` are ignored so they cannot leak into the fast model's prompt.
    """
    lines = []
    for event in list(events)[-limit:]:
        tool = str(event.get("tool") or "work")[:40]
        target = os.path.basename(str(event.get("target") or ""))[:60]
        status = str(event.get("status") or "")[:20]
        lines.append(" ".join(p for p in (tool, target, f"({status})" if status else "") if p))
    return "\n".join(f"- {line}" for line in lines) or "- idle"

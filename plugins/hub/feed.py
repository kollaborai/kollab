"""Hub Feed - live dashboard showing agent activity and messages.

Opens a fullscreen live modal that shows:
- Agent roster with status and current task
- Real-time message feed from all agents
- Refreshes every second
"""

import json
import logging
import time
from typing import Dict, List, Tuple

from kollabor_agent.runtime import AgentRuntime
from kollabor_tui.color_contrast import readable_agent_color
from kollabor_tui.design_system import T

from .presence import get_presence_dir
from .vault import find_active_stream, get_vaults_dir

logger = logging.getLogger(__name__)

# Fallback colors for non-gem identities
_FALLBACK_COLORS = [
    (120, 200, 255),
    (255, 180, 100),
    (150, 255, 150),
    (255, 140, 180),
    (200, 170, 255),
    (100, 240, 220),
]


def _color(identity: str) -> Tuple[int, int, int]:
    """Get gem color for identity, or hash-based fallback."""
    from .models import GEM_BY_NAME

    gem = GEM_BY_NAME.get(identity)
    if gem:
        raw_color = gem.color_rgb
    else:
        base = identity.rsplit("-", 1)[0] if "-" in identity else identity
        gem = GEM_BY_NAME.get(base)
        if gem:
            raw_color = gem.color_rgb
        else:
            raw_color = _FALLBACK_COLORS[hash(identity) % len(_FALLBACK_COLORS)]

    return readable_agent_color(
        raw_color,
        background=T().dark[0],
        target=T().text,
        muted_target=T().text_dim,
    )


def _fg(text: str, r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m{text}\033[39m"


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[22m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[22m"


class HubFeed:
    """Generates live feed content for the dashboard."""

    def __init__(self):
        self._last_stream_ts: Dict[str, float] = {}
        self._message_buffer: List[Dict] = []
        self._max_messages = 100

    def get_live_agents(self) -> List[AgentRuntime]:
        """Get all live agents from presence files."""
        import os

        agents = []
        presence_dir = get_presence_dir()
        for f in presence_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                agent = AgentRuntime.from_presence_dict(data)
                try:
                    os.kill(agent.pid, 0)
                    agents.append(agent)
                except (OSError, ProcessLookupError):
                    pass
            except Exception:
                pass
        return agents

    def get_recent_messages(self, limit: int = 30) -> List[Dict]:
        """Get recent messages from all vault streams."""
        all_entries = []
        vaults_dir = get_vaults_dir()
        if not vaults_dir.exists():
            return []

        for vault_dir in vaults_dir.iterdir():
            if not vault_dir.is_dir():
                continue
            stream = find_active_stream(vault_dir)
            if not stream.exists():
                continue

            identity = vault_dir.name
            last_ts = self._last_stream_ts.get(identity, 0)

            try:
                with open(stream) as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            ts = entry.get("ts", 0)
                            if ts > last_ts:
                                entry["_identity"] = identity
                                all_entries.append(entry)
                        except json.JSONDecodeError:
                            continue
                # Update last seen timestamp
                if all_entries:
                    max_ts = max(
                        e.get("ts", 0)
                        for e in all_entries
                        if e.get("_identity") == identity
                    )
                    if max_ts > last_ts:
                        self._last_stream_ts[identity] = max_ts
            except Exception:
                pass

        # Add new entries to buffer
        self._message_buffer.extend(all_entries)
        # Sort by timestamp
        self._message_buffer.sort(key=lambda e: e.get("ts", 0))
        # Trim buffer
        if len(self._message_buffer) > self._max_messages:
            self._message_buffer = self._message_buffer[-self._max_messages :]

        return self._message_buffer[-limit:]

    def generate_feed_lines(self, width: int, height: int) -> List[str]:
        """Generate the full feed display."""
        lines = []

        # Header
        agents = self.get_live_agents()
        lines.append(_bold("  hub feed"))
        lines.append("")

        # Roster section
        lines.append(_dim("  roster:"))
        if agents:
            for agent in agents:
                r, g, b = _color(agent.identity)
                coord = " *" if agent.is_coordinator else ""
                state_icon = "+" if agent.state == "working" else "-"
                task = f" {agent.current_task[:50]}" if agent.current_task else ""

                name_str = _fg(f"  {state_icon} {agent.identity}{coord}", r, g, b)
                state_str = _dim(f" {agent.state}{task}")
                lines.append(f"{name_str}{state_str}")
        else:
            lines.append(_dim("    no agents online"))

        lines.append("")

        # Message feed section
        lines.append(_dim("  channel:"))

        messages = self.get_recent_messages(limit=height - len(lines) - 4)

        if not messages:
            lines.append(_dim("    no messages yet"))
        else:
            for entry in messages:
                entry_type = entry.get("type", "")
                identity = entry.get("_identity", entry.get("identity", "?"))
                content = entry.get("content", "")[: width - 20]
                from_agent = entry.get("from", "")
                to_agent = entry.get("to", "")
                ts = entry.get("ts", 0)
                time_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""

                r, g, b = _color(identity)

                if entry_type == "sent":
                    target = to_agent or "?"
                    tr, tg, tb = _color(target)
                    line = (
                        f"  {_dim(time_str)} "
                        f"{_fg(identity, r, g, b)}"
                        f"{_dim(' -> ')}"
                        f"{_fg(target, tr, tg, tb)}"
                        f" {content[:width - 40]}"
                    )
                    lines.append(line)

                elif entry_type == "received":
                    sender = from_agent or "?"
                    sr, sg, sb = _color(sender)
                    line = (
                        f"  {_dim(time_str)} "
                        f"{_fg(sender, sr, sg, sb)}"
                        f"{_dim(' -> ')}"
                        f"{_fg(identity, r, g, b)}"
                        f" {content[:width - 40]}"
                    )
                    lines.append(line)

                elif entry_type == "session_start":
                    lines.append(
                        f"  {_dim(time_str)} "
                        f"{_fg('+', 100, 200, 100)} "
                        f"{_fg(identity, r, g, b)} came online"
                    )

                elif entry_type == "session_end":
                    lines.append(
                        f"  {_dim(time_str)} "
                        f"{_fg('-', 200, 100, 100)} "
                        f"{_fg(identity, r, g, b)} went offline"
                    )

                elif entry_type == "user_input":
                    lines.append(
                        f"  {_dim(time_str)} "
                        f"{_fg(identity, r, g, b)}"
                        f"{_dim(' received task: ')}"
                        f"{content[:width - 40]}"
                    )

        # Footer
        lines.append("")
        lines.append(
            _dim(
                f"  {len(agents)} agent(s) online | {len(self._message_buffer)} message(s) | refreshing..."
            )
        )

        return lines

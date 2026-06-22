"""Hub Console AltView -- agent management with sidebar + live feed.

Two-pane layout modeled after conversations_altview:
  Left sidebar: list of online agents with status and identity
  Right panel: selected agent's live feed (vault stream + hub messages)

Controls:
  up/down: select agent in sidebar
  tab: toggle active pane (sidebar vs feed)
  enter: attach to selected agent (interactive -- type to send messages)
  escape: detach / exit back to main session
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)

# Matches all ANSI escape sequences (CSI, OSC, and single-char Fe sequences)
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;]*[mA-Za-z]?|\][^\x07]*\x07?|[^[])")


class HubConsoleAltView(AltView):
    """AltView for browsing and managing hub agents."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="hub-console",
            description="Hub agent console",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[HC]",
            aliases=["console"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 2.0
        self.render_on_timer = True

        # State
        self.agents: List[Dict[str, Any]] = []
        self.feed_lines: List[str] = []
        self._feed_is_raw = False  # True when feed_lines contain ANSI (socket capture)
        self.selected_idx = 0
        self.active_pane = "agents"
        self.agent_scroll = 0
        self.feed_scroll = 0

        # Attach state
        self.attached_to: Optional[str] = None
        self._attached_socket: Optional[str] = None

        # Interactive input buffer (used when attached to an agent)
        self._input_buffer: str = ""
        self._my_identity: str = ""

        # Layout
        self.left_width = 28

        # References
        self._event_bus: Any = None
        self._last_refresh: float = 0.0

    def set_event_bus(self, event_bus: Any) -> None:
        """Set event bus for service lookups."""
        self._event_bus = event_bus

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self.selected_idx = 0
        self.feed_scroll = 0
        self.agent_scroll = 0
        self.active_pane = "agents"
        self._input_buffer = ""
        self._resolve_my_identity()
        self._refresh_agents()
        self._refresh_feed()

    def _resolve_my_identity(self) -> None:
        """Resolve the display name for console-sent messages.

        Uses plugins.hub.user_name config (same name shown in hub broadcasts),
        falling back to $USER. Never uses the hub agent designation — that's
        for agent-to-agent routing, not human identity.
        """
        import os

        default = os.environ.get("USER", "user")
        if not self._event_bus:
            self._my_identity = default
            return
        try:
            hub_plugin = self._event_bus.get_service("hub_plugin")
            if hub_plugin and hub_plugin.config:
                self._my_identity = hub_plugin.config.get(
                    "plugins.hub.user_name", default
                )
            else:
                self._my_identity = default
        except Exception:
            self._my_identity = default

    # -- data loading --

    def _refresh_agents(self) -> None:
        """Load live agents from hub presence."""
        import os

        from plugins.hub.presence import get_presence_dir

        agents = []
        presence_dir = get_presence_dir()
        if not presence_dir.exists():
            self.agents = []
            return

        for f in presence_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                pid = data.get("pid", 0)
                try:
                    os.kill(pid, 0)
                except (OSError, ProcessLookupError):
                    continue
                agents.append(data)
            except Exception:
                continue

        # Sort: coordinator first, then alphabetical
        agents.sort(
            key=lambda a: (not a.get("is_coordinator", False), a.get("identity", ""))
        )
        self.agents = agents

    def _refresh_feed(self) -> None:
        """Schedule a feed refresh for the selected agent.

        Tries live socket capture first (same data as `kollab --hub capture`).
        Falls back to vault stream JSONL for self or when socket is unavailable.
        """
        if not self.agents or self.selected_idx >= len(self.agents):
            self.feed_lines = ["(no agent selected)"]
            return

        agent = self.agents[self.selected_idx]
        socket_path = agent.get("socket_path", "")
        ident = agent.get("identity", "?")

        # Don't socket-capture self — coordinator runs in-process, no get_output
        is_self = ident == self._my_identity

        if socket_path and not is_self:
            asyncio.ensure_future(self._fetch_feed_from_socket(ident, socket_path))
        else:
            self._load_vault_stream(ident)

    async def _fetch_feed_from_socket(self, ident: str, socket_path: str) -> None:
        """Fetch live output from agent socket (same as `--hub capture`)."""
        from plugins.hub.messenger import AgentMessenger

        try:
            raw = await AgentMessenger.request_output(socket_path, lines=100)
        except Exception:
            raw = []

        if raw:
            # Each element may be a multi-line rendered block; split on newlines.
            # Keep ANSI codes — _render_feed_panel will write them raw.
            lines = []
            for block in raw:
                for line in str(block).split("\n"):
                    lines.append(line.rstrip("\r"))
            self.feed_lines = lines
            self._feed_is_raw = True
        else:
            # Socket returned nothing — fall back to vault stream
            self._load_vault_stream(ident)

    def _load_vault_stream(self, ident: str) -> None:
        """Load feed from persisted vault stream JSONL."""
        from plugins.hub.vault import get_vaults_dir

        vault_dir = get_vaults_dir() / ident

        try:
            from plugins.hub.vault import find_active_stream

            stream = find_active_stream(vault_dir)
        except ImportError:
            stream = vault_dir / "stream.jsonl"

        lines = []
        if stream.exists():
            try:
                with open(stream) as f:
                    entries = f.readlines()
                max_line = 120
                for entry_str in entries[-600:]:
                    try:
                        entry = json.loads(entry_str.strip())
                        ts = entry.get("ts", 0)
                        etype = entry.get("type", "?")
                        content = entry.get("content", "")
                        from_agent = entry.get("from", "")
                        to_agent = entry.get("to", "")

                        time_str = time.strftime("%H:%M:%S", time.localtime(ts))

                        if etype in ("sent", "received"):
                            direction = "->" if etype == "sent" else "<-"
                            other = to_agent if etype == "sent" else from_agent
                            lines.append(f"{time_str} {direction} {other}")
                            for cline in content.split("\n")[:5]:
                                lines.append(f"  {cline[:max_line]}")
                        elif etype == "response":
                            lines.append(f"{time_str} [response]")
                            for cline in content.split("\n")[:4]:
                                lines.append(f"  {cline[:max_line]}")
                        elif etype == "user_input":
                            lines.append(f"{time_str} [input]")
                            lines.append(f"  {content[:max_line]}")
                        elif etype in ("session_start", "session_end"):
                            lines.append(f"{time_str} -- {content[:max_line]}")
                        else:
                            lines.append(f"{time_str} [{etype}] {content[:max_line]}")
                    except Exception:
                        continue
            except Exception:
                lines = ["(error reading stream)"]
        else:
            lines = ["(no vault stream for this agent)"]

        self.feed_lines = lines if lines else ["(empty feed)"]
        self._feed_is_raw = False

    # -- rendering --

    async def render_frame(self, delta_time: float) -> bool:
        if not self.renderer:
            return False

        # Refresh data every 2 seconds
        now = time.monotonic()
        if now - self._last_refresh > 2.0:
            self._refresh_agents()
            self._refresh_feed()
            self._last_refresh = now

        width, height = self.renderer.get_terminal_size()
        theme = T()

        self.left_width = min(30, max(22, width // 4))
        right_width = width - self.left_width - 1

        # Header
        self._render_header(width, right_width, theme)

        # Content
        content_height = height - 3
        self._render_agent_sidebar(content_height, theme)
        self._render_feed_panel(content_height, right_width, theme)

        # Separator — paint bg=dark fg=primary so the cell background matches the
        # theme instead of bleeding through with terminal-default colors.
        sep_char = solid(str(C["line_v"]), theme.dark[0], theme.primary[0], 1)
        for row in range(2, 2 + content_height):
            self.renderer.write_at(self.left_width, row, sep_char, "")

        # Footer
        self._render_footer(width, height, theme)

        return True

    def _render_header(self, width: int, right_width: int, theme: Any) -> None:
        assert self.renderer is not None
        agent_count = len(self.agents)
        left_title = f" Agents ({agent_count}) "

        if self.active_pane == "agents":
            left_hdr = solid(
                left_title.ljust(self.left_width),
                theme.primary[0],
                theme.text_dark,
                self.left_width,
            )
        else:
            left_hdr = solid(
                left_title.ljust(self.left_width),
                theme.dark[0],
                theme.text_dim,
                self.left_width,
            )

        # Right header
        if self.attached_to:
            right_title = f" {self.attached_to} (attached) "
        elif self.agents and self.selected_idx < len(self.agents):
            agent = self.agents[self.selected_idx]
            ident = agent.get("identity", "?")
            state = agent.get("state", "?")
            right_title = f" {ident} ({state}) "
        else:
            right_title = " (none) "

        if self.active_pane == "feed":
            right_hdr = solid(
                right_title.ljust(right_width),
                theme.primary[0],
                theme.text_dark,
                right_width,
            )
        else:
            right_hdr = solid(
                right_title.ljust(right_width),
                theme.dark[0],
                theme.text_dim,
                right_width,
            )

        self.renderer.write_at(0, 0, left_hdr, "")
        sep = solid_fg(C["line_v"], theme.primary[0])
        self.renderer.write_at(self.left_width, 0, sep, "")
        self.renderer.write_at(self.left_width + 1, 0, right_hdr, "")

        # Subheader line
        self.renderer.write_at(
            0,
            1,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )

    def _render_agent_sidebar(self, content_height: int, theme: Any) -> None:
        assert self.renderer is not None
        from plugins.hub.models import GEM_BY_NAME

        visible_count = content_height
        if self.selected_idx < self.agent_scroll:
            self.agent_scroll = self.selected_idx
        elif self.selected_idx >= self.agent_scroll + visible_count:
            self.agent_scroll = self.selected_idx - visible_count + 1

        for i in range(visible_count):
            row = i + 2
            idx = i + self.agent_scroll

            if idx < len(self.agents):
                agent = self.agents[idx]
                ident = agent.get("identity", "?")
                state = agent.get("state", "idle")
                coord = "*" if agent.get("is_coordinator", False) else " "
                state_icon = "+" if state == "working" else "-"

                line = f" {state_icon}{coord}{ident}"
                line = line[: self.left_width - 1].ljust(self.left_width - 1)

                is_selected = idx == self.selected_idx

                # Get gem color
                gem = GEM_BY_NAME.get(ident)
                if gem and is_selected:
                    bg = gem.color_rgb
                    fg = theme.text_on(bg) if hasattr(theme, "text_on") else theme.text
                elif is_selected:
                    bg = theme.primary[0]
                    fg = theme.text_on(bg) if hasattr(theme, "text_on") else theme.text
                else:
                    bg = theme.dark[0]
                    fg = theme.text_dim if state == "idle" else theme.text

                self.renderer.write_at(0, row, solid(line, bg, fg, self.left_width), "")
            else:
                empty = " " * self.left_width
                self.renderer.write_at(
                    0, row, solid(empty, theme.dark[0], theme.text, self.left_width), ""
                )

    def _render_feed_panel(
        self, content_height: int, right_width: int, theme: Any
    ) -> None:
        assert self.renderer is not None

        lines = self.feed_lines
        total = len(lines)

        # Scroll from bottom by default
        if self.feed_scroll == 0:
            start = max(0, total - content_height)
        else:
            start = max(0, total - content_height - self.feed_scroll)

        visible = lines[start : start + content_height]

        x_offset = self.left_width + 1

        for i in range(content_height):
            row = i + 2
            if i < len(visible):
                line = visible[i]
            else:
                line = ""

            if self._feed_is_raw and line:
                # Measure visible (non-ANSI) width so we can pad correctly
                visible_text = _ANSI_RE.sub("", line)
                vis_len = len(visible_text)
                # Truncate to right_width visible chars
                if vis_len > right_width:
                    kept, count = [], 0
                    for tok in re.split(
                        r"(\x1b(?:\[[0-9;]*[mA-Za-z]?|\][^\x07]*\x07?|[^[]]))", line
                    ):
                        if _ANSI_RE.fullmatch(tok):
                            kept.append(tok)
                        else:
                            remaining = right_width - count
                            kept.append(tok[:remaining])
                            count += len(tok[:remaining])
                            if count >= right_width:
                                break
                    line = "".join(kept)
                    vis_len = right_width

                padding = " " * (right_width - vis_len)
                # Write content with agent's own ANSI colors; fill the remaining
                # width with the theme dark background so no raw terminal default
                # shows through on the right side of the panel.
                dr, dg, db = theme.dark[0]
                dark_bg = f"\033[48;2;{dr};{dg};{db}m"
                self.renderer.move_cursor(x_offset, row)
                self.renderer.write_raw(f"{line}\033[0m{dark_bg}{padding}\033[0m")
            else:
                plain = line[:right_width].ljust(right_width)
                self.renderer.write_at(
                    x_offset,
                    row,
                    solid(plain, theme.dark[0], theme.text, right_width),
                    "",
                )

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        assert self.renderer is not None
        if self.attached_to:
            if self._input_buffer:
                # Show input buffer as a prompt line
                prompt = f" > {self._input_buffer}"
                cursor = "_"
                avail = width - len(prompt) - 1
                if avail > 0:
                    footer_line = (prompt + cursor).ljust(width)[:width]
                else:
                    # Scroll input to show end of buffer
                    visible = self._input_buffer[-(width - 5) :]
                    footer_line = (f" > {visible}{cursor}").ljust(width)[:width]
                self.renderer.write_at(
                    0,
                    height - 1,
                    solid(footer_line, theme.dark[0], theme.text, width),
                    "",
                )
            else:
                footer = (
                    f" {self.attached_to} | type to send | "
                    f"esc: detach | up/down: scroll "
                )
                footer_line = footer.center(width)
                self.renderer.write_at(
                    0,
                    height - 1,
                    solid(footer_line, theme.dark[1], theme.text_dim, width),
                    "",
                )
        else:
            footer = " up/down: select | tab: switch pane | enter: attach | esc: exit "
            footer_line = footer.center(width)
            self.renderer.write_at(
                0,
                height - 1,
                solid(footer_line, theme.dark[1], theme.text_dim, width),
                "",
            )

    # -- input --

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name in ("Escape", "Ctrl+C"):
            if self.attached_to:
                self._detach()
                return False
            return True

        # When attached, route keyboard input to the input buffer
        if self.attached_to:
            return await self._handle_attached_input(key_press)

        if key_press.name == "Tab":
            self.active_pane = "feed" if self.active_pane == "agents" else "agents"
            return False

        if self.active_pane == "agents":
            if key_press.name == "ArrowUp":
                self.selected_idx = max(0, self.selected_idx - 1)
                self._refresh_feed()
                return False
            elif key_press.name == "ArrowDown":
                self.selected_idx = min(len(self.agents) - 1, self.selected_idx + 1)
                self._refresh_feed()
                return False
            elif key_press.name == "Enter":
                if self.agents and self.selected_idx < len(self.agents):
                    agent = self.agents[self.selected_idx]
                    socket_path = agent.get("socket_path", "")
                    ident = agent.get("identity", "?")
                    if socket_path:
                        self._attach_to_agent(ident, socket_path)
                else:
                    self.active_pane = "feed"
                return False

        elif self.active_pane == "feed":
            if key_press.name == "ArrowUp":
                self.feed_scroll = min(self.feed_scroll + 3, len(self.feed_lines))
                return False
            elif key_press.name == "ArrowDown":
                self.feed_scroll = max(0, self.feed_scroll - 3)
                return False

        return False

    async def _handle_attached_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input when attached to an agent."""
        if key_press.name == "Enter":
            if self._input_buffer.strip():
                await self._send_attached_message(self._input_buffer.strip())
                self._input_buffer = ""
            return False
        elif key_press.name == "Backspace":
            if self._input_buffer:
                self._input_buffer = self._input_buffer[:-1]
            return False
        elif key_press.name == "ArrowUp":
            self.feed_scroll = min(self.feed_scroll + 3, len(self.feed_lines))
            return False
        elif key_press.name == "ArrowDown":
            self.feed_scroll = max(0, self.feed_scroll - 3)
            return False
        elif key_press.char and len(key_press.char) == 1 and ord(key_press.char) >= 32:
            # Printable character
            self._input_buffer += key_press.char
            return False
        return False

    async def _send_attached_message(self, content: str) -> None:
        """Send a message to the attached agent via hub socket."""
        if not self._attached_socket or not self.attached_to:
            return

        from plugins.hub.messenger import AgentMessenger
        from plugins.hub.models import HubMessage

        sender = self._my_identity or "console"
        msg = HubMessage(
            action="message",
            from_identity=sender,
            to=self.attached_to,
            content=content,
        )

        try:
            ok = await AgentMessenger.send_to_agent(self._attached_socket, msg)
            if ok:
                ts = time.strftime("%H:%M:%S")
                self.feed_lines.append(f"{ts} -> {self.attached_to}")
                self.feed_lines.append(f"  {content[:70]}")
                self.feed_scroll = 0
            else:
                self.feed_lines.append("(send failed)")
        except Exception as exc:
            self.feed_lines.append(f"(send error: {exc})")

    def _attach_to_agent(self, ident: str, socket_path: str) -> None:
        """Attach to an agent -- start streaming its output into the feed panel."""
        self.attached_to = ident
        self._attached_socket = socket_path
        self.active_pane = "feed"
        self.feed_scroll = 0
        self.feed_lines = [f"attaching to {ident}..."]

        # Kick off initial fetch via the unified refresh path
        asyncio.ensure_future(self._fetch_feed_from_socket(ident, socket_path))

    def _detach(self) -> None:
        """Detach from the currently attached agent."""
        self.attached_to = None
        self._attached_socket = None
        self._input_buffer = ""
        self.active_pane = "agents"
        self._refresh_feed()

    async def on_complete(self) -> None:
        self.agents = []
        self.feed_lines = []
        self.attached_to = None
        self._attached_socket = None
        self._input_buffer = ""

"""Voice classifier log: each decision on the left, what it saw and said on the right.

Opened by ``/voicemode classifier``. Reads ``voice_plugin.classifier_log()``,
the plugin's record of every classifier call (oldest first), and refreshes
while open so a new decision shows up as you speak.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg, wrap_text
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.status.core_widgets import VOICE_DECISION_WORDS

logger = logging.getLogger(__name__)


def _word(entry: Dict[str, Any]) -> str:
    if entry.get("echo"):
        return "echo"
    if entry.get("error"):
        return "error"
    decision = (entry.get("decision") or {}).get("decision")
    return VOICE_DECISION_WORDS.get(decision, "pending")


def _clock(at: Optional[float]) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at)) if at else "--:--:--"


def detail_lines(entry: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The right pane as (style, text) rows: heading, text, dim, good, warn, bad."""
    lines: List[Tuple[str, str]] = []
    decision = entry.get("decision") or {}
    records = entry.get("records") or []
    name = "Laya" if entry.get("classifier") == "laya" else "AI provider"

    lines.append(("heading", "Heard"))
    for record in records:
        lines.append(("text", f'  "{record.get("text", "")}"'))
        for overlap in record.get("playback_overlap") or []:
            lines.append(("warn", f'  while the agent said: "{overlap.get("text", "")}"'))

    if entry.get("echo"):
        lines.append(("heading", "Not classified"))
        lines.append(("dim", "  The microphone heard the agent's own voice."))
        lines.append(("heading", "Sent to the agent"))
        lines.append(("dim", "  nothing"))
        return lines

    lines.append(("heading", f"{name} saw"))
    traces = decision.get("trace") or []
    if traces:
        for trace in traces:
            state = trace.get("state") or {}
            for key, value in state.items():
                lines.append(("dim", f"  {key}"))
                for item in value if isinstance(value, list) else str(value).splitlines() or [""]:
                    lines.append(("text", f"    {item}"))
    else:
        history = entry.get("transcript_context") or []
        lines.append(("dim", f"  previous transcript lines ({len(history)})"))
        lines.extend(("text", f"    {r.get('text', '')}") for r in history)
        if entry.get("context"):
            lines.append(("dim", "  recent conversation"))
            lines.extend(("text", f"    {row}") for row in entry["context"].splitlines())

    lines.append(("heading", f"{name} answered"))
    for trace in traces:
        for p in trace.get("passes") or []:
            lines.append(("text", f"  {p.get('label', ''):<14} {p.get('choice', ''):<8} {p.get('probability', 0):.2f}"))
    if entry.get("error"):
        lines.append(("bad", f"  {entry['error']}"))
    elif decision:
        parts = [f"{_word(entry)} ({decision.get('decision')})"]
        if decision.get("confidence") is not None:
            parts.append(f"confidence {decision['confidence']:.2f}")
        if decision.get("model"):
            parts.append(decision["model"])
        if entry.get("seconds") is not None:
            parts.append(f"{entry['seconds']:.2f}s")
        lines.append(("text", "  " + " · ".join(parts)))
        if decision.get("detail"):
            lines.append(("dim", f"  {decision['detail']}"))

    lines.append(("heading", "Sent to the agent"))
    sent = set(entry.get("delivered") or [])
    unsure = set(decision.get("deferred_event_ids") or [])
    if not sent:
        lines.append(("dim", "  nothing"))
    if entry.get("note"):
        lines.append(("warn", f"  {entry['note']}"))
    for record in records:
        if record.get("event_id") in sent:
            how = "as unsure speech" if record["event_id"] in unsure else "as speech"
            style = "warn" if record["event_id"] in unsure else "good"
            lines.append((style, f'  {how}: "{record.get("text", "")}"'))
    return lines


class VoiceClassifierAltView(AltView):
    """Two panes: classifier events, and the selected event's request and answer."""

    def __init__(self) -> None:
        super().__init__(
            AltViewMetadata(
                plugin_type="voice-classifier",
                description="Voice classifier decisions",
                version="1.0.0",
                author="Kollabor",
                category="internal",
                icon="[VC]",
                aliases=[],
                supports_named_sessions=False,
                supports_background=False,
            )
        )
        self.target_fps = 2.0
        self.render_on_timer = True
        self.plugin: Any = None
        self.entries: List[Dict[str, Any]] = []
        self.selected_seq: Optional[int] = None  # None follows the newest event
        self.active_pane = "events"
        self.event_scroll = 0
        self.detail_scroll = 0
        self.left_width = 34

    def set_plugin(self, plugin: Any) -> None:
        self.plugin = plugin

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self.selected_seq, self.active_pane = None, "events"
        self.event_scroll = self.detail_scroll = 0

    # -- data --

    def _refresh(self) -> None:
        try:
            self.entries = list(reversed(self.plugin.classifier_log())) if self.plugin else []
        except Exception as e:
            logger.debug("voice classifier log unavailable: %s", e)
            self.entries = []

    def _selected_idx(self) -> int:
        for idx, entry in enumerate(self.entries):
            if entry.get("seq") == self.selected_seq:
                return idx
        return 0

    def _select(self, idx: int) -> None:
        idx = max(0, min(idx, len(self.entries) - 1))
        # The top row follows new events as they arrive.
        self.selected_seq = self.entries[idx].get("seq") if idx and self.entries else None
        self.detail_scroll = 0

    # -- rendering --

    async def render_frame(self, delta_time: float) -> bool:
        if not self.renderer:
            return False
        self._refresh()
        width, height = self.renderer.get_terminal_size()
        theme = T()
        self.left_width = min(40, max(24, width // 3))
        right_width = width - self.left_width - 1
        content_height = height - 4
        selected = self._selected_idx()
        if selected < self.event_scroll:
            self.event_scroll = selected
        elif selected >= self.event_scroll + content_height:
            self.event_scroll = selected - content_height + 1

        self._render_headers(right_width, selected, theme)
        self._render_events(content_height, selected, theme)
        self._render_detail(content_height, right_width, selected, theme)
        for row in range(2, 2 + content_height):
            self.renderer.write_at(self.left_width, row, C["line_v"], "")
        self._render_footer(width, height, theme)
        return True

    def _pane_header(self, text: str, width: int, active: bool, theme: Any) -> str:
        if active:
            return solid(text.ljust(width), theme.primary[0], theme.text_dark, width)
        return solid(text.ljust(width), theme.dark[0], theme.text_dim, width)

    def _render_headers(self, right_width: int, selected: int, theme: Any) -> None:
        assert self.renderer is not None
        if self.entries:
            entry = self.entries[selected]
            seconds = entry.get("seconds")
            right = f" {_clock(entry.get('at'))} · {_word(entry)}"
            if seconds is not None:
                right += f" · {seconds:.2f}s"
        else:
            right = " No classifier calls yet"
        events_active = self.active_pane == "events"
        panes = ((0, self.left_width, events_active), (self.left_width + 1, right_width, not events_active))
        for x, width, active in panes:
            edge = theme.primary[0] if active else theme.dark[0]
            self.renderer.write_at(x, 0, solid_fg(str(C["half_bottom"]) * width, edge), "")
        left = self._pane_header(f" Voice Events {len(self.entries)}", self.left_width, events_active, theme)
        right_header = self._pane_header(right, right_width + 1, not events_active, theme)
        self.renderer.write_at(0, 1, left + " " + right_header, "")

    def _render_events(self, content_height: int, selected: int, theme: Any) -> None:
        assert self.renderer is not None
        colors = {"sent": theme.success[0], "unsure": theme.warning[0], "error": theme.error[0]}
        for row in range(content_height):
            idx = self.event_scroll + row
            if idx >= len(self.entries):
                blank = solid(" " * self.left_width, theme.dark[0], theme.text_dim, self.left_width)
                self.renderer.write_at(0, 2 + row, blank, "")
                continue
            entry = self.entries[idx]
            heard = " ".join(r.get("text", "") for r in entry.get("records") or [])
            arrow = C["arrow_right"] if idx == selected else " "
            line = f" {arrow} {_clock(entry.get('at'))} {_word(entry):<7} {heard}"
            line = line[: self.left_width].ljust(self.left_width)
            if idx == selected and self.active_pane == "events":
                self.renderer.write_at(0, 2 + row, solid(line, theme.primary[0], theme.text_dark, self.left_width), "")
            else:
                bg = theme.dark[1] if idx == selected else theme.dark[0]
                fg = colors.get(_word(entry), theme.text_dim)
                self.renderer.write_at(0, 2 + row, solid(line, bg, fg, self.left_width), "")

    def _detail_rows(self, width: int, selected: int) -> List[Tuple[str, str]]:
        if not self.entries:
            if self.plugin is None or not getattr(self.plugin, "requested", False):
                return [("dim", "Voice is off. /voicemode on, then speak.")]
            return [("dim", "Speak; each classifier decision shows here.")]
        rows: List[Tuple[str, str]] = []
        for style, text in detail_lines(self.entries[selected]):
            indent = len(text) - len(text.lstrip())
            for part in wrap_text(text.strip(), max(8, width - indent)) or [""]:
                rows.append((style, " " * indent + part))
        return rows

    def _render_detail(self, content_height: int, pane_width: int, selected: int, theme: Any) -> None:
        assert self.renderer is not None
        rows = self._detail_rows(pane_width - 2, selected)
        self.detail_scroll = max(0, min(self.detail_scroll, len(rows) - 1))
        fg = {
            "heading": theme.primary[0],
            "text": theme.text,
            "dim": theme.text_dim,
            "good": theme.success[0],
            "warn": theme.warning[0],
            "bad": theme.error[0],
        }
        x = self.left_width + 1
        for row in range(content_height):
            idx = self.detail_scroll + row
            style, text = rows[idx] if idx < len(rows) else ("dim", "")
            display = (" " + text)[:pane_width].ljust(pane_width)
            self.renderer.write_at(x, 2 + row, solid(display, theme.dark[0], fg[style], pane_width), "")

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        assert self.renderer is not None
        name = getattr(self.plugin, "classifier_name", "") or "none"
        hints = (
            f" Classifier: {name} (/voicemode classifier laya|provider)"
            " · Tab: switch · Up/Down: move · q/Esc: close"
        )
        self.renderer.write_at(0, height - 2, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), "")
        footer = solid(hints[:width].ljust(width), theme.dark[1], theme.text_dim, width)
        self.renderer.write_at(0, height - 1, footer, "")

    # -- input --

    async def handle_input(self, key_press: KeyPress) -> bool:
        """True closes the view."""
        if key_press.name == "Escape" or key_press.char in ("q", "\x1b"):
            return True
        if key_press.name == "Tab" or key_press.char == "\t":
            self.active_pane = "detail" if self.active_pane == "events" else "events"
            return False
        step = {"ArrowUp": -1, "ArrowDown": 1, "PageUp": -10, "PageDown": 10}.get(key_press.name or "")
        if step is None:
            return False
        if self.active_pane == "events" and abs(step) == 1:
            self._select(self._selected_idx() + step)
        else:
            self.detail_scroll = max(0, self.detail_scroll + step)
        return False

"""The shared look of the Connect, join and Knocks screens.

A branded title bar, dim labels over bright values, the join code as a chip,
colored status words and colored keys. Painting only adds color: each function
returns the same visible text it was given, so the plain line builders (and
their width tests) stay the source of truth for what a screen says.
"""

from __future__ import annotations

import re
from typing import Any

from kollabor_tui.design_system import S, T, TagBox, solid, solid_fg

# A join code as the screens show it (enrollment_codes' display alphabet).
CODE_RE = re.compile(r"(?<![0-9A-Za-z-])[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}(?![0-9A-Za-z-])")

# Value parts, first match wins: (pattern, style).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_VALUE_RULES = [
    (re.compile(r"^\S+ \(offline\)$"), "dim"),
    (re.compile(r"expires in 0:\d\d"), "warn"),
    (re.compile(r"one device, expires in \d+:\d\d"), "dim"),
    (re.compile(r"^(?:expired|used|can't connect|could not create a code)\b"), "warn"),
    (re.compile(r"\bwants to join\b"), "accent"),
    (re.compile(r"\bthis device is offline\b"), "warn"),
    (re.compile(r"^ ?> "), "accent"),
    (re.compile(r"\[\w\]"), "accent"),
    (re.compile(r"\((?:this device|offline|enter privately)\)"), "dim"),
    (re.compile(r"\bvia \S+|\bfingerprint \S+|\bdevice ID \S+|\btrust:|/connect \w+|@\S+"), "dim"),
    (re.compile(r"\bpress c (?:for a new code|to try again)\b"), "dim"),
    (re.compile(r"^(?:none|loading…|creating…|unnamed)$"), "dim"),
]


def _style(text: str, style: str) -> str:
    theme = T()
    if not text:
        return ""
    if style == "accent":
        return S.BOLD + solid_fg(text, theme.primary[0])
    if style == "warn":
        return solid_fg(text, theme.warning[0])
    if style == "good":
        return S.BOLD + solid_fg(text, theme.success[0])
    if style == "bad":
        return solid_fg(text, theme.error[0])
    if style == "dim":
        return solid_fg(text, theme.text_dim)
    if style == "strong":
        return S.BOLD + solid_fg(text, theme.text)
    return solid_fg(text, theme.text)


def paint_value(text: str) -> str:
    """A row's value: bright, with its quiet and loud parts colored."""
    spans: list[tuple[int, int, str]] = []
    for pattern, style in _VALUE_RULES:
        for match in pattern.finditer(text):
            start, end = match.span()
            if start < end and all(end <= s or start >= e for s, e, _ in spans):
                spans.append((start, end, style))
    out, at = [], 0
    for start, end, style in sorted(spans):
        out += [_style(text[at:start], "plain"), _style(text[start:end], style)]
        at = end
    out.append(_style(text[at:], "plain"))
    return "".join(out)


def paint_row(line: str, head_width: int, labels: frozenset[str]) -> str | None:
    """`label   value` (label column `head_width` wide), or None if not a row.

    A row's label is one of ``labels`` (a leading ``>`` marks the focused
    field); a blank label continues the row above. The join code becomes a
    chip that borrows the spaces on either side, so the width never changes.
    """
    head, body = line[:head_width], line[head_width:]
    label = head.strip().lstrip(">").strip()
    if label not in labels and (head.strip() or not body.strip()):
        return None
    if head.lstrip().startswith(">"):
        marker_at = head.index(">")
        painted_head = (
            head[:marker_at]
            + _style(">", "accent")
            + _style(head[marker_at + 1 :], "strong")
        )
    else:
        painted_head = _style(head, "dim")
    code = CODE_RE.match(body)
    if code and head.endswith(" ") and body[code.end() : code.end() + 1] == " ":
        chip = solid(" " + code.group(0) + " ", T().primary[0], T().text_dark)
        return _style(head[:-1], "dim") + chip + paint_value(body[code.end() + 1 :])
    return painted_head + paint_value(body)


def paint_keys(line: str) -> str:
    """A key hint line (`c new code  esc close`): keys loud, words quiet."""
    out = []
    for part in re.split(r"(\s{2,})", line):
        if not part.strip():
            out.append(part)
            continue
        lead = part[: len(part) - len(part.lstrip())]
        key, space, words = part.strip().partition(" ")
        out.append(lead + _style(key, "accent") + space + _style(words, "dim"))
    return "".join(out)


def paint_tip(line: str) -> str:
    """`no code? run /connect code …`: the question strong, commands loud, the rest quiet."""
    question, mark, rest = line.partition("?")
    if not mark:
        return paint_value(line)
    parts = re.split(r"(/connect(?: \w+)?)", rest)
    painted = "".join(
        _style(part, "accent") if part.startswith("/connect") else _style(part, "dim")
        for part in parts
    )
    return _style(question + mark, "strong") + painted


def paint_status(text: str, kind: str) -> str:
    """A one-line outcome: ``good``, ``bad``, ``accent`` (waiting) or ``plain``."""
    return _style(text, kind)


def draw_header(renderer: Any, top: int, width: int, title: str, subtitle: str = "") -> int:
    """The branded title bar at row ``top``; returns the first row under it."""
    theme = T()
    text = f" {title}"
    if subtitle and len(title) + len(subtitle) + 4 <= width - 4:
        text += f"   {subtitle}"
    box = TagBox.render(
        lines=[text],
        tag_bg=theme.primary[0],
        tag_fg=theme.text_dark,
        tag_width=3,
        content_colors=theme.dark[0],
        content_fg=theme.text,
        content_width=max(1, width - 3),
        tag_chars=[" ◈ "],
        use_gradient=False,
        disable_wrapping=True,
    )
    rows = box.split("\n")
    for offset, row in enumerate(rows):
        renderer.write_at(0, top + offset, row, "")
    if text != f" {title}":
        # TagBox paints one foreground, so the tagline is redrawn dim in place.
        for offset, row in enumerate(rows):
            plain = _ANSI.sub("", row)
            if subtitle in plain:
                dim = solid(subtitle, theme.dark[0], theme.text_dim)
                renderer.write_at(plain.index(subtitle), top + offset, dim, "")
    return top + len(rows)

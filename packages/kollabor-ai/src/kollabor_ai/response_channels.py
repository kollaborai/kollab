"""Separate visible answer data from optional spoken narration, never from tools."""

import re

_TAG = re.compile(
    r"<(/?)([A-Za-z_][\w:-]*)(?:\s+(?:\"[^\"]*\"|'[^']*'|[^'\">])*)?\s*(/?)>"
)


def protect_response_channels(text, restore, *, partial=False):
    """Protect display payloads from tool parsing and remove narration from display.

    The caller masks code first. Only top-level delivery fields are recognized;
    strings inside reasoning or tool calls are never narration.
    """
    stack, spoken, pieces = [], [], []
    offset = 0
    position = 0
    malformed = False
    while match := _TAG.search(text, position):
        closing, name, self_closing = match.groups()
        position = match.end()
        name = name.lower()
        if not stack and not closing and name in {"display_text", "spoken_text"}:
            end = re.search(rf"</{name}\s*>", text[position:], re.IGNORECASE)
            body_end = position + end.start() if end else len(text)
            finish = position + end.end() if end else len(text)
            body = text[position:body_end]
            if partial and end is None:
                last_tag = body.rfind("<")
                if last_tag >= 0 and f"</{name}>".startswith(body[last_tag:]):
                    body = body[:last_tag]
            pieces.append(text[offset : match.start()])
            if name == "display_text":
                key = f"\x00DISPLAY{len(restore)}\x00"
                # Expand masked code before adding the outer display placeholder.
                for token, original in restore.items():
                    body = body.replace(token, original)
                restore[key] = body
                pieces.append(key)
            else:
                for token, original in restore.items():
                    body = body.replace(token, original)
                spoken.append(body.strip())
            malformed |= end is None
            offset = position = finish
        elif closing:
            if stack and stack[-1] == name:
                stack.pop()
        elif not self_closing:
            stack.append(name)
    tail = text[offset:]
    if partial:
        tail = re.sub(r"<(?:/?(?:display_text|spoken_text))?[^>]*$", "", tail)
        # Also hide a field tag split in the middle of its name.
        start = tail.rfind("<")
        if start >= 0 and any(
            tag.startswith(tail[start:])
            for tag in (
                "<display_text>",
                "</display_text>",
                "<spoken_text>",
                "</spoken_text>",
            )
        ):
            tail = tail[:start]
    pieces.append(tail)
    value = (
        spoken[0]
        if len(spoken) == 1 and not malformed
        else "" if spoken or malformed else None
    )
    return "".join(pieces), value


def display_response_text(text, *, partial=False):
    """Visible response for replay or streaming; preserve literal code examples."""
    restore = {}

    def code(match):
        key = f"\x00CODE{len(restore)}\x00"
        restore[key] = match[0]
        return key

    masked = re.sub(r"```.*?(?:```|$)|`[^`\n]*(?:`|$)", code, text, flags=re.S)
    visible, _ = protect_response_channels(masked, restore, partial=partial)
    for key, original in restore.items():
        visible = visible.replace(key, original)
    return visible


def display_stream_text(text):
    """Filter channel metadata from streaming previews, including partial tags."""
    return display_response_text(text, partial=True)

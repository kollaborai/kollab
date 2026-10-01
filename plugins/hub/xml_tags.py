"""Hub XML tags: find a tag by name and read its attributes in any order.

A model writes <hub_msg wait="true" to="lapis"> as readily as <hub_msg to="lapis"
wait="true">, and kind='answer' as readily as kind="answer". A regex that spells
the attributes out in one fixed order takes one of those and neither runs nor
hides the rest: the tag lands on screen as raw text and nothing happens.
``tag_pattern`` matches the attributes in any order and either quote style;
``tag_attrs`` reads them back.
"""

import re
from typing import Dict, Mapping, Optional, Tuple

# One piece of a tag's opening: a character outside quotes, or a whole quoted
# string, so a ">" inside a value does not end the tag. "<" is not allowed
# outside quotes, so a match can never swallow the start of a later tag.
_PIECE = r"""(?:[^<>"']|"[^"]*"|'[^']*')"""
_BARE = r"""[^\s"'<>/=]+"""
_VALUE = rf"""(?:"[^"]*"|'[^']*'|{_BARE})"""

# name=value at a piece boundary after whitespace, or one skipped piece. Walking
# piece by piece keeps the reader on the same boundaries as the pattern: a
# quoted value is never mined for attributes of its own.
_SCAN_RE = re.compile(
    rf"""(?<!\S)([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|({_BARE}))|{_PIECE}"""
)

_TAG_ENDS = {
    "body": r"(?<!/)\s*>(.*?)</{name}>",
    "body?": r"(?<!/)\s*>(.*?)(?:</{name}>|$)",
    "/>": r"\s*/>",
    "/?>": r"\s*/?>",
}


def _value(kind: str) -> str:
    """Regex for one attribute value.

    ``some`` is a non-empty value, ``any`` may be empty, anything else is a
    regex the whole value has to match.
    """
    if kind == "some":
        return rf"""(?:"[^"]+"|'[^']+'|{_BARE})"""
    if kind == "any":
        return _VALUE
    return rf"""(?:"(?:{kind})"|'(?:{kind})'|(?:{kind})(?![^\s<>/]))"""


def tag_pattern(
    name: str,
    *,
    required: Optional[Mapping[str, str]] = None,
    valid: Optional[Mapping[str, str]] = None,
    end: str = "body",
) -> "re.Pattern[str]":
    """Pattern for ``<name a="1" b='2' ...>`` with the attributes in any order.

    required: attributes that must be present, each as ``some`` (non-empty),
        ``any`` (may be empty) or a regex the whole value must match. A tag
        without one does not match at all.
    valid: optional attributes that, when present, must match this regex.
        A tag with a bad value does not match at all.
    end: ``body`` for ``...>text</name>``, ``body?`` when the closing tag may be
        missing, ``/>`` for ``.../>``, ``/?>`` when the slash may be missing.

    Group 1 is the attribute text, for ``tag_attrs``; group 2 is the body.
    """
    # The first occurrence of an attribute is the one tag_attrs reads.
    has = "".join(
        rf"(?=(?:(?!\s{re.escape(attr)}\s*=){_PIECE})*\s{re.escape(attr)}"
        rf"\s*=\s*{_value(kind)})"
        for attr, kind in (required or {}).items()
    )
    # \s*+ is atomic: backing off the space would turn `limit= "3"` into a bad value.
    ok = "".join(
        rf"(?!{_PIECE}*?\s{re.escape(attr)}\s*=\s*+(?!{_value(kind)}))"
        for attr, kind in (valid or {}).items()
    )
    tail = _TAG_ENDS[end].format(name=re.escape(name))
    return re.compile(
        rf"<{re.escape(name)}(?=[\s/>]){has}{ok}({_PIECE}*){tail}",
        re.DOTALL | re.IGNORECASE,
    )


def tag_attrs(text: str) -> Dict[str, str]:
    """Attributes in ``text`` as ``{lowercase name: value}``; the first of a repeat wins."""
    attrs: Dict[str, str] = {}
    for match in _SCAN_RE.finditer(text or ""):
        if match.group(1) is not None:
            value = next(group for group in match.groups()[1:] if group is not None)
            attrs.setdefault(match.group(1).lower(), value)
    return attrs


def embedded_attrs(name: str, text: str) -> Optional[Tuple[Dict[str, str], str]]:
    """Attributes and body of a tag whose text was jammed into one argument.

    A native tool call sometimes puts ``to="lapis" wait="true"`` or a whole
    ``<hub_msg to="lapis">text</hub_msg>`` in a single argument. Returns
    ``(attrs, body)``, or None when ``text`` is not attribute text.
    """
    tag = re.escape(name)
    pair = rf"[\w:-]+\s*=\s*{_VALUE}"
    match = re.fullmatch(
        rf"(?:<{tag}(?=[\s/>])\s*)?({pair}(?:\s+{pair})*)\s*"
        rf"(?:>\s*(.*?)(?:</{tag}>)?)?",
        (text or "").strip(),
        re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        return None
    return tag_attrs(match.group(1)), match.group(2) or ""

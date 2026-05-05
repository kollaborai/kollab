"""Tests for hub_stop attribute syntax + hub_restart tag.

Covers:
  - <hub_stop>name</hub_stop>              body syntax (legacy)
  - <hub_stop identity="name" />           attribute syntax (new)
  - <hub_stop identity="all" />            attr syntax with 'all' target
  - <hub_restart />                        new tag — self-restart
  - extractor functions produce correct dicts for handlers

The handler-level behavior (self-stop watchdog + restart execvp) is
integration-grade and not covered here; these are the regex + extractor
fundamentals.
"""

from __future__ import annotations

import re

# Reproduce the regex from plugin.py — keep them in sync.
HUB_STOP_PAT = re.compile(
    r"<hub_stop"
    r'(?:\s+identity=["\']([^"\']+)["\'])?'
    r"\s*(?:/>|>(.*?)</hub_stop>)",
    re.DOTALL | re.IGNORECASE,
)

HUB_RESTART_PAT = re.compile(
    r"<hub_restart\s*/?>|<hub_restart>\s*</hub_restart>",
    re.IGNORECASE,
)


def _extract_hub_stop(m):
    attr = m.group(1)
    body = m.group(2)
    target = (attr or (body.strip() if body else "") or "").strip()
    return {"target": target}


class TestHubStopBodySyntax:
    def test_simple_body(self):
        text = "before <hub_stop>lapis</hub_stop> after"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}

    def test_body_with_whitespace(self):
        text = "<hub_stop>  lapis  </hub_stop>"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}

    def test_body_all(self):
        text = "<hub_stop>all</hub_stop>"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "all"}


class TestHubStopAttributeSyntax:
    def test_double_quotes(self):
        text = '<hub_stop identity="lapis" />'
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}

    def test_single_quotes(self):
        text = "<hub_stop identity='lapis' />"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}

    def test_identity_all(self):
        text = '<hub_stop identity="all" />'
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "all"}

    def test_attribute_without_self_close(self):
        """<hub_stop identity="lapis"> ... </hub_stop>"""
        text = '<hub_stop identity="lapis"></hub_stop>'
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}

    def test_attribute_takes_precedence_over_body(self):
        """If both attribute and body present, attribute wins."""
        text = '<hub_stop identity="lapis">ignored</hub_stop>'
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": "lapis"}


class TestHubStopEmptyTarget:
    def test_empty_body(self):
        text = "<hub_stop></hub_stop>"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": ""}

    def test_self_closing_no_attr(self):
        """<hub_stop /> with no identity attribute → empty target"""
        text = "<hub_stop />"
        m = HUB_STOP_PAT.search(text)
        assert m is not None
        assert _extract_hub_stop(m) == {"target": ""}


class TestHubRestartTag:
    def test_self_closing_space(self):
        text = "restart: <hub_restart />"
        m = HUB_RESTART_PAT.search(text)
        assert m is not None

    def test_self_closing_tight(self):
        text = "<hub_restart/>"
        m = HUB_RESTART_PAT.search(text)
        assert m is not None

    def test_paired_tags(self):
        text = "<hub_restart></hub_restart>"
        m = HUB_RESTART_PAT.search(text)
        assert m is not None

    def test_case_insensitive(self):
        text = "<HUB_RESTART />"
        m = HUB_RESTART_PAT.search(text)
        assert m is not None

    def test_embedded_in_response(self):
        text = (
            "i'm going to reload my code now.\n"
            "<hub_restart />\n"
            "see you on the other side."
        )
        m = HUB_RESTART_PAT.search(text)
        assert m is not None


class TestHubStopAndRestartDistinct:
    """hub_stop and hub_restart regex must not cross-match."""

    def test_hub_stop_does_not_match_hub_restart(self):
        text = "<hub_restart />"
        assert HUB_STOP_PAT.search(text) is None

    def test_hub_restart_does_not_match_hub_stop(self):
        text = "<hub_stop>self</hub_stop>"
        assert HUB_RESTART_PAT.search(text) is None

    def test_hub_restart_does_not_match_hub_stop_attr(self):
        text = '<hub_stop identity="all" />'
        assert HUB_RESTART_PAT.search(text) is None

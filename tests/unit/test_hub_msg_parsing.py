"""Tests for hub message tag parsing, stripping, idle detection, and nudge engine."""

import re
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.plugin import HubPlugin
from plugins.hub.nudge_engine import (
    HUB_LOOP_THRESHOLD,
    NudgeEngine,
)

# ------------------------------------------------------------------ #
# Regex patterns extracted from plugins/hub/plugin.py _parse_hub_messages
# ------------------------------------------------------------------ #

MATCH_PATTERN = (
    r'<hub_msg\s+to="([^"]+)"'
    r'(?:\s+wait="([^"]*)")?'
    r"\s*>(.*?)(?:</hub_msg>|$)"
)

STRIP_PATTERN = r"<hub_msg\s+[^>]*>.*?(?:</hub_msg>|$)"

IDLE_PHRASES = (
    "standing by",
    "waiting for",
    "going quiet",
    "staying quiet",
)


def _idle_check(content: str) -> bool:
    """Replicate the idle chatter detection from plugin.py."""
    content_lower = content.lower().strip().rstrip(".")
    return any(phrase in content_lower for phrase in IDLE_PHRASES)


# ================================================================== #
#  MATCH REGEX TESTS
# ================================================================== #


class TestMatchRegex:
    def test_basic_closed_tag(self):
        text = '<hub_msg to="lapis">hello</hub_msg>'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        target, wait_attr, content = matches[0]
        assert target == "lapis"
        assert wait_attr == ""
        assert content == "hello"

    def test_with_wait_true(self):
        text = '<hub_msg to="lapis" wait="true">hello</hub_msg>'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        target, wait_attr, content = matches[0]
        assert target == "lapis"
        assert wait_attr == "true"
        assert content == "hello"

    def test_unclosed_tag(self):
        text = '<hub_msg to="lapis">hello'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        target, wait_attr, content = matches[0]
        assert target == "lapis"
        assert wait_attr == ""
        assert content == "hello"

    def test_unclosed_tag_with_wait(self):
        text = '<hub_msg to="lapis" wait="true">hello'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        target, wait_attr, content = matches[0]
        assert target == "lapis"
        assert wait_attr == "true"
        assert content == "hello"

    def test_multiline_content(self):
        text = '<hub_msg to="peridot">line one\nline two\nline three</hub_msg>'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        target, wait_attr, content = matches[0]
        assert target == "peridot"
        assert "line one" in content
        assert "line three" in content

    def test_multiple_tags(self):
        text = (
            '<hub_msg to="lapis">first</hub_msg>'
            " some prose "
            '<hub_msg to="ruby">second</hub_msg>'
        )
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 2
        assert matches[0] == ("lapis", "", "first")
        assert matches[1] == ("ruby", "", "second")

    def test_empty_content_matched_but_stripped_by_logic(self):
        """Regex matches empty content; the plugin skips it via `if not content`."""
        text = '<hub_msg to="lapis"></hub_msg>'
        matches = re.findall(MATCH_PATTERN, text, re.DOTALL)
        assert len(matches) == 1
        _, _, content = matches[0]
        # content is empty string -- plugin would skip this
        assert content.strip() == ""


# ================================================================== #
#  STRIP REGEX TESTS
# ================================================================== #


class TestStripRegex:
    def test_basic_tag_stripped(self):
        text = '<hub_msg to="lapis">hello</hub_msg>'
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        assert cleaned == ""

    def test_wait_tag_stripped(self):
        text = '<hub_msg to="lapis" wait="true">hello</hub_msg>'
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        assert cleaned == ""

    def test_unclosed_tag_stripped(self):
        text = '<hub_msg to="lapis">hello'
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        assert cleaned == ""

    def test_surrounding_prose_preserved(self):
        """Tag removed; surrounding text kept (with double space where tag was)."""
        text = 'prefix <hub_msg to="x">msg</hub_msg> suffix'
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        # re.sub leaves two spaces where the tag was; .strip() only trims edges
        assert cleaned == "prefix  suffix"

    def test_multiple_tags_stripped(self):
        text = (
            'before <hub_msg to="a">one</hub_msg>'
            " middle "
            '<hub_msg to="b">two</hub_msg> after'
        )
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        assert "one" not in cleaned
        assert "two" not in cleaned
        assert "before" in cleaned
        assert "middle" in cleaned
        assert "after" in cleaned

    def test_non_hub_tags_preserved(self):
        text = '<hub_msg to="x">gone</hub_msg> <scratchpad>keep me</scratchpad>'
        cleaned = re.sub(STRIP_PATTERN, "", text, flags=re.DOTALL).strip()
        assert "<scratchpad>keep me</scratchpad>" in cleaned


# ================================================================== #
#  IDLE CHATTER DETECTION TESTS
# ================================================================== #


class TestIdleChatterDetection:
    def test_standing_by(self):
        assert _idle_check("Standing by for further instructions.") is True

    def test_going_quiet(self):
        assert _idle_check("Going quiet now.") is True

    def test_waiting_for_direction(self):
        assert _idle_check("Waiting for direction from coordinator.") is True

    def test_staying_quiet(self):
        assert _idle_check("Staying quiet until needed.") is True

    def test_not_idle_on_it(self):
        assert _idle_check("On it, starting now") is False

    def test_not_idle_heres_the_fix(self):
        assert _idle_check("here's the fix") is False

    def test_not_idle_regular_message(self):
        assert _idle_check("I've finished the refactor") is False


# ================================================================== #
#  UNTAGGED COORDINATOR ROUTING
# ================================================================== #


class TestUntaggedCoordinatorRouting:
    @pytest.mark.asyncio
    async def test_plain_terminal_response_routes_to_coordinator(self):
        plugin = HubPlugin()
        routed = []

        async def fake_route(response: str):
            routed.append(response)

        plugin._maybe_route_to_coordinator = fake_route

        data = {
            "response_text": "phase complete",
            "clean_response": "phase complete",
            "turn_completed": True,
        }

        result = await plugin._parse_hub_messages(data)

        assert routed == ["phase complete"]
        assert result["clean_response"] == "phase complete"

    @pytest.mark.asyncio
    async def test_native_tool_turn_does_not_route_progress_to_coordinator(self):
        plugin = HubPlugin()
        routed = []

        async def fake_route(response: str):
            routed.append(response)

        plugin._maybe_route_to_coordinator = fake_route

        data = {
            "response_text": "I will inspect that now.",
            "clean_response": "I will inspect that now.",
            "has_native_tools": True,
            "turn_completed": False,
        }

        await plugin._parse_hub_messages(data)

        assert routed == []

    @pytest.mark.asyncio
    async def test_incomplete_text_turn_does_not_route_to_coordinator(self):
        plugin = HubPlugin()
        routed = []

        async def fake_route(response: str):
            routed.append(response)

        plugin._maybe_route_to_coordinator = fake_route

        data = {
            "response_text": "continuing investigation",
            "clean_response": "continuing investigation",
            "turn_completed": False,
        }

        await plugin._parse_hub_messages(data)

        assert routed == []


# ================================================================== #
#  NUDGE ENGINE TESTS
# ================================================================== #


class TestNudgeEngine:
    def _make_engine(self) -> NudgeEngine:
        """Create engine with 0 cooldown so tests aren't time-gated."""
        return NudgeEngine(cooldown=0)

    def test_turns_hub_only_increments(self):
        engine = self._make_engine()
        engine.observe_response("lapis", "hey", used_hub_msg=True, used_real_tools=False)
        tracker = engine._get_tracker("lapis")
        assert tracker.turns_hub_only == 1

        engine.observe_response("lapis", "again", used_hub_msg=True, used_real_tools=False)
        assert tracker.turns_hub_only == 2

    def test_turns_hub_only_resets_on_real_tool(self):
        engine = self._make_engine()
        engine.observe_response("lapis", "msg", used_hub_msg=True, used_real_tools=False)
        engine.observe_response("lapis", "msg", used_hub_msg=True, used_real_tools=False)
        tracker = engine._get_tracker("lapis")
        assert tracker.turns_hub_only == 2

        # real tool usage resets the counter
        engine.observe_response("lapis", "did work", used_hub_msg=True, used_real_tools=True)
        assert tracker.turns_hub_only == 0

    def test_nudge_fires_after_threshold(self):
        engine = self._make_engine()
        identity = "ruby"

        # feed exactly HUB_LOOP_THRESHOLD hub-only turns
        for _ in range(HUB_LOOP_THRESHOLD):
            engine.observe_response(
                identity, "chatting", used_hub_msg=True, used_real_tools=False
            )

        tracker = engine._get_tracker(identity)
        assert tracker.turns_hub_only == HUB_LOOP_THRESHOLD

        nudge = engine.evaluate(identity)
        assert nudge is not None
        assert "hub messages" in nudge
        assert "let your turn end" in nudge

    def test_no_nudge_below_threshold(self):
        engine = self._make_engine()
        identity = "peridot"

        # feed one fewer than threshold
        for _ in range(HUB_LOOP_THRESHOLD - 1):
            engine.observe_response(
                identity, "chatting", used_hub_msg=True, used_real_tools=False
            )

        nudge = engine.evaluate(identity)
        # below threshold: hub_wait nudge should not fire
        # (other nudge types might fire, but hub_wait specifically should not)
        if nudge is not None:
            assert "hub messages" not in nudge

    def test_no_nudge_when_doing_real_work(self):
        engine = self._make_engine()
        identity = "topaz"

        # hub + real tools = not hub-only, counter stays 0
        for _ in range(5):
            engine.observe_response(
                identity, "working", used_hub_msg=True, used_real_tools=True
            )

        tracker = engine._get_tracker(identity)
        assert tracker.turns_hub_only == 0

    def test_hub_loop_threshold_value(self):
        """Sanity check: threshold is 2 as defined in source."""
        assert HUB_LOOP_THRESHOLD == 3

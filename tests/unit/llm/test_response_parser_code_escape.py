"""Tests for code-block stripping in response_parser.

Verifies that XML tags inside <code>, triple-backtick, and single-backtick
blocks are NOT executed, while tags outside those blocks ARE executed.
"""

import pytest

from kollabor_ai.response_parser import ResponseParser


@pytest.fixture
def parser():
    return ResponseParser()


# ── <code> wrapper ──────────────────────────────────────────────


class TestCodeBlockStripping:
    """Tags inside <code> blocks must not be executed."""

    def test_terminal_inside_code_block_not_executed(self, parser):
        resp = 'Here is an example:\n<code><terminal-kill>dev</terminal-kill></code>\nDone.'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_terminal_outside_code_block_executed(self, parser):
        resp = '<terminal>git status</terminal>'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 1
        assert tools[0]["type"] == "terminal"

    def test_mixed_code_and_real_tags(self, parser):
        resp = (
            '<code><terminal-kill>dev</terminal-kill></code>\n'
            '<terminal>git status</terminal>\n'
            '<code><terminal-output>build</terminal-output></code>'
        )
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        # Only the real <terminal> tag should execute
        assert len(tools) == 1
        assert tools[0]["command"] == "git status"

    def test_multiline_code_block(self, parser):
        resp = (
            '<code>\n'
            '<terminal background="true" name="dev">npm run dev</terminal>\n'
            '<terminal-kill>dev</terminal-kill>\n'
            '</code>'
        )
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_code_block_case_insensitive(self, parser):
        resp = '<CODE><terminal>rm -rf /</terminal></CODE>'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_file_operation_inside_code_not_executed(self, parser):
        resp = '<code><delete><file>important.py</file></delete></code>'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_real_file_operation_still_works(self, parser):
        resp = '<read><file>src/main.py</file></read>'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 1
        assert tools[0]["type"] == "file_read"


# ── triple-backtick blocks ──────────────────────────────────────


class TestTripleBacktickStripping:
    """Tags inside ```...``` blocks must not be executed."""

    def test_terminal_inside_triple_backtick(self, parser):
        resp = '```\n<terminal-kill>dev</terminal-kill>\n```'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_mixed_triple_backtick_and_real(self, parser):
        resp = (
            'Example:\n```\n<terminal-kill>dev</terminal-kill>\n```\n'
            'Real command:\n<terminal>ls -la</terminal>'
        )
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 1
        assert tools[0]["command"] == "ls -la"


# ── single-backtick inline ──────────────────────────────────────


class TestSingleBacktickStripping:
    """Tags inside `...` inline code must not be executed."""

    def test_tag_inside_single_backtick(self, parser):
        resp = 'Use `<terminal-kill>dev</terminal-kill>` to stop it.'
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 0

    def test_real_tag_next_to_backtick_example(self, parser):
        resp = (
            'Use `<terminal-kill>dev</terminal-kill>` to stop. '
            'Now running:\n<terminal>pwd</terminal>'
        )
        parsed = parser.parse_response(resp)
        tools = parser.get_all_tools(parsed)
        assert len(tools) == 1
        assert tools[0]["command"] == "pwd"

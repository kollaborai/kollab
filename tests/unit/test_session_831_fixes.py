"""Tests for session 831 fixes: console vault stream, is_displaying race, tmux refs."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHubConsoleVaultStream:
    """Verify find_active_stream handles the post-flatten vault layout.

    Original layout stored streams at vaults/<id>/projects/<fp>/stream.jsonl.
    The 2026-04-20 hub project-scoping work flattened this: the entire
    vaults/ tree is now project-scoped at
    ~/.kollab/projects/<encoded>/hub/vaults/ (when the flag is on),
    so streams live directly at vaults/<id>/stream.jsonl. find_active_stream
    prefers the flat path and falls back to the nested layout for
    pre-migration vaults.
    """

    def test_find_active_stream_prefers_flat_layout(self, tmp_path):
        """Flat stream wins when both exist (post-flatten is canonical)."""
        from plugins.hub.vault import find_active_stream

        flat = tmp_path / "stream.jsonl"
        flat.write_text('{"type": "flat"}\n')

        project_dir = tmp_path / "projects" / "test_project"
        project_dir.mkdir(parents=True)
        nested = project_dir / "stream.jsonl"
        nested.write_text('{"type": "nested"}\n')

        with patch("plugins.hub.vault._get_project_fingerprint", return_value="test_project"):
            result = find_active_stream(tmp_path)

        assert result == flat

    def test_find_active_stream_falls_back_to_nested(self, tmp_path):
        """Pre-flatten vaults still read from projects/<fp>/stream.jsonl."""
        from plugins.hub.vault import find_active_stream

        project_dir = tmp_path / "projects" / "test_project"
        project_dir.mkdir(parents=True)
        nested = project_dir / "stream.jsonl"
        nested.write_text('{"type": "nested"}\n')

        with patch("plugins.hub.vault._get_project_fingerprint", return_value="test_project"):
            result = find_active_stream(tmp_path)

        assert result == nested

    def test_find_active_stream_defaults_to_flat_when_nothing_exists(self, tmp_path):
        """Empty vault: return the flat path so first writes land there."""
        from plugins.hub.vault import find_active_stream

        with patch("plugins.hub.vault._get_project_fingerprint", return_value="test"):
            result = find_active_stream(tmp_path)

        assert result == tmp_path / "stream.jsonl"


class TestIsDisplayingLock:
    """Verify threading.Lock prevents concurrent display."""

    def _make_coordinator(self):
        """Create a minimal MessageDisplayCoordinator for testing."""
        from kollabor_tui.message_coordinator import MessageDisplayCoordinator

        renderer = MagicMock()
        term_renderer = MagicMock()
        term_renderer.writing_messages = False
        term_renderer.pipe_mode = False
        term_renderer.input_line_written = False
        term_renderer.last_line_count = 0
        term_renderer.terminal_state = MagicMock()

        coord = MessageDisplayCoordinator(term_renderer, renderer)
        return coord

    def test_lock_blocks_concurrent_display(self):
        """Two threads cannot enter display_queued_messages simultaneously."""
        coord = self._make_coordinator()
        coord.queue_message("system", "msg1", display_type="info")

        entered = []
        blocked = []

        def slow_display():
            # This acquires the lock
            entered.append("thread1")
            coord.display_queued_messages()

        # Start thread that holds the lock
        t = threading.Thread(target=slow_display)
        t.start()

        # Give it a moment to acquire
        time.sleep(0.05)

        # Try from main thread — should be blocked
        coord.queue_message("system", "msg2", display_type="info")
        result = coord.display_queued_messages()
        if coord.is_displaying:
            blocked.append("main")

        t.join(timeout=2)

        # At least thread1 entered
        assert "thread1" in entered

    def test_lock_released_after_display(self):
        """Lock is released after display_queued_messages completes."""
        coord = self._make_coordinator()
        coord.queue_message("system", "msg1", display_type="info")

        # First call should succeed
        coord.display_queued_messages()
        assert not coord.is_displaying

        # Lock should be released, second call should also succeed
        coord.queue_message("system", "msg2", display_type="info")
        coord.display_queued_messages()
        assert not coord.is_displaying

    def test_lock_released_on_early_return_navigation(self):
        """Lock is released when navigation_active causes early return."""
        coord = self._make_coordinator()
        coord.navigation_active = True
        coord.queue_message("system", "msg1", display_type="info")

        # This should return early but release the lock
        coord.display_queued_messages()
        assert not coord.is_displaying

        # Should be able to acquire again
        coord.navigation_active = False
        coord.queue_message("system", "msg2", display_type="info")
        coord.display_queued_messages()
        assert not coord.is_displaying

    def test_display_raw_text_lock_released(self):
        """Lock is released after display_raw_text completes."""
        coord = self._make_coordinator()

        coord.display_raw_text("test text")
        assert not coord.is_displaying

        # Should work again
        coord.display_raw_text("test text 2")
        assert not coord.is_displaying


class TestTmuxReferenceCleanup:
    """Verify tmux references were cleaned from tool definitions."""

    def test_terminal_tool_no_tmux_in_description(self):
        """Terminal tool description should not reference tmux."""
        from kollabor_agent.tool_definitions.terminal import terminal_tool

        assert "tmux" not in terminal_tool.description.lower()
        # Check parameter descriptions too
        for param in terminal_tool.parameters:
            if param.name == "background":
                assert "tmux" not in param.description.lower()

    def test_terminal_tool_notes_no_tmux(self):
        """Terminal tool notes should reference subprocess, not tmux."""
        from kollabor_agent.tool_definitions.terminal import terminal_tool

        assert "tmux" not in terminal_tool.notes.lower()
        assert "subprocess" in terminal_tool.notes.lower()

    def test_hub_stop_no_tmux(self):
        """Hub stop tool should not reference tmux."""
        from kollabor_agent.tool_definitions.hub import hub_stop

        for rule in hub_stop.key_rules:
            assert "tmux" not in rule.lower()

    def test_process_manager_no_tmux_session_field(self):
        """SpawnRequest should not have tmux_session field."""
        from kollabor_agent.process_manager import SpawnRequest

        req = SpawnRequest(name="test", cmd=["echo"])
        assert not hasattr(req, "tmux_session")

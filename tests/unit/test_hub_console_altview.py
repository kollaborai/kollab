"""Tests for HubConsoleAltView — hub agent management console.

Covers: initialization, agent loading, feed refresh, input handling,
attach/detach, and rendering edge cases.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.altview.hub_console_altview import HubConsoleAltView, _ANSI_RE
from kollabor_tui.key_parser import KeyPress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_json(
    identity: str = "lapis",
    pid: int | None = None,
    state: str = "idle",
    is_coordinator: bool = False,
    socket_path: str = "",
) -> dict:
    """Build a presence JSON dict for an agent."""
    if pid is None:
        # Use current PID so os.kill(pid, 0) succeeds
        pid = os.getpid()
    return {
        "identity": identity,
        "pid": pid,
        "state": state,
        "is_coordinator": is_coordinator,
        "socket_path": socket_path,
    }


def _write_presence_files(presence_dir: Path, agents: list[dict]) -> None:
    """Write agent presence JSON files."""
    presence_dir.mkdir(parents=True, exist_ok=True)
    for agent in agents:
        ident = agent.get("identity", "unknown")
        path = presence_dir / f"{ident}.json"
        path.write_text(json.dumps(agent))


def _make_keypress(name: str, char: str = "") -> KeyPress:
    """Build a KeyPress for testing."""
    kp = MagicMock(spec=KeyPress)
    kp.name = name
    kp.char = char
    return kp


def _make_mock_renderer(width: int = 120, height: int = 40) -> MagicMock:
    """Build a mock renderer with terminal size."""
    renderer = MagicMock()
    renderer.get_terminal_size.return_value = (width, height)
    return renderer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def altview() -> HubConsoleAltView:
    """Create a fresh HubConsoleAltView."""
    return HubConsoleAltView()


@pytest.fixture
def mock_event_bus() -> MagicMock:
    """Create a mock event bus."""
    bus = MagicMock()
    bus.get_service.return_value = None
    return bus


@pytest.fixture
def presence_dir(tmp_path: Path) -> Path:
    """Create a temporary presence directory and patch get_presence_dir."""
    pd = tmp_path / "presence"
    pd.mkdir()
    with patch("plugins.hub.presence.get_presence_dir", return_value=pd):
        yield pd


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_metadata(self, altview: HubConsoleAltView):
        assert altview.metadata.plugin_type == "hub-console"
        assert altview.metadata.aliases == ["console"]
        assert altview.target_fps == 2.0

    def test_initial_state(self, altview: HubConsoleAltView):
        assert altview.agents == []
        assert altview.feed_lines == []
        assert altview.selected_idx == 0
        assert altview.active_pane == "agents"
        assert altview.attached_to is None
        assert altview._input_buffer == ""


# ---------------------------------------------------------------------------
# Agent loading tests
# ---------------------------------------------------------------------------


class TestRefreshAgents:
    def test_loads_agents_from_presence(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [
                _make_agent_json("lapis", state="working"),
                _make_agent_json("sapphire", state="idle"),
            ],
        )
        altview._refresh_agents()
        assert len(altview.agents) == 2
        identities = {a["identity"] for a in altview.agents}
        assert identities == {"lapis", "sapphire"}

    def test_sorts_coordinator_first(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [
                _make_agent_json("lapis", is_coordinator=False),
                _make_agent_json("koordinator", is_coordinator=True),
                _make_agent_json("sapphire", is_coordinator=False),
            ],
        )
        altview._refresh_agents()
        assert altview.agents[0]["identity"] == "koordinator"

    def test_skips_dead_processes(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [
                _make_agent_json("lapis", pid=999999999),
                _make_agent_json("sapphire"),
            ],
        )
        altview._refresh_agents()
        assert len(altview.agents) == 1
        assert altview.agents[0]["identity"] == "sapphire"

    def test_empty_presence_dir(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        altview._refresh_agents()
        assert altview.agents == []

    def test_handles_malformed_json(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        (presence_dir / "bad.json").write_text("not json")
        _write_presence_files(presence_dir, [_make_agent_json("lapis")])
        altview._refresh_agents()
        assert len(altview.agents) == 1

    def test_missing_presence_dir(self, altview: HubConsoleAltView, tmp_path: Path):
        with patch(
            "plugins.hub.presence.get_presence_dir",
            return_value=tmp_path / "nonexistent",
        ):
            altview._refresh_agents()
            assert altview.agents == []


# ---------------------------------------------------------------------------
# Identity resolution tests
# ---------------------------------------------------------------------------


class TestResolveIdentity:
    def test_uses_hub_config_name(self, altview: HubConsoleAltView):
        bus = MagicMock()
        hub_plugin = MagicMock()
        hub_plugin.config = {"plugins.hub.user_name": "marco"}
        bus.get_service.return_value = hub_plugin
        altview.set_event_bus(bus)
        altview._resolve_my_identity()
        assert altview._my_identity == "marco"

    def test_falls_back_to_user_env(self, altview: HubConsoleAltView):
        altview.set_event_bus(None)
        altview._resolve_my_identity()
        assert altview._my_identity == os.environ.get("USER", "user")

    def test_handles_missing_config(self, altview: HubConsoleAltView):
        bus = MagicMock()
        hub_plugin = MagicMock()
        hub_plugin.config = None
        bus.get_service.return_value = hub_plugin
        altview.set_event_bus(bus)
        altview._resolve_my_identity()
        assert altview._my_identity == os.environ.get("USER", "user")


# ---------------------------------------------------------------------------
# Feed loading tests
# ---------------------------------------------------------------------------


class TestRefreshFeed:
    def test_no_agents_shows_placeholder(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        altview.agents = []
        altview._refresh_feed()
        assert altview.feed_lines == ["(no agent selected)"]

    def test_self_agent_uses_vault_stream(
        self, altview: HubConsoleAltView, presence_dir: Path, tmp_path: Path
    ):
        altview._my_identity = "lapis"
        altview.agents = [_make_agent_json("lapis")]
        altview.selected_idx = 0

        # Create vault stream
        vault_dir = tmp_path / "vaults" / "lapis"
        vault_dir.mkdir(parents=True)
        stream = vault_dir / "stream.jsonl"
        entries = [
            json.dumps({"ts": time.time(), "type": "sent", "content": "hello", "from": "lapis", "to": "sapphire"}),
            json.dumps({"ts": time.time(), "type": "received", "content": "hi back", "from": "sapphire", "to": "lapis"}),
        ]
        stream.write_text("\n".join(entries) + "\n")

        with (
            patch("plugins.hub.vault.get_vaults_dir", return_value=tmp_path / "vaults"),
            patch("plugins.hub.vault.find_active_stream", return_value=stream),
        ):
            altview._refresh_feed()

        assert len(altview.feed_lines) >= 2
        assert any("->" in line for line in altview.feed_lines)
        assert any("<-" in line for line in altview.feed_lines)

    def test_empty_vault_stream(
        self, altview: HubConsoleAltView, presence_dir: Path, tmp_path: Path
    ):
        altview._my_identity = "lapis"
        altview.agents = [_make_agent_json("lapis")]
        altview.selected_idx = 0

        vault_dir = tmp_path / "vaults" / "lapis"
        vault_dir.mkdir(parents=True)

        with (
            patch("plugins.hub.vault.get_vaults_dir", return_value=tmp_path / "vaults"),
            patch("plugins.hub.vault.find_active_stream", return_value=vault_dir / "nonexistent.jsonl"),
        ):
            altview._refresh_feed()

        assert any("no vault stream" in line for line in altview.feed_lines)


# ---------------------------------------------------------------------------
# Input handling tests
# ---------------------------------------------------------------------------


class TestInputHandling:
    @pytest.mark.asyncio
    async def test_escape_exits(self, altview: HubConsoleAltView):
        result = await altview.handle_input(_make_keypress("Escape"))
        assert result is True

    @pytest.mark.asyncio
    async def test_escape_detaches_when_attached(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        result = await altview.handle_input(_make_keypress("Escape"))
        assert result is False
        assert altview.attached_to is None
        assert altview._attached_socket is None

    @pytest.mark.asyncio
    async def test_tab_toggles_pane(self, altview: HubConsoleAltView):
        assert altview.active_pane == "agents"
        await altview.handle_input(_make_keypress("Tab"))
        assert altview.active_pane == "feed"
        await altview.handle_input(_make_keypress("Tab"))
        assert altview.active_pane == "agents"

    @pytest.mark.asyncio
    async def test_arrow_up_selects_previous(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [_make_agent_json("lapis"), _make_agent_json("sapphire")],
        )
        altview._refresh_agents()
        altview.selected_idx = 1
        await altview.handle_input(_make_keypress("ArrowUp"))
        assert altview.selected_idx == 0

    @pytest.mark.asyncio
    async def test_arrow_down_selects_next(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [_make_agent_json("lapis"), _make_agent_json("sapphire")],
        )
        altview._refresh_agents()
        altview.selected_idx = 0
        await altview.handle_input(_make_keypress("ArrowDown"))
        assert altview.selected_idx == 1

    @pytest.mark.asyncio
    async def test_arrow_up_clamps_at_zero(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(presence_dir, [_make_agent_json("lapis")])
        altview._refresh_agents()
        altview.selected_idx = 0
        await altview.handle_input(_make_keypress("ArrowUp"))
        assert altview.selected_idx == 0

    @pytest.mark.asyncio
    async def test_enter_attaches_to_agent(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [_make_agent_json("lapis", socket_path="/tmp/lapis.sock")],
        )
        altview._refresh_agents()
        altview.selected_idx = 0
        await altview.handle_input(_make_keypress("Enter"))
        assert altview.attached_to == "lapis"
        assert altview._attached_socket == "/tmp/lapis.sock"
        assert altview.active_pane == "feed"

    @pytest.mark.asyncio
    async def test_enter_no_socket_no_attach(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        _write_presence_files(
            presence_dir,
            [_make_agent_json("lapis", socket_path="")],
        )
        altview._refresh_agents()
        altview.selected_idx = 0
        await altview.handle_input(_make_keypress("Enter"))
        assert altview.attached_to is None


# ---------------------------------------------------------------------------
# Attached input tests
# ---------------------------------------------------------------------------


class TestAttachedInput:
    @pytest.mark.asyncio
    async def test_printable_chars_append_to_buffer(
        self, altview: HubConsoleAltView
    ):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        await altview.handle_input(_make_keypress("char", "h"))
        await altview.handle_input(_make_keypress("char", "i"))
        assert altview._input_buffer == "hi"

    @pytest.mark.asyncio
    async def test_backspace_removes_char(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview._input_buffer = "hello"
        await altview.handle_input(_make_keypress("Backspace"))
        assert altview._input_buffer == "hell"

    @pytest.mark.asyncio
    async def test_backspace_empty_buffer(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview._input_buffer = ""
        await altview.handle_input(_make_keypress("Backspace"))
        assert altview._input_buffer == ""

    @pytest.mark.asyncio
    async def test_enter_sends_message(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview._input_buffer = "hello"

        with patch(
            "plugins.hub.messenger.AgentMessenger.send_to_agent",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await altview.handle_input(_make_keypress("Enter"))

        assert altview._input_buffer == ""
        assert any("-> lapis" in line for line in altview.feed_lines)

    @pytest.mark.asyncio
    async def test_enter_empty_buffer_no_send(
        self, altview: HubConsoleAltView
    ):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview._input_buffer = "  "

        with patch(
            "plugins.hub.messenger.AgentMessenger.send_to_agent",
            new_callable=AsyncMock,
        ) as mock_send:
            await altview.handle_input(_make_keypress("Enter"))
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_scroll_up_in_attached(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview.feed_lines = [f"line {i}" for i in range(100)]
        altview.feed_scroll = 0
        await altview.handle_input(_make_keypress("ArrowUp"))
        assert altview.feed_scroll == 3

    @pytest.mark.asyncio
    async def test_scroll_down_in_attached(self, altview: HubConsoleAltView):
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview.feed_scroll = 10
        await altview.handle_input(_make_keypress("ArrowDown"))
        assert altview.feed_scroll == 7


# ---------------------------------------------------------------------------
# Feed scroll tests (non-attached)
# ---------------------------------------------------------------------------


class TestFeedScroll:
    @pytest.mark.asyncio
    async def test_scroll_up_in_feed_pane(self, altview: HubConsoleAltView):
        altview.active_pane = "feed"
        altview.feed_lines = [f"line {i}" for i in range(100)]
        altview.feed_scroll = 0
        await altview.handle_input(_make_keypress("ArrowUp"))
        assert altview.feed_scroll == 3

    @pytest.mark.asyncio
    async def test_scroll_down_in_feed_pane(self, altview: HubConsoleAltView):
        altview.active_pane = "feed"
        altview.feed_scroll = 10
        await altview.handle_input(_make_keypress("ArrowDown"))
        assert altview.feed_scroll == 7

    @pytest.mark.asyncio
    async def test_scroll_clamps_at_zero(self, altview: HubConsoleAltView):
        altview.active_pane = "feed"
        altview.feed_scroll = 1
        await altview.handle_input(_make_keypress("ArrowDown"))
        assert altview.feed_scroll == 0


# ---------------------------------------------------------------------------
# ANSI regex tests
# ---------------------------------------------------------------------------


class TestAnsiRegex:
    def test_strips_csi_codes(self):
        text = "\x1b[32mgreen\x1b[0m"
        assert _ANSI_RE.sub("", text) == "green"

    def test_strips_osc_codes(self):
        text = "\x1b]0;title\x07rest"
        assert _ANSI_RE.sub("", text) == "rest"

    def test_preserves_plain_text(self):
        text = "hello world"
        assert _ANSI_RE.sub("", text) == "hello world"

    def test_strips_single_fe(self):
        text = "\x1b[Acursor up"
        assert _ANSI_RE.sub("", text) == "cursor up"


# ---------------------------------------------------------------------------
# on_enter / on_complete lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_on_enter_resets_state(
        self, altview: HubConsoleAltView, presence_dir: Path
    ):
        renderer = _make_mock_renderer()
        altview.selected_idx = 5
        altview.active_pane = "feed"
        altview._input_buffer = "old"

        with patch(
            "plugins.hub.presence.get_presence_dir",
            return_value=presence_dir,
        ):
            await altview.on_enter(renderer)

        assert altview.selected_idx == 0
        assert altview.active_pane == "agents"
        assert altview._input_buffer == ""

    @pytest.mark.asyncio
    async def test_on_complete_clears_state(self, altview: HubConsoleAltView):
        altview.agents = [{"identity": "lapis"}]
        altview.feed_lines = ["some content"]
        altview.attached_to = "lapis"
        altview._attached_socket = "/tmp/test.sock"
        altview._input_buffer = "typing"

        await altview.on_complete()

        assert altview.agents == []
        assert altview.feed_lines == []
        assert altview.attached_to is None
        assert altview._attached_socket is None
        assert altview._input_buffer == ""

"""First-launch guided setup (issue #121, Story 1): marker, branches, texts, gate."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import plugins.hub.plugin as hub_plugin
from plugins.altview.connect_altview import (
    ConnectAltView,
    ConnectGuideAltView,
    ConnectOutcome,
    ConnectScreenState,
    connect_guide_lines,
    connect_screen_lines,
)
from plugins.hub.connect_guide import (
    MARKER_ENV,
    guide_applies,
    guide_seen,
    mark_guide_seen,
    post_join_line,
)
from plugins.hub.relay_commands import NO_NETWORK


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "state" / "connect-guide-seen"
    monkeypatch.setenv(MARKER_ENV, str(path))
    monkeypatch.delenv("KOLLAB_PARENT_PID", raising=False)
    return path


def plain_args(**overrides):
    base = dict(pipe=False, detached=False, query=None, hub=None, attach=None)
    return SimpleNamespace(**{**base, **overrides})


def key(name):
    return SimpleNamespace(name=name, char="", ctrl=False, modifiers={})


class Pane:
    """The bits of a renderer the views touch."""

    def __init__(self, width=80, height=24):
        self.size = (width, height)
        self.rows = []

    def get_terminal_size(self):
        return self.size

    def clear_screen(self):
        self.rows = []

    def write_at(self, x, y, text, style=""):
        self.rows.append((y, text))

    def text(self):
        return "\n".join(text for _, text in sorted(self.rows))


def press(view, *names):
    """Keys for a guide view; "render" is a redraw (arms Enter on the choices)."""

    async def run():
        pane = Pane()
        await view.on_enter(pane)
        done = False
        for name in names:
            if name == "render":
                await view.render_frame(0)
            else:
                done = await view.handle_input(key(name))
        return done

    return asyncio.run(run())


# ----------------------------------------------------------------- marker


def test_notice_shows_once_per_machine(marker):
    assert guide_applies(plain_args(), interactive=True)
    mark_guide_seen()
    assert marker.exists() and guide_seen()
    assert not guide_applies(plain_args(), interactive=True)


@pytest.mark.parametrize("answer", ["Enter", "Escape"])
def test_enter_or_esc_answers_and_writes_the_marker(marker, answer):
    view = ConnectGuideAltView(has_network=True, on_answer=mark_guide_seen)
    assert not marker.exists()
    assert press(view, answer) is True
    assert marker.exists()
    assert not guide_applies(plain_args(), interactive=True)


def test_quitting_without_an_answer_shows_it_again(marker):
    view = ConnectGuideAltView(has_network=False, on_answer=mark_guide_seen)
    press(view, "render")  # drawn, never answered
    assert not marker.exists()
    assert guide_applies(plain_args(), interactive=True)


def test_marker_write_failure_is_not_fatal(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(MARKER_ENV, str(blocker / "sub" / "seen"))  # parent is a file
    mark_guide_seen()  # no raise
    assert not guide_seen()


# ------------------------------------------------------------------- gate


@pytest.mark.parametrize(
    "overrides",
    [
        dict(pipe=True),
        dict(detached=True),  # the daemon and every hub-spawned agent
        dict(query="hello"),
        dict(hub=["status"]),
        dict(web_ui=True),
    ],
)
def test_nothing_shown_in_pipe_detached_or_cli_modes(marker, overrides):
    assert not guide_applies(plain_args(**overrides), interactive=True)


def test_nothing_shown_without_a_terminal_or_for_a_spawned_child(marker, monkeypatch):
    assert not guide_applies(plain_args(), interactive=False)
    monkeypatch.setenv("KOLLAB_PARENT_PID", "123")
    assert not guide_applies(plain_args(), interactive=True)


def test_attached_client_and_single_process_both_qualify(marker):
    assert guide_applies(plain_args(attach="koordinator"), interactive=True)
    assert guide_applies(plain_args(attach=None), interactive=True)


def _plugin(**attrs):
    cls = next(
        value
        for value in vars(hub_plugin).values()
        if isinstance(value, type) and hasattr(value, "_on_startup_connect_guide")
    )
    plugin = object.__new__(cls)
    plugin._cli_args = plain_args()
    plugin.__dict__.update(attrs)
    return plugin


class Tty:
    def isatty(self):
        return True

    def write(self, text):
        return len(text)

    def flush(self):
        pass


def test_startup_hook_starts_the_flow_only_for_a_person_at_a_terminal(marker, monkeypatch):
    async def run(interactive, **args):
        plugin = _plugin(_cli_args=plain_args(**args))
        plugin._run_connect_guide = AsyncMock()
        monkeypatch.setattr(sys, "stdin", Tty() if interactive else open("/dev/null"))
        monkeypatch.setattr(sys, "stdout", Tty())
        await plugin._on_startup_connect_guide({}, None)
        task = getattr(plugin, "_connect_guide_task", None)
        if task is not None:
            await task
        return plugin._run_connect_guide.await_count

    assert asyncio.run(run(True)) == 1
    assert asyncio.run(run(False)) == 0
    assert asyncio.run(run(True, pipe=True)) == 0
    assert asyncio.run(run(True, detached=True)) == 0


# ---------------------------------------------------------------- branches


def test_a_device_on_a_network_goes_straight_to_the_screen(marker):
    view = ConnectGuideAltView(has_network=True, on_answer=mark_guide_seen)
    assert press(view, "Enter") is True
    assert view.answer == "screen"


def test_a_device_with_no_network_gets_the_two_choices(marker):
    view = ConnectGuideAltView(has_network=False, on_answer=mark_guide_seen)
    assert press(view, "Enter") is False
    assert view.stage == "choices" and view.answer is None and marker.exists()
    view = ConnectGuideAltView(has_network=False)
    assert press(view, "Enter", "render", "Enter") is True
    assert view.answer == "new_network"
    view = ConnectGuideAltView(has_network=False)
    assert press(view, "Enter", "render", "ArrowDown", "Enter") is True
    assert view.answer == "join"
    view = ConnectGuideAltView(has_network=False)
    assert press(view, "Enter", "render", "Escape") is True
    assert view.answer == "later"


def test_a_double_tapped_enter_does_not_pick_a_choice_unseen(marker):
    view = ConnectGuideAltView(has_network=False)
    assert press(view, "Enter", "Enter") is False
    assert view.answer is None


@pytest.mark.parametrize("width", [80, 120])
def test_guide_text_fits_and_names_the_choices(width):
    notice = "\n".join(connect_guide_lines("notice", 0, width))
    assert "New: connect your agents across computers." in notice
    assert "Enter sets it up now; Esc for later (/connect any time)." in notice
    choices = connect_guide_lines("choices", 1, width)
    text = "\n".join(choices)
    assert "  Start a new network on kollabor.ai" in text
    assert "> Join with a code" in text
    assert all(len(line) <= width for line in choices + connect_guide_lines("notice", 0, width))


def test_guide_text_wraps_on_a_narrow_terminal():
    lines = connect_guide_lines("notice", 0, 30)
    assert all(len(line) <= 30 for line in lines)
    assert "sets it up now;" in " ".join(lines)


def _flow(marker, has_network, keys):
    """Run the plugin's guide flow against a fake stack that presses `keys`."""
    plugin = _plugin()
    plugin._startup_task = None
    plugin._connect_has_network = AsyncMock(return_value=has_network)
    plugin._connect_home = AsyncMock(return_value="")
    plugin._guided_new_network = AsyncMock(return_value="")
    plugin._open_connect_altview = AsyncMock(return_value="connect: boom")
    plugin._say_connect = Mock()

    class Stack:
        is_in_altview = False

        async def push(self, view, name, reuse=True):
            await asyncio.to_thread(lambda: None)
            press_keys = keys

            await view.on_enter(Pane())
            for name_ in press_keys:
                if name_ == "render":
                    await view.render_frame(0)
                elif await view.handle_input(key(name_)):
                    break
            return True

    plugin._altview_stack = lambda: Stack()
    asyncio.run(plugin._run_connect_guide())
    return plugin


def test_flow_routes_each_answer(marker):
    plugin = _flow(marker, True, ["Enter"])
    plugin._connect_home.assert_awaited_once()
    assert marker.exists()

    marker.unlink()
    plugin = _flow(marker, False, ["Enter", "render", "Enter"])
    plugin._guided_new_network.assert_awaited_once()
    plugin._connect_home.assert_not_awaited()

    plugin = _flow(marker, False, ["Enter", "render", "ArrowDown", "Enter"])
    plugin._open_connect_altview.assert_awaited_once_with("kollabor.ai")
    plugin._say_connect.assert_called_once_with("connect: boom")

    marker.unlink()
    plugin = _flow(marker, False, ["Escape"])
    for name in ("_connect_home", "_guided_new_network", "_open_connect_altview"):
        getattr(plugin, name).assert_not_awaited()
    assert marker.exists()


def test_flow_leaves_an_open_screen_alone(marker):
    plugin = _plugin(_startup_task=None)
    plugin._connect_has_network = AsyncMock(return_value=False)
    plugin._altview_stack = lambda: SimpleNamespace(is_in_altview=True)
    asyncio.run(plugin._run_connect_guide())
    plugin._connect_has_network.assert_not_awaited()
    assert not marker.exists()  # unanswered: it shows next launch


def test_new_network_creates_then_opens_the_screen_with_the_steps():
    plugin = _plugin()
    plugin._relay_network_domain = lambda: ""
    plugin._start_connect_network = AsyncMock(return_value=True)
    plugin._open_connect_screen = AsyncMock(return_value="")
    assert asyncio.run(plugin._guided_new_network()) == ""
    plugin._start_connect_network.assert_awaited_once_with("kollabor.ai")
    plugin._open_connect_screen.assert_awaited_once_with("kollabor.ai", guide=True)

    plugin._start_connect_network = AsyncMock(return_value=False)
    plugin._open_connect_screen = AsyncMock()
    assert "could not start a network" in asyncio.run(plugin._guided_new_network())
    plugin._open_connect_screen.assert_not_awaited()


@pytest.mark.parametrize(
    "reply,started",
    [("network mac-net via kollabor.ai", True), (NO_NETWORK, False), ("", False)],
)
def test_start_network_reads_the_first_line_of_the_reply(reply, started):
    plugin = _plugin()
    plugin._attached = lambda: False
    plugin._run_connect_command = AsyncMock(return_value=reply)
    assert asyncio.run(plugin._start_connect_network("kollabor.ai")) is started


# ---------------------------------------------------------------- the texts


@pytest.mark.parametrize("width", [80, 120])
def test_other_computer_box_on_the_new_network_screen(width):
    state = ConnectScreenState(
        code="7QK4-M2XP", code_remaining=290, code_status="active", guide=True
    )
    lines = connect_screen_lines(state, width)
    text = "\n".join(lines)
    assert "On your other computer" in text
    assert "1) kollab --upgrade (or pip install -U kollab)" in text
    assert "2) run kollab and press Enter on the same notice" in text
    assert "3) choose Join with a code and type the code" in text
    assert all(len(line) <= width for line in lines)
    plain = "\n".join(connect_screen_lines(ConnectScreenState(), width))
    assert "On your other computer" not in plain


def test_post_join_line_names_the_primary_and_the_login():
    line = post_join_line("mac-kollab")
    assert "Settings arrive sealed from mac-kollab" in line
    assert "Run /login on this computer: a ChatGPT login does not travel." in line
    assert "the device that issued the code" in post_join_line("")


def test_join_outcome_shows_the_note_under_the_joined_line():
    note = post_join_line("mac-kollab")
    outcome = ConnectOutcome.approved("joined mac-net as box. trust: open", note)
    assert outcome.note == note
    view = ConnectAltView()
    view._renderer = pane = Pane()
    view._outcome, view._stage = outcome, "outcome"
    view._render_outcome(4, 80)
    text = " ".join(row for _, row in sorted(pane.rows))
    assert "joined mac-net as box. trust: open" in text
    assert "Settings arrive sealed from mac-kollab" in text
    assert "Run /login on this computer:" in text
    assert "enter/esc close" in text
    with pytest.raises(ValueError):
        ConnectOutcome.rejected().__class__(
            ConnectOutcome.rejected().status, note="nope"
        )


def test_join_line_waits_for_the_name_then_falls_back_after_the_patience():
    from plugins.hub.connect_guide import JoinLine

    now, name = [0.0], [""]
    line = JoinLine(lambda: name[0], patience=30.0, clock=lambda: now[0])
    assert line.text() == ""  # the name may still come
    now[0] = 29.0
    assert line.text() == ""
    name[0] = "mac-kollab"
    assert line.text() == post_join_line("mac-kollab")
    name[0], now[0] = "", 30.0
    assert line.text() == post_join_line("")  # about 30 s without a name


@pytest.mark.asyncio
async def test_join_line_is_said_once_with_the_name_as_soon_as_it_is_known():
    from plugins.hub.connect_guide import JoinLine

    names, said = iter(["", "", "mac-kollab"]), []

    async def sleep(_seconds):
        return None

    line = JoinLine(lambda: next(names, "mac-kollab"), patience=30.0)
    await line.say(said.append, poll=0.0, sleep=sleep)
    await line.say(said.append, poll=0.0, sleep=sleep)  # asking again says nothing more
    assert said == [post_join_line("mac-kollab")]


@pytest.mark.asyncio
async def test_join_line_says_the_stand_in_once_when_no_name_comes():
    from plugins.hub.connect_guide import JoinLine

    now, said = [0.0], []

    async def sleep(seconds):
        now[0] += seconds

    line = JoinLine(lambda: "", patience=30.0, clock=lambda: now[0])
    await line.say(said.append, poll=1.0, sleep=sleep)
    assert said == [post_join_line("")] and now[0] == 30.0

"""The daemon's terminal (kollabor/code_service/terminal.py): links, long polls, replay, the session cap (#987).

The engine bridges a browser socket to ``terminal.message`` / ``terminal.poll`` / ``terminal.detach``. Commands print
their marker with a quoted split (``12''3`` shows as ``123``), so the echo of the typed line can never satisfy a
check; only running the command does.
"""

import json
import re
import time
import uuid

import pytest
import pytest_asyncio

from kollabor import code_service
from kollabor.code_service import terminal
from kollabor.code_service.settings import CodeSettings

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _shells(tmp_path):
    """HOME is tmp_path (conftest); an empty rc skips zsh's first-run wizard. Every shell ends with the test."""
    (tmp_path / ".zshrc").write_text("")
    yield
    await code_service.shutdown()


class Link:
    """What the engine does for one browser socket, minus the socket."""

    def __init__(self, cwd, settings: CodeSettings | None = None) -> None:
        self.settings = settings
        self.id = uuid.uuid4().hex
        self.cwd = str(cwd)
        self.ack: int | None = None

    async def call(self, op: str, **params):
        answer = await code_service.call(
            f"terminal.{op}", {"link": self.id, **params}, root=self.cwd, settings=self.settings
        )
        assert answer["status"] == 200, answer
        return answer["body"]["data"]

    async def send(self, **message):
        return (await self.call("message", raw=json.dumps(message)))["messages"]

    async def poll(self, wait: float = 5.0, *, advance: bool = True):
        params = {"wait": wait}
        if self.ack is not None:
            params["ack"] = self.ack
        data = await self.call("poll", **params)
        if advance:
            self.ack = data["ack"]
        return data["messages"]

    async def open(self, **extra) -> str:
        ready = (await self.send(type="open", cols=100, rows=30, **extra))[0]
        assert ready["type"] == "ready" and ready["sessionId"], ready
        return ready["sessionId"]

    async def until(self, marker: str) -> str:
        """Poll until ``marker`` shows up in the output; any message that is not output fails the test."""
        seen = ""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for message in await self.poll():
                if message["type"] != "output":
                    pytest.fail(f"expected output, got: {message}")
                seen += message["data"]
            if marker in seen:
                return seen
        pytest.fail(f"{marker!r} never showed up")


async def test_open_answers_ready_at_once_then_runs_the_shell_in_the_folder(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="pwd; echo kollab-marker-12''3\n")

    output = await link.until("kollab-marker-123")

    assert str(tmp_path.resolve()) in output


async def test_a_folder_named_in_the_message_is_ignored(tmp_path):
    # the shell opens in the daemon's own folder, whatever a client sends
    link = Link(tmp_path)
    await link.open(workspace="/etc")
    await link.send(type="input", data="pwd; echo kollab-marker-12''4\n")

    output = await link.until("kollab-marker-124")

    assert str(tmp_path.resolve()) in output
    assert "/etc\r" not in output


async def test_a_reattach_replays_the_output_and_the_shell_keeps_running(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    await first.send(type="input", data="echo kollab-marker-12''6\n")
    await first.until("kollab-marker-126")
    await first.call("detach")  # the browser socket is gone; the shell is not

    again = Link(tmp_path)
    assert await again.send(type="open", sessionId=session_id) == [{"type": "ready", "sessionId": session_id}]
    assert "kollab-marker-126" in await again.until("kollab-marker-126")
    await again.send(type="input", data="echo kollab-after-''7\n")
    await again.until("kollab-after-7")


async def test_reattach_with_cursor_skips_already_received_output(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    await first.send(type="input", data="echo old-marker-1''26\n")
    await first.until("old-marker-126")
    cursor = first.ack
    await first.call("detach")

    again = Link(tmp_path)
    await again.open(sessionId=session_id, cursor=cursor)
    await again.send(type="input", data="echo new-marker-1''27\n")
    output = await again.until("new-marker-127")
    assert "old-marker-126" not in output


async def test_reattach_reports_a_gap_when_the_ring_has_wrapped(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    session = terminal._SESSIONS[session_id]
    session._ring_limit = 4
    session._publish("old!")
    session._publish("new!")
    link = terminal._Link()
    link.attach(session, 0)
    messages, _ = link.pending()
    assert messages[0] == {"type": "gap", "nextSeq": session.first_seq}
    assert messages[1]["data"] == "new!"
    assert messages[1]["seq"] == session.first_seq
    link.detach()


async def test_large_escaped_scrollback_is_paged_and_replayed(tmp_path):
    client = Link(tmp_path, CodeSettings(scrollback_kb=65536))
    sid = await client.open()
    session = terminal._SESSIONS[sid]
    text = "\x01" * terminal.READ_BYTES
    for _ in range(64):
        session._publish(text)
    link = terminal._Link()
    link.attach(session, 0)
    result = []
    while True:
        messages, nxt = link.pending()
        assert len(json.dumps(messages).encode()) <= 1024 * 1024
        assert link.pending() == (messages, nxt)  # lost reply is replayed
        result.extend(m for m in messages if m["type"] == "output")
        if nxt == session.next_seq:
            break
        assert nxt > link.seq
        link.seq = nxt
    assert [m["seq"] for m in result] == list(range(session.first_seq, session.next_seq))
    assert "".join(m["data"] for m in result) == text * 64
    link.detach()


async def test_paged_scrollback_keeps_gap_and_exit_order(tmp_path):
    client = Link(tmp_path)
    sid = await client.open()
    session = terminal._SESSIONS[sid]
    session._ring_limit = terminal.MAX_POLL_MESSAGES + 5
    for _ in range(terminal.MAX_POLL_MESSAGES * 2):
        session._publish("x")
    link = terminal._Link()
    link.attach(session, 0)
    try:
        session.exited = True  # simulate exit without allowing the fixture to lose the shell
        first, ack = link.pending()
        assert first[0] == {"type": "gap", "nextSeq": session.first_seq}
        assert len(first) == terminal.MAX_POLL_MESSAGES
        assert all(message["type"] != "exit" for message in first)
        link.seq = ack
        second, ack = link.pending()
        assert all(message["type"] == "output" for message in second)
        assert ack == session.next_seq
        link.seq = ack
        assert link.pending()[0] == [{"type": "exit", "code": None}]
    finally:
        session.exited = False
        link.detach()


async def test_a_poll_whose_answer_was_lost_is_sent_again(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="echo kollab-marker-12''8\n")
    await link.until("kollab-marker-128")
    kept = link.ack

    await link.send(type="input", data="echo kollab-later-''9\n")
    await link.until("kollab-later-9")
    link.ack = kept  # the engine never saw the last answer, so it acknowledges the old position
    resent = "".join(m["data"] for m in await link.poll(advance=False))

    assert "kollab-later-9" in resent


async def test_a_poll_never_holds_the_connection_for_long_and_output_ends_it_early(tmp_path):
    # the daemon answers one connection's requests in order: a long poll would hold up every keystroke behind it
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="echo kollab-ready-''0\n")
    await link.until("kollab-ready-0")
    while await link.poll(wait=0.5):  # drain what follows the marker (the next prompt)
        pass
    started = time.monotonic()

    quiet = await link.poll(wait=20)

    assert quiet == [] and 0.05 <= time.monotonic() - started < 1.0
    await link.send(type="input", data="echo kollab-wake-''1\n")
    output = await link.until("kollab-wake-1")  # polls again and again until the output arrives
    assert "kollab-wake-1" in output


async def test_the_ninth_open_is_refused_with_an_error(tmp_path):
    for _ in range(terminal.MAX_SESSIONS):
        await Link(tmp_path).open()

    refused = (await Link(tmp_path).send(type="open"))[0]

    assert refused["type"] == "error"
    assert "limit" in refused["message"]


async def test_a_bad_link_id_is_refused_and_detach_forgets_the_link(tmp_path):
    bad = await code_service.call("terminal.poll", {"link": "short"}, root=str(tmp_path))
    link = Link(tmp_path)
    await link.open()
    await link.call("detach")

    assert bad["status"] == 400
    assert link.id not in terminal._LINKS


async def test_an_idle_link_is_swept_and_its_shell_is_left_to_the_reaper(tmp_path):
    link = Link(tmp_path)
    session_id = await link.open()
    terminal._LINKS[link.id].touched -= terminal.LINK_IDLE_SECONDS + 1

    terminal._sweep()

    assert link.id not in terminal._LINKS
    assert terminal._SESSIONS[session_id].attached == 0


async def test_the_shell_gets_no_kollab_or_secret_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="env; echo kollab-end-$((40+2))\n")

    output = await link.until("kollab-end-42")

    assert "TERM=xterm-256color" in output  # the environment was printed
    assert "KOLLAB_" not in output
    assert "MY_TOKEN" not in output


async def test_the_shell_sees_secret_named_variables_when_the_setting_is_on(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    link = Link(tmp_path, CodeSettings(pass_secret_env=True))
    await link.open()
    await link.send(type="input", data="env; echo kollab-end-$((40+2))\n")

    output = await link.until("kollab-end-42")

    assert "MY_TOKEN=2" in output
    assert "KOLLAB_" not in output


def test_shell_env_drops_kollab_and_secret_names(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")

    env = terminal._shell_env()

    assert "KOLLAB_X" not in env and "MY_TOKEN" not in env
    assert env["TERM"] == "xterm-256color"


def test_shell_env_drops_common_credential_names_and_keeps_ordinary_ones(monkeypatch):
    # #987 review: these slipped through the four-marker rule.
    secrets = (
        "DATABASE_URL",
        "GITHUB_PAT",
        "PAT",
        "OPENAI_APIKEY",
        "DB_PASSWD",
        "AWS_CREDENTIALS",
        "SSH_PRIVATE_KEY",
        "AWS_ACCESS_KEY_ID",
    )
    for name in secrets:
        monkeypatch.setenv(name, "x")
    for name in ("PATTERN_DIR", "EDITOR_PATH_HINT", "PATHEXT_NOTE"):
        monkeypatch.setenv(name, "ok")

    env = terminal._shell_env()

    assert not [name for name in secrets if name in env]
    assert env["PATTERN_DIR"] == "ok" and env["EDITOR_PATH_HINT"] == "ok" and env["PATHEXT_NOTE"] == "ok"
    assert "PATH" in env


def test_shell_env_passes_common_credential_names_when_the_setting_is_on(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "p")
    monkeypatch.setenv("DATABASE_URL", "d")

    env = terminal._shell_env(CodeSettings(pass_secret_env=True))

    assert env["GITHUB_PAT"] == "p" and env["DATABASE_URL"] == "d"


def test_shell_env_keeps_secret_names_when_the_setting_is_on_but_never_kollab_names(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("KOLLABOR_HUB_BRIDGE_TOKEN", "3")
    monkeypatch.setenv("MY_TOKEN", "2")

    env = terminal._shell_env(CodeSettings(pass_secret_env=True))

    assert env["MY_TOKEN"] == "2"
    assert "KOLLAB_X" not in env and "KOLLABOR_HUB_BRIDGE_TOKEN" not in env


async def test_the_shell_sits_on_a_real_terminal_and_leads_its_process_group(tmp_path):
    """The shell's stdio is a PTY device, and the shell is its own process group leader (job control needs both)."""
    link = Link(tmp_path)
    await link.open()
    await link.send(
        type="input", data="echo kollab-tty-$$-$(ps -o pgid= -p $$ | tr -d ' ')-$(tty)-kollab-end-''$((6*7))\n"
    )

    output = await link.until("kollab-end-42")

    found = re.search(r"kollab-tty-(\d+)-(\d+)-(/dev/ttys\d+|/dev/pts/\d+)-kollab-end-42", output)
    assert found, output
    shell_pid, process_group, _device = found.groups()
    assert shell_pid == process_group


async def test_ctrl_c_interrupts_a_running_command_and_the_shell_prompts_again(tmp_path):
    link = Link(tmp_path)
    await link.open()
    started = time.monotonic()
    await link.send(type="input", data="echo kollab-sleeping-''$((2*3)); sleep 30\n")
    await link.until("kollab-sleeping-6")
    time.sleep(0.5)  # let sleep take the foreground; a Ctrl-C that lands before that would reach the shell

    await link.send(type="input", data="\x03")
    await link.send(type="input", data="echo kollab-after-''$((5*5))\n")
    await link.until("kollab-after-25")

    assert time.monotonic() - started < 20  # a sleep that ignored Ctrl-C would hold this test for its 30 seconds


async def test_a_shell_that_exits_ends_the_stream_with_an_exit_message(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="exit 3\n")

    kinds = []
    deadline = time.monotonic() + 20
    while "exit" not in kinds and time.monotonic() < deadline:
        kinds += [message["type"] for message in await link.poll()]

    assert kinds[-1] == "exit"

"""Kill switch review fixes (F2): sweep reach, bad halt.json, reboot, guards, boot edges.

No real halt, sweep or listener: process lists and stop functions are injected.
A real-child test spawns its own throwaway `sleep` and kills it itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

FOLDER = "/work/kollab"
NET = "net1"


@pytest.fixture(autouse=True)
def _clean_entry():
    hr.ENTRY.clear()
    yield
    hr.ENTRY.clear()


def _write_raw(root: Path, content: bytes, mode: int = 0o600) -> Path:
    path = root / "private" / "halt.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return path


async def _stop_ok(rec):
    return True, "ok"


class _Link:
    async def close(self):
        pass


def _book(root: Path, net: str = NET) -> str:
    """A recorded, active halt in halt.json (what halt_act leaves behind)."""
    h = "ab" * 32
    nh.HaltFile(root).write({
        "scopes": {}, "stopped": [],
        "networks": {net: {"halts": {h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}},
    })
    return h


# ---------------------------------------------------------------- B3


def test_sweep_reaches_a_tool_child_with_no_kollab_in_its_command_line(tmp_path):
    """Started like shell_executor does: own session, env tagged with the agent's pid."""
    py = sys._base_executable  # not a system binary: macOS hides the environment of those
    sleeper = [py, "-c", "import time; time.sleep(60)"]
    assert "kollab" not in " ".join(sleeper)
    (tmp_path / "kollabor").mkdir()
    # the agent starts the tool child itself (F10: a tool child is found by its parent chain, not its tag)
    (tmp_path / "main.py").write_text(
        "import os, subprocess, sys, time\n"
        f"t = subprocess.Popen({sleeper!r}, start_new_session=True,\n"
        "    env={**os.environ, 'KOLLAB_TOOL_SHELL': '1', 'KOLLAB_AGENT_PID': str(os.getpid())})\n"
        "print(t.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    agent = subprocess.Popen([py, str(tmp_path / "main.py")], cwd=tmp_path, start_new_session=True,
                             stdout=subprocess.PIPE, text=True)

    class _Tool:  # the agent's child: signalled and polled by pid, never waited on here
        pid = int(agent.stdout.readline())

        def wait(self, timeout=None):
            deadline = time.monotonic() + (timeout or 0)
            while time.monotonic() < deadline:
                try:
                    os.kill(self.pid, 0)
                except ProcessLookupError:
                    return 0
                time.sleep(0.05)
            return None

        def kill(self):
            with contextlib.suppress(OSError):
                os.kill(self.pid, 9)

    tool = _Tool()
    try:
        assert os.getpgid(tool.pid) == tool.pid != os.getpgid(agent.pid)
        install = nh.Install(prefix=sys.base_prefix)
        procs = hr._read_candidates(install)
        assert tool.pid in {p.pid for p in procs}
        stopped = []

        async def stop(rec):
            stopped.append(rec["pid"])
            hr.group_kill({os.getpid()})(rec["pid"], 15)  # its own group only
            return True, "ok"

        asyncio.run(hr.sweep(
            [[str(tmp_path)]], procs=procs, stop=stop, protect={os.getpid()},
            agents=[{"identity": "x", "pid": agent.pid, "project": str(tmp_path)}],
            install=nh.Install(prefix=sys.base_prefix), tree=hr.process_tree(),
        ))
        assert tool.pid in stopped and agent.pid in stopped
        assert tool.wait(timeout=5) is not None
    finally:
        tool.kill()
        agent.kill()
        agent.wait()


# ---------------------------------------------------------------- S1


@pytest.mark.parametrize(
    "content,mode",
    [
        (b"{not json", 0o600),
        (b"null", 0o600),
        (b"[]", 0o600),
        (b'{"scopes": []}', 0o600),
        (b'{"scopes": {"n": "machine"}, "networks": {}}', 0o644),
    ],
)
def test_a_bad_halt_json_holds_every_folder(tmp_path, monkeypatch, capsys, content, mode):
    monkeypatch.chdir(tmp_path)
    _write_raw(tmp_path, content, mode)
    held = hr.read_halt_held(tmp_path)
    assert hr.folder_halted("/anywhere", held)
    with pytest.raises(SystemExit) as stop:
        hr.boot_check(["kollab", "-p", "hi"], {}, root=tmp_path)
    assert stop.value.code == 75
    assert "halt.json" in capsys.readouterr().err


def test_a_big_halt_json_is_still_read_whole(tmp_path):
    body = json.dumps({"scopes": {NET: "machine"}, "networks": {}}).encode() + b" " * (5 << 20)
    _write_raw(tmp_path, body)
    assert hr.read_halt(tmp_path)["scopes"] == {NET: "machine"}


def test_the_listener_tick_survives_a_bad_halt_json(tmp_path):
    _write_raw(tmp_path, b"null")
    listener = hr.HaltListener(tmp_path, grace=0)
    asyncio.run(listener.tick())  # logs, keeps going
    assert not listener._done.is_set()


def test_write_refuses_what_it_could_not_read_back(tmp_path):
    big = {"scopes": {}, "networks": {}, "pad": "x" * (nh.HALT_FILE_LIMIT + 1)}
    with pytest.raises(nh.HaltError):
        nh.HaltFile(tmp_path).write(big)


# ---------------------------------------------------------------- S2


def test_a_machine_halt_reopens_its_link_from_the_recorded_folder_after_a_reboot(tmp_path, monkeypatch):
    opened = []

    async def open_link(folder, state_dir):
        opened.append(folder)
        return _Link()

    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: Path(folder))
    nh.HaltFile(tmp_path).write(
        {"scopes": {NET: "machine"}, "networks": {}, "folders": {NET: [FOLDER]}, "stopped": []}
    )
    listener = hr.HaltListener(tmp_path, open_link=open_link, grace=0)
    asyncio.run(listener._reopen_links())
    assert opened == [FOLDER] and NET in listener.links


def test_take_halt_records_the_hand_off_folder(tmp_path, monkeypatch):
    async def open_link(folder, state_dir):
        return _Link()

    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: Path(folder))
    _book(tmp_path)
    listener = hr.HaltListener(tmp_path, open_link=open_link, grace=0, stop=_stop_ok,
                               processes=lambda: [], agents=lambda: [])
    listener._networks_of = lambda folder: [NET]  # F15b: the hand-off folder belongs to this network
    asyncio.run(listener.handle({"op": "halt", "network": NET, "scope": "machine", "folder": FOLDER}))
    assert hr.read_halt(tmp_path)["folders"][NET] == [FOLDER]


# ---------------------------------------------------------------- S5


def test_halt_sock_halt_without_a_recorded_order_sweeps_nothing(tmp_path):
    swept = []

    async def stop(rec):
        swept.append(rec)
        return True, "ok"

    listener = hr.HaltListener(tmp_path, stop=stop, processes=lambda: [], grace=0,
                               agents=lambda: [{"identity": "a", "pid": 99999, "project": FOLDER}])
    reply = asyncio.run(listener.handle({"op": "halt", "network": NET, "scope": "machine"}))
    assert not reply["ok"] and swept == []
    assert hr.read_halt(tmp_path).get("scopes", {}) == {}


def test_halt_sock_halt_with_a_wrong_hash_is_refused(tmp_path):
    _book(tmp_path)
    listener = hr.HaltListener(tmp_path, processes=lambda: [], agents=lambda: [], grace=0, stop=_stop_ok)
    reply = asyncio.run(listener.handle({"op": "halt", "network": NET, "scope": "machine", "hash": "cd" * 32}))
    assert not reply["ok"]


def test_act_from_a_tool_shell_peer_is_refused(tmp_path, monkeypatch):
    listener = hr.HaltListener(tmp_path, grace=0)
    monkeypatch.setattr(hr, "_peer_is_tool_shell", lambda writer: True)

    class W:
        written = b""

        def write(self, b):
            W.written += b

        async def drain(self):
            pass

        def close(self):
            pass

    class R:
        async def readline(self):
            return b'{"op":"act","action":"halt"}\n'

    asyncio.run(listener._client(R(), W()))
    reply = json.loads(W.written)
    assert not reply["ok"] and reply["error"] == nh.GUARD_REFUSAL


def test_slash_halt_and_revoke_refuse_in_a_tool_shell(monkeypatch):
    from plugins.hub.relay_commands import RelayCommands

    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    rc = object.__new__(RelayCommands)
    rc.agent_bridge = object()
    assert asyncio.run(rc._halt_command("halt", "")) == nh.GUARD_REFUSAL
    assert asyncio.run(rc._halt_command("resume", "--local")) == nh.GUARD_REFUSAL
    assert hr.tool_shell_refusal() == nh.GUARD_REFUSAL


def test_connect_revoke_refuses_in_a_tool_shell(monkeypatch):
    from plugins.hub.relay_commands import RelayCommands

    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    rc = object.__new__(RelayCommands)
    rc.agent_bridge = object()
    out = asyncio.run(rc._run("revoke someone", source_agent="x"))
    assert out == nh.GUARD_REFUSAL


def test_hub_halt_act_refuses_in_a_tool_shell(monkeypatch):
    from plugins.hub.relay_agent import RelayAgentBridge
    from plugins.hub.relay_state import RelayError

    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    bridge = object.__new__(RelayAgentBridge)
    with pytest.raises(RelayError):
        asyncio.run(bridge.hub_halt_act("halt", {}))


# ---------------------------------------------------------------- M1

from kollabor_events.permissions_models import RiskAssessmentRules  # noqa: E402


def _high(cmd: str) -> bool:
    return any(p.search(cmd) for p in RiskAssessmentRules().high_risk_patterns)


@pytest.mark.parametrize("cmd", [
    "rm -rf ~/.kollab/private",
    "cd ~/.kollab/private && rm halt.json",
    "find ~/.kollab/private -name 'halt*' -delete",
    "python3 -c \"import os; os.remove('/home/u/.kollab/private/halt.json')\"",
    "python -c 'open(\"/home/u/.kollab/private/halt.json\",\"w\")'",
    "chmod 000 ~/.kollab/private/halt.json",
    "chmod -R 777 ~/.kollab/private",
    "kollab --hub 'halt'",
    'kollab --hub "resume" --local',
    "python -m kollabor_cli_main --hub halt",
    "uv run python -m kollabor_cli_main --hub resume --confirm ab",
])
def test_high_risk_covers_the_kill_switch_shapes(cmd):
    assert _high(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "cat ~/.kollab/private/halt.json", "kollab --hub status", "ls ~/.kollab/private",
    "kollab --hub capture lapis 60", "python -c 'print(1)'",
])
def test_high_risk_leaves_read_only_alone(cmd):
    assert not _high(cmd), cmd


# ---------------------------------------------------------------- M4, d

HALTED = {"scopes": {NET: [FOLDER]}, "networks": {}}


def test_a_prompt_that_looks_like_an_exempt_flag_does_not_skip_the_halt():
    assert hr.boot_decision(["kollab", "-p", "--", "-h"], {}, FOLDER, HALTED) == "exit"
    assert hr.boot_decision(["kollab", "fix the bug", "--version"], {}, FOLDER, HALTED) == "interactive"
    assert hr.boot_decision(["kollab", "--model", "x", "-h"], {}, FOLDER, HALTED) == "run"
    assert hr.boot_decision(["kollab", "--hub", "status"], {}, FOLDER, HALTED) == "run"


def test_pipe_mode_without_p_exits_75_to_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    nh.HaltFile(tmp_path).write({"scopes": {NET: "machine"}, "networks": {}})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as stop:
        hr.boot_check(["kollab", "write a poem"], {}, root=tmp_path)
    assert stop.value.code == 75
    out = capsys.readouterr()
    assert "Network halted" in out.err and out.out == ""


# ---------------------------------------------------------------- M6


def test_group_kill_survives_a_protected_pid_dying_mid_check(monkeypatch):
    real = os.getpgid
    calls = []

    def getpgid(pid):
        if pid == 777:
            raise ProcessLookupError
        return real(pid)

    monkeypatch.setattr(os, "getpgid", getpgid)
    monkeypatch.setattr(hr, "_alive", lambda p: True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid)))
    monkeypatch.setattr(os, "killpg", lambda pg, sig: calls.append(("killpg", pg)))
    hr.group_kill({777})(4242424, 15)  # must not raise
    assert calls


# ---------------------------------------------------------------- M7


def test_a_failed_detached_exec_fails_closed_under_a_halt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nh.HaltFile(tmp_path).write({"scopes": {NET: "machine"}, "networks": {}})
    assert hr.halted_after_exec_failure(["kollab", "--detached"], root=tmp_path)
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})
    assert not hr.halted_after_exec_failure(["kollab", "--detached"], root=tmp_path)


# ---------------------------------------------------------------- extras


def test_waiters_are_found_by_the_waiters_dir_not_the_environment(tmp_path):
    hr.register_waiter(tmp_path)
    assert os.getpid() in hr.waiter_pids(tmp_path)
    hr.unregister_waiter(tmp_path)
    assert os.getpid() not in hr.waiter_pids(tmp_path)


def test_sweep_spares_a_registered_waiter_that_has_no_presence():
    install = nh.Install(prefix="/x")
    waiter = nh.Proc(11, 1, ["/x/python", "-m", "kollabor_cli_main"], {}, FOLDER, "/x/python")
    got = []

    async def stop(rec):
        got.append(rec["pid"])
        return True, "ok"

    asyncio.run(hr.sweep(["machine"], agents=[], procs=[waiter], stop=stop, protect={0}, install=install,
                         waiters={11}))
    assert got == []


def test_a_presence_pid_that_is_not_this_installs_kollab_is_not_stopped():
    install = nh.Install(prefix="/x")
    reused = nh.Proc(50, 1, ["/usr/bin/vim", "notes.txt"], {}, FOLDER, "/usr/bin/vim")
    got = []

    async def stop(rec):
        got.append(rec["pid"])
        return True, "ok"

    asyncio.run(hr.sweep(["machine"], agents=[{"identity": "a", "pid": 50, "project": FOLDER}],
                         procs=[reused], stop=stop, protect=set(), install=install))
    assert got == []


def test_a_non_counting_machine_halt_covers_every_folder_of_the_network(tmp_path, monkeypatch):
    async def open_link(folder, state_dir):
        return _Link()

    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: Path(folder))
    _book(tmp_path)
    listener = hr.HaltListener(tmp_path, open_link=open_link, grace=0, stop=_stop_ok, processes=lambda: [],
                               agents=lambda: [{"identity": "b", "pid": 1, "project": "/work/other",
                                                "state": "halted"}])
    monkeypatch.setattr(listener, "_networks_of", lambda folder: [NET])
    asyncio.run(listener.handle({"op": "halt", "network": NET, "scope": [FOLDER], "folder": FOLDER}))
    assert set(hr.read_halt(tmp_path)["scopes"][NET]) == {FOLDER, "/work/other"}


def test_listener_running_never_takes_the_lock(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("listener_running took a lock")

    monkeypatch.setattr(hr.fcntl, "flock", boom)
    assert hr.listener_running(tmp_path) is False


def test_listener_running_sees_a_listener_by_its_recorded_pid(tmp_path):
    listener = hr.HaltListener(tmp_path)
    assert listener.take_lock()
    try:
        assert hr.listener_running(tmp_path) is True
    finally:
        listener.drop_lock()


# ---------------------------------------------------------------- M5


def test_cli_halt_goes_through_the_running_owner_else_direct(monkeypatch):
    from plugins.hub import halt_context as hc

    calls = []

    class Control:
        def halt_act(self, action, payload):
            calls.append(("direct", action))
            return {"ok": True}

    async def owner(self, op, args):
        calls.append(("owner", args["action"]))
        return {"ok": True}

    monkeypatch.setattr(hc.HaltContext, "_owner", owner)
    asyncio.run(hr._via_owner("/f", Control(), "halt", None, {}))

    async def none(self, op, args):
        raise hc.NoOwner("nothing runs this folder's network")

    monkeypatch.setattr(hc.HaltContext, "_owner", none)
    asyncio.run(hr._via_owner("/f", Control(), "halt", None, {}))
    assert calls == [("owner", "halt"), ("direct", "halt")]

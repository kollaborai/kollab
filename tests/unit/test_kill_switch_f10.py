"""Kill switch round 7 (F10): group kill only by a leader, the caller's ppid chain decides, odd scopes hold.

The R6-1 and NEW-1 tests start real processes on this machine (sleep, sh, nc) and signal only those.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

INSTALL = nh.Install(prefix="/opt/this-install")
posix_only = pytest.mark.skipif(sys.platform not in ("darwin",) and not sys.platform.startswith("linux"),
                                reason="needs real process groups")


def _gone(proc: subprocess.Popen, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def _child_of(pid: int, seconds: float = 3.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
        kids = [int(a) for a, b in (line.split() for line in out.splitlines()) if int(b) == pid]
        if kids:
            return kids[0]
        time.sleep(0.05)
    raise AssertionError(f"no child of {pid}")


# ------------------------------------------------------------------ R6-1


@posix_only
def test_r6_1_group_kill_of_a_non_leader_leaves_the_launcher_and_siblings():
    """Launcher 300 leads the group; sibling 310, agent and its child share it. Only the agent dies."""
    launcher = subprocess.Popen(["/bin/sleep", "60"], process_group=0)  # leads a group in this session
    group = launcher.pid
    sibling = subprocess.Popen(["/bin/sleep", "60"], process_group=group)
    agent = subprocess.Popen(["/bin/sleep", "60"], process_group=group)
    child = subprocess.Popen(["/bin/sleep", "60"], process_group=group)
    procs = (launcher, sibling, agent, child)
    try:
        assert os.getpgid(agent.pid) == group != agent.pid
        hr.group_kill({os.getpid()})(agent.pid, 15)
        assert _gone(agent)
        time.sleep(0.2)
        assert launcher.poll() is None and sibling.poll() is None and child.poll() is None
        # a leader still takes its whole group
        hr.group_kill({os.getpid()})(launcher.pid, 15)
        assert _gone(launcher) and _gone(sibling) and _gone(child)
    finally:
        for p in procs:
            with contextlib.suppress(OSError):
                p.kill()
            p.wait()


# ------------------------------------------------------------------ NEW-1


def _socket_dir() -> str:
    return tempfile.mkdtemp(prefix="ksf10", dir="/tmp")  # sun_path is short: tmp_path is too long on macOS


@posix_only
@pytest.mark.skipif(not shutil.which("nc"), reason="needs nc")
@pytest.mark.parametrize("is_agent", [True, False])
def test_new_1_nc_with_an_empty_env_under_an_agent_cannot_lift_a_halt(monkeypatch, is_agent):
    """The exact attack: an agent's shell runs `nc -U halt.sock` with resume_local + confirmed:true.

    The shell and nc carry no environment at all; the only thing that gives them away is their parent chain.
    """
    d = _socket_dir()
    sock = os.path.join(d, "h.sock")
    request = json.dumps({"op": "act", "action": "resume_local", "payload": {"confirmed": True}})
    listener = hr.HaltListener(Path(d), grace=0)
    handled: list = []

    async def handle(req, peer=None):
        handled.append(req)
        return {"ok": False, "error": "handled"}

    monkeypatch.setattr(listener, "handle", handle)
    shell: dict = {}
    monkeypatch.setattr(hr, "_live_agents", lambda: [{"pid": shell["pid"]}] if is_agent else [])

    async def run() -> dict:
        server = await asyncio.start_unix_server(listener._client, path=sock)
        try:
            # the "agent": a process whose child (sh -> nc) has no environment; the agent itself is the sh here
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", f"printf '%s\\n' '{request}' | {shutil.which('nc')} -U {sock}; true",
                env={}, stdout=asyncio.subprocess.PIPE,
            )
            shell["pid"] = proc.pid  # nc's parent; read lazily, after the request line arrived
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
            return json.loads(out.decode().strip().splitlines()[-1])
        finally:
            server.close()
            await server.wait_closed()

    try:
        reply = asyncio.run(run())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if is_agent:
        assert reply == {"ok": False, "error": nh.GUARD_REFUSAL} and handled == []
    else:  # not under an agent: the act reaches the listener (no over-refusal)
        assert reply.get("error") == "handled" and handled


def test_new_1_an_unknown_or_gone_caller_is_refused(monkeypatch):
    monkeypatch.setattr(hr, "_live_agents", lambda: [])
    assert hr.caller_refusal(0) == nh.GUARD_REFUSAL
    assert hr.caller_refusal(None) == nh.GUARD_REFUSAL
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead.wait()
    assert hr.caller_refusal(dead.pid) == nh.GUARD_REFUSAL

    class W:
        def get_extra_info(self, name):
            return None  # no socket: the peer cannot be read

    assert hr._peer_is_tool_shell(W()) is True


@posix_only
def test_new_1_hub_path_refuses_roster_and_count_acts_by_the_callers_chain(monkeypatch):
    """relay_agent.hub_halt_act judges HALT_CALLER (set by the hub socket), for every confirmed act."""
    from plugins.hub.relay_agent import RelayAgentBridge
    from plugins.hub.relay_state import RelayError

    agent = subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 60; true"], env={})
    try:
        caller = _child_of(agent.pid)  # sleep, env hidden/empty, under the "agent"
        monkeypatch.setattr(hr, "_live_agents", lambda: [{"pid": agent.pid}])
        bridge = object.__new__(RelayAgentBridge)
        for action in sorted(nh.CONFIRMED_ACTIONS):
            token = hr.HALT_CALLER.set(caller)
            try:
                with pytest.raises(RelayError, match="tool shell|agent|person"):
                    asyncio.run(bridge.hub_halt_act(action, {"confirmed": True}))
            finally:
                hr.HALT_CALLER.reset(token)
    finally:
        agent.kill()
        agent.wait()


@posix_only
def test_new_1_the_hub_socket_records_its_peer_for_the_act():
    """The real hub socket's connect_proxy sets HALT_CALLER to the connecting process while the act runs."""
    from plugins.hub.messenger import AgentMessenger, AgentSocketServer

    seen: list = []

    async def on_proxy(op, args):
        seen.append(hr.HALT_CALLER.get())
        return {}

    async def run():
        server = AgentSocketServer("f10", lambda _m: None, on_connect_proxy=on_proxy,
                                   socket_name=f"f10-proxy-{os.getpid()}")
        path = await server.start()
        try:
            await AgentMessenger.request_connect_proxy(path, "halt_act", {})
        finally:
            await server.stop()

    asyncio.run(run())
    assert seen == [os.getpid()] and hr.HALT_CALLER.get() is None


# ------------------------------------------------------------------ R6-3


def test_r6_3_an_odd_scope_holds_the_machine_instead_of_lifting_its_network(tmp_path):
    path = tmp_path / "private" / "halt.json"
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})
    path.write_text(json.dumps({"scopes": {"A": "machine", "B": {"odd": 1}}, "networks": {}}))
    note = hr.clear_malformed_halt(tmp_path)
    assert "1 odd scope" in note
    assert hr.read_halt(tmp_path)["scopes"] == {"A": "machine", "B": "machine"}


def _cli(monkeypatch, acts):
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.setattr(hr, "tool_shell_refusal", lambda: "")
    monkeypatch.setattr(hr, "_folder_control", lambda f: object())
    calls: list = []

    async def act(folder, control, action, payload, root):
        calls.append(action)
        outcome = acts[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(hr, "_act", act)
    return calls


def test_r6_3_cli_resume_local_leaves_a_good_file_when_the_act_fails(tmp_path, monkeypatch):
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine"}, "networks": {"n": {"halts": {}}}})
    before = (tmp_path / "private" / "halt.json").read_bytes()
    calls = _cli(monkeypatch, [RuntimeError("listener refused")])
    assert asyncio.run(hr.cli_control("resume", ["--local"], folder="/f", root=tmp_path)) == 1
    assert calls == ["resume_local"]
    assert (tmp_path / "private" / "halt.json").read_bytes() == before
    assert not (tmp_path / "private" / "halt.json.bad").exists()


def test_r6_3_cli_resume_local_repairs_an_odd_scope_to_machine_then_retries(tmp_path, monkeypatch):
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})
    (tmp_path / "private" / "halt.json").write_text(
        json.dumps({"scopes": {"A": "machine", "B": 7}, "networks": {}}))
    calls = _cli(monkeypatch, [RuntimeError("unreadable"), {"line": nh.LOCAL_RESUME_LINE}])
    assert asyncio.run(hr.cli_control("resume", ["--local"], folder="/f", root=tmp_path)) == 0
    assert calls == ["resume_local", "resume_local"]
    assert hr.read_halt(tmp_path)["scopes"] == {"A": "machine", "B": "machine"}


# ------------------------------------------------------------------ R6-2


def _sweep(tree, procs, agents=(100,)):
    return {p.pid for p in nh.select_sweep(
        procs, install=INSTALL, scopes=["machine"], agent_pids=list(agents), protect=set(), tree=tree,
    )}


AGENT = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")


def test_r6_2_a_tagged_orphan_is_not_swept():
    orphan = nh.Proc(130, 1, ["ssh", "-fN", "host"], {"KOLLAB_AGENT_PID": "100"}, "/work")
    owner_tagged = nh.Proc(140, 1, ["nohup", "x"], {"KOLLAB_OWNER_PID": "100"}, "/work")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 130: (1, 130, 130), 140: (1, 140, 140)}
    assert _sweep(tree, [AGENT, orphan, owner_tagged]) == {100}


@pytest.mark.parametrize("server", ["zellij", "abduco", "dtach", "tmux: server", "SCREEN"])
def test_r6_2_any_multiplexer_server_and_its_panes_survive(server):
    srv = nh.Proc(400, 1, [server], {"KOLLAB_AGENT_PID": "100"}, "/")
    pane = nh.Proc(500, 400, ["zsh"], {"KOLLAB_AGENT_PID": "100"}, "/home")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 400: (1, 400, 400), 500: (400, 500, 500)}
    assert _sweep(tree, [AGENT, srv, pane]) == {100}


def test_r6_2_no_process_tree_sweeps_nothing_tagged_only():
    child = nh.Proc(120, 100, ["python", "srv.py"], {"KOLLAB_AGENT_PID": "100"}, "/work")
    assert _sweep(None, [AGENT, child]) == {100}


def test_r6_2_the_agents_own_descendants_are_still_swept():
    child = nh.Proc(120, 100, ["python", "srv.py"], {"KOLLAB_AGENT_PID": "100"}, "/work")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 120: (100, 120, 120), 121: (120, 120, 120)}
    assert _sweep(tree, [AGENT, child]) == {100, 120, 121}

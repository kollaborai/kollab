"""Kill switch round 12 (F15b): a halt hand-off never narrows a held scope and never trusts a caller's
scope, folder or protect list; every signal (SIGTERM and SIGKILL, in the CLI ladder and the fallback)
is bound to the process selected (start time, exe, argv hash) and re-verified right before it goes out.

The reviewer reproduced the hand-off hole with an isolated socket and a fake book: a caller supplied
scope=machine, an unrelated folder and an arbitrary protected pid, and the listener accepted all three;
a later hand-off replaced a machine-wide scope with a narrower folder one while the first sweep ran.

Real-process tests signal only processes this test started. ``P1``/``P2`` sit above macOS's pid
ceiling so a stray signal hits nobody.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import types

from kollabor import cli as kcli
from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from tests.unit.test_kill_switch_f13 import KOLLAB_ARGV, P1, P2, _book, _no_link


def _scope_book(tmp_path, scopes, net="net1"):
    data = hr.read_halt_held(tmp_path)
    data.setdefault("scopes", {}).update(scopes)
    nh.HaltFile(tmp_path).write(data)


def _control(net="net1", counts=True, machine="m" * 64):
    """A folder's network control as the listener reads it from disk (view + this machine's key)."""
    return types.SimpleNamespace(
        network_id=lambda: net,
        machine_hex=machine,
        view=lambda: types.SimpleNamespace(counts=lambda m: counts and m == machine),
    )


def _listener(tmp_path, monkeypatch, *, stop=None, agents=None, procs=None, folders=None, scopes=None):
    _book(tmp_path)
    if scopes:
        _scope_book(tmp_path, scopes)
    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: None)
    listener = hr.HaltListener(
        tmp_path, stop=stop,
        processes=(lambda: procs) if procs is not None else None,
        agents=agents, open_link=_no_link,
    )
    for net, fs in (folders or {}).items():
        listener.folders.setdefault(net, set()).update(fs)
    return listener




# ------------------------------------------------------- 1. the scope of a hand-off


def test_handoff_scope_is_derived_not_claimed(tmp_path, monkeypatch):
    """A machine-wide hold stays machine-wide: the request's scope list is not a source of truth."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)  # no roster reachable: nothing derivable
    listener = _listener(tmp_path, monkeypatch, scopes={"net1": "machine"}, agents=lambda: [], procs=[])
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": ["/attacker"], "folder": "/attacker"}))
    assert reply["ok"]
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == "machine"
    assert listener.held["net1"] == "machine"


def test_handoff_only_widens_never_narrows(tmp_path, monkeypatch):
    """First hand-off holds the machine (this machine counts); a later narrow claim cannot shrink it."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: _control(counts=True))
    listener = _listener(tmp_path, monkeypatch, agents=lambda: [], procs=[])
    first = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": ["/w"], "folder": "/w"}))
    assert first["ok"] and hr.read_halt(tmp_path)["scopes"]["net1"] == "machine"
    second = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": ["/narrow"], "folder": "/narrow"}))
    assert second["ok"]
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == "machine"
    assert listener.held["net1"] == "machine"


def test_handoff_folder_scope_comes_from_the_roster(tmp_path, monkeypatch):
    """A folder-scoped hold (this machine does not count) is the folder the roster names, not the claim."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: _control(counts=False))
    listener = _listener(tmp_path, monkeypatch, folders={"net1": ["/real"]}, agents=lambda: [], procs=[])
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": ["/attacker"], "folder": "/real"}))
    assert reply["ok"]
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == ["/real"]


def test_concurrent_handoff_cannot_narrow_while_a_sweep_runs(tmp_path, monkeypatch):
    """Two hand-offs in flight: the narrower one may not win the write (one lock serializes them)."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: _control(counts=True))
    stopped = []
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def stop(r):
        nonlocal calls
        calls += 1
        if calls == 1:  # the first hand-off's sweep is the suspended one
            started.set()
            await gate.wait()
        stopped.append(r.get("pid"))
        return True, "stopped"

    agents = [{"identity": "lapis", "pid": P1, "project": "/w"}]
    procs = [nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")]
    listener = _listener(tmp_path, monkeypatch, stop=stop, agents=lambda: agents, procs=procs)

    async def main():
        wide = asyncio.create_task(listener.handle(
            {"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
        while not started.is_set():
            await asyncio.sleep(0.01)
        narrow = asyncio.create_task(listener.handle(  # arrives while the first sweep is suspended
            {"op": "halt", "network": "net1", "scope": ["/narrow"], "folder": "/narrow"}))
        await asyncio.wait_for(narrow, 5)
        gate.set()
        return await asyncio.wait_for(wide, 5)

    assert asyncio.run(main())["ok"]
    assert set(stopped) == {P1}
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == "machine"


def test_a_held_folder_scope_survives_a_machine_claim(tmp_path, monkeypatch):
    """No roster reachable: the hold stays the recorded folder list; even a machine claim widens nothing."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)
    listener = _listener(tmp_path, monkeypatch, scopes={"net1": ["/real"]}, agents=lambda: [], procs=[])
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": "machine", "folder": "/real"}))
    assert reply["ok"]
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == ["/real"]


def test_state_changes_happen_under_the_listener_lock(tmp_path, monkeypatch):
    """F15b: the halt.json read-modify-write of a hand-off runs holding the listener's state lock."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)
    listener = _listener(tmp_path, monkeypatch, agents=lambda: [], procs=[])
    real, locked = hr.update_halt, []

    def watching(fn, root=None):
        locked.append(listener.state_lock.locked())
        return real(fn, root)

    monkeypatch.setattr(hr, "update_halt", watching)
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
    assert reply["ok"] and locked and all(locked)


def test_handoff_still_requires_an_active_recorded_halt(tmp_path, monkeypatch):
    listener = _listener(tmp_path, monkeypatch, agents=lambda: [], procs=[])
    listener.held["net1"] = "machine"
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})  # the book goes empty: nothing to take
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine"}))
    assert not reply["ok"] and "no recorded halt" in reply["error"]


# ------------------------------------------------------- 2. the protect list


def test_caller_protect_list_is_ignored(tmp_path, monkeypatch):
    """A hostile `protect` pid list cannot exempt the agents from the sweep."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)
    stopped = []

    async def stop(r):
        stopped.append(r.get("pid"))
        return True, "stopped"

    agents = [{"identity": "lapis", "pid": P1, "project": "/w"}]
    procs = [nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")]
    listener = _listener(tmp_path, monkeypatch, stop=stop, agents=lambda: agents, procs=procs)
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": "machine", "folder": "/w",
         "protect": [P1, P2, 1, 2]}))
    assert reply["ok"] and stopped == [P1]


def test_peer_parents_are_protected_from_the_socket_peer(tmp_path, monkeypatch):
    """The caller's chain is read from the socket peer pid: its parents survive, a stranger does not."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)
    peer, parent = 41_900, 41_901
    monkeypatch.setattr(nh, "ancestors", lambda pid: {pid, parent})
    stopped = []

    async def stop(r):
        stopped.append(r.get("pid"))
        return True, "stopped"

    agents = [{"identity": "lapis", "pid": P1, "project": "/w"}]
    procs = [nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w"), nh.Proc(parent, 1, KOLLAB_ARGV, {}, "/w")]
    listener = _listener(tmp_path, monkeypatch, stop=stop, agents=lambda: agents, procs=procs)
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}, peer=peer))
    assert reply["ok"] and stopped == [P1] and parent not in stopped


def test_hostile_folder_is_remembered_only_for_a_network_it_belongs_to(tmp_path, monkeypatch):
    """The request's folder is a hint, taken only when this machine's memberships say it is that network's."""
    monkeypatch.setattr(hr, "_folder_control", lambda folder: None)
    listener = _listener(tmp_path, monkeypatch, agents=lambda: [], procs=[])
    monkeypatch.setattr(listener, "_networks_of", lambda folder: ["net2"])  # /attacker is net2's, not net1's
    asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/attacker"}))
    assert "/attacker" not in listener.folders.get("net1", set())
    assert listener.folders.get("net2", set()) == {"/attacker"}


# ------------------------------------------------------- 3. identity before every signal (cli ladder)


def _record_kill():
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))

    return kill, calls


def test_stop_refuses_a_reused_pid_before_sigterm(monkeypatch):
    """The pid was replaced between selection and SIGTERM: no signal, the agent is reported not killed."""
    kill, calls = _record_kill()
    ident = iter([(1000.0, "/bin/x", "h1"), (9000.0, "/bin/x", "h1")])
    monkeypatch.setattr(kcli, "_wait_for_pid_exit", lambda pid, timeout=5.0: asyncio.sleep(0, result=False))
    ok, message = asyncio.run(kcli.stop_agent_record(
        {"identity": "lapis", "pid": P1}, "test", kill=kill, identity=lambda pid: next(ident)))
    assert not ok and "identity changed" in message and calls == []


def test_stop_refuses_a_reused_pid_before_sigkill(monkeypatch):
    """SIGTERM went out, the waits passed, and the pid is someone else now: no SIGKILL."""
    kill, calls = _record_kill()
    ident = iter([(1000.0, "/bin/x", "h1"), (1000.0, "/bin/x", "h1"), (9000.0, "/bin/y", "h2")])
    monkeypatch.setattr(kcli, "_wait_for_pid_exit", lambda pid, timeout=5.0: asyncio.sleep(0, result=False))
    ok, message = asyncio.run(kcli.stop_agent_record(
        {"identity": "lapis", "pid": P1}, "test", kill=kill, identity=lambda pid: next(ident)))
    assert not ok and "identity changed" in message
    assert calls == [(P1, signal.SIGTERM)]  # the TERM, never the KILL


def test_stop_allows_a_one_second_start_jitter(monkeypatch):
    """ps start times drift by a second between reads (F13): the same process still dies."""
    kill, calls = _record_kill()
    ident = iter([(1000.0, "/bin/x", "h1"), (1001.0, "/bin/x", "h1"), (1001.5, "/bin/x", "h1")])
    waits = iter([False, True])  # survives SIGTERM, gone after SIGKILL
    monkeypatch.setattr(kcli, "_wait_for_pid_exit",
                        lambda pid, timeout=5.0: asyncio.sleep(0, result=next(waits)))
    ok, message = asyncio.run(kcli.stop_agent_record(
        {"identity": "lapis", "pid": P1}, "test", kill=kill, identity=lambda pid: next(ident)))
    assert ok and calls == [(P1, signal.SIGTERM), (P1, signal.SIGKILL)]


def test_stop_refuses_a_pid_whose_identity_cannot_be_read():
    """Alive but unreadable: no signal goes out on a guess."""
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    kill, calls = _record_kill()
    try:
        ok, message = asyncio.run(kcli.stop_agent_record(
            {"identity": "lapis", "pid": alive.pid}, "test", kill=kill, identity=lambda pid: None))
        assert not ok and "identity could not be read" in message and calls == []
    finally:
        alive.kill()
        alive.wait()


def test_stop_reports_a_replaced_exe_not_the_same_process(monkeypatch):
    """Same start time but a different binary: not the selected process, no signal."""
    kill, calls = _record_kill()
    ident = iter([(1000.0, "/bin/old", "h1"), (1000.0, "/bin/new", "h1")])
    monkeypatch.setattr(kcli, "_wait_for_pid_exit", lambda pid, timeout=5.0: asyncio.sleep(0, result=False))
    ok, message = asyncio.run(kcli.stop_agent_record(
        {"identity": "lapis", "pid": P1}, "test", kill=kill, identity=lambda pid: next(ident)))
    assert not ok and "identity changed" in message and calls == []


# ------------------------------------------------------- 4. identity in the fallback


def _fake_kill_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)), raising=True)
    return calls


def test_fallback_reverifies_identity_before_sigterm(monkeypatch):
    """The process was replaced between selection and SIGTERM: no signal at all."""
    calls = _fake_kill_recorder(monkeypatch)
    me = nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")
    ident = iter([(1000.0, "/bin/x", "h1"), (1000.0, "/bin/y", "h1")])
    got = asyncio.run(hr.fallback_stop(["machine"], [], [me], set(), grace=0.1,
                                       identify=lambda pid: next(ident)))
    assert calls == [] and len(got) == 1 and not got[0][1] and "identity changed" in got[0][2]


def test_fallback_sigkill_binds_exe_and_argv(monkeypatch):
    """It survived SIGTERM; the pid now runs a different binary: no SIGKILL for the start time alone."""
    calls = _fake_kill_recorder(monkeypatch)
    monkeypatch.setattr(hr, "_running", lambda pid: True)  # it ignores SIGTERM
    me = nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")
    ident = iter([(1000.0, "/bin/x", "h1"), (1000.0, "/bin/x", "h1"), (1000.0, "/bin/x", "h2")])
    got = asyncio.run(hr.fallback_stop(["machine"], [], [me], set(), grace=0.1,
                                       identify=lambda pid: next(ident)))
    assert calls == [(P1, signal.SIGTERM)] and len(got) == 1 and not got[0][1]
    assert "not verified as the same kollab" in got[0][2] or "identity changed" in got[0][2]


def test_fallback_still_kills_the_same_process_with_jitter(monkeypatch):
    """Start drifts a second across the three reads: the stubborn kollab still gets SIGKILL."""
    calls = _fake_kill_recorder(monkeypatch)
    monkeypatch.setattr(hr, "_running", lambda pid: True)
    me = nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")
    ident = iter([(1000.0, "/bin/x", "h1"), (1001.0, "/bin/x", "h1"), (1000.5, "/bin/x", "h1")])
    got = asyncio.run(hr.fallback_stop(["machine"], [], [me], set(), grace=0.1,
                                       identify=lambda pid: next(ident)))
    assert calls == [(P1, signal.SIGTERM), (P1, signal.SIGKILL)] and got[0][1]

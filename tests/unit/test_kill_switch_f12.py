"""Kill switch round 9 (F12): a bad presence row never stops the sweep, forged rows and names, deterministic
revocations, the caller check without ps, and a deleted halt.json that must not release a halted machine.

The real-process tests start sh, bash and sleep on this machine and signal only those.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from tests.unit.test_network_halt import Net, rev

INSTALL = nh.Install(prefix="/opt/this-install")
posix_only = pytest.mark.skipif(sys.platform != "darwin" and not sys.platform.startswith("linux"),
                                reason="needs real process groups")
GARBAGE = [
    [120, float("nan")], [120, float("inf")], [120, float("-inf")], [10**30, 1.0], [120, 10**400],
    [-5, 1.0], [0, 1.0], [1, 1.0], ["120", 1.0], [120, "1.0"], [True, 1.0], [120.0, 1.0], [[120], 1.0],
    {"pid": 120}, "120", None, [120], [120, 1.0, 999, 4], [120, 1.0, float("nan")], [120, {"a": [1]}],
]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    out = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(out.strip()) and not out.strip().startswith("Z")


def _wait(cond, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


# ------------------------------------------------------------------ NEW-4: bad rows


def test_shell_rows_keep_only_real_rows():
    good = [[300, 1000.0], [301, 1001, 100]]
    rows = nh.shell_rows([*GARBAGE, *good], 100)
    assert rows == [(300, 1000.0, 100), (301, 1001.0, 100)]
    assert nh.shell_rows([[120, 1.0, 101]], 100) == []  # recorded under another parent
    assert nh.shell_rows({"x": 1}, 100) == [] and nh.shell_rows(good, float("nan")) == []
    # json reads NaN and Infinity: a presence file can carry them
    loaded = json.loads('{"tool_shells": [[120, NaN], [121, Infinity], [122, -Infinity], [300, 5]]}')
    assert nh.shell_rows(loaded["tool_shells"], 100) == [(300, 5.0, 100)]
    assert len(nh.shell_rows([[300, 1.0]] * 10_000, 100)) == nh.MAX_SHELL_ROWS


def _agent_case(shells, **extra):
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/python", "-m", "kollabor_cli_main"], {}, "/work")
    rec = {"identity": "lapis", "pid": 100, "project": "/work", "state": "idle", "tool_shells": shells, **extra}
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 121: (1, 120, 120)}
    info = {100: (900.0, "kollab", "??"), 121: (1001.0, "sleep", "??")}
    return agent, rec, tree, info


async def _ok(r):
    return True, str(r["pid"])


def test_new_4_garbage_tool_shells_never_stop_the_sweep():
    """NaN, Infinity, huge, negative, strings and nested junk: the agent still stops, the good row still counts."""
    agent, rec, tree, info = _agent_case([*GARBAGE, [120, 1000.0]])
    bad_agents = [{"pid": float("nan")}, {"pid": "x", "project": "/w"}, {"pid": 4242, "project": "a\x00b"},
                  "not a dict", {"pid": 10**30, "tool_shells": [[1, float("nan")]]}]
    got = asyncio.run(hr.sweep(["machine"], agents=[*bad_agents, rec], procs=[agent], stop=_ok, protect=set(),
                               install=INSTALL, tree=tree, info=info))
    assert {r["pid"] for r, _ok_, _m in got} == {100, 121}


def test_new_4_a_one_megabyte_presence_row_is_bounded(tmp_path):
    junk = [[i % 50000 + 2, float("nan")] for i in range(80_000)] + [[120, 1000.0]]  # the good row is past the cap
    raw = json.dumps({"tool_shells": junk})
    assert len(raw) > 1_000_000
    agent, rec, tree, info = _agent_case(json.loads(raw)["tool_shells"])
    start = time.monotonic()
    got = asyncio.run(hr.sweep(["machine"], agents=[rec], procs=[agent], stop=_ok, protect=set(),
                               install=INSTALL, tree=tree, info=info))
    assert {r["pid"] for r, _o, _m in got} == {100} and time.monotonic() - start < 5


def test_new_4_a_row_naming_a_protected_process_takes_nothing_of_it():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    tree = {40: (1, 40, 40), 50: (40, 50, 50), 60: (50, 50, 50), 100: (50, 100, 50), 70: (1, 50, 50)}
    info = {p: (1000.0 if p == 100 else 1001.0, "x", "??") for p in tree}
    got = nh.select_sweep([agent], install=INSTALL, scopes=["machine"], agent_pids=[100], protect={60}, tree=tree,
                          tool_shells=[(50, 1000.0, 100), (60, 1000.0, 100), (40, 1000.0, 100)], info=info)
    assert {p.pid for p in got} == {100}  # 60's group and session are never chosen; 40 is not the agent's child


def test_new_4_a_failing_sweep_still_stops_the_agents(tmp_path, monkeypatch):
    agent = subprocess.Popen(["/bin/sleep", "60"])
    h = "ab" * 32
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {"net1": {"halts": {
        h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}}})

    async def boom(*a, **k):
        raise ValueError("cannot convert float NaN to integer")

    async def open_link(folder, state_dir):
        return None

    monkeypatch.setattr(hr, "sweep", boom)
    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: None)
    listener = hr.HaltListener(tmp_path, processes=lambda: [], open_link=open_link,
                               agents=lambda: [{"identity": "lapis", "pid": agent.pid, "project": "/work",
                                                "tool_shells": [[agent.pid, float("nan")]]}])
    try:
        reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/work"}))
        assert reply["ok"] and "NaN" in reply["sweep_error"] and reply["count"] == 1
        assert agent.wait(timeout=5) == -signal.SIGTERM
        assert hr.read_halt(tmp_path)["stopped"] == [{"identity": "lapis", "folder": "/work", "service": False}]
    finally:
        agent.kill()
        agent.wait()


# ------------------------------------------------------------------ forged rows and names


@pytest.fixture
def agent_proc():
    agent = subprocess.Popen(["/bin/sleep", "120"])
    time.sleep(1.1)
    yield agent
    agent.kill()
    agent.wait()


def _select(agent, rows, procs=(), tree=None):
    me = nh.Proc(agent.pid, os.getpid(), ["/opt/this-install/bin/kollab"], {}, "/work")
    return {p.pid for p in nh.select_sweep(
        [me, *procs], install=INSTALL, scopes=["machine"], agent_pids=[agent.pid],
        protect=nh.ancestors(os.getpid()), tree=tree or hr.process_tree(), tool_shells=rows, info=hr.process_info(),
    )}


@posix_only
def test_forged_row_naming_a_live_victim_group_is_ignored(agent_proc):
    """[<victim pgid>, <its real start>] written into presence: the victim is not the agent's child."""
    victim = subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)  # stands in for a login shell
    try:
        _wait(lambda: victim.pid in hr.process_info())
        start = hr.process_info()[victim.pid][0]
        for row in [(victim.pid, start, agent_proc.pid), (victim.pid, start + 1, agent_proc.pid),
                    (victim.pid, hr.process_info()[agent_proc.pid][0] - 50, agent_proc.pid)]:
            assert victim.pid not in _select(agent_proc, [row])
    finally:
        victim.kill()
        victim.wait()


def test_rows_recorded_before_the_agent_or_with_a_terminal_are_not_swept():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 201: (1, 200, 200), 301: (1, 300, 300)}
    info = {100: (1000.0, "kollab", "??"), 201: (1002.0, "sleep", "??"), 301: (1002.0, "zsh", "ttys003")}

    def pick(rows):
        return {p.pid for p in nh.select_sweep([agent], install=INSTALL, scopes=["machine"], agent_pids=[100],
                                               protect=set(), tree=tree, tool_shells=rows, info=info)}

    assert pick([(200, 1001.0, 100)]) == {100, 201}  # sanity: a real dead shell's leftover
    assert pick([(200, 900.0, 100)]) == {100}  # recorded before its agent started
    assert pick([(200, 1001.0, 101)]) == {100}  # a row of an agent that is not being stopped
    assert pick([(300, 1001.0, 100)]) == {100}  # a person's shell on a terminal


@posix_only
def test_a_process_named_tmux_by_argv0_is_still_swept(agent_proc):
    """`exec -a tmux sleep` is not a multiplexer: the real executable decides."""
    env = {**os.environ, "KOLLAB_TOOL_SHELL": "1", "KOLLAB_AGENT_PID": str(agent_proc.pid)}
    t0 = time.time()
    shell = subprocess.Popen(["/bin/bash", "-c", "(exec -a tmux /bin/sleep 120) >/dev/null 2>&1 & exit 0"],
                             env=env, start_new_session=True)
    shell.wait()

    def group() -> set[int]:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout
        return {int(x) for x, g in (line.split() for line in out.splitlines()) if int(g) == shell.pid}

    left: set[int] = set()
    try:
        _wait(lambda: bool(left.update(group()) or left))
        (fake,) = left
        assert hr.process_info()[fake][1].endswith("tmux")  # ps names it tmux
        assert nh.exe_path(fake).endswith("/sleep")
        assert fake in _select(agent_proc, [(shell.pid, t0, agent_proc.pid)])
    finally:
        for pid in left:
            os.kill(pid, signal.SIGKILL)


def test_tagged_orphan_under_a_systemd_user_subreaper_is_swept():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    reaper = nh.Proc(300, 1, ["/usr/lib/systemd/systemd", "--user"], {}, "/", "/usr/lib/systemd/systemd")
    fake_reaper = nh.Proc(400, 1, ["systemd"], {}, "/", "/home/me/bin/systemd")
    orphan = nh.Proc(310, 300, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/w")
    under_fake = nh.Proc(410, 400, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/w")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 300: (1, 300, 300), 310: (300, 310, 310),
            400: (1, 400, 400), 410: (400, 410, 410)}
    info = {100: (1000.0, "kollab", "??"), 300: (10.0, "systemd", "??"), 310: (1001.0, "python", "??"),
            400: (10.0, "systemd", "??"), 410: (1001.0, "python", "??")}
    got = nh.select_sweep([agent, reaper, fake_reaper, orphan, under_fake], install=INSTALL, scopes=["machine"],
                          agent_pids=[100], protect=set(), tree=tree, info=info)
    assert {p.pid for p in got} == {100, 310}


# ------------------------------------------------------------------ NEW-2: deterministic revocations


def _four():
    net = Net()
    net.prod = net.admit(net.founder, "prod", bootstrap=True)
    a, b = net.founder, net.prod
    c = net.admit(a, "c")
    d = net.admit(a, "d")
    net.confirm(b, c)
    net.confirm(b, d)
    assert len(net.view().counting) == 4
    return net, a, b, c, d


def _recount(net):
    return lambda removed: nh.count_machines(dataclasses.replace(net.roster, revoked_machines=set(removed)))


def test_new_2_crossing_revocations_converge_in_every_order_and_batch():
    net, a, b, c, d = _four()
    t0 = 1_000_000
    recs = [
        nh.revocation(b.folders[0], b.key, net.id, a.fk(), t0), nh.revocation(c.folders[0], c.key, net.id, a.fk(), t0),
        nh.revocation(a.folders[0], a.key, net.id, c.fk(), t0 + 1), nh.revocation(b.folders[0], b.key, net.id, c.fk(),
                                                                                   t0 + 1),
    ]
    recount = _recount(net)
    view = recount(set())

    def apply(have, now):
        return nh.revocations_to_apply(have, view, recount=recount, now=now)

    final = apply(recs, t0 + 10_000)
    assert len({view.machine_of(k) for k in final}) == 1
    outcomes = set()
    for order in itertools.permutations(recs):
        for cut in range(len(order) + 1):
            applied = set()
            applied |= apply(list(order[:cut]), t0 + 5)  # batch one, inside the settle window
            applied |= apply(list(order), t0 + 6)  # batch two arrives
            applied |= apply(list(order), t0 + nh.REVOKE_SETTLE + 10)
            outcomes.add(frozenset(applied))
            gone = {view.machine_of(k) or k for k in applied}
            assert len(recount(gone).counting) >= 2
    assert outcomes == {frozenset(final)}


def test_new_2_the_answer_ignores_what_this_machine_already_applied():
    """Two machines, one of which already applied C locally: the same records give the same answer."""
    net, a, b, c, d = _four()
    recs = [rev(b, net, a.fk()), rev(c, net, a.fk()), rev(a, net, c.fk()), rev(b, net, c.fk())]
    recount = _recount(net)
    fresh = nh.revocations_to_apply(recs, recount(set()), recount=recount)
    stale_view = dataclasses.replace(recount({c.mk}), revoked={c.mk})
    assert nh.revocations_to_apply(recs, recount(set()), recount=recount) == fresh
    assert fresh != set() and stale_view.revoked  # the base view, not the applied one, is what is passed


def test_new_2_control_judges_from_the_roster_before_any_revocation(tmp_path, monkeypatch):
    from tests.unit.test_network_control import join, machine, meet

    monkeypatch.setattr(nh, "REVOKE_SETTLE", 0)
    f = machine(tmp_path, "f")
    b, c, d = machine(tmp_path, "b"), machine(tmp_path, "c"), machine(tmp_path, "d")
    for m in (b, c, d):
        join(f, m)
    meet(f, b, c, d)
    f.halt_act("make_bootstrap", {"machine": b.machine_hex})
    meet(f, b, c, d)
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    b.halt_act("confirm_machine", {"machine": d.machine_hex})
    meet(f, b, c, d)
    assert len(f.view().counting) == 4
    ck, dk = c.client.public_key, d.client.public_key
    b.sign_revocation(dk)
    c.sign_revocation(dk)
    d.sign_revocation(ck)
    b.sign_revocation(ck)
    meet(f, b, c, d)
    answer = f.revocations_to_apply()
    assert len(answer) == 1
    (first,) = answer
    other = ck if first == dk else dk
    f.client.revoke(other, announce=True)  # this machine heard the other order first and applied it
    assert f.revocations_to_apply() == answer


def test_new_2_a_hostile_member_alone_cannot_revoke_the_founder():
    net, a, b, c, d = _four()
    i = d.add_folder()
    net.join(d, folder=i)
    recs = [rev(d, net, a.fk()), nh.revocation(d.folders[i], d.key, net.id, a.fk()), rev(d, net, a.mk)]
    recount = _recount(net)
    assert nh.revocations_to_apply(recs, recount(set()), recount=recount) == set()


def test_new_2_a_revocation_that_would_uncount_others_below_two_is_skipped():
    """Revoking B uncounts C (confirmed only by B) and E (only by C): one machine would be left, so it waits."""
    net = Net()
    net.prod = net.admit(net.founder, "prod", bootstrap=True)
    a, b = net.founder, net.prod
    c = net.admit(a, "c")
    e = net.admit(a, "e")
    net.confirm(b, c)
    net.confirm(c, e)
    recount = _recount(net)
    assert len(recount(set()).counting) == 4 and len(recount({b.mk}).counting) == 1
    recs = [rev(c, net, b.fk()), rev(e, net, b.fk())]
    assert nh.revocations_to_apply(recs, recount(set()), recount=recount) == set()


def test_new_2_a_revocation_waits_for_the_settle_window():
    net, a, b, c, d = _four()
    recs = [nh.revocation(b.folders[0], b.key, net.id, d.fk(), 5000),
            nh.revocation(c.folders[0], c.key, net.id, d.fk(), 5001)]
    recount = _recount(net)
    assert nh.revocations_to_apply(recs, recount(set()), recount=recount, now=5001 + nh.REVOKE_SETTLE - 1) == set()
    assert d.fk() in nh.revocations_to_apply(recs, recount(set()), recount=recount, now=5001 + nh.REVOKE_SETTLE)


# ------------------------------------------------------------------ NEW-3: no ps


def test_new_3_caller_chain_falls_back_to_proc(tmp_path, monkeypatch):
    def no_ps(*a, **k):
        raise FileNotFoundError("/bin/ps")

    for pid, ppid in ((300, 200), (200, 100), (100, 1)):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (sh) S {ppid} {pid} {pid} 0\n")
        (tmp_path / str(pid) / "status").write_text(f"Name:\tsh\nUid:\t{os.getuid()}\t0\t0\t0\n")
    monkeypatch.setattr(nh.subprocess, "run", no_ps)
    monkeypatch.setattr(nh, "PROC", tmp_path)
    assert nh.caller_chain(300) == [(300, os.getuid()), (200, os.getuid()), (100, os.getuid())]
    (tmp_path / "200" / "stat").unlink()
    assert nh.caller_chain(300) is None  # a missing link still refuses
    monkeypatch.setattr(nh, "PROC", tmp_path / "none")
    assert nh.caller_chain(300) is None  # neither ps nor /proc: refused


# ------------------------------------------------------------------ item 7: deleted halt.json (D7 bug 1)


class _Link:
    async def close(self):
        pass


def _held_listener(tmp_path, monkeypatch):
    h = "cd" * 32
    book = {"halts": {h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {"net1": book}})

    async def open_link(folder, state_dir):
        return _Link()

    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: Path(folder))
    listener = hr.HaltListener(tmp_path, processes=lambda: [], agents=lambda: [], open_link=open_link, grace=0,
                               stop=_ok)
    assert asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))["ok"]
    return listener, h, book


def test_a_deleted_halt_json_does_not_release_the_machine(tmp_path, monkeypatch):
    listener, h, book = _held_listener(tmp_path, monkeypatch)
    (tmp_path / "private" / "halt.json").unlink()
    asyncio.run(listener.tick())
    assert hr.read_halt(tmp_path)["scopes"] == {"net1": "machine"} and not listener._done.is_set()
    assert hr.folder_halted("/anywhere", hr.read_halt(tmp_path))
    # the exchange writes the record back without its scope (what D7 saw): the scope stays
    nh.HaltFile(tmp_path).write({"networks": {"net1": book}, "stopped": []})
    asyncio.run(listener.tick())
    assert hr.read_halt(tmp_path)["scopes"] == {"net1": "machine"} and not listener._done.is_set()
    # a real lift still releases it
    lifted = nh.HaltBook("net1", book)
    lifted.lifted.add(h)

    def lift(data):
        data["networks"]["net1"] = lifted.to_json()

    hr.update_halt(lift, tmp_path)
    asyncio.run(listener.tick())
    asyncio.run(listener.tick())
    assert hr.read_halt(tmp_path).get("scopes") == {} and listener._done.is_set()


def test_resume_local_through_a_listener_that_closed_is_reported_truthfully(tmp_path, monkeypatch):
    async def closed(*a, **k):
        return {}

    monkeypatch.setattr(hr, "listener_running", lambda root=None: True)
    monkeypatch.setattr(hr, "sock_call", closed)
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}, "stopped": []})
    reply = asyncio.run(hr._act("/w", None, "resume_local", {}, tmp_path))
    assert reply == {"ok": True, "line": nh.LOCAL_RESUME_LINE}
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine"}, "networks": {}, "stopped": []})
    with pytest.raises(RuntimeError, match="still halted"):
        asyncio.run(hr._act("/w", None, "resume_local", {}, tmp_path))

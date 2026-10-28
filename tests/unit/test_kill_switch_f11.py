"""Kill switch round 8 (F11): sweep what an agent backgrounded, revocation quorum, caller check fails closed.

The sweep tests start real processes on this machine (sh, sleep, nohup, a setsid python, a private tmux
server) and signal only those.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

INSTALL = nh.Install(prefix="/opt/this-install")
posix_only = pytest.mark.skipif(sys.platform != "darwin" and not sys.platform.startswith("linux"),
                                reason="needs real process groups")


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


def _group(pgid: int) -> set[int]:
    out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout
    return {int(a) for a, b in (line.split() for line in out.splitlines()) if int(b) == pgid}


def _select(agent: subprocess.Popen, shells, procs=(), tree=None):
    me = nh.Proc(agent.pid, os.getpid(), ["/opt/this-install/bin/kollab"], {}, "/work")
    rows = [(pid, start, agent.pid) for pid, start in shells]  # F12: each row names the agent that wrote it
    return {p.pid for p in nh.select_sweep(
        [me, *procs], install=INSTALL, scopes=["machine"], agent_pids=[agent.pid],
        protect=nh.ancestors(os.getpid()), tree=tree or hr.process_tree(), tool_shells=rows, info=hr.process_info(),
    )}


@pytest.fixture
def real(tmp_path):
    """An agent stand-in, an exited tool shell's leftovers, an unrelated session and a private tmux server."""
    started: list[int] = []
    agent = subprocess.Popen(["/bin/sleep", "120"])
    started.append(agent.pid)
    time.sleep(1.1)  # lstart has one-second resolution: everything below starts after the agent
    env = {**os.environ, "KOLLAB_TOOL_SHELL": "1", "KOLLAB_AGENT_PID": str(agent.pid)}
    pyfile = tmp_path / "py.pid"
    t0 = time.time()
    shell = subprocess.Popen(
        ["/bin/sh", "-c",
         "nohup /bin/sleep 120 >/dev/null 2>&1 & "
         "( /bin/sleep 121 ) >/dev/null 2>&1 & "
         f"{sys.executable} -c 'import os,time; os.setsid(); open(\"{pyfile}\",\"w\").write(str(os.getpid())); "
         "time.sleep(120)' >/dev/null 2>&1 & exit 0"],
        env=env, start_new_session=True,
    )
    shell.wait()
    _wait(lambda: pyfile.exists() and pyfile.read_text().strip())
    py = int(pyfile.read_text())
    _wait(lambda: len(_group(shell.pid)) >= 2)
    left = _group(shell.pid)
    started += [*left, py]
    unrelated = subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)
    started.append(unrelated.pid)
    sock = f"f11-{os.getpid()}"
    tmux = shutil.which("tmux")
    if tmux:
        subprocess.run([tmux, "-L", sock, "new-session", "-d", "/bin/sleep 120"], env=env, check=True)
    try:
        yield {"agent": agent, "shell": (shell.pid, t0), "left": left, "py": py, "unrelated": unrelated,
               "tmux": (tmux, sock)}
    finally:
        if tmux:
            subprocess.run([tmux, "-L", sock, "kill-server"], capture_output=True)
        for pid in started:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        agent.wait()
        unrelated.wait()


def _tmux_pids(tmux: str, sock: str) -> set[int]:
    out = subprocess.run([tmux, "-L", sock, "list-panes", "-a", "-F", "#{pane_pid}"], capture_output=True, text=True)
    panes = {int(x) for x in out.stdout.split()}
    server = subprocess.run(["/bin/ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return panes | {int(line.split()[0]) for line in server.splitlines() if f"-L {sock}" in line}


@posix_only
def test_backgrounded_jobs_of_an_exited_tool_shell_are_swept(real):
    """nohup, ( ) & and a setsid python tagged with the agent survive the shell; the halt picks them all."""
    shell_pid, t0 = real["shell"]
    for pid in real["left"]:
        assert _alive(pid)
        assert hr.process_tree()[pid][0] == 1 or sys.platform.startswith("linux")  # orphaned to launchd/init
    py = nh.read_process(real["py"], with_cwd=False)
    assert py is not None and py.env.get("KOLLAB_AGENT_PID") == str(real["agent"].pid)
    chosen = _select(real["agent"], [(shell_pid, t0)], [py])
    assert real["left"] <= chosen  # rule 2: the dead tool shell's group
    assert real["py"] in chosen  # rule 3: a tagged orphan in its own session, started after the agent
    # end to end: stopping what was chosen leaves none of the agent's leftovers running
    for pid in chosen - {real["agent"].pid}:
        os.kill(pid, signal.SIGTERM)
    _wait(lambda: not any(_alive(p) for p in real["left"] | {real["py"]}))


@posix_only
def test_unrelated_groups_sessions_and_a_private_tmux_server_are_not_swept(real):
    shell_pid, t0 = real["shell"]
    tmux, sock = real["tmux"]
    muxed = _tmux_pids(tmux, sock) if tmux else set()
    procs = [p for p in (nh.read_process(x, with_cwd=False) for x in muxed) if p is not None]
    for p in procs:
        p.ppid = hr.process_tree().get(p.pid, (0, 0, 0))[0]
    chosen = _select(real["agent"], [(shell_pid, t0)], [nh.read_process(real["py"], with_cwd=False), *procs])
    assert real["unrelated"].pid not in chosen
    assert not chosen & nh.ancestors(os.getpid())  # this test's own shell chain
    # a record naming the halting caller's own session (as if its pid were reused) takes nothing of it, not even
    # a job in another group of that session
    sid = os.getsid(0)
    job = subprocess.Popen(["/bin/sleep", "60"], process_group=0)
    try:
        took = _select(real["agent"], [(sid, hr.process_info().get(sid, (0.0, ""))[0])])
        assert job.pid not in took and not (took - {real["agent"].pid}) & nh.ancestors(os.getpid())
    finally:
        job.kill()
        job.wait()
    if tmux:
        assert muxed and not chosen & muxed  # the tagged tmux server and its pane are a person's
    assert real["left"] <= chosen  # sanity: the same call still takes the real leftovers


@posix_only
def test_pid_reuse_guard(real):
    """A recorded pid now leading a later process, or a group older than the record, is never ours."""
    other = real["unrelated"]  # leads its own group and session
    born = hr.process_info()[other.pid][0]
    # the record is older than the live leader by more than the slack: that pid was reused
    assert other.pid not in _select(real["agent"], [(other.pid, born - 100)])
    # the group started before the record: a pid recorded later cannot own it
    assert other.pid not in _select(real["agent"], [(other.pid, born + 100)])
    # F12: a live recorded pid that is not the agent's child is a forged row
    assert other.pid not in _select(real["agent"], [(other.pid, born)])
    # sanity: a matching record of a live child of the agent does take it, so the lines above are the guards
    tree = hr.process_tree()
    tree[other.pid] = (real["agent"].pid, *tree[other.pid][1:])
    assert other.pid in _select(real["agent"], [(other.pid, born)], tree=tree)


def test_no_process_tree_sweeps_nothing_recorded_or_tagged():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    orphan = nh.Proc(130, 1, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/work")
    got = nh.select_sweep([agent, orphan], install=INSTALL, scopes=["machine"], agent_pids=[100], protect=set(),
                          tree=None, tool_shells=[(120, 1.0)], info={100: (1.0, "kollab"), 130: (5.0, "python")})
    assert {p.pid for p in got} == {100}


def test_tagged_orphan_needs_a_start_after_the_agent_and_no_multiplexer():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    early = nh.Proc(130, 1, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/w")
    late = nh.Proc(140, 1, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/w")
    srv = nh.Proc(150, 1, ["/opt/homebrew/bin/tmux"], {"KOLLAB_AGENT_PID": "100"}, "/w", "/opt/homebrew/bin/tmux")
    under = nh.Proc(161, 1, ["python"], {"KOLLAB_AGENT_PID": "100"}, "/w")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 130: (1, 130, 130), 140: (1, 140, 140), 150: (1, 150, 150),
            160: (150, 160, 160), 161: (1, 161, 161)}
    info = {100: (1000.0, "kollab"), 130: (999.0, "python"), 140: (1001.0, "python"), 150: (1001.0, "tmux"),
            160: (1001.0, "zsh"), 161: (1001.0, "python")}
    got = {p.pid for p in nh.select_sweep([agent, early, late, srv, under], install=INSTALL, scopes=["machine"],
                                          agent_pids=[100], protect=set(), tree=tree, info=info)}
    assert got == {100, 140, 161}


def test_sweep_forwards_the_agents_recorded_tool_shells(tmp_path):
    """hr.sweep hands presence's tool_shells to select_sweep, so a live halt sees them."""
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/python", "-m", "kollabor_cli_main"], {}, "/work")
    rec = {"identity": "lapis", "pid": 100, "project": "/work", "state": "idle", "tool_shells": [[120, 1000.0, 100]]}
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 121: (1, 120, 120)}
    info = {100: (900.0, "kollab"), 121: (1001.0, "sleep")}

    async def stop(r):
        return True, str(r["pid"])

    got = asyncio.run(hr.sweep(["machine"], agents=[rec], procs=[agent], stop=stop, protect=set(),
                               install=INSTALL, tree=tree, info=info))
    assert {r["pid"] for r, _ok, _m in got} == {100, 121}


def test_tool_shells_are_recorded_pruned_capped_and_published(monkeypatch):
    from kollabor_agent import shell_executor as se
    from kollabor_agent.runtime import AgentRuntime

    calls = []
    monkeypatch.setattr(se, "TOOL_SHELLS", [])
    monkeypatch.setattr(se, "TOOL_SHELL_LISTENERS", [lambda: calls.append(1)])
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"], start_new_session=True)
    dead.wait()
    se.record_tool_shell(dead.pid, time.time())  # F15a: real starts, or the reuse check drops the row
    live = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        se.record_tool_shell(live.pid, time.time())
        assert [e[0] for e in se.TOOL_SHELLS] == [live.pid] and calls == [1, 1]  # the dead shell's group is gone
        for _ in range(100):
            se.record_tool_shell(live.pid, time.time())
        assert len(se.TOOL_SHELLS) == se.MAX_TOOL_SHELLS
        me = AgentRuntime(name="lapis", identity="lapis", agent_id="a1").to_presence_dict()
        assert me["tool_shells"][-1][0] == live.pid and me["tool_shells"][-1][2] == os.getpid()
        assert len(me["tool_shells"]) == se.MAX_TOOL_SHELLS
        peer = AgentRuntime(name="ruby", identity="ruby", agent_id="a2", pid=1).to_presence_dict()
        assert peer["tool_shells"] == []  # only this process's own shells
    finally:
        live.kill()
        live.wait()


@posix_only
def test_shell_executor_records_each_tool_shell(monkeypatch):
    from kollabor_agent import shell_executor as se

    monkeypatch.setattr(se, "TOOL_SHELLS", [])
    monkeypatch.setattr(se, "TOOL_SHELL_LISTENERS", [])
    before = time.time()
    result = asyncio.run(se.ShellExecutor().run("echo $$", timeout=10))
    assert result.success
    assert se.TOOL_SHELLS and se.TOOL_SHELLS[-1][0] == int(result.stdout.strip())
    assert se.TOOL_SHELLS[-1][1] <= time.time() and se.TOOL_SHELLS[-1][1] >= before - 5


# ------------------------------------------------------------------ NEW-2


def test_new_2_a_crossing_on_three_machines_is_one_answer_everywhere():
    """F15a: keys are judged one by one, so a mutual crossing on a three-machine network revokes both
    sides and dissolves it to one counting machine — deterministic on every machine. The F11-F14
    answer (one winner, by key order or by local completion order) is exactly what let two machines
    that heard different orders keep opposite revocations; the aggregate floor cannot survive that
    fix (test_kill_switch_f15a proves the four-machine floor still holds per key)."""
    from tests.unit.test_network_halt import Net, rev

    net = Net()
    net.prod = net.admit(net.founder, "prod", bootstrap=True)
    a, b = net.founder, net.prod
    c = net.admit(a, "c")
    net.confirm(b, c)
    view = net.view()
    assert {a.mk, b.mk, c.mk} <= view.counting and len(view.counting) == 3
    records = [rev(b, net, a.fk()), rev(c, net, a.fk()), rev(a, net, c.fk()), rev(b, net, c.fk())]
    out = nh.revocations_to_apply(records, view)
    gone = {view.machine_of(k) for k in out}
    assert gone == {a.mk, c.mk}
    assert out == nh.revocations_to_apply(list(reversed(records)), view)  # same answer on every machine


# ------------------------------------------------------------------ NEW-3


def test_new_3_a_caller_chain_that_cannot_be_read_is_refused(monkeypatch):
    monkeypatch.setattr(hr, "_live_agents", lambda: [])
    assert hr.caller_refusal(os.getpid()) == ""  # sanity: this test's chain is clean

    def broken(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(nh.subprocess, "run", broken)
    monkeypatch.setattr(nh, "PROC", nh.Path("/nonexistent-proc"))  # F12: and no /proc to fall back on
    assert hr.caller_refusal(os.getpid()) == nh.GUARD_REFUSAL


def test_new_3_an_unreadable_process_of_this_user_in_the_chain_is_refused(monkeypatch):
    monkeypatch.setattr(hr, "_live_agents", lambda: [])
    monkeypatch.setattr(nh, "read_process", lambda pid, with_cwd=True: None)
    assert hr.caller_refusal(os.getpid()) == nh.GUARD_REFUSAL


def test_new_3_a_missing_link_is_refused(monkeypatch):
    class Out:
        stdout = f"{os.getpid()} 99999999 {os.getuid()}\n"

    monkeypatch.setattr(nh.subprocess, "run", lambda *a, **k: Out())
    assert nh.caller_chain(os.getpid()) is None
    assert nh.caller_chain(4242) is None

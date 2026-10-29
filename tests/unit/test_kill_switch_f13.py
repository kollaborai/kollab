"""Kill switch round 10 (F13): the fallback stop verifies its pids, take_halt never raises, forged rows naming a
dead leader, revocation order independent of every signer's clock, an empty halt.json holds, a deleted lock.

The real-process tests start sleep and python on this machine and signal only those.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from tests.unit.test_kill_switch_f12 import _four, _recount, _wait

INSTALL = nh.Install(prefix="/opt/this-install")
posix_only = pytest.mark.skipif(sys.platform != "darwin" and not sys.platform.startswith("linux"),
                                reason="needs real process groups")
P1, P2 = 4_190_001, 4_190_002
KOLLAB_ARGV = [f"{sys.prefix}/bin/python", "-m", "kollabor_cli_main"]  # a kollab of this install, by argv


def _book(tmp_path, net="net1"):
    h = "ab" * 32
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {net: {"halts": {
        h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}}})


async def _no_link(folder, state_dir):
    return None


# ------------------------------------------------------------------ NEW-4b: the fallback stop


def test_fallback_never_signals_a_presence_pid_that_is_not_a_kollab():
    """Stale presence (a pid reused by an unrelated process) or a forged row: the fallback leaves it alone."""
    victim = subprocess.Popen(["/bin/sleep", "60"])
    try:
        got = asyncio.run(hr.fallback_stop(["machine"], [{"identity": "lapis", "pid": victim.pid, "project": "/w"}],
                                           [], set(), grace=0.2))
        time.sleep(0.2)
        assert victim.poll() is None and got == []
    finally:
        victim.kill()
        victim.wait()


def test_fallback_kills_only_a_pid_that_is_still_the_same_kollab():
    """SIGTERM ignored: SIGKILL goes out only while the start time read before SIGTERM still matches."""
    for same in (True, False):
        code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        stubborn = subprocess.Popen([sys.executable, "-c", code])
        time.sleep(0.5)
        starts = iter([(1000.0, "/x", "h")] * 3 if same else [(1000.0, "/x", "h"), (2000.0, "/x", "h")])
        me = nh.Proc(stubborn.pid, os.getpid(), KOLLAB_ARGV, {}, "/w")
        try:
            got = asyncio.run(hr.fallback_stop(["machine"], [], [me], set(), grace=0.3,
                                               identify=lambda pid: next(starts)))
            if same:
                assert stubborn.wait(timeout=5) == -signal.SIGKILL and got[0][1]
            else:
                time.sleep(0.2)
                assert stubborn.poll() is None and not got[0][1] and "not killed" in got[0][2]
        finally:
            stubborn.kill()
            stubborn.wait()


def test_fallback_scope_is_judged_and_it_never_raises():
    me = nh.Proc(4_000_001, 1, KOLLAB_ARGV, {}, "/elsewhere")
    assert asyncio.run(hr.fallback_stop([["/work"]], [], [me], set(), grace=0)) == []

    class Bad:
        pid = property(lambda self: 1 / 0)

    out = asyncio.run(hr.fallback_stop(["machine"], ["x", {"pid": float("nan")}], [Bad()], set(), grace=0))
    assert out == []


# ------------------------------------------------------------------ NEW-4c: take_halt never raises


def _listener(tmp_path, monkeypatch, *, stop, agents, procs):
    _book(tmp_path)
    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: None)
    return hr.HaltListener(tmp_path, stop=stop, processes=lambda: procs, agents=agents, open_link=_no_link)


def test_take_halt_reports_every_failing_step_and_still_stops_the_rest(tmp_path, monkeypatch):
    stopped = []

    async def stop(r):
        if r["pid"] == P1:
            raise OSError("stop blew up")
        stopped.append(r["pid"])
        return True, f"stopped {r['identity']}"

    def no_waiters(root=None):
        raise OSError("waiters dir gone")

    monkeypatch.setattr(hr, "waiter_pids", no_waiters)
    # pids above macOS's ceiling and almost never used on Linux: a fallback signal (mutation runs) hits nobody
    agents = [{"identity": "lapis", "pid": P1, "project": "/w"}, {"identity": "ruby", "pid": P2, "project": "/w"}]
    procs = [nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w"), nh.Proc(P2, 1, KOLLAB_ARGV, {}, "/w")]
    listener = _listener(tmp_path, monkeypatch, stop=stop, agents=lambda: agents, procs=procs)
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
    assert reply["ok"] and reply["count"] == 1 and stopped == [P2]
    assert any("waiters" in e for e in reply["errors"]) and any("stop blew up" in e for e in reply["errors"])
    assert hr.read_halt(tmp_path)["scopes"] == {"net1": "machine"}


def test_take_halt_holds_in_memory_when_presence_and_the_record_fail(tmp_path, monkeypatch):
    def broken():
        raise ValueError("presence unreadable")

    real_update = hr.update_halt
    monkeypatch.setattr(hr, "update_halt", lambda fn, root=None: (_ for _ in ()).throw(OSError("disk full")))
    listener = _listener(tmp_path, monkeypatch, stop=None, agents=broken, procs=[])
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
    assert reply["ok"] and listener.held == {"net1": "machine"}
    assert {e.split(":")[0] for e in reply["errors"]} >= {"presence", "record", "record stopped"}
    monkeypatch.setattr(hr, "update_halt", real_update)
    asyncio.run(listener.tick())  # the next tick writes the held scope back
    assert hr.read_halt(tmp_path)["scopes"] == {"net1": "machine"}


def test_take_halt_fallback_path_never_raises(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise ValueError("sweep broke")

    def no_waiters(root=None):
        raise OSError("waiters dir gone")

    async def stop(r):
        return True, "x"

    monkeypatch.setattr(hr, "sweep", boom)
    monkeypatch.setattr(hr, "waiter_pids", no_waiters)
    listener = _listener(tmp_path, monkeypatch, stop=stop, agents=lambda: [], procs=[])
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
    assert reply["ok"] and reply["sweep_error"] == "ValueError: sweep broke"


# ------------------------------------------------------------------ forged rows naming a dead leader


def test_two_field_rows_are_rejected():
    assert nh.shell_rows([[300, 1000.0], [301, 1000.0, 100]], 100) == [(301, 1000.0, 100)]


def test_forged_row_naming_a_dead_leader_takes_only_the_agents_own():
    """Group 200's leader exited. F14 restores F12: every member started after the shell is taken, tagged or not
    (macOS hides /bin/* env, so a tag rule let every `nohup sleep &` escape)."""
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 201: (77, 200, 200), 202: (77, 200, 200), 203: (202, 200, 200),
            204: (77, 200, 200), 205: (204, 200, 200)}
    info = {p: (1001.0, "x", "??") for p in tree}
    info[100] = (1000.0, "kollab", "??")
    procs = [agent, nh.Proc(202, 77, ["py"], {"KOLLAB_AGENT_PID": "100"}, "/w"),
             nh.Proc(204, 77, ["py"], {"KOLLAB_AGENT_PID": "999"}, "/w")]
    got = {p.pid for p in nh.select_sweep(procs, install=INSTALL, scopes=["machine"], agent_pids=[100],
                                          protect=set(), tree=tree, tool_shells=[(200, 1000.5, 100)], info=info)}
    assert got == {100, 201, 202, 203, 204, 205}


@posix_only
def test_a_dead_shells_untagged_child_is_taken_through_a_tagged_parent(tmp_path):
    """Real processes: the tool shell exits; its python (tagged), that python's /bin/sleep child (env hidden on
    macOS) and an untagged python left in the same group are all taken (F14)."""
    agent = subprocess.Popen(["/bin/sleep", "120"])
    time.sleep(1.1)
    pidfile = tmp_path / "py.pid"
    env = {**os.environ, "KOLLAB_TOOL_SHELL": "1", "KOLLAB_AGENT_PID": str(agent.pid)}
    t0 = time.time()
    script = (f"import os,subprocess,time; c=subprocess.Popen(['/bin/sleep','120']); "
              f"open({str(pidfile)!r},'w').write(f'{{os.getpid()}} {{c.pid}}'); time.sleep(120)")
    shell = subprocess.Popen(["/bin/sh", "-c", f"{sys.executable} -c \"{script}\" >/dev/null 2>&1 & "
                              f"env -u KOLLAB_AGENT_PID {sys.executable} -c 'import time; time.sleep(120)' "
                              ">/dev/null 2>&1 & exit 0"], env=env, start_new_session=True)
    shell.wait()
    started = [agent.pid]
    try:
        _wait(lambda: pidfile.exists() and len(pidfile.read_text().split()) == 2)
        py, child = map(int, pidfile.read_text().split())
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout
        group = {int(a) for a, g in (line.split() for line in out.splitlines()) if int(g) == shell.pid}
        started += list(group)
        untagged = group - {py, child}
        assert untagged
        me = nh.Proc(agent.pid, os.getpid(), ["/opt/this-install/bin/kollab"], {}, "/work")
        procs = [p for p in (nh.read_process(x, with_cwd=False) for x in group) if p is not None]
        got = {p.pid for p in nh.select_sweep(
            [me, *procs], install=INSTALL, scopes=["machine"], agent_pids=[agent.pid],
            protect=nh.ancestors(os.getpid()), tree=hr.process_tree(), tool_shells=[(shell.pid, t0, agent.pid)],
            info=hr.process_info())}
        assert group <= got
    finally:
        for pid in started:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        agent.wait()


# ------------------------------------------------------------------ NEW-2a: no signer's clock decides


def _who(view, keys):
    return frozenset(view.machine_of(k) or k for k in keys)


def test_revocation_outcome_ignores_issued_at():
    """rev.py's attacks: a co-signer backdating (0, -10**15), far-future (10**30) or honest stamps: one answer."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())

    def recs(stamps):
        honest = [nh.revocation(b.folders[0], b.key, net.id, d.fk(), stamps[0]),
                  nh.revocation(c.folders[0], c.key, net.id, d.fk(), stamps[1])]
        stale = [nh.revocation(b.folders[0], b.key, net.id, c.fk(), stamps[2]),
                 nh.revocation(d.folders[0], d.key, net.id, c.fk(), stamps[3])]
        return honest + stale

    base = nh.revocations_to_apply(recs([9000, 9001, 500, 9500]), view, recount=recount)
    rng = random.Random(13)
    for stamps in ([9000, 9001, 500, 0], [9000, 9001, 500, -10**15], [9000, 9001, 500, 10**30],
                   [10**30, -1, 0, 7], *([rng.randint(-10**18, 10**18) for _ in range(4)] for _ in range(20))):
        assert nh.revocations_to_apply(recs(stamps), view, recount=recount) == base
    # the pair c+d backdating "revoke b" against a+b revoking d: the stamp changes nothing either
    pair = [nh.revocation(a.folders[0], a.key, net.id, d.fk(), 9000),
            nh.revocation(b.folders[0], b.key, net.id, d.fk(), 9001)]
    early = [nh.revocation(m.folders[0], m.key, net.id, b.fk(), 1) for m in (c, d)]
    late = [nh.revocation(m.folders[0], m.key, net.id, b.fk(), 9900) for m in (c, d)]
    assert nh.revocations_to_apply(pair + early, view, recount=recount) == \
        nh.revocations_to_apply(pair + late, view, recount=recount)


def test_crossing_revocations_with_wild_stamps_converge_in_every_order_and_batch():
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    recs = [nh.revocation(b.folders[0], b.key, net.id, a.fk(), 10**30),
            nh.revocation(c.folders[0], c.key, net.id, a.fk(), -10**15),
            nh.revocation(a.folders[0], a.key, net.id, c.fk(), 0),
            nh.revocation(b.folders[0], b.key, net.id, c.fk(), 1)]
    final = nh.revocations_to_apply(recs, view, recount=recount)
    assert len(_who(view, final)) == 2  # F15a: a crossing with two signers a side revokes both
    for order in itertools.permutations(recs):
        for cut in range(len(order) + 1):
            applied = set()
            applied |= nh.revocations_to_apply(list(order[:cut]), view, recount=recount)
            applied |= nh.revocations_to_apply(list(order), view, recount=recount)
            assert applied == final and len(recount(_who(view, applied)).counting) >= 2


def test_a_far_future_stamp_does_not_hold_back_an_honest_revocation():
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    recs = [nh.revocation(m.folders[0], m.key, net.id, a.fk(), 9_999_999_999) for m in (c, d)]
    recs += [nh.revocation(m.folders[0], m.key, net.id, d.fk(), 9000) for m in (b, c)]
    out = nh.revocations_to_apply(recs, view, recount=recount)
    assert _who(view, out) == {a.mk, d.mk}  # F15a: the far-future stamp delays nothing


def test_a_record_that_arrives_after_a_revocation_applied_converges():
    """F15a: the F14 divergence is gone. Machine X heard only one side of a crossing and applied it;
    when the other side arrives, any delay later, X lands on the full-set answer like Y."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    to_a = [nh.revocation(m.folders[0], m.key, net.id, a.fk()) for m in (b, c)]
    to_c = [nh.revocation(m.folders[0], m.key, net.id, c.fk()) for m in (a, b)]
    y = nh.revocations_to_apply(to_a + to_c, view, recount=recount)
    x = nh.revocations_to_apply(to_c, view, recount=recount)
    x |= nh.revocations_to_apply(to_a + to_c, view, recount=recount)
    assert _who(view, x) == _who(view, y) == {a.mk, c.mk}


def test_control_applies_the_second_signature_with_no_clock_of_its_own(tmp_path):
    from plugins.hub.network_control import NetworkControl
    from tests.unit.test_network_control import join, machine, meet

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
    dk = d.client.public_key
    b.sign_revocation(dk)
    c.sign_revocation(dk)
    meet(f, b, c, d)
    assert dk in f.revocations_to_apply()  # F15a: at once, and no first-seen clock is kept anywhere
    assert "revoke_seen" not in NetworkControl(f.client, machine=f.machine).data


# ------------------------------------------------------------------ halt.json and halt.lock


@pytest.mark.parametrize("body", [b"", b"   \n", b"\t"])
def test_an_empty_halt_json_holds(tmp_path, body):
    path = nh.HaltFile(tmp_path).path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o600)
    with pytest.raises(nh.HaltError):
        nh.HaltFile(tmp_path).read()
    assert hr.folder_halted("/any", hr.read_halt_held(tmp_path))


def test_a_deleted_private_dir_does_not_let_a_second_listener_start():
    tmp_path = Path(tempfile.mkdtemp(prefix="ks13", dir="/tmp"))  # a unix socket path must stay short

    async def run():
        first = hr.HaltListener(tmp_path, open_link=_no_link)
        assert first.take_lock()
        await first._serve()
        shutil.rmtree(tmp_path / "private")
        await first.tick()
        second = hr.HaltListener(tmp_path, open_link=_no_link)
        try:
            return second.take_lock(), (tmp_path / "private" / hr.LOCK).read_text(), hr.sock_path(tmp_path).exists()
        finally:
            first._server.close()
            first.drop_lock()

    try:
        took, pid, sock = asyncio.run(run())
    finally:
        shutil.rmtree(tmp_path)
    assert not took and pid == str(os.getpid()) and sock


def test_halt_lock_unchanged_is_left_alone(tmp_path):
    listener = hr.HaltListener(tmp_path, open_link=_no_link)
    assert listener.take_lock()
    fd = listener._lock_fd
    asyncio.run(listener._keep_lock())
    assert listener._lock_fd == fd
    listener.drop_lock()

"""Kill switch round 11 (F14): the dead-leader orphan sweep is back on macOS, fallback start-time slack and own-folder
scope, a malformed scope list holds the machine, revocations judged as a pure function of the signed
record set (F15a: both sides of a crossing apply, a lone signature never completes), a directory named
halt.json is recoverable.

The real-process tests run commands through the real ShellExecutor and signal only what they started.
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

from kollabor_agent import shell_executor as se
from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from tests.unit.test_kill_switch_f12 import _four, _recount
from tests.unit.test_kill_switch_f13 import KOLLAB_ARGV, P1, _book, _no_link

posix_only = pytest.mark.skipif(sys.platform != "darwin" and not sys.platform.startswith("linux"),
                                reason="needs real process groups")


def _ps() -> dict[int, str]:
    out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return {int(a): b for a, b in (line.strip().split(None, 1) for line in out.splitlines() if line.strip())}


# ------------------------------------------------------------------ 1. the overnight orphan, for real


@posix_only
def test_real_shell_executor_leftovers_are_swept_even_with_hidden_env(tmp_path):
    """Round 11's dry run: every one of these survived F13 on macOS (env of /bin/* hidden, parent is pid 1)."""
    mark = f"7{os.getpid():07d}"  # unique to this run: other runs on the machine never match
    log = tmp_path / "log"
    log.touch()
    cmds = [
        f"nohup sleep {mark}1 >/dev/null 2>&1 &",
        f"( sleep {mark}2 ) >/dev/null 2>&1 &",
        f"sleep {mark}3 >/dev/null 2>&1 & disown",
        f"sh -c 'sleep {mark}4; :' >/dev/null 2>&1 &",
        f"nohup tail -f {log} >/dev/null 2>&1 &",
        f"while true; do sleep {mark}6; done >/dev/null 2>&1 &",  # a runaway loop
    ]
    if shutil.which("caffeinate"):
        cmds.append(f"nohup caffeinate -i -t {mark}7 >/dev/null 2>&1 &")
    se.TOOL_SHELLS.clear()

    async def run_all():
        ex = se.ShellExecutor()
        for cmd in cmds:
            await ex.run(cmd, timeout=5)

    asyncio.run(run_all())
    time.sleep(0.5)

    def ours() -> set[int]:
        return {p for p, c in _ps().items() if mark in c or str(log) in c} - {os.getpid()}

    left = ours()
    try:
        assert len(left) >= len(cmds)
        tree, info = hr.process_tree(), hr.process_info()
        protect = nh.ancestors(os.getpid()) | {os.getpid()}
        known = {p.pid: p for p in (nh.read_process(x, with_cwd=False) for x in left) if p is not None}
        rows = nh.shell_rows([list(r) for r in se.TOOL_SHELLS], os.getpid())
        assert len(rows) == len(cmds)
        picked = nh._backgrounded({os.getpid()}, tree, info, protect, rows, known)
        assert left <= picked, sorted(left - picked)
        assert not picked & protect
        # a forged row naming the caller's own group or session takes nothing of it (it is protected)
        for key in (os.getpgrp(), os.getsid(0)):
            forged = [(key, info.get(key, (0.0,))[0], os.getpid())]
            assert not nh._backgrounded({os.getpid()}, tree, info, protect, forged, known) & protect
    finally:
        for pid in ours() | left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        se.TOOL_SHELLS.clear()


# ------------------------------------------------------------------ 2. fallback_stop


def test_fallback_allows_a_one_second_start_drift():
    """ps start times drift by a second between reads: the stubborn kollab still gets SIGKILL."""
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    stubborn = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.5)
    starts = iter([1000.0, 1001.0])
    me = nh.Proc(stubborn.pid, os.getpid(), KOLLAB_ARGV, {}, "/w")
    try:
        got = asyncio.run(hr.fallback_stop(["machine"], [], [me], set(), grace=0.3, identify=lambda pid: next(starts)))
        assert stubborn.wait(timeout=5) == -signal.SIGKILL and got[0][1]
    finally:
        stubborn.kill()
        stubborn.wait()


def test_fallback_scope_comes_from_the_process_not_presence(tmp_path):
    """A forged presence project cannot pull a kollab running in another folder into a folder-scoped halt."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    me = nh.Proc(other.pid, os.getpid(), KOLLAB_ARGV, {}, "/elsewhere")
    forged = [{"identity": "lapis", "pid": other.pid, "project": "/in-scope"}]
    try:
        got = asyncio.run(hr.fallback_stop([["/in-scope"]], forged, [me], set(), grace=0.2,
                                           identify=lambda pid: 1000.0))
        time.sleep(0.2)
        assert got == [] and other.poll() is None
    finally:
        other.kill()
        other.wait()


# ------------------------------------------------------------------ 3. take_halt


@pytest.mark.parametrize("scope", [[["x"]], [1, "a"], [None], [{"a": 1}]])
def test_a_malformed_scope_list_holds_the_machine(tmp_path, monkeypatch, scope):
    _book(tmp_path)
    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: None)

    async def stop(r):
        return True, "x"

    listener = hr.HaltListener(tmp_path, stop=stop, processes=lambda: [], agents=lambda: [], open_link=_no_link)
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": scope, "folder": "/w"}))
    assert reply["ok"] and hr.read_halt(tmp_path)["scopes"] == {"net1": "machine"}


def test_a_failing_default_stop_still_lets_the_fallback_see_the_process_table(tmp_path, monkeypatch):
    _book(tmp_path)
    monkeypatch.setattr(hr, "_state_dir_for", lambda folder, net: None)
    monkeypatch.setattr(hr, "_default_stop", lambda protect, root: (_ for _ in ()).throw(ImportError("no cli")))
    # a kollab with no presence row: only the process table names it (pid above every OS's ceiling: no signal lands)
    monkeypatch.setattr(hr, "_read_candidates", lambda: [nh.Proc(P1, 1, KOLLAB_ARGV, {}, "/w")])
    listener = hr.HaltListener(tmp_path, agents=lambda: [], open_link=_no_link)
    reply = asyncio.run(listener.handle({"op": "halt", "network": "net1", "scope": "machine", "folder": "/w"}))
    assert reply["ok"] and any(str(P1) in m for m in reply["stopped"]), reply


# ------------------------------------------------------------------ 4. revocations as a pure function of the record set


def _rec(m, net, key, t=0):
    return nh.revocation(m.folders[0], m.key, net.id, key, t)


def test_a_stale_lone_signature_cannot_save_its_owner():
    """b once signed "revoke c" alone; much later hostile d co-signs it while b+c revoke d. F15a: both complete,
    everywhere the same — d does not escape (F13 lost d in 16 of 24 key orders; F14 needed a local clock)."""
    for _ in range(12):
        net, a, b, c, d = _four()
        recount = _recount(net)
        view = recount(set())
        stale = [_rec(b, net, c.fk())]
        honest = [_rec(b, net, d.fk()), _rec(c, net, d.fk())]
        revive = [_rec(d, net, c.fk())]
        out = nh.revocations_to_apply(stale + honest + revive, view, recount=recount)
        gone = {view.machine_of(k) or k for k in out}
        assert gone == {c.mk, d.mk}


def test_completions_are_not_ordered_by_this_machine_clock():
    """Two revocations, whichever completes here first: one answer. The F14 first-completed-here rule is
    what let two machines keep opposite revocations off the same records."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    to_c = [_rec(a, net, c.fk()), _rec(b, net, c.fk())]
    to_a = [_rec(b, net, a.fk()), _rec(c, net, a.fk())]
    for first, second in ((to_c, to_a), (to_a, to_c)):
        applied = set()
        applied |= nh.revocations_to_apply(first, view, recount=recount)
        applied |= nh.revocations_to_apply(first + second, view, recount=recount)
        assert {view.machine_of(k) or k for k in applied} == {a.mk, c.mk}


def test_a_lone_signature_pairs_at_any_delay():
    """F15a: no pair window — a co-signature hours later still completes the revocation."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    one = [_rec(b, net, d.fk())]
    both = one + [_rec(c, net, d.fk())]
    assert nh.revocations_to_apply(one, view, recount=recount) == set()
    assert d.fk() in nh.revocations_to_apply(both, view, recount=recount)


# ------------------------------------------------------------------ 5. a directory named halt.json


def test_resume_local_moves_a_directory_named_halt_json_aside(tmp_path):
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})
    path = nh.HaltFile(tmp_path).path
    path.unlink()
    path.mkdir()
    (path / "junk").write_text("x")
    assert hr.read_halt_held(tmp_path)["scopes"] == {"unreadable": "machine"}  # holds
    note = hr.clear_malformed_halt(tmp_path)
    assert "directory" in note
    assert not hr.read_halt(tmp_path).get("scopes") and path.is_file()
    assert (path.with_name("halt.json.bad") / "junk").read_text() == "x"
    # a second one does not collide with the first .bad
    path.unlink()
    path.mkdir()
    assert "directory" in hr.clear_malformed_halt(tmp_path) and path.is_file()

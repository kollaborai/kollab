"""Kill switch round 11 (F14): the dead-leader orphan sweep is back on macOS, fallback start-time slack and own-folder
scope, a malformed scope list holds the machine, revocations judged as a pure function of the signed
record set (F15a: both sides of a crossing apply, a lone signature never completes), a directory named
halt.json is recoverable.

The real-process tests run commands through the real ShellExecutor and signal only what they started.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

import psutil
import pytest

from kollabor_agent import shell_executor as se
from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from tests.unit._owned_processes import bounded_child_command, cleanup_group, wait_for_exit, watchdog_command
from tests.unit.test_kill_switch_f12 import _four, _recount
from tests.unit.test_kill_switch_f13 import KOLLAB_ARGV, P1, _book, _no_link

posix_only = pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"), reason="needs real process groups"
)


def _group_members(groups: set[int]) -> dict[int, int]:
    """Snapshot PIDs in our private groups, independent of the sweep under test."""
    out = subprocess.run(
        ["/bin/ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True, check=True, timeout=5
    ).stdout
    return {int(pid): int(group) for pid, group in (line.split() for line in out.splitlines()) if int(group) in groups}


# ------------------------------------------------------------------ 1. the overnight orphan, for real


@posix_only
def test_real_shell_executor_leftovers_are_swept_even_with_hidden_env(tmp_path):
    """The real background shapes are still selected even when system env is hidden."""
    log = tmp_path / "log"
    log.touch()
    cmds = [
        "nohup sleep 45 >/dev/null 2>&1 &",
        "( sleep 45 ) >/dev/null 2>&1 &",
        "sleep 45 >/dev/null 2>&1 & disown",
        "sh -c 'sleep 45; :' >/dev/null 2>&1 &",
        f"nohup tail -f {shlex.quote(str(log))} >/dev/null 2>&1 &",
        "while true; do sleep 45; done >/dev/null 2>&1 &",
    ]
    if shutil.which("caffeinate"):
        cmds.append("nohup caffeinate -i -t 45 >/dev/null 2>&1 &")
    # Capture the actual subprocess handles rather than trusting TOOL_SHELLS,
    # which is part of the behavior under test and may itself be broken.
    shells = []
    owned_members = {}

    def remember_members(pgid, ready):
        # Record individual PID identities as a second cleanup path in case a
        # future sweep kills the watchdog but leaves other group members alive.
        try:
            data = json.loads(ready.read_text())
            guard = psutil.Process(data["pid"])
            if (
                data["pgid"] != pgid
                or guard.create_time() != data["birth"]
                or str(ready) not in guard.cmdline()
                or os.getpgid(guard.pid) != pgid
            ):
                return
            candidates = []
            for pid in _group_members({pgid}):
                try:
                    member = psutil.Process(pid)
                    candidates.append((member, member.create_time()))
                except psutil.NoSuchProcess:
                    pass
            if guard.is_running() and guard.create_time() == data["birth"] and os.getpgid(guard.pid) == pgid:
                owned_members.setdefault(pgid, []).extend(candidates)
        except FileNotFoundError, psutil.NoSuchProcess, ProcessLookupError:
            pass

    class CapturingExecutor(se.ShellExecutor):
        ready = None

        async def _create_unix_process(self, *args, **kwargs):
            process = await super()._create_unix_process(*args, **kwargs)
            # Retain the handle before attempting any fallible PID lookup.
            shells.append([process.pid, process, None, self.ready])
            try:
                shells[-1][2] = psutil.Process(process.pid)
            except psutil.NoSuchProcess:
                pass
            return process

    async def run_all():
        ex = CapturingExecutor()
        for index, cmd in enumerate(cmds):
            ex.ready = tmp_path / f"watchdog-{index}.json"
            try:
                result = await ex.run(watchdog_command(ex.ready) + cmd, timeout=5)
                assert result.success, result
            finally:
                if shells and shells[-1][3] == ex.ready:
                    remember_members(shells[-1][0], ex.ready)

    se.TOOL_SHELLS.clear()
    try:
        asyncio.run(run_all())
        time.sleep(0.5)
        for pgid, _, _, ready in shells:
            remember_members(pgid, ready)  # also capture delayed grandchildren
        groups = {pgid for pgid, _, _, _ in shells}
        guards = {json.loads(ready.read_text())["pid"] for _, _, _, ready in shells}
        members = _group_members(groups)
        left = set(members) - guards
        assert len(groups) == len(cmds)
        assert all(any(members[pid] == group for pid in left) for group in groups)
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
        cleanup_errors = []
        for pgid, process, leader, ready in reversed(shells):
            try:
                # Stop a shell that failed during setup before looking for its
                # watchdog. No workload starts until the watchdog is ready.
                try:
                    if leader is not None:
                        leader.kill()
                    elif process.returncode is None:
                        process.kill()  # retained direct child handle
                except psutil.NoSuchProcess, ProcessLookupError:
                    pass
                deadline = time.monotonic() + 1
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                cleanup_group(pgid, ready, anchors=owned_members.get(pgid, ()))
            except Exception as exc:
                cleanup_errors.append(exc)
        # These identities were captured while a verified watchdog anchored
        # the group. psutil checks creation time again before signalling a PID.
        for members in owned_members.values():
            for member, _ in members:
                try:
                    member.kill()
                except psutil.NoSuchProcess:
                    pass
                except Exception as exc:
                    cleanup_errors.append(exc)
        se.TOOL_SHELLS.clear()
        if cleanup_errors:
            raise ExceptionGroup("shell test cleanup failed", cleanup_errors)


@posix_only
@pytest.mark.parametrize("failure_stage", ["launch", "sweep", "watchdog"])
def test_shell_fixture_cleans_up_if_launch_or_sweep_raises(tmp_path, monkeypatch, failure_stage):
    """Failures during setup or the sweep still stop only this test's processes."""
    original_run = se.ShellExecutor.run
    spawned = []
    outsider = subprocess.Popen(bounded_child_command())

    def remember_processes():
        groups = {row[0] for row in se.TOOL_SHELLS}
        for pid in _group_members(groups):
            with contextlib.suppress(psutil.NoSuchProcess):
                spawned.append(psutil.Process(pid))

    async def fail_after_first_launch(executor, *args, **kwargs):
        result = await original_run(executor, *args, **kwargs)
        assert result.success
        remember_processes()
        raise RuntimeError("injected fixture failure")

    def fail_sweep(*args, **kwargs):
        remember_processes()
        if failure_stage == "watchdog":
            # Simulate a broken sweep killing each guard but missing workloads.
            for ready in tmp_path.glob("watchdog-*.json"):
                data = json.loads(ready.read_text())
                guard = psutil.Process(data["pid"])
                assert guard.create_time() == data["birth"]
                assert os.getpgid(guard.pid) == data["pgid"] != os.getpgrp()
                guard.kill()
                wait_for_exit(guard)
        raise RuntimeError("injected fixture failure")

    try:
        if failure_stage == "launch":
            monkeypatch.setattr(se.ShellExecutor, "run", fail_after_first_launch)
        else:
            monkeypatch.setattr(nh, "_backgrounded", fail_sweep)
        with pytest.raises(RuntimeError, match="injected fixture failure"):
            test_real_shell_executor_leftovers_are_swept_even_with_hidden_env(tmp_path)
        assert len(spawned) >= 2  # watchdog plus a real background workload

        def alive(process):
            try:
                return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return False

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(alive(p) for p in spawned):
            time.sleep(0.02)
        assert not any(alive(p) for p in spawned)
        assert outsider.poll() is None
    finally:
        if outsider.poll() is None:
            outsider.kill()
        outsider.wait(timeout=5)


# ------------------------------------------------------------------ 2. fallback_stop


def test_fallback_allows_a_one_second_start_drift():
    """ps start times drift by a second between reads: the stubborn kollab still gets SIGKILL."""
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    stubborn = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.5)
    starts = iter([(1000.0, "/x", "h"), (1001.0, "/x", "h"), (1001.0, "/x", "h")])
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
        got = asyncio.run(
            hr.fallback_stop([["/in-scope"]], forged, [me], set(), grace=0.2, identify=lambda pid: 1000.0)
        )
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
    monkeypatch.setattr(hr, "kollab_identity", lambda pid, install=None: (1000.0, "/x", "h"))  # F15b seam
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

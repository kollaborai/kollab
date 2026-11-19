"""Kill switch round 11 (F14): the dead-leader orphan sweep is back on macOS, fallback start-time slack and own-folder
scope, a malformed scope list holds the machine, revocations judged as a pure function of the signed
record set (F15a: both sides of a crossing apply, a lone signature never completes), a directory named
halt.json is recoverable.

The real-process tests run commands through the real ShellExecutor. A private
watchdog stops each workload group; if that watchdog is missing, teardown waits
briefly for exact recorded identities and defers survivors to CI.
"""

from __future__ import annotations

import asyncio
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
from tests.unit._owned_processes import (
    bounded_child_command,
    cleanup_group,
    wait_for_recorded_identities,
    watchdog_command,
)
from tests.unit.test_kill_switch_f12 import _four, _recount
from tests.unit.test_kill_switch_f13 import KOLLAB_ARGV, P1, _book, _no_link

posix_only = pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"), reason="needs real process groups"
)


def _ps() -> dict[int, str]:
    out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return {int(a): b for a, b in (line.strip().split(None, 1) for line in out.splitlines() if line.strip())}


def _record_last_background_pid(command: str, pids_file) -> str:
    """Record the exact PID started by one controlled shell command."""
    path = shlex.quote(str(pids_file))
    return f'{command}\n_test_owned_pid=$!; printf "%s\\n" "$_test_owned_pid" >> {path}'


def _live_private_group_anchor(ready, pgid):
    try:
        data = json.loads(ready.read_text())
        if data.get("pgid") != pgid:
            return None
        # While the watchdog is live it pins this private PGID, so discard a
        # recycled workload PID before asking psutil for process details.
        if os.getpgid(data["pid"]) != pgid:
            return None
        guard = psutil.Process(data["pid"])
        birth = data["birth"]
        if (
            not guard.is_running()
            or guard.status() == psutil.STATUS_ZOMBIE
            or guard.create_time() != birth
            or os.getpgid(guard.pid) != pgid
            or guard.create_time() != birth
        ):
            return None
        return guard, birth
    except FileNotFoundError, psutil.NoSuchProcess, ProcessLookupError, ValueError:
        return None


def _remember_owned_members(pgid, ready, pids_file, owned_members):
    """Retain only PIDs reported by this test's shell and verified in its private group."""
    if _live_private_group_anchor(ready, pgid) is None:
        return
    try:
        pids = [int(line) for line in pids_file.read_text().splitlines() if line.isdigit()]
        candidates = owned_members.setdefault(pgid, {})
        for pid in pids:
            if pid <= 1:
                continue
            try:
                if os.getpgid(pid) != pgid:
                    continue
                process = psutil.Process(pid)
                birth = process.create_time()
                if os.getpgid(pid) != pgid or process.create_time() != birth:
                    continue
                if _live_private_group_anchor(ready, pgid) is None:
                    return
                candidates[(pid, birth)] = (process, birth)
            except psutil.NoSuchProcess, ProcessLookupError:
                continue
    except FileNotFoundError, psutil.NoSuchProcess, ProcessLookupError, ValueError:
        return


def _owned_process_snapshot(owned_members):
    """Build a test-owned sweep input with runtime process start epochs."""
    tree, info, known = {}, {}, {}
    runtime_info = hr.process_info()
    current = psutil.Process()
    current_birth = current.create_time()
    current_info = runtime_info.get(current.pid)
    if current_info is None:
        raise AssertionError(f"test process {current.pid} missing from runtime process info")
    info[current.pid] = (current_info[0], current.name(), "")
    tree[current.pid] = (current.ppid(), os.getpgrp(), os.getsid(0))

    for pgid, members in owned_members.items():
        for (pid, birth), (process, _) in members.items():
            try:
                if process.create_time() != birth or os.getpgid(pid) != pgid:
                    continue
                runtime_row = runtime_info.get(pid)
                if runtime_row is None:
                    raise AssertionError(f"owned process {pid} missing from runtime process info")
                ppid = process.ppid()
                name = process.name()
                executable = process.exe()
                sid = os.getsid(pid)
                if process.create_time() != birth or os.getpgid(pid) != pgid:
                    continue
                tree[pid] = (ppid, pgid, sid)
                info[pid] = (runtime_row[0], name, "")
                known[pid] = nh.Proc(pid, ppid, [name], {}, "", executable)
            except psutil.NoSuchProcess, ProcessLookupError:
                continue
    if current.create_time() != current_birth:
        raise AssertionError("the test process identity changed while building its owned snapshot")
    return tree, info, known


@posix_only
def test_owned_process_snapshot_uses_runtime_start_epochs(monkeypatch):
    class FakeProcess:
        def __init__(self, pid, birth, parent, process_name, executable):
            self.pid = pid
            self.birth = birth
            self.parent = parent
            self.process_name = process_name
            self.executable = executable

        def create_time(self):
            return self.birth

        def ppid(self):
            return self.parent

        def name(self):
            return self.process_name

        def exe(self):
            return self.executable

    current = FakeProcess(101, 10.25, 1, "python", "/usr/bin/python")
    workload = FakeProcess(202, 20.25, 101, "sleep", "/usr/bin/sleep")
    runtime_info = {
        101: (1000.75, "runtime-python", ""),
        202: (2000.75, "runtime-sleep", ""),
    }
    monkeypatch.setattr(psutil, "Process", lambda: current)
    monkeypatch.setattr(hr, "process_info", lambda: runtime_info)
    monkeypatch.setattr(os, "getpgid", lambda pid: 202 if pid == 202 else 101)
    monkeypatch.setattr(os, "getpgrp", lambda: 101)
    monkeypatch.setattr(os, "getsid", lambda pid: 202 if pid == 202 else 101)

    _, info, _ = _owned_process_snapshot({202: {(202, workload.birth): (workload, workload.birth)}})

    assert info == {
        101: (1000.75, "python", ""),
        202: (2000.75, "sleep", ""),
    }


# ------------------------------------------------------------------ 1. the overnight orphan, for real


@posix_only
def test_real_shell_executor_leftovers_are_swept_even_with_hidden_env(tmp_path):
    """The real background shapes are selected from exact private-group handles."""
    log = tmp_path / "log"
    log.touch()
    commands = [
        "nohup sleep 45 >/dev/null 2>&1 &",
        "( sleep 45 ) >/dev/null 2>&1 &",
        "sleep 45 >/dev/null 2>&1 & disown",
        "sh -c 'sleep 45; :' >/dev/null 2>&1 &",
        f"nohup tail -f {shlex.quote(str(log))} >/dev/null 2>&1 &",
        "while true; do sleep 45; done >/dev/null 2>&1 &",
    ]
    if shutil.which("caffeinate"):
        commands.append("nohup caffeinate -i -t 45 >/dev/null 2>&1 &")
    # Capture the actual subprocess handles rather than trusting TOOL_SHELLS,
    # which is part of the behavior under test and may itself be broken.
    shells = []
    owned_members = {}

    class CapturingExecutor(se.ShellExecutor):
        ready = None
        pids_file = None

        async def _create_unix_process(self, *args, **kwargs):
            process = await super()._create_unix_process(*args, **kwargs)
            # Retain the handle before attempting any fallible PID lookup.
            shells.append([process.pid, process, None, self.ready, self.pids_file])
            try:
                leader = psutil.Process(process.pid)
                shells[-1][2] = (leader, leader.create_time())
            except psutil.NoSuchProcess:
                pass
            return process

    async def run_all():
        ex = CapturingExecutor()
        for index, command in enumerate(commands):
            ex.ready = tmp_path / f"watchdog-{index}.json"
            ex.pids_file = tmp_path / f"owned-{index}.pids"
            try:
                cmd = _record_last_background_pid(command, ex.pids_file)
                result = await ex.run(watchdog_command(ex.ready) + cmd, timeout=5)
                assert result.success, result
            finally:
                if shells and shells[-1][3] == ex.ready:
                    _remember_owned_members(shells[-1][0], ex.ready, ex.pids_file, owned_members)

    se.TOOL_SHELLS.clear()
    try:
        asyncio.run(run_all())
        time.sleep(0.5)
        for pgid, _, _, ready, pids_file in shells:
            _remember_owned_members(pgid, ready, pids_file, owned_members)
        groups = {pgid for pgid, _, _, _, _ in shells}
        workloads = {pid for members in owned_members.values() for pid, _ in members}
        assert len(groups) == len(commands)
        assert all(owned_members.get(group) for group in groups)
        tree, info, known = _owned_process_snapshot(owned_members)
        protect = {os.getpid()}
        rows = nh.shell_rows([list(r) for r in se.TOOL_SHELLS], os.getpid())
        assert len(rows) == len(commands)
        picked = nh._backgrounded({os.getpid()}, tree, info, protect, rows, known)
        assert workloads <= picked, sorted(workloads - picked)
        assert not picked & protect
        # a forged row naming the caller's own group or session takes nothing of it (it is protected)
        for key in (os.getpgrp(), os.getsid(0)):
            forged = [(key, info.get(key, (0.0,))[0], os.getpid())]
            assert not nh._backgrounded({os.getpid()}, tree, info, protect, forged, known) & protect
    finally:
        cleanup_errors = []
        cleanup_deadline = time.monotonic() + 5
        for pgid, _, leader, ready, _ in reversed(shells):
            try:
                # Do not signal by PID or PGID after the event loop closes.
                # A live watchdog is the only group-signal authority.
                deadline = time.monotonic() + 1
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                cleanup_group(pgid, ready)

                identities = set(owned_members.get(pgid, {}))
                if leader is not None:
                    identities.add((leader[0].pid, leader[1]))
                try:
                    data = json.loads(ready.read_text())
                    identities.add((data["pid"], data["birth"]))
                except FileNotFoundError:
                    pass  # No workload starts before the watchdog handshake.
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    cleanup_errors.append(RuntimeError(f"could not verify cleanup identities for group {pgid}: {exc}"))

                remaining = max(0, cleanup_deadline - time.monotonic())
                wait_for_recorded_identities(identities, timeout=remaining)
            except Exception as exc:
                cleanup_errors.append(exc)
        se.TOOL_SHELLS.clear()
        if cleanup_errors:
            raise ExceptionGroup("shell test cleanup failed", cleanup_errors)


@posix_only
@pytest.mark.parametrize("failure_stage", ["launch", "sweep"])
def test_shell_fixture_cleans_up_if_launch_or_sweep_raises(tmp_path, monkeypatch, failure_stage):
    """Failures during setup or the sweep still stop only this test's processes."""
    original_run = se.ShellExecutor.run
    spawned = []
    outsider = subprocess.Popen(bounded_child_command())

    def remember_processes():
        for ready in tmp_path.glob("watchdog-*.json"):
            try:
                data = json.loads(ready.read_text())
                pgid = data["pgid"]
                anchor = _live_private_group_anchor(ready, pgid)
                if anchor is not None:
                    spawned.append(anchor[0])
                index = ready.stem.split("-")[-1]
                pids_file = tmp_path / f"owned-{index}.pids"
                for line in pids_file.read_text().splitlines():
                    if not line.isdigit():
                        continue
                    process = psutil.Process(int(line))
                    birth = process.create_time()
                    if (
                        process.is_running()
                        and process.status() != psutil.STATUS_ZOMBIE
                        and os.getpgid(process.pid) == pgid
                        and process.create_time() == birth
                    ):
                        spawned.append(process)
            except FileNotFoundError, psutil.NoSuchProcess, ProcessLookupError, ValueError:
                continue

    async def fail_after_first_launch(executor, *args, **kwargs):
        result = await original_run(executor, *args, **kwargs)
        assert result.success
        remember_processes()
        raise RuntimeError("injected fixture failure")

    def fail_sweep(*args, **kwargs):
        remember_processes()
        raise RuntimeError("injected fixture failure")

    try:
        if failure_stage == "launch":
            monkeypatch.setattr(se.ShellExecutor, "run", fail_after_first_launch)
        else:
            monkeypatch.setattr(nh, "_backgrounded", fail_sweep)
        with pytest.raises(RuntimeError, match="injected fixture failure"):
            test_real_shell_executor_leftovers_are_swept_even_with_hidden_env(tmp_path)
        assert len(spawned) >= 2  # exact watchdog and workload identities from private files

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


@posix_only
def test_sweep_survives_a_ps_start_drift(tmp_path):
    """#710: ps reads a start a second or two off the agent's clock; the leftovers are still swept."""
    mark = f"6{os.getpid():07d}"
    cmds = [f"nohup sleep {mark}1 >/dev/null 2>&1 &", f"( sleep {mark}2 ) >/dev/null 2>&1 &",
            f"sh -c 'sleep {mark}3; :' >/dev/null 2>&1 &"]
    se.TOOL_SHELLS.clear()

    async def run_all():
        ex = se.ShellExecutor()
        for cmd in cmds:
            await ex.run(cmd, timeout=5)

    asyncio.run(run_all())
    time.sleep(0.5)

    def ours() -> set[int]:
        return {p for p, c in _ps().items() if mark in c} - {os.getpid()}

    left = ours()
    try:
        assert len(left) >= len(cmds)
        tree, info = hr.process_tree(), hr.process_info()
        drifted = {p: (v[0] - 2.0, *v[1:]) for p, v in info.items()}
        protect = nh.ancestors(os.getpid()) | {os.getpid()}
        known = {p.pid: p for p in (nh.read_process(x, with_cwd=False) for x in left) if p is not None}
        rows = nh.shell_rows([list(r) for r in se.TOOL_SHELLS], os.getpid())
        picked = nh._backgrounded({os.getpid()}, tree, drifted, protect, rows, known)
        assert left <= picked, sorted(left - picked)
        assert not picked & protect
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

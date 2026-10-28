"""Kill switch slice B: boot check, waiter, listener, hand-off, sweep, CLI.

Never starts a real listener or sweeps a real process: the process list, the
stop function and the link are injected, and every root is a tmp dir.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

FOLDER = "/work/kollab"
HALTED = {"scopes": {"net1": [FOLDER]}, "networks": {}}
MACHINE = {"scopes": {"net1": "machine"}, "networks": {}}
CHILD = {"KOLLAB_DETACHED_CHILD": "1"}
SERVICE = {"KOLLAB_SERVICE": "1"}


@pytest.fixture(autouse=True)
def _clean_entry():
    hr.ENTRY.clear()
    yield
    hr.ENTRY.clear()


@pytest.mark.parametrize(
    "args",
    [
        ["--hub", "status"], ["--hub", "halt"], ["--hub", "resume", "--local"], ["service", "install"],
        ["--version"], ["-V"], ["--help"], ["-h"], ["--attach", "lapis"], ["--web-ui"],
        ["--halt-listener"], ["relay", "serve"], ["mcp", "hub"],
    ],
)
def test_exempt_launches_run_under_a_halt(args):
    assert hr.boot_decision(["kollab", *args], {}, FOLDER, MACHINE) == "run"


@pytest.mark.parametrize(
    "args,env,want",
    [
        (["-p", "hi"], {}, "exit"),
        (["--pipe"], {}, "exit"),
        (["acp"], {}, "exit"),
        (["--detached"], CHILD, "waiter"),
        (["-d", "--as", "lapis"], CHILD, "waiter"),
        (["--detached"], SERVICE, "waiter"),
        ([], {}, "interactive"),
        (["--agent", "coder"], {}, "interactive"),
        (["--detached"], {}, "run"),  # the parent forks; the child decides
    ],
)
def test_launches_in_scope(args, env, want):
    assert hr.boot_decision(["kollab", *args], env, FOLDER, HALTED) == want


def test_out_of_scope_and_lifted_folders_run():
    assert hr.boot_decision(["kollab", "-p"], {}, "/elsewhere", HALTED) == "run"
    assert hr.boot_decision(["kollab"], {}, FOLDER + "/sub", HALTED) == "interactive"
    assert hr.boot_decision(["kollab"], {}, FOLDER, {"scopes": {}}) == "run"
    assert hr.boot_decision(["kollab", "--detached"], CHILD, FOLDER, {"scopes": {}}) == "run"


def test_boot_check_exits_75_for_a_pipe_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine"}, "networks": {}})
    with pytest.raises(SystemExit) as stop:
        hr.boot_check(["kollab", "-p", "hi"], {}, root=tmp_path)
    assert stop.value.code == nh.EX_TEMPFAIL == 75
    assert "Network halted by" in capsys.readouterr().err


def test_boot_check_without_halt_json_does_nothing(tmp_path):
    hr.boot_check(["kollab", "-p"], {}, root=tmp_path)  # no exception, no listener


def test_entry_is_captured_before_the_pops(monkeypatch):
    monkeypatch.setenv("KOLLAB_SERVICE", "1")
    monkeypatch.setenv("KOLLAB_DETACHED_CHILD", "1")
    hr.capture_entry(["kollab", "--detached", "--as", "lapis"])
    os.environ.pop("KOLLAB_SERVICE")  # what cli_main does next
    os.environ.pop("KOLLAB_DETACHED_CHILD")
    hr.capture_entry(["kollab", "other"])  # a later call never overwrites
    assert hr.ENTRY["argv"] == ["kollab", "--detached", "--as", "lapis"]
    assert hr.ENTRY["env"]["KOLLAB_SERVICE"] == "1"
    assert hr.ENTRY["env"]["KOLLAB_DETACHED_CHILD"] == "1"


def test_waiter_execs_with_captured_argv_and_env_once_lifted(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "_waiter_presence", lambda *a: (None, None))
    monkeypatch.setattr(hr, "listener_running", lambda root=None: True)
    monkeypatch.setattr(hr, "restart_argv", lambda argv: ["/bin/kollab", *argv[1:]])
    hf = nh.HaltFile(tmp_path)
    hf.write(HALTED)
    calls, polls = [], []

    def sleep(_s):
        polls.append(1)
        hf.write({"scopes": {}, "networks": {}})  # the listener handed back

    env = {"KOLLAB_SERVICE": "1", "PATH": "/bin", hr.WAITER_ENV: "1"}  # inherited from a waiter parent
    hr.run_waiter(["kollab", "--detached", "--as", "lapis"], env, FOLDER, root=tmp_path,
                  execve=lambda *a: calls.append(a), sleep=sleep)
    assert polls == [1]
    assert calls == [("/bin/kollab", ["/bin/kollab", "--detached", "--as", "lapis"],
                      {"KOLLAB_SERVICE": "1", "PATH": "/bin"})]
    assert hr.WAITER_ENV not in calls[0][2]


def test_waiter_never_takes_halt_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "_waiter_presence", lambda *a: (None, None))
    started = []
    monkeypatch.setattr(hr, "start_listener", lambda root=None, **k: started.append(1))
    nh.HaltFile(tmp_path).write(HALTED)
    hr.run_waiter(["kollab"], {}, FOLDER, root=tmp_path, execve=None, sleep=lambda s: None, max_polls=2)
    assert started == [1, 1]  # no listener: the waiter starts one
    assert not hr.listener_running(tmp_path)  # and it never held the lock itself


def test_write_lock_loses_no_writes(tmp_path):
    def bump(data):
        data["n"] = data.get("n", 0) + 1

    threads = [threading.Thread(target=lambda: [hr.update_halt(bump, tmp_path) for _ in range(10)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hr.read_halt(tmp_path)["n"] == 40


# ------------------------------------------------------------ listener


class FakeLink:
    def __init__(self, log):
        self.log = log

    async def close(self):
        self.log.append("link closed")

    async def hub_halt_snapshot(self):
        return {"active": [{"machine": "m" * 64, "reason": "r"}]}


def test_hand_off_records_scope_sweeps_and_opens_the_link(tmp_path, monkeypatch):
    stopped, log = [], []
    me = os.getpid()
    cli = 4242
    procs = [
        nh.Proc(101, 1, ["/x/python", "-m", "kollabor_cli_main"], {}, FOLDER),
        nh.Proc(cli, 1, ["/x/python", "-m", "kollabor_cli_main", "--hub", "stop", "x"], {}, FOLDER),
    ]
    install = nh.Install(prefix="/x")
    h = "ab" * 32  # the halt_act record the hand-off must find in the book
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {"net1": {"halts": {
        h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}}})

    async def stop(agent):
        stopped.append(agent.get("pid"))
        return True, f"stopped {agent.get('identity')}"

    async def open_link(folder, state_dir):
        log.append(("open", folder))
        return FakeLink(log)

    listener = hr.HaltListener(
        tmp_path, stop=stop, processes=lambda: procs,
        agents=lambda: [{"identity": "lapis", "pid": 101, "project": FOLDER}], open_link=open_link,
    )
    monkeypatch.setattr(nh, "ancestors", lambda pid: {pid, me})  # F15b: the peer's chain, read by the listener
    monkeypatch.setattr(hr, "caller_refusal", lambda pid: "")  # F15b: the peer is a person's CLI, not an agent
    listener._networks_of = lambda folder: ["net1"]  # F15b: the folder belongs to this network
    orig = hr.sweep

    async def sweep(*a, **k):
        return await orig(*a, **{**k, "install": install})

    hr_sweep, hr.sweep = hr.sweep, sweep
    hr_state, hr._state_dir_for = hr._state_dir_for, lambda folder, net: Path(folder)
    try:
        reply = asyncio.run(listener.handle(
            {"op": "halt", "network": "net1", "scope": [FOLDER], "folder": FOLDER, "protect": [me, cli]},
            peer=cli,
        ))
    finally:
        hr.sweep, hr._state_dir_for = hr_sweep, hr_state
    assert reply["ok"] and reply["count"] == 1
    assert stopped == [101]  # the issuing CLI (protected) survives
    data = hr.read_halt(tmp_path)
    assert data["scopes"] == {"net1": [FOLDER]}
    assert data["stopped"] == [{"identity": "lapis", "folder": FOLDER, "service": False}]
    assert log == [("open", FOLDER)] and "net1" in listener.links


def test_protect_set_never_shields_a_peer_that_is_a_live_agent(tmp_path, monkeypatch):
    """F17/#363: a halt.sock peer that IS itself a live agent — the daemon handing off a
    listener-learned halt — is not spared by the sweep, even though the F10 guard clears it
    (its ancestors are clean and its env carries no tool-shell tag; that is the live D12 state).
    Until F17 no test made the caller itself a live agent, which is how #363 shipped green."""
    me = os.getpid()
    daemon = 101  # the machine's agent: in presence, parented by init, no tool-shell env
    parent = 4242
    listener = hr.HaltListener(
        tmp_path, agents=lambda: [{"identity": "koordinator", "pid": daemon, "project": FOLDER}]
    )
    monkeypatch.setattr(nh, "ancestors", lambda pid: {pid, parent, me})
    monkeypatch.setattr(hr, "caller_refusal", lambda pid: "")  # the guard clears it — not enough
    protect = listener._protect_set(daemon, waiters=set())
    assert daemon not in protect  # the agent peer is swept
    assert parent in protect and me in protect  # its chain (a person's shell, tmux) survives


def test_protect_set_unreadable_presence_fails_closed(tmp_path, monkeypatch):
    """F17/#363: when presence cannot be read, the peer is spared nothing (caller_refusal's
    own posture — it refuses on unreadable presence too)."""
    me = os.getpid()
    listener = hr.HaltListener(tmp_path)

    def boom():
        raise OSError("presence gone")

    monkeypatch.setattr(hr, "_live_agents", boom)
    monkeypatch.setattr(nh, "ancestors", lambda pid: {pid, me})
    monkeypatch.setattr(hr, "caller_refusal", lambda pid: "")
    assert 4242 not in listener._protect_set(4242, waiters=set())


def test_hand_off_from_the_daemon_sweeps_the_daemon(tmp_path, monkeypatch):
    """F17/#363, the live D12 path replayed: the automatic hand-off's caller is the machine's
    own --detached daemon (a presence agent). The sweep must stop it — before F17 it returned
    {"ok": true} with stopped: [] and the daemon lived on under a machine-wide halt."""
    stopped = []
    me = os.getpid()
    daemon = 101
    procs = [nh.Proc(daemon, 1, ["/x/python", "-m", "kollabor_cli_main", "--detached"], {}, FOLDER)]
    install = nh.Install(prefix="/x")
    h = "ab" * 32
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {"net1": {"halts": {
        h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}}})

    async def stop(agent):
        stopped.append(agent.get("pid"))
        return True, f"stopped {agent.get('identity')}"

    listener = hr.HaltListener(
        tmp_path, stop=stop, processes=lambda: procs, open_link=lambda folder, state_dir: None,
        agents=lambda: [{"identity": "koordinator", "pid": daemon, "project": FOLDER}],
    )
    monkeypatch.setattr(nh, "ancestors", lambda pid: {pid, 1, me})
    monkeypatch.setattr(hr, "caller_refusal", lambda pid: "")  # guard clears: ancestors clean, no env tag
    listener._networks_of = lambda folder: ["net1"]
    orig = hr.sweep

    async def sweep(*a, **k):
        return await orig(*a, **{**k, "install": install})

    hr_sweep, hr.sweep = hr.sweep, sweep
    hr_state, hr._state_dir_for = hr._state_dir_for, lambda folder, net: Path(folder)
    try:
        reply = asyncio.run(listener.handle(
            {"op": "halt", "network": "net1", "scope": "machine", "folder": FOLDER, "hash": h},
            peer=daemon,
        ))
    finally:
        hr.sweep, hr._state_dir_for = hr_sweep, hr_state
    assert reply["ok"] and reply["count"] == 1, reply
    assert stopped == [daemon]  # the daemon that handed the halt off is swept, not spared


def test_hand_back_releases_the_link_before_marking_lifted(tmp_path):
    log = []
    nh.HaltFile(tmp_path).write({"scopes": {"net1": [FOLDER]}, "networks": {}})
    listener = hr.HaltListener(tmp_path, grace=0)
    listener.links["net1"] = FakeLink(log)
    orig = hr.update_halt

    def spy(fn, root=None):
        log.append("marked lifted" if "net1" in (hr.read_halt(root).get("scopes") or {}) else "other")
        return orig(fn, root)

    hr.update_halt = spy
    try:
        asyncio.run(listener.tick())  # no active halt in the book: hand back
    finally:
        hr.update_halt = orig
    assert log == ["link closed", "marked lifted"]
    assert hr.read_halt(tmp_path)["scopes"] == {}
    assert listener._done.is_set()  # no halt left: the listener exits


def test_hand_back_tells_the_members_online_before_it_leaves_the_relay(tmp_path):
    # the confirmer's listener used to exit at once, leaving the proposer halted (found in Docker)
    log = []

    class Flushing(FakeLink):
        async def flush(self):
            self.log.append("flushed")

    nh.HaltFile(tmp_path).write({"scopes": {"net1": [FOLDER]}, "networks": {}})
    listener = hr.HaltListener(tmp_path, grace=0)
    listener.links["net1"] = Flushing(log)
    asyncio.run(listener.hand_back("net1"))
    assert log == ["flushed", "link closed"]


def test_hand_back_keeps_the_halt_when_the_flush_brings_it_back(tmp_path, monkeypatch):
    # `resume --local`, then the flush hears a member that still holds the halt (found in Docker)
    log = []

    class Flushing(FakeLink):
        async def flush(self):
            self.log.append("flushed")

    nh.HaltFile(tmp_path).write({"scopes": {"net1": [FOLDER]}, "networks": {"net1": {}}})
    listener = hr.HaltListener(tmp_path, grace=0)
    link = Flushing(log)
    listener.links["net1"] = link
    monkeypatch.setattr(hr, "_book_active", lambda net, book: True)
    asyncio.run(listener.hand_back("net1"))
    assert log == ["flushed"] and listener.links["net1"] is link
    assert hr.read_halt(tmp_path)["scopes"] == {"net1": [FOLDER]}


def test_a_fresh_listener_waits_for_the_hand_off_that_started_it(tmp_path):
    # the CLI records the halt, then starts the listener, then hands off: with no
    # scope yet the listener must not exit on its first tick (found in Docker)
    nh.HaltFile(tmp_path).write({"scopes": {}, "networks": {}})
    listener = hr.HaltListener(tmp_path)
    asyncio.run(listener.tick())
    assert not listener._done.is_set()
    listener._hold_until = 0
    asyncio.run(listener.tick())
    assert listener._done.is_set()


def test_state_dir_for_falls_back_to_the_folders_default_dir(tmp_path, monkeypatch):
    # a machine that never registered a membership has no Memberships row (found in Docker)
    from plugins.hub import membership_runtime, relay_state

    class Empty:
        def __init__(self, folder):
            pass

        def list(self):
            return []

    monkeypatch.setattr(membership_runtime, "Memberships", Empty)
    monkeypatch.setattr(relay_state, "default_workspace_state_dir", lambda folder: tmp_path)
    monkeypatch.setattr(hr, "_network_of", lambda folder, state_dir: "net1")
    assert hr._state_dir_for(FOLDER, "net1") == tmp_path
    assert hr._state_dir_for(FOLDER, "other") is None


def test_global_config_gives_the_listener_the_relay_ca_setting(tmp_path, monkeypatch):
    # without it the listener's link never connected to a relay on a private CA (found in Docker)
    import json

    (tmp_path / ".kollab").mkdir()
    (tmp_path / ".kollab" / "config.json").write_text(
        json.dumps({"plugins": {"hub": {"endpoint_tls_ca": "/x/ca.pem"}}})
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = hr._global_config()
    assert config is not None and config.get("plugins.hub.endpoint_tls_ca", "") == "/x/ca.pem"


def test_listener_socket_serves_only_halt_calls(tmp_path):
    listener = hr.HaltListener(tmp_path)
    assert asyncio.run(listener.handle({"op": "relay.send"}))["ok"] is False
    assert asyncio.run(listener.handle({"op": "ping"})) == {"ok": True}


class FakeClient:
    def __init__(self, members, links=()):
        self._members = set(members)
        self.state = type("S", (), {"links": set(links)})()

    def members(self):
        return self._members


class FakeTransport:
    def __init__(self, inner_method):
        self.inner_method = inner_method
        self.reached = []

    def identity_response(self, peer, payload):
        self.reached.append(("identity", peer))
        return {"id": 1}

    async def handle_packet(self, peer, payload, app):
        return await app(peer, self.inner_method, payload)


def _link(client, transport):
    link = hr.HaltLink.__new__(hr.HaltLink)
    link.client, link.transport = client, transport

    class Sync:
        async def receive(self, peer, payload):
            transport.reached.append(("network_halt", peer))
            return {}

    link.sync = Sync()
    return link


@pytest.mark.parametrize(
    "peer,method,inner,allowed",
    [
        ("member", "secure_identity", None, True),
        ("member", "secure_packet", "network_halt", True),
        ("member", "secure_packet", "message", False),
        ("member", "secure_packet", "config_sync", False),
        ("member", "relay.send", None, False),
        ("stranger", "secure_identity", None, False),
        ("stranger", "secure_packet", "network_halt", False),
        ("nobody", "secure_packet", "network_halt", False),
    ],
)
def test_listener_allowlist_runs_before_dispatch(peer, method, inner, allowed):
    from plugins.hub.relay_state import RelayError

    client = FakeClient(members={"member", "stranger"}, links={"stranger"})
    transport = FakeTransport(inner)
    link = _link(client, transport)
    if allowed:
        asyncio.run(link.receive(peer, method, {}))
        assert transport.reached
    else:
        with pytest.raises(RelayError, match="halted"):
            asyncio.run(link.receive(peer, method, {}))
        assert not [r for r in transport.reached if r[0] == "network_halt"]


# ------------------------------------------------------------------ sweep


def test_sweep_spares_listener_cli_and_its_ancestors_and_waiters():
    install = nh.Install(prefix="/x")
    cli, shell = 500, 499
    procs = [
        nh.Proc(10, 1, ["/x/python", "-m", "kollabor_cli_main", "--detached"], {}, FOLDER),
        nh.Proc(cli, shell, ["/x/python", "-m", "kollabor_cli_main", "--hub", "stop", "all"], {}, FOLDER),
        nh.Proc(shell, 1, ["/bin/zsh"], {}, FOLDER),
        nh.Proc(11, 1, ["/x/python", "-m", "kollabor_cli_main"], {nh.WAITER_ENV: "1"}, FOLDER),
    ]
    got = []

    async def stop(agent):
        got.append(agent["pid"])
        return True, "ok"

    agents = [{"pid": 11, "project": FOLDER, "state": "halted"}, {"pid": cli, "project": FOLDER}]
    asyncio.run(hr.sweep(["machine"], agents=agents, procs=procs, stop=stop, protect={cli, shell}, install=install))
    assert got == [10]


# -------------------------------------------------------------------- CLI


@pytest.mark.parametrize("env,tty", [({"KOLLAB_TOOL_SHELL": "1"}, True), ({}, False)])
def test_cli_refuses_from_a_tool_shell_or_without_a_tty(monkeypatch, capsys, env, tty):
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(os, "isatty", lambda fd: tty)
    monkeypatch.setattr(hr, "_folder_control", lambda f: pytest.fail("must refuse before anything else"))
    for sub in ("halt", "resume"):
        assert asyncio.run(hr.cli_control(sub, [])) == 1
        assert capsys.readouterr().out.strip() == nh.GUARD_REFUSAL


def test_cli_member_lines_never_print_an_unanswered_device_as_done():
    members = [{"name": "mac", "folder": "a"}, {"name": "prod", "folder": "b"}, {"name": "pi", "folder": "c"}]
    assert hr.member_lines(members, acked={"c"}, own_folder="a", stopped=3) == [
        "mac: halted, 3 agents stopped",
        "prod: offline, halts on reconnect",
        "pi: halted",
    ]


def test_cli_member_lines_online_but_unacked_is_ack_pending():
    members = [{"name": "mac", "folder": "a"}, {"name": "prod", "folder": "b"}, {"name": "pi", "folder": "c"}]
    assert hr.member_lines(members, acked=set(), own_folder="a", stopped=1, online={"b"}) == [
        "mac: halted, 1 agents stopped",
        "prod: halted, ack pending",
        "pi: offline, halts on reconnect",
    ]


def test_await_acks_waits_longer_for_an_online_member(monkeypatch):
    calls = []

    async def fake_call(req, root, timeout=5.0):
        calls.append(1)
        acked = ["b"] if len(calls) >= 3 else []
        return {"members": [{"folder": "a"}, {"folder": "b"}], "acked": acked, "online": ["b"]}

    async def nosleep(_):
        return None

    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(hr, "sock_call", fake_call)
    monkeypatch.setattr(hr.asyncio, "sleep", nosleep)
    monkeypatch.setattr(hr.time, "monotonic", lambda: next(clock))
    status = asyncio.run(hr._await_acks("/f", None, seconds=10.0, online_seconds=40.0))
    assert status["acked"] == ["b"] and len(calls) == 3


def test_cli_resume_lines(monkeypatch, capsys):
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
def test_cli_resume_lines(monkeypatch, capsys):
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    # F10 guard isolation: this test runs pytest itself, which inside an agent
    # tool shell carries that shell's env tags in its ancestor chain. The
    # sibling cli tests stub this refusal; this one missed it and failed only
    # where pytest runs under an agent (CI, a person's shell: green).
    monkeypatch.setattr(hr, "tool_shell_refusal", lambda: "")


    class Control:
        def halt_snapshot(self):
            return {"active": [{"hash": "ab" * 32, "reason": "oops"}]}

    results = {
        "resume_local": {"line": nh.LOCAL_RESUME_LINE},
        "confirm_resume": {"lifted": ["h1", "h2"]},
        "propose_resume": {"hash": "cd" * 32},
    }

    async def act(folder, control, action, payload, root):
        return results[action]

    monkeypatch.setattr(hr, "_folder_control", lambda f: Control())
    monkeypatch.setattr(hr, "_act", act)
    assert asyncio.run(hr.cli_control("resume", ["--local"])) == 0
    assert capsys.readouterr().out.strip() == nh.LOCAL_RESUME_LINE
    asyncio.run(hr.cli_control("resume", ["--confirm", "abababab"]))
    assert capsys.readouterr().out.strip() == "Resume confirmed: lifts 2 halts"
    asyncio.run(hr.cli_control("resume", []))
    out = capsys.readouterr().out
    assert "abababab oops" in out and hr.RESUME_PROPOSED in out
    assert asyncio.run(hr.cli_control("resume", ["--bogus"])) == 2


def test_parse_flags():
    assert hr.parse_flags(["--reason", "two words", "--revoke", "prod"], {"--reason": True, "--revoke": True}) == {
        "reason": "two words", "revoke": "prod"}
    with pytest.raises(ValueError):
        hr.parse_flags(["--reason"], {"--reason": True})

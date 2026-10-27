"""Kill switch review fixes (F6): legacy founder takeover, pick flooding, opt-out, scope, tool trees.

Unit tests only. The real-child test starts its own throwaway python processes and kills them itself;
nothing here halts, sweeps or signals anything else on the host.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest
from nacl.signing import SigningKey

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from plugins.hub.enrollment_codes import split_join_code
from plugins.hub.network_control import MAX_PER_SIGNER, NetworkControl
from tests.unit.test_network_control import ROOM, machine, meet, three  # noqa: F401


def _legacy_network(tmp_path):
    """A pre-release network: members existed before the founder record, so the id is sha256(room)."""
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)
    f.found()
    assert f.network_id() == hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()
    return f, f.network_id()


# ---------------------------------------------------------------- B2: legacy founder takeover


def test_b2_a_held_unconfirmed_founder_does_not_lock_out_the_real_one(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    r = machine(tmp_path, "r", inviter="ee" * 32)  # the compromised inviter
    m = machine(tmp_path, "m")
    m.client.state.inviter = r.client.public_key
    rogue = nh.founder_record(r.machine, legacy)
    m.take_join({"founder": rogue}, anchor=nh.join_anchor(legacy))  # the join decision names the inviter itself
    assert m.data["founder"] == rogue and m.view().founder == ""  # held, never accepted
    meet(f, r, m)
    keys = {c["founder"] for c in m.data.get("founder_candidates", [])}
    assert f.machine_hex in keys
    m.halt_act("confirm_founder", {"founder": f.machine_hex[:12]})  # the fingerprint the person chose
    assert m.view().founder == f.machine_hex
    assert m.network_id() == legacy


def test_b2_confirm_founder_names_one_among_the_held_record_and_the_candidates(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    r = machine(tmp_path, "r", inviter="ee" * 32)
    m = machine(tmp_path, "m")
    m.client.state.inviter = r.client.public_key
    m.take_join({"founder": nh.founder_record(r.machine, legacy)}, anchor=nh.join_anchor(legacy))
    meet(f, r, m)
    with pytest.raises(nh.HaltError):
        m.halt_act("confirm_founder", {})  # two to choose from: the person must name one
    with pytest.raises(nh.HaltError):
        m.halt_act("confirm_founder", {"founder": "00" * 6})
    m.halt_act("confirm_founder", {"founder": r.machine_hex[:12]})  # the held one is still choosable
    assert m.view().founder == r.machine_hex


def test_b2_the_anchor_is_60_bits_and_a_code_carries_three_groups():
    anchor = nh.join_anchor("ab" * 32)
    groups = anchor.split("-")
    assert len(groups) == 3 and all(len(g) == 4 for g in groups)
    assert split_join_code(f"ABCD-EFGH-{anchor}") == ("ABCD-EFGH", anchor)
    assert split_join_code("ABCD-EFGH-1234-5678") == ("ABCD-EFGH-1234-5678", "")  # the old 40-bit tail is not an anchor


# ---------------------------------------------------------------- S4: pick flooding


def test_s4_a_member_cannot_lock_the_real_founders_pick_out(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    r = machine(tmp_path, "r", inviter="ee" * 32)  # a real approved member turned hostile
    m = machine(tmp_path, "m", inviter="ee" * 32)
    meet(f, r, m)
    m.learn(r.client.public_key, {"founder": nh.founder_record(r.machine, legacy)})
    picks = [nh.admission(r.machine, legacy, nh._hex(SigningKey.generate()), bootstrap=True) for _ in range(8)]
    m.learn(r.client.public_key, {"bootstrap_picks": picks})
    assert sum(1 for p in m.data["bootstrap_picks"] if p["admitter"] == r.machine_hex) <= 2  # per admitter
    f.halt_act("make_bootstrap", {"machine": m.machine_hex})
    meet(f, r, m)
    m.halt_act("confirm_founder", {"founder": f.machine_hex})
    meet(f, r, m)
    held = m.data.get("bootstrap_picks", [])
    assert any(p["admitter"] == f.machine_hex for p in held)
    assert all(p["admitter"] == f.machine_hex for p in held)  # the confirmed founder evicts the rest
    assert m.view().counts(m.machine_hex)


def test_s4_picks_of_a_non_founder_are_dropped_once_a_founder_is_confirmed(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    r = machine(tmp_path, "r", inviter="ee" * 32)
    m = machine(tmp_path, "m", inviter="ee" * 32)
    meet(f, r, m)
    m.learn(r.client.public_key, {"founder": nh.founder_record(r.machine, legacy)})
    m.learn(r.client.public_key, {"bootstrap_picks": [nh.admission(r.machine, legacy, m.machine_hex, bootstrap=True)]})
    assert m.data["bootstrap_picks"]
    m.halt_act("confirm_founder", {"founder": f.machine_hex})
    assert m.data["bootstrap_picks"] == []  # gone at once, not on the next gossip


# ---------------------------------------------------------------- S2: opt-out and scope


def test_s2_one_member_cannot_drop_counting_below_two(three):  # noqa: F811
    f, b, c = three
    assert len(f.view().counting) == 2
    with pytest.raises(nh.HaltError) as refused:
        b.halt_act("no_count")
    assert "second machine" in str(refused.value)  # a plain line, not a silent no-op
    # a hostile member signs the record itself and publishes it: the count ignores it
    rec = nh.no_count_record(b.machine, b.network_id(), issued_at=int(time.time()) + 5, opted_out=True)
    b.data.setdefault("opt_outs", []).append(rec)
    b.save()
    meet(f, b, c)
    assert f.view().counting == {f.machine_hex, b.machine_hex}
    f.halt_act("halt", {"reason": "x"})  # a halt is still allowed


def test_s2_an_opt_out_that_leaves_two_counting_machines_still_works(three):  # noqa: F811
    f, b, c = three
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    meet(f, b, c)
    assert len(f.view().counting) == 3
    c.halt_act("no_count")
    meet(f, b, c)
    assert f.view().counting == {f.machine_hex, b.machine_hex}


def test_s2_two_opt_outs_of_three_keep_two_counting(three):  # noqa: F811
    f, b, c = three
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    meet(f, b, c)
    for who in (b, c):  # both sign directly, so neither action check can stop them
        who.data.setdefault("opt_outs", []).append(
            nh.no_count_record(who.machine, who.network_id(), issued_at=int(time.time()) + 5, opted_out=True)
        )
        who.save()
    meet(f, b, c)
    assert len(f.view().counting) >= 2


@pytest.mark.parametrize("scope", ["Machine", "machines", "", 5, [5], [], {"a": 1}, None, ["/ok", 7]])
def test_s2_an_odd_scope_holds_the_whole_machine(tmp_path, monkeypatch, scope):
    monkeypatch.chdir(tmp_path)
    assert nh.in_scope(str(tmp_path), [scope])  # never "run", never a TypeError
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    (path / "halt.json").write_text(json.dumps({"scopes": {"n": scope}, "networks": {}}))
    (path / "halt.json").chmod(0o600)
    held = hr.read_halt_held(tmp_path)
    assert hr.folder_halted(str(tmp_path), held)
    with pytest.raises(SystemExit) as stop:
        hr.boot_check(["kollab", "-p", "hi"], {}, root=tmp_path)
    assert stop.value.code == 75


def test_s2_a_known_scope_still_scopes(tmp_path):
    inside, outside = tmp_path / "a", tmp_path / "b"
    inside.mkdir()
    outside.mkdir()
    assert nh.in_scope(str(inside), ["machine"])
    assert nh.in_scope(str(inside), [[str(inside)]])
    assert not nh.in_scope(str(outside), [[str(inside)]])


# ---------------------------------------------------------------- B3: process tree


def _spawn_agent_with_untagged_tool(tmp_path):
    """An 'agent' (main.py beside a kollabor dir) that starts a tool in its own session with no tag, no kollab."""
    py = sys._base_executable
    (tmp_path / "kollabor").mkdir(exist_ok=True)
    pidfile = tmp_path / "tool.pid"
    (tmp_path / "main.py").write_text(
        "import os, subprocess, sys, time\n"
        f"env = {{k: v for k, v in os.environ.items() if not k.startswith('KOLLAB')}}\n"
        f"p = subprocess.Popen([{py!r}, '-c', 'import time; time.sleep(60)'], start_new_session=True, env=env)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    agent = subprocess.Popen([py, str(tmp_path / "main.py")], cwd=tmp_path, start_new_session=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    return agent, int(pidfile.read_text())


def test_b3_the_sweep_walks_the_tree_to_a_tool_with_an_unreadable_environment(tmp_path):
    agent, tool = _spawn_agent_with_untagged_tool(tmp_path)
    bystander = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        assert os.getpgid(tool) == tool != os.getpgid(agent.pid)
        install = nh.Install(prefix=sys.base_prefix)
        procs = hr._read_candidates(install)
        assert tool not in {p.pid for p in procs}  # no tag and no kollab in argv: the env tag cannot find it
        tree = hr.process_tree()
        assert tree[tool][0] == agent.pid
        stopped = []

        async def stop(rec):
            stopped.append(rec["pid"])
            hr.group_kill({os.getpid()})(rec["pid"], 15)
            return True, "ok"

        asyncio.run(hr.sweep(
            [[str(tmp_path)]], procs=procs, stop=stop, protect={os.getpid()}, tree=tree,
            agents=[{"identity": "x", "pid": agent.pid, "project": str(tmp_path)}], install=install,
        ))
        assert tool in stopped and agent.pid in stopped
        assert bystander.pid not in stopped and os.getpid() not in stopped
        assert bystander.poll() is None
    finally:
        for c in (agent, bystander):
            c.kill()
            c.wait()
        with __import__("contextlib").suppress(OSError):
            os.kill(tool, 9)


def test_b3_the_tree_walk_never_selects_a_protected_process_or_another_install():
    install = nh.Install(prefix="/opt/this-install")
    agent = nh.Proc(100, 1, ["/opt/this-install/bin/kollab", "--detached"], {}, "/work")
    other = nh.Proc(300, 100, ["/opt/other/bin/python", "-m", "kollabor_cli_main"], {}, "/work")
    tree = {100: (1, 100, 100), 200: (100, 200, 200), 300: (100, 300, 300), 400: (200, 200, 200), 500: (1, 500, 500)}
    out = nh.select_sweep(
        [agent, other], install=install, scopes=["machine"], agent_pids=[100], protect={400}, tree=tree,
    )
    got = {p.pid for p in out}
    assert 100 in got and 200 in got
    assert 400 not in got  # protected (the issuing CLI sat in the same group)
    assert 300 not in got  # another install's kollab
    assert 500 not in got  # not under the agent


# ---------------------------------------------------------------- fork_daemon fails closed


def test_exec_failure_check_fails_closed_when_it_cannot_run(monkeypatch):
    from kollabor import daemon

    monkeypatch.setitem(sys.modules, "plugins.hub.halt_runtime", None)  # the import raises ImportError
    assert daemon._halt_holds_after_exec_failure(["kollab", "--detached"]) is True
    monkeypatch.undo()
    monkeypatch.setattr(hr, "halted_after_exec_failure", lambda argv: (_ for _ in ()).throw(OSError("x")))
    assert daemon._halt_holds_after_exec_failure(["kollab"]) is True
    monkeypatch.setattr(hr, "halted_after_exec_failure", lambda argv: False)
    assert daemon._halt_holds_after_exec_failure(["kollab"]) is False


# ---------------------------------------------------------------- network id migration


def test_an_old_control_file_without_net_keeps_the_id_it_used(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    bound = f.network_id()
    assert bound != hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()
    f.data.pop("net")
    f.save()
    g = NetworkControl(f.client, machine=f.machine, root=f._root)
    assert g.network_id() == bound
    assert g.data["net"] == bound  # stored on first load
    assert g.file.read()["net"] == bound


def test_an_old_legacy_control_file_stores_the_legacy_id(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    f.data.pop("net")
    f.save()
    g = NetworkControl(f.client, machine=f.machine, root=f._root)
    assert g.data["net"] == legacy == g.network_id()


def test_a_fresh_folder_with_no_control_data_is_not_pinned(tmp_path):
    f = machine(tmp_path, "f")
    assert "net" not in f.data
    f.found()  # the bound id is still chosen at founding
    assert f.network_id() == nh.network_id_for(ROOM, f.machine_hex)


# ---------------------------------------------------------------- MAX_PER_SIGNER


def test_one_signer_holds_at_most_max_per_signer_records(three):  # noqa: F811
    f, b, c = three
    net = f.network_id()
    spam = [nh.machine_confirmation(b.machine, net, nh._hex(SigningKey.generate())) for _ in range(MAX_PER_SIGNER + 8)]
    for chunk in (spam[:32], spam[32:]):
        f.learn(b.client.public_key, {"confirmations": chunk})
    mine = [r for r in f.data["confirmations"] if r["by"] == b.machine_hex]
    assert len(mine) == MAX_PER_SIGNER


def test_halt_file_read_rejects_an_odd_scope_itself(tmp_path):
    for scope in ("Machine", 5, [5], [], {"a": 1}):
        nh.HaltFile(tmp_path).write({"scopes": {"n": scope}, "networks": {}})
        with pytest.raises(nh.HaltError):
            nh.HaltFile(tmp_path).read()
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine", "m": ["/a"]}, "networks": {}})
    assert nh.HaltFile(tmp_path).read()["scopes"]["m"] == ["/a"]


def test_b3_the_tree_walk_reaches_grandchildren_and_spares_a_protected_group():
    install = nh.Install(prefix="/opt/this-install")
    agent = nh.Proc(100, 1, ["/opt/this-install/bin/kollab", "--detached"], {}, "/work")
    tree = {
        100: (1, 100, 100),
        200: (100, 900, 900),  # a tool shell
        600: (200, 600, 600),  # its child, in a group of its own
        410: (1, 900, 900),  # the issuing CLI, protected, in the same group as 200
        450: (1, 900, 900),  # a stranger in that group
    }
    got = {p.pid for p in nh.select_sweep(
        [agent], install=install, scopes=["machine"], agent_pids=[100], protect={410}, tree=tree,
    )}
    assert {100, 200, 600} <= got
    assert 410 not in got and 450 not in got


# ---------------------------------------------------------------- F7


def test_f7_an_opt_out_the_recount_ignores_is_refused_and_not_kept(three):  # noqa: F811
    f, b, c = three
    b.halt_act("confirm_machine", {"machine": c.machine_hex})  # C counts only because B confirmed it
    meet(f, b, c)
    assert len(f.view().counting) == 3
    with pytest.raises(nh.HaltError):
        b.halt_act("no_count")  # B stopping would drop C and leave one machine, so the count ignores it
    assert not b.data.get("opt_outs")
    meet(f, b, c)
    assert b.view().counts(b.machine_hex)


def _sweep(tree, protect=(), agents=(100,), procs=None):
    install = nh.Install(prefix="/opt/this-install")
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    return {p.pid for p in nh.select_sweep(
        procs or [agent], install=install, scopes=["machine"], agent_pids=list(agents), protect=set(protect), tree=tree,
    )}


def test_f7_a_tui_agent_never_takes_the_users_terminal_session(monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    # login shell 50 leads the session; vim 60 and the TUI agent 100 (own group) run in it
    tree = {50: (1, 50, 50), 60: (50, 60, 50), 100: (50, 100, 50), 110: (100, 100, 50)}
    got = _sweep(tree)
    assert got == {100, 110}  # not the login shell or vim


def test_f7_a_session_the_agent_leads_is_still_swept(monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    tree = {100: (1, 100, 100), 120: (1, 120, 100)}  # a daemon (setsid) and an orphan left in its session
    assert _sweep(tree) == {100, 120}


def test_f7_the_group_of_a_stopped_agent_is_swept():
    tree = {100: (1, 100, 100), 130: (1, 100, 700)}  # reparented, still in the agent's group
    assert 130 in _sweep(tree)


def test_f7_a_protected_parent_is_not_walked_through():
    tree = {100: (1, 100, 100), 400: (100, 400, 400), 410: (400, 410, 410)}
    got = _sweep(tree, protect={400})
    assert 400 not in got and 410 not in got  # the issuing CLI's own child is not the agent's


def test_f7_the_listener_gives_the_sweep_the_live_tree(tmp_path, monkeypatch):
    seen = {}

    async def fake_sweep(scopes, **kw):
        seen["tree"] = kw.get("tree")
        return []

    async def no_links(self, net):
        return None

    h = "ab" * 32
    nh.HaltFile(tmp_path).write({"scopes": {}, "stopped": [], "networks": {"net1": {"halts": {
        h: {"hash": h, "kind": "halt", "machine": "m" * 64, "issued_at": 1}}}}})
    monkeypatch.setattr(hr, "sweep", fake_sweep)
    monkeypatch.setattr(hr, "process_tree", lambda: {1: (0, 1, 1)})
    monkeypatch.setattr(hr, "_read_candidates", lambda: [])
    monkeypatch.setattr(hr.HaltListener, "open_links", no_links)
    listener = hr.HaltListener(tmp_path, agents=lambda: [], stop=None)
    reply = asyncio.run(listener.handle(
        {"op": "halt", "network": "net1", "scope": "machine", "folder": str(tmp_path), "protect": []}
    ))
    assert reply["ok"]
    assert seen["tree"] == {1: (0, 1, 1)}  # a live listener walks the real tree


def test_f7_the_anchor_carries_sixty_bits_of_the_digest():
    alphabet = nh._ANCHOR_ALPHABET
    seen_top = set()
    for i in range(300):
        net = hashlib.sha256(str(i).encode()).hexdigest()
        text = nh.join_anchor(net).replace("-", "")
        value = 0
        for ch in text:
            value = value * 32 + alphabet.index(ch)
        digest = hashlib.sha256(b"kollab-join-anchor-v1\n" + net.encode()).digest()
        assert value == int.from_bytes(digest[:8], "big") >> 4  # all 60 bits, from the front of the digest
        seen_top.add(alphabet.index(text[0]))
    assert max(seen_top) >= 16  # the first character uses its whole 5 bits


def test_f7_an_empty_folder_list_is_malformed_and_resume_local_clears_a_malformed_file(tmp_path):
    assert nh.in_scope(str(tmp_path), [[]])
    nh.HaltFile(tmp_path).write({"scopes": {"n": []}, "networks": {"n": {"halts": {}}}})
    with pytest.raises(nh.HaltError):
        hr.update_halt(lambda d: None, tmp_path)  # the old text could only say chmod
    assert "could not be read" in hr.clear_malformed_halt(tmp_path)
    assert (tmp_path / "private" / "halt.json.bad").exists()
    assert not hr.read_halt(tmp_path).get("scopes")
    assert hr.clear_malformed_halt(tmp_path) == ""  # a good file is left alone
    assert "chmod" not in hr.UNREADABLE_LINE and "resume --local" in hr.UNREADABLE_LINE


def test_f7_learn_evicts_picks_that_the_founder_did_not_sign(three):  # noqa: F811
    f, b, c = three
    stray = nh.admission(c.machine, c.network_id(), b.machine_hex, bootstrap=True)  # not the founder's
    b.data.setdefault("bootstrap_picks", []).append(stray)
    b.save()
    b.learn(f.client.public_key, f.wire())  # an ordinary gossip round
    assert all(p["admitter"] == f.machine_hex for p in b.data.get("bootstrap_picks", []))


def test_f7_once_a_founder_is_confirmed_a_candidates_pick_is_not_taken(tmp_path):
    f, legacy = _legacy_network(tmp_path)
    r = machine(tmp_path, "r", inviter="ee" * 32)
    m = machine(tmp_path, "m", inviter="ee" * 32)
    meet(f, r, m)
    m.learn(r.client.public_key, {"founder": nh.founder_record(r.machine, legacy)})
    m.halt_act("confirm_founder", {"founder": f.machine_hex})
    assert m.view().founder == f.machine_hex
    pick = nh.admission(r.machine, legacy, m.machine_hex, bootstrap=True)  # r was a candidate, f is the founder
    m.learn(r.client.public_key, {"bootstrap_picks": [pick]})
    assert all(p["admitter"] != r.machine_hex for p in m.data.get("bootstrap_picks", []))

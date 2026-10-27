"""Kill switch rules (docs/specs/network-sync-and-control.md section 1)."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from plugins.hub import network_halt as nh

ROOM = secrets.token_hex(32)


class M:
    """One machine (one HOME): a machine key and one or more folder keys."""

    def __init__(self, name: str):
        self.name = name
        self.key = SigningKey.generate()
        self.folders = [SigningKey.generate()]

    @property
    def mk(self) -> str:
        return bytes(self.key.verify_key).hex()

    def fk(self, i: int = 0) -> str:
        return bytes(self.folders[i].verify_key).hex()

    def add_folder(self) -> int:
        self.folders.append(SigningKey.generate())
        return len(self.folders) - 1


class Net:
    def __init__(self):
        self.founder = M("mac")
        self.id = nh.network_id_for(ROOM, self.founder.mk)
        self.roster = nh.Roster(
            network=self.id, founder=nh.founder_record(self.founder.key, self.id), room=ROOM
        )
        self.join(self.founder, admission=None)

    def join(self, m: M, admission=None, folder: int = 0) -> M:
        self.roster.members.append(
            nh.Member(
                m.fk(folder),
                m.name,
                m.mk,
                nh.sign_folder_machine(m.key, self.id, m.fk(folder)),
                admission,
                nh.sign_folder_binding(m.folders[folder], self.id, m.mk),
            )
        )
        return m

    def admit(self, admitter: M, name: str, *, bootstrap: bool = False) -> M:
        m = M(name)
        return self.join(m, nh.admission(admitter.key, self.id, m.mk, bootstrap=bootstrap))

    def confirm(self, by: M, m: M) -> None:
        self.roster.confirmations.append(nh.machine_confirmation(by.key, self.id, m.mk))

    def view(self) -> nh.Counting:
        return nh.count_machines(self.roster)

    def member(self, folder: str) -> bool:
        return folder in {x.folder for x in self.roster.members}


@pytest.fixture
def net():
    n = Net()
    n.prod = n.admit(n.founder, "prod", bootstrap=True)
    return n


# ----------------------------------------------------------- counting


def test_founder_and_bootstrap_count(net):
    view = net.view()
    assert view.counting == {net.founder.mk, net.prod.mk}
    assert nh.halt_allowed(view)


def test_one_machine_network_cannot_halt():
    n = Net()
    assert not nh.halt_allowed(n.view())


def test_forged_founder_record_is_refused(net):
    liar = M("liar")
    net.roster.founder = nh.founder_record(liar.key, net.id)
    assert net.view().founder == ""
    # existing networks: only the founder a person confirmed here
    net.roster.room = ""
    net.roster.founder = nh.founder_record(net.founder.key, net.id)
    assert net.view().founder == ""
    net.roster.founder_confirmed = net.founder.mk
    assert net.view().founder == net.founder.mk


def test_only_one_bootstrap_ever(net):
    net.admit(net.founder, "fake", bootstrap=True)
    assert net.view().counting == {net.founder.mk}


def test_second_home_admitted_by_founder_needs_the_other_machine(net):
    fake = net.admit(net.founder, "fake-home")
    net.confirm(net.founder, fake)  # founder is in fake's chain: does not count
    assert not net.view().counts(fake.mk)
    net.confirm(net.prod, fake)
    assert net.view().counts(fake.mk)


def test_second_home_admitted_by_bootstrap_needs_the_founder(net):
    fake = net.admit(net.prod, "fake-home")
    net.confirm(net.prod, fake)
    assert not net.view().counts(fake.mk)
    assert not nh.may_confirm_machine(net.view(), net.prod.mk, fake.mk)
    assert nh.may_confirm_machine(net.view(), net.founder.mk, fake.mk) is False  # founder is in chain too
    net.confirm(net.founder, fake)
    assert not net.view().counts(fake.mk)


def test_chained_admission_attack(net):
    """R5-1: counting C admits F1; F1 cannot admit; C cannot confirm F1's F2."""
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    d = net.admit(net.founder, "d")
    net.confirm(net.prod, d)
    view = net.view()
    assert view.counts(c.mk) and view.counts(d.mk)
    f1 = net.admit(c, "f1")
    assert not nh.may_admit(net.view(), f1.mk)
    # F1 forced in anyway (an old record): F1 admits F2.
    f2 = net.admit(f1, "f2")
    net.confirm(c, f2)
    assert not net.view().counts(f2.mk)
    assert not nh.may_confirm_machine(net.view(), c.mk, f2.mk)
    assert net.view().chains[f2.mk][:2] == [f1.mk, c.mk]
    net.confirm(prod := net.prod, f2)  # prod is outside f2's chain
    assert prod.mk not in net.view().chains[f2.mk]
    assert net.view().counts(f2.mk)


def test_chain_unknown_needs_two_outside_confirmations(net):
    old = net.join(M("old"))  # pre-release: no admission
    other = net.admit(net.founder, "other")
    net.confirm(net.prod, other)
    third = net.admit(net.prod, "third")
    net.confirm(net.founder, third)  # founder is in third's chain
    net.confirm(other, third)
    # old claims prod invited it; founder's record says founder decided it.
    net.roster.inviter_claims.append(nh.inviter_claim(old.key, net.id, net.prod.mk))
    net.roster.join_records.append(nh.join_record(net.founder.key, net.id, old.mk))
    view = net.view()
    assert old.mk in view.unknown
    net.confirm(other, old)
    assert not net.view().counts(old.mk)
    net.confirm(third, old)
    assert net.view().counts(old.mk)


def test_pre_release_chain_agreeing_ends_is_known(net):
    old = net.join(M("old"))
    net.roster.inviter_claims.append(nh.inviter_claim(old.key, net.id, net.founder.mk))
    net.roster.join_records.append(nh.join_record(net.founder.key, net.id, old.mk))
    view = net.view()
    assert old.mk not in view.unknown
    net.confirm(net.prod, old)
    assert net.view().counts(old.mk)


def test_unsigned_folder_binding_is_nobodys(net):
    thief = M("thief")
    net.roster.members.append(
        nh.Member(thief.fk(), "thief", net.prod.mk, nh.sign_folder_machine(thief.key, net.id, thief.fk()))
    )
    assert net.view().machine_of(thief.fk()) == ""


def test_revoked_and_opted_out_machines_stop_counting(net):
    net.roster.opt_outs.append(nh.no_count_record(net.prod.key, net.id))
    assert not net.view().counts(net.prod.mk)
    net.roster.opt_outs.clear()
    net.roster.revoked_machines.add(net.prod.mk)
    assert not net.view().counts(net.prod.mk)


# -------------------------------------------------------------- orders


def halt(m: M, net: Net, **kw):
    return nh.make_order(m.folders[0], m.key, net.id, "halt", **kw)


def resume(m: M, net: Net, lifts, **kw):
    return nh.make_order(m.folders[0], m.key, net.id, "resume", lifts=lifts, **kw)


def test_resume_needs_two_counting_machines(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net, reason="bad agent")
    assert book.take(h, net.view(), is_member=net.member).taken
    hh = nh.order_hash(h)
    r = resume(net.founder, net, [hh])
    rh = nh.order_hash(r)
    assert book.take(r, net.view(), is_member=net.member).lifted == []
    assert hh in book.active()
    result = book.confirm(rh, net.prod.folders[0], net.prod.key, net.view())
    assert result.lifted == [hh]
    assert not book.active()


def test_confirmation_from_second_folder_on_same_machine_lifts_nothing(net):
    i = net.founder.add_folder()
    net.join(net.founder, folder=i)
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    r = resume(net.founder, net, [nh.order_hash(h)])
    r["confirmations"] = [nh.resume_confirmation(net.founder.folders[i], net.founder.key, nh.order_hash(r))]
    assert book.take(r, net.view(), is_member=net.member).lifted == []
    with pytest.raises(nh.HaltError):
        book.confirm(nh.order_hash(r), net.founder.folders[i], net.founder.key, net.view())


def test_confirmation_from_non_counting_machine_lifts_nothing(net):
    side = net.admit(net.founder, "side")  # unconfirmed: does not count
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    r = resume(net.founder, net, [nh.order_hash(h)])
    book.take(r, net.view(), is_member=net.member)
    assert book.confirm(nh.order_hash(r), side.folders[0], side.key, net.view()).lifted == []
    good, bad = nh.resume_signers(book.proposals[nh.order_hash(r)], net.view())
    assert good == {net.founder.mk} and bad == {side.mk}


def test_replayed_halt_is_refused_after_resume(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    r = resume(net.founder, net, [nh.order_hash(h)])
    r["confirmations"] = [nh.resume_confirmation(net.prod.folders[0], net.prod.key, nh.order_hash(r))]
    assert book.take(r, net.view(), is_member=net.member).lifted
    assert book.take(h, net.view(), is_member=net.member).reason == "already seen"
    assert not book.active()


def test_tampered_or_stranger_orders_are_refused(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    h["reason"] = "edited"
    assert not book.take(h, net.view(), is_member=net.member).taken
    stranger = M("contact")
    s = halt(stranger, net)
    assert not book.take(s, net.view(), is_member=net.member).taken


def test_supersede_never_ends_a_halt_and_marks_parent_seen(net):
    book = nh.HaltBook(net.id)
    h1 = halt(net.prod, net)
    h2 = halt(net.prod, net, supersedes=nh.order_hash(h1))
    book.take(h2, net.view(), is_member=net.member)
    assert book.take(h1, net.view(), is_member=net.member).reason == "already seen"
    assert nh.order_hash(h2) in book.active()


def test_two_open_proposals_and_the_honest_one_takes_effect(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    junk = resume(c, net, [])  # lifts nothing
    honest = resume(net.founder, net, [nh.order_hash(h)])
    book.take(junk, net.view(), is_member=net.member)
    book.take(honest, net.view(), is_member=net.member)
    assert len(book.open_proposals()) == 2
    assert book.confirm(nh.order_hash(honest), net.prod.folders[0], net.prod.key, net.view()).lifted


def test_tie_keeps_the_smaller_proposal(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    a = resume(net.founder, net, [nh.order_hash(h)], reason="a")
    b = resume(net.founder, net, [nh.order_hash(h)], reason="b")
    book.take(a, net.view(), is_member=net.member)
    book.take(b, net.view(), is_member=net.member)
    assert list(book.open_proposals()) == [min(nh.order_hash(a), nh.order_hash(b))]


def test_revoked_proposer_is_dropped(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    r = resume(net.founder, net, [nh.order_hash(h)])
    book.take(r, net.view(), is_member=net.member)
    net.roster.revoked_machines.add(net.founder.mk)
    book.drop_revoked(net.view())
    assert not book.open_proposals()


def test_local_resume_and_the_halt_comes_back(net):
    mac, prod = nh.HaltBook(net.id), nh.HaltBook(net.id)
    h = halt(net.prod, net)
    for b in (mac, prod):
        b.take(h, net.view(), is_member=net.member)
    assert mac.resume_local() == [nh.order_hash(h)]
    assert not mac.active()
    mac.merge(prod.exchange_payload(), net.view(), is_member=net.member)
    assert nh.order_hash(h) in mac.active()


def test_deleted_halt_json_comes_back_at_next_exchange(net):
    prod = nh.HaltBook(net.id)
    prod.take(halt(net.prod, net), net.view(), is_member=net.member)
    fresh = nh.HaltBook(net.id)
    fresh.merge(prod.exchange_payload(), net.view(), is_member=net.member)
    assert fresh.active()


def test_lost_book_learns_resume_from_peer(net):
    h = halt(net.prod, net)
    r = resume(net.founder, net, [nh.order_hash(h)])
    r["confirmations"] = [nh.resume_confirmation(net.prod.folders[0], net.prod.key, nh.order_hash(r))]
    peer = nh.HaltBook(net.id)
    peer.take(h, net.view(), is_member=net.member)
    peer.take(r, net.view(), is_member=net.member)
    replayed = nh.HaltBook(net.id)
    replayed.take(h, net.view(), is_member=net.member)  # a replay before any member
    replayed.merge(peer.exchange_payload(), net.view(), is_member=net.member)
    assert not replayed.active()


def test_next_supersedes_learns_head_from_peers(net):
    h1 = halt(net.prod, net)
    peer = nh.HaltBook(net.id)
    peer.take(h1, net.view(), is_member=net.member)
    lost = nh.HaltBook(net.id)
    assert lost.next_supersedes(net.prod.fk(), peer.exchange_payload()["heads"].values()) == nh.order_hash(h1)


# --------------------------------------------------------- revocation


def rev(by: M, net: Net, key: str):
    return nh.revocation(by.folders[0], by.key, net.id, key)


def test_one_machine_revocation_does_not_spread(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    target = net.admit(net.founder, "target")
    records = [rev(net.prod, net, target.fk())]
    assert nh.revocations_to_apply(records, net.view()) == set()
    records.append(rev(c, net, target.fk()))
    assert target.fk() in nh.revocations_to_apply(records, net.view())


def test_two_folders_on_one_machine_are_one_revoker(net):
    i = net.prod.add_folder()
    net.join(net.prod, folder=i)
    target = net.admit(net.founder, "target")
    records = [rev(net.prod, net, target.fk()), nh.revocation(net.prod.folders[i], net.prod.key, net.id, target.fk())]
    assert nh.revocations_to_apply(records, net.view()) == set()


def test_revoking_a_machine_revokes_every_folder_key(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    target = net.admit(net.founder, "target")
    j = target.add_folder()
    net.join(target, folder=j)
    out = nh.revocations_to_apply([rev(net.prod, net, target.mk), rev(c, net, target.mk)], net.view())
    assert {target.fk(), target.fk(j)} <= out


def test_primary_is_never_revoked_by_peers(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    d = net.admit(net.founder, "d")
    net.confirm(net.prod, d)
    records = [rev(net.prod, net, net.founder.fk()), rev(c, net, net.founder.fk()), rev(d, net, net.founder.fk())]
    assert nh.revocations_to_apply(records, net.view(), protected={net.founder.fk()}) == set()
    assert net.founder.fk() in nh.revocations_to_apply(records, net.view())


def test_non_counting_signer_does_not_count(net):
    side = net.admit(net.founder, "side")
    target = net.admit(net.founder, "target")
    assert nh.revocations_to_apply([rev(net.prod, net, target.fk()), rev(side, net, target.fk())], net.view()) == set()


def test_revoke_that_leaves_one_counting_machine(net):
    assert not nh.revoke_leaves_quorum(net.view(), net.prod.fk())
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    assert nh.revoke_leaves_quorum(net.view(), net.prod.fk())


# --------------------------------------------------------------- scope


def test_scope_is_machine_wide_only_where_this_machine_counts(net, tmp_path):
    assert nh.halt_scope(net.view(), net.prod.mk, [str(tmp_path)]) == "machine"
    side = net.admit(net.founder, "side")
    assert nh.halt_scope(net.view(), side.mk, [str(tmp_path)]) == [str(tmp_path)]
    assert nh.in_scope(str(tmp_path / "sub"), [[str(tmp_path)]])
    assert not nh.in_scope("/elsewhere", [[str(tmp_path)]])
    assert nh.in_scope("/elsewhere", ["machine"])


# ------------------------------------------------------ accident guard


def test_accident_guard():
    assert nh.network_control_refusal({}, True) == ""
    assert nh.network_control_refusal({"KOLLAB_TOOL_SHELL": "1"}, True) == nh.GUARD_REFUSAL
    assert nh.network_control_refusal({}, False) == nh.GUARD_REFUSAL
    env = nh.mark_tool_env({})
    assert env["KOLLAB_TOOL_SHELL"] == "1" and env["KOLLAB_AGENT_PID"] == str(os.getpid())
    assert nh.strip_tool_env(env) == {}


# ------------------------------------------------------------ listener


@pytest.mark.parametrize(
    "stranger,member,method,inner,ok",
    [
        (False, True, "secure_identity", None, True),
        (False, True, "secure_packet", "network_halt", True),
        (False, True, "secure_packet", "network_members", True),
        (False, True, "secure_packet", "message", False),
        (False, True, "secure_packet", "config_sync", False),
        (False, True, "peer.exchange", None, False),
        (True, False, "secure_packet", "network_halt", False),
        (False, False, "secure_identity", None, False),
    ],
)
def test_listener_allowlist(stranger, member, method, inner, ok):
    assert nh.listener_allows(stranger=stranger, member=member, method=method, inner=inner) is ok


# --------------------------------------------------------------- sweep


@pytest.fixture
def install(tmp_path):
    prefix = tmp_path / "venv"
    (prefix / "bin").mkdir(parents=True)
    for name in ("python", "kollab"):
        (prefix / "bin" / name).write_text("")
    return nh.Install(prefix=str(prefix), scie="/opt/kollab-bin")


def proc(pid, argv, cwd="/work/a", env=None, ppid=1, exe=""):
    return nh.Proc(pid, ppid, argv, env or {}, cwd, exe)


def test_sweep_matches_binary_venv_and_console_script(install, tmp_path):
    py = f"{install.prefix}/bin/python"
    procs = [
        proc(10, ["/x/python", "--detached"], env={"SCIE": "/opt/kollab-bin"}),
        proc(11, [py, "-m", "kollabor_cli_main", "-p", "hi"]),
        proc(12, [py, "-I", f"{install.prefix}/bin/kollab", "--agent", "lapis"]),
        proc(13, [py, "-m", "pytest"]),  # other python in the same venv
        proc(14, ["/usr/bin/python3", "-m", "kollabor_cli_main"]),  # another install
        proc(15, ["/x/python"], env={"SCIE": "/opt/other-bin"}),  # another binary
    ]
    got = {p.pid for p in nh.select_sweep(procs, install=install, scopes=[["/work/a"]])}
    assert got == {10, 11, 12}


def test_sweep_exclusions(install):
    py = f"{install.prefix}/bin/python"
    procs = [
        proc(20, [py, "-m", "kollabor_cli_main", "--web-ui"]),
        proc(21, [py, "-m", "kollabor_cli_main", "--halt-listener"]),
        proc(22, [py, "-m", "kollabor_cli_main"], env={nh.WAITER_ENV: "1"}),
        proc(23, [py, "-m", "kollabor_cli_main", "--hub", "status"]),
        proc(24, [py, "-m", "kollabor_cli_main", "--hub", "halt"]),
        proc(25, [py, "-m", "kollabor_cli_main", "--version"]),
        proc(26, [py, "-m", "kollabor_cli_main", "--hub", "stop", "all"]),
        proc(27, [py, "-m", "kollabor_cli_main"]),  # protected: the caller
    ]
    got = {p.pid for p in nh.select_sweep(procs, install=install, scopes=["machine"], protect={27})}
    assert got == {26}


def test_folder_scope_versus_machine_wide(install):
    py = f"{install.prefix}/bin/python"
    second_home = proc(30, [py, "-m", "kollabor_cli_main"], cwd="/tmp/fake-home", env={"HOME": "/tmp/fake-home"})
    inside = proc(31, [py, "-m", "kollabor_cli_main"], cwd="/work/a/sub")
    assert {p.pid for p in nh.select_sweep([second_home, inside], install=install, scopes=[["/work/a"]])} == {31}
    assert {p.pid for p in nh.select_sweep([second_home, inside], install=install, scopes=["machine"])} == {30, 31}


def test_sweep_takes_agent_children_but_not_tmux(install):
    py = f"{install.prefix}/bin/python"
    agent = proc(40, [py, "-m", "kollabor_cli_main"], ppid=1)
    child = proc(41, ["codex"], env={"KOLLAB_AGENT_PID": "40"}, ppid=40)
    grandchild = proc(42, ["node"], env={"KOLLAB_AGENT_PID": "40"}, ppid=41)
    orphan = proc(43, ["claude"], env={"KOLLAB_AGENT_PID": "40"}, ppid=1)
    tmux = proc(44, ["tmux", "new"], env={"KOLLAB_AGENT_PID": "40"}, ppid=1)
    stranger = proc(45, ["vim"], env={"KOLLAB_AGENT_PID": "999"}, ppid=1)
    got = nh.select_sweep([agent, child, grandchild, orphan, tmux, stranger], install=install, scopes=[["/work/a"]])
    assert {p.pid for p in got} == {40, 41, 42, 43}


def test_presence_agents_are_swept_even_without_argv_match(install):
    agent = proc(50, ["kollab-renamed"])
    assert [p.pid for p in nh.select_sweep([agent], install=install, scopes=[[]], agent_pids={50})] == [50]


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="needs process introspection")
def test_read_process_reads_this_process():
    me = nh.read_process(os.getpid())
    assert me is not None
    assert me.argv and me.env.get("PATH") == os.environ.get("PATH")
    assert Path(me.cwd).resolve() == Path.cwd().resolve()
    assert me.ppid == os.getppid()


# ------------------------------------------------------------ halt.json


def test_halt_file_is_private_and_never_through_a_link(tmp_path):
    f = nh.HaltFile(tmp_path)
    assert f.read() == {"networks": {}, "stopped": []}
    f.write({"networks": {"x": {}}, "stopped": []})
    assert oct(f.path.stat().st_mode & 0o777) == "0o600"
    assert f.read()["networks"] == {"x": {}}
    f.path.chmod(0o644)
    with pytest.raises(nh.HaltError):
        f.read()
    f.path.unlink()
    (tmp_path / "elsewhere.json").write_text("{}")
    f.path.symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(OSError):
        f.read()


def test_primary_machine_key_revocation_spares_its_other_folders(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    i = net.founder.add_folder()
    net.join(net.founder, folder=i)
    records = [rev(net.prod, net, net.founder.mk), rev(c, net, net.founder.mk)]
    assert nh.revocations_to_apply(records, net.view(), protected={net.founder.fk()}) == set()


def test_confirmation_naming_an_unbound_folder_does_not_count(net):
    book = nh.HaltBook(net.id)
    h = halt(net.prod, net)
    book.take(h, net.view(), is_member=net.member)
    r = resume(net.founder, net, [nh.order_hash(h)])
    loose = SigningKey.generate()
    r["confirmations"] = [nh.resume_confirmation(loose, net.prod.key, nh.order_hash(r))]
    assert book.take(r, net.view(), is_member=net.member).lifted == []


def test_revoked_or_unapproved_issuer_is_refused(net):
    book = nh.HaltBook(net.id)
    assert book.take(halt(net.prod, net), net.view(), is_member=lambda f: False).reason.startswith("issuer is not")
    net.roster.revoked_machines.add(net.prod.mk)
    assert book.take(halt(net.prod, net), net.view(), is_member=net.member).reason == "issuer is revoked"


def test_a_halts_revoke_entry_counts_as_its_issuers_revocation(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    target = net.admit(net.founder, "target")
    order = halt(net.prod, net, revoke=[target.fk()])
    assert nh.revocations_to_apply([], net.view(), orders=[order]) == set()  # one machine
    both = nh.revocations_to_apply([rev(c, net, target.fk())], net.view(), orders=[order])
    assert target.fk() in both
    forged = {**order, "revoke": [target.fk(), c.fk()]}  # not what prod signed
    assert nh.revocations_to_apply([rev(c, net, target.fk())], net.view(), orders=[forged]) == set()
    as_resume = resume(net.prod, net, [], revoke=[target.fk()])
    assert nh.revocations_to_apply([rev(c, net, target.fk())], net.view(), orders=[as_resume]) == set()


def test_non_hex_room_binds_no_founder(net):
    net.roster.room = "not-hex"
    assert net.view().founder == ""


# ------------------------------------------------ review fixes F1 (B1, S3, M3)


def _members_only(n: Net) -> None:
    n.roster.member_folders = {x.folder for x in n.roster.members}


def test_an_admitter_from_outside_the_network_makes_no_counting_machine(net):
    """B1: F signs its own folder binding, a non-member Z admits it, prod confirms."""
    z, f = M("z"), M("f")
    net.join(f, nh.admission(z.key, net.id, f.mk))  # Z has no row, is not a member
    net.confirm(net.prod, f)
    _members_only(net)
    view = net.view()
    assert f.mk in view.unknown and f.mk not in view.counting
    net.confirm(net.founder, f)  # two outside confirmations still count it
    assert net.view().counts(f.mk)


def test_a_machine_whose_folder_is_not_a_relay_member_does_not_count(net):
    f = net.admit(net.founder, "f")
    net.confirm(net.prod, f)
    _members_only(net)
    assert net.view().counts(f.mk)
    net.roster.member_folders.discard(f.fk())
    view = net.view()
    assert not view.counts(f.mk) and view.machine_of(f.fk()) == ""
    order = nh.make_order(f.folders[0], f.key, net.id, "resume", lifts=["ab" * 32])
    assert nh.resume_signers(order, view) == (set(), {f.mk})


def test_a_chain_that_does_not_end_at_the_founder_needs_two_confirmations(net):
    a, b = M("a"), M("b")
    net.join(a, nh.admission(b.key, net.id, a.mk))
    net.join(b, nh.admission(a.key, net.id, b.mk))  # a cycle that never reaches the founder
    net.confirm(net.prod, a)
    _members_only(net)
    view = net.view()
    assert a.mk in view.unknown and not view.counts(a.mk)


def test_the_chained_admission_still_counts(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    d = net.admit(c, "d")
    net.confirm(net.prod, d)
    _members_only(net)
    view = net.view()
    assert view.counts(d.mk) and d.mk not in view.unknown and view.chains[d.mk] == [c.mk, net.founder.mk]


def test_a_halt_is_refused_where_fewer_than_two_machines_count():
    n = Net()  # the founder alone
    book = nh.HaltBook(n.id)
    order = nh.make_order(n.founder.folders[0], n.founder.key, n.id, "halt")
    res = book.take(order, n.view(), is_member=n.member)
    assert not res.taken and "two" in res.reason and not book.halts


def test_an_opted_out_machine_confirms_nobody(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    net.roster.opt_outs.append(nh.no_count_record(net.prod.key, net.id))
    assert not net.view().counts(c.mk)  # prod's lone confirmation no longer counts
    net.roster.opt_outs.clear()
    assert net.view().counts(c.mk)


def test_a_forged_supersedes_cannot_suppress_another_issuers_halt(net):
    """R2-3: M names X (the founder's halt) as its parent; X must still be taken."""
    x = halt(net.founder, net)
    xh = nh.order_hash(x)
    forged = halt(net.prod, net, supersedes=xh)
    book = nh.HaltBook(net.id)
    assert book.take(forged, net.view(), is_member=net.member).taken
    assert xh not in book.seen
    assert book.take(x, net.view(), is_member=net.member).taken and xh in book.halts
    own = halt(net.prod, net)
    own2 = halt(net.prod, net, supersedes=nh.order_hash(own), reason="2")
    b2 = nh.HaltBook(net.id)
    b2.take(own, net.view(), is_member=net.member)
    b2.take(own2, net.view(), is_member=net.member)
    assert b2.take(own, net.view(), is_member=net.member).reason == "already seen"
    b3 = nh.HaltBook(net.id)  # the claim made first still bites that issuer's own parent
    b3.take(own2, net.view(), is_member=net.member)
    assert b3.take(own, net.view(), is_member=net.member).reason == "already seen"


def test_peer_heads_naming_another_issuers_order_are_ignored(net):
    x = halt(net.founder, net)
    book = nh.HaltBook(net.id)
    book.take(x, net.view(), is_member=net.member)
    assert book.next_supersedes(net.prod.fk(), [nh.order_hash(x)]) is None


def test_an_opted_out_machine_stays_out_even_with_a_valid_confirmation(net):
    c = net.admit(net.founder, "c")
    net.confirm(net.prod, c)
    net.roster.opt_outs.append(nh.no_count_record(c.key, net.id))
    assert not net.view().counts(c.mk)


def test_a_binding_the_folder_key_did_not_sign_is_ignored(net):
    thief = M("thief")
    row = nh.Member(
        net.prod.fk(), "x", thief.mk, nh.sign_folder_machine(thief.key, net.id, net.prod.fk()), None,
        nh.sign_folder_binding(thief.folders[0], net.id, thief.mk),  # signed by the wrong folder key
    )
    net.roster.members = [x for x in net.roster.members if x.folder != net.prod.fk()] + [row]
    assert net.view().machine_of(net.prod.fk()) == ""

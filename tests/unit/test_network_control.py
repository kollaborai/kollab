"""Kill switch wiring (slice A): network_control.py over the network_halt rules.

Each "machine" here has its own machine key and its own ~/.kollab root under
tmp_path, so counting sees separate machines. Nothing touches the real HOME.
"""

from __future__ import annotations

import hashlib
import os
import stat
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from plugins.hub import network_halt as nh
from plugins.hub.network_control import (
    ADMIT_REFUSAL,
    HALT_METHOD,
    NETWORK_MISMATCH_LINE,
    ONE_COUNTING_LINE,
    HaltSync,
    JoinRefused,
    NetworkControl,
)
from plugins.hub.network_members import open_members, seal_members
from plugins.hub.relay_state import RelayError

ROOM = "ab" * 32


def _hex(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


class FakeClient:
    """The slice of RelayClient that NetworkControl reads."""

    def __init__(self, tmp_path, name: str, inviter: str = ""):
        self.folder = SigningKey.generate()
        self.public_key = _hex(self.folder)
        self._store = SimpleNamespace(key=self.folder)
        self.state_dir = tmp_path / name / "relay"
        self.state = SimpleNamespace(
            room=ROOM, inviter=inviter, approvals=[], revoked=[], links=[], device_name=name
        )
        if inviter:
            self.state.approvals.append(inviter)

    def members(self):
        return [k for k in self.state.approvals if k not in self.state.links]

    def revoke(self, key, *, announce=False):
        if key in self.state.approvals:
            self.state.approvals.remove(key)
        if announce and key not in self.state.revoked:
            self.state.revoked.append(key)


def machine(tmp_path, name, inviter=""):
    client = FakeClient(tmp_path, name, inviter)
    control = NetworkControl(client, machine=SigningKey.generate(), root=tmp_path / name / "kollab")
    client.control = control
    return control


def meet(*controls):
    """Every pair approves the other and trades control blocks twice (rows, then records)."""
    for a in controls:
        for b in controls:
            if a is not b and b.client.public_key not in a.client.state.approvals:
                a.client.state.approvals.append(b.client.public_key)
    for _ in range(2):
        for a in controls:
            for b in controls:
                if a is not b:
                    b.learn(a.client.public_key, a.wire())


def decide(issuer, joiner, offer="offer", round_id="round", proof=None):
    """The issuer's decision fields for a joiner that proves its keys (or sends ``proof``)."""
    proof = proof if proof is not None else joiner.join_proof(offer, round_id)
    return issuer.join_decision_fields(
        proof, offer_id=offer, round_id=round_id, folder=joiner.client.public_key
    )["network_control"]


def join(issuer, joiner):
    joiner.client.state.inviter = issuer.client.public_key
    fields = decide(issuer, joiner)
    joiner.take_join(fields, anchor=nh.join_anchor(issuer.network_id()))
    return fields


@pytest.fixture
def three(tmp_path):
    """A new network: founder F, bootstrap B, and C that F admitted after."""
    f = machine(tmp_path, "f")
    assert f.admit_refusal() == ""  # alone: F founds the network here
    b = machine(tmp_path, "b")
    c = machine(tmp_path, "c")
    first = join(f, b)
    second = join(f, c)
    assert first["admission"]["bootstrap"] is False and second["admission"]["bootstrap"] is False
    meet(f, b, c)
    f.halt_act("make_bootstrap", {"machine": b.machine_hex})  # a person on F picks it
    meet(f, b, c)
    return f, b, c


def test_a_new_network_binds_its_founder_into_the_id(tmp_path):
    f = machine(tmp_path, "f")
    assert f.network_id() == hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()  # before founding
    f.found()
    assert f.network_id() == nh.network_id_for(ROOM, f.machine_hex)
    b = machine(tmp_path, "b")
    join(f, b)
    assert b.network_id() == f.network_id()
    assert b.data["admission"]["machine"] == b.machine_hex


def test_founder_and_bootstrap_count_and_a_third_needs_a_confirmation(three):
    f, b, c = three
    view = f.view()
    assert view.counting == {f.machine_hex, b.machine_hex}
    assert nh.halt_allowed(view)
    assert c.admit_refusal() == ADMIT_REFUSAL.format(network="this network", machine="f")
    with pytest.raises(nh.HaltError):
        f.halt_act("confirm_machine", {"machine": c.machine_hex})  # F is in C's chain
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    meet(f, b, c)
    assert c.view().counts(c.machine_hex) and c.admit_refusal() == ""


def test_a_forged_admission_from_a_joiner_is_not_kept(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    fields = decide(f, b)
    fields["admission"] = {**fields["admission"], "machine": "cd" * 32}
    b.client.state.inviter = f.client.public_key
    b.take_join(fields, anchor=nh.join_anchor(f.network_id()))
    assert "admission" not in b.data


def test_an_existing_network_keeps_its_id_and_confirms_the_founder(tmp_path):
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)  # members from before the upgrade
    f.found()
    legacy = hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()
    assert f.network_id() == legacy and f.view().counts(f.machine_hex)
    m = machine(tmp_path, "m", inviter="ee" * 32)  # joined through someone else
    meet(f, m)
    assert m.view().founder == ""
    m.halt_act("confirm_founder")
    assert m.view().founder == f.machine_hex
    direct = machine(tmp_path, "d", inviter=f.client.public_key)  # joined through the founder
    meet(f, direct)
    assert direct.view().founder == ""  # F4-2: even the founder's own invitee confirms by hand
    with pytest.raises(nh.HaltError):
        m.halt_act("make_bootstrap", {"machine": direct.machine_hex})  # only the founder picks
    f.halt_act("make_bootstrap", {"machine": m.machine_hex})
    meet(f, m)
    assert m.view().counting == {f.machine_hex, m.machine_hex}
    with pytest.raises(nh.HaltError):
        f.halt_act("make_bootstrap", {"machine": direct.machine_hex})  # once


def test_revocations_apply_on_two_counting_machines_and_spare_the_inviter(three, tmp_path, monkeypatch):
    # F15a: no settle window — the second counting signature applies at once
    f, b, c = three
    d = machine(tmp_path, "d")
    join(f, d)
    meet(f, b, c, d)
    f.sign_revocation(d.client.public_key)
    meet(f, b, c, d)
    assert c.revocations_to_apply() == set()  # one machine's word
    b.sign_revocation(d.client.public_key)
    meet(f, b, c, d)
    assert d.client.public_key in c.revocations_to_apply()
    # F is C's inviter: never revoked on peers' word, two machines or not.
    b.sign_revocation(f.client.public_key)
    c.sign_revocation(f.client.public_key)
    meet(b, c)
    assert f.client.public_key not in c.revocations_to_apply()


def test_a_revoke_that_breaks_quorum_needs_revoke_here_only(three):
    f, b, c = three
    with pytest.raises(RelayError, match="Revoke here only: b stays a member"):
        f.check_revoke(b.client.public_key, here_only=False)
    assert f.check_revoke(b.client.public_key, here_only=True) is False
    assert f.check_revoke(c.client.public_key, here_only=False) is True  # C does not count
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    meet(f, b, c)
    assert f.check_revoke(b.client.public_key, here_only=False) is True  # F and C remain


def test_a_halt_that_revokes_asks_for_revoke_here_only_like_connect_revoke(three):
    # found in Docker: `halt --revoke <founder>` severed a secondary's link with no question
    f, b, c = three
    with pytest.raises(RelayError, match="Revoke here only"):
        b.halt_act("halt", {"reason": "x", "revoke": [f.client.public_key]})
    assert f.client.public_key in b.client.state.approvals and not b.halt_book().active()
    b.halt_act("halt", {"reason": "x", "revoke": [f.client.public_key], "revoke_here_only": True})
    assert f.client.public_key not in b.client.state.approvals
    assert not b.client.state.revoked  # local only: nothing is announced network-wide


def test_a_device_that_joined_never_founds(tmp_path):
    m = machine(tmp_path, "m", inviter="ee" * 32)
    m.found()
    assert "founder" not in m.data


def test_revoking_a_machine_that_does_not_count_never_asks(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    m = machine(tmp_path, "m", inviter=f.client.public_key)
    meet(f, m)
    assert f.check_revoke(m.client.public_key, here_only=False) is True


def test_one_counting_machine_cannot_halt(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    with pytest.raises(nh.HaltError, match="one machine that can confirm a resume"):
        f.halt_act("halt", {"reason": "x"})


class Link:
    """In-process ``network_halt`` transport between HaltSyncs."""

    def __init__(self):
        self.syncs: dict[str, HaltSync] = {}
        self.down = False

    def for_(self, control):
        link = self

        class Transport:
            async def request(self, key, method, payload, *, timeout):
                assert method == HALT_METHOD
                if link.down:
                    raise RelayError("offline")
                return await link.syncs[key].receive(control.client.public_key, payload)

        return Transport()


def syncs(*controls):
    link = Link()
    for c in controls:
        link.syncs[c.client.public_key] = HaltSync(
            client=c.client,
            transport=link.for_(c),
            online=lambda c=c: {k: "s1" for k in c.client.members()},
        )
    return link


@pytest.mark.asyncio
async def test_a_halt_reaches_a_member_and_needs_two_machines_to_lift(three):
    f, b, c = three
    swept = []
    b.on_halt_change = swept.append
    link = syncs(f, b, c)
    order = f.halt_act("halt", {"reason": "stop"})
    await link.syncs[f.client.public_key].tick()
    assert list(b.halt_book().active()) == [order["hash"]]
    assert swept == [b.network_id()]
    proposal = b.halt_act("propose_resume")
    await link.syncs[b.client.public_key].tick()
    assert f.halt_book().active()  # a proposal lifts nothing
    with pytest.raises(nh.HaltError):
        b.halt_act("confirm_resume", {"hash": proposal["hash"][:8]})  # not on the proposer
    lifted = f.halt_act("confirm_resume", {"hash": proposal["hash"][:8]})["lifted"]
    assert lifted == [order["hash"]]
    await link.syncs[f.client.public_key].tick()
    assert not b.halt_book().active() and swept == [b.network_id()] * 2


@pytest.mark.asyncio
async def test_a_locally_lifted_halt_comes_back_at_the_next_exchange(three):
    f, b, c = three
    link = syncs(f, b, c)
    f.halt_act("halt", {"reason": "stop"})
    await link.syncs[f.client.public_key].tick()
    assert b.halt_act("resume_local")["line"] == nh.LOCAL_RESUME_LINE
    assert not b.halt_book().active()
    await link.syncs[b.client.public_key].tick()  # B's reply came from F, who holds it
    assert b.halt_book().active()


@pytest.mark.asyncio
async def test_an_unacked_member_is_retried_10s_doubling_to_5_minutes(three):
    f, b, c = three
    link = syncs(f, b, c)
    sync = link.syncs[f.client.public_key]
    assert [sync.retry_delay(n) for n in (1, 2, 3, 6, 9)] == [10, 20, 40, 300, 300]
    link.down = True
    await sync.tick()
    assert sync._failures == {b.client.public_key: 1, c.client.public_key: 1}
    link.down = False
    await sync.tick()  # still inside the 10 s wait
    assert sync._failures and not sync._delivered
    sync._retry_at.clear()
    await sync.tick()
    assert set(sync._delivered) == {b.client.public_key, c.client.public_key}


@pytest.mark.asyncio
async def test_a_non_member_gets_no_halt_book(three):
    f, _b, _c = three
    sync = HaltSync(client=f.client, transport=None, online=dict)
    with pytest.raises(RelayError, match="not part of this network"):
        await sync.receive("cd" * 32, {"halts": []})
    with pytest.raises(RelayError):
        f.halt_merge("not a payload")


def test_no_count_is_refused_while_a_halt_is_active(three):
    f, b, c = three
    b.halt_act("confirm_machine", {"machine": c.machine_hex})  # a third machine, so an opt-out still leaves two (F6)
    meet(f, b, c)
    c.halt_act("no_count")
    meet(f, b, c)
    assert not f.view().counts(c.machine_hex)
    c.halt_act("count")
    meet(f, b, c)
    assert f.view().counts(c.machine_hex) and b.view().counts(c.machine_hex)
    f.halt_act("halt", {"reason": "x"})
    with pytest.raises(nh.HaltError, match="while a halt is active"):
        f.halt_act("no_count")


def test_control_files_are_private_and_hold_no_private_key(three):
    f, _b, _c = three
    f.halt_act("halt", {"reason": "x"})
    for path in (f.file.path, f._halt_file().path):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        text = path.read_text()
        assert bytes(f.machine).hex() not in text and bytes(f.client.folder).hex() not in text


def test_a_v3_member_list_carries_the_control_block(three):
    f, b, _c = three
    payload = seal_members(
        key=f.client.folder, to=b.client.public_key, net=f.network_id(),
        members=[], revoked=[], issued_at=5, control=f.wire(),
    )
    assert payload["v"] == 3
    _m, _r, issued, control = open_members(
        payload, peer=f.client.public_key, own_key=b.client.public_key, net=f.network_id()
    )
    assert issued == 5 and control == f.wire()
    bad = seal_members(
        key=f.client.folder, to=b.client.public_key, net=f.network_id(),
        members=[], revoked=[], issued_at=5, control=[],  # signed, but not a block
    )
    with pytest.raises(RelayError):
        open_members(bad, peer=f.client.public_key, own_key=b.client.public_key, net=f.network_id())


def test_the_digest_changes_with_a_halt(three):
    f, _b, _c = three
    before = f.digest()
    f.halt_act("halt", {"reason": "x"})
    assert f.digest() != before


@pytest.mark.asyncio
async def test_another_counting_machine_seen_since_the_halt_is_remembered(three):
    """`Resume Here Only` hides once a second counting machine was online after the halt."""
    f, b, c = three
    assert f.halt_snapshot(set())["others_online_since_halt"] is False  # no halt
    f.halt_act("halt", {"reason": "stop"})
    assert f.halt_snapshot(set())["others_online_since_halt"] is False
    assert f.halt_snapshot({c.client.public_key})["others_online_since_halt"] is False  # c does not count
    assert f.halt_snapshot({f.client.public_key})["others_online_since_halt"] is False  # itself
    assert f.halt_snapshot({b.client.public_key})["others_online_since_halt"] is True
    assert f.halt_snapshot(set())["others_online_since_halt"] is True  # b went away: still seen
    # The sync loop records it too, with no panel open.
    link = syncs(f, b)
    await link.syncs[f.client.public_key].tick()  # b gets the halt
    await link.syncs[b.client.public_key].tick()  # b sees f online since it
    assert b.halt_snapshot(set())["others_online_since_halt"] is True


# ------------------------------------------------ review fixes F1


def _row(folder_key: SigningKey, machine_key: SigningKey, net: str, name: str, admission=None, *, bind_with=None):
    mk = _hex(machine_key)
    fk = _hex(folder_key)
    return {
        "folder": fk,
        "name": name,
        "machine": mk,
        "folder_sig": nh.sign_folder_machine(machine_key, net, fk),
        "bind_sig": nh.sign_folder_binding(bind_with or folder_key, net, mk),
        "admission": admission,
    }


def test_a_row_for_a_non_member_folder_cannot_count_even_if_a_counting_machine_confirms_it(three):
    """B1: b invents F, lists it (admitted by a stranger Z), confirms it. F is no relay member."""
    f, b, _c = three
    net = f.network_id()
    fake_folder, fake_machine, z = SigningKey.generate(), SigningKey.generate(), SigningKey.generate()
    row = _row(fake_folder, fake_machine, net, "fake", nh.admission(z, net, _hex(fake_machine)))
    conf = nh.machine_confirmation(b.machine, net, _hex(fake_machine))
    f.learn(b.client.public_key, {**b.wire(), "members": [*b.wire()["members"], row], "confirmations": [conf]})
    view = f.view()
    assert not view.counts(_hex(fake_machine)) and view.machine_of(_hex(fake_folder)) == ""
    f.client.state.approvals.append(_hex(fake_folder))  # even a member: Z never reaches the founder
    assert not f.view().counts(_hex(fake_machine))


def test_a_member_cannot_make_itself_founder_or_move_the_network_id(three, tmp_path):
    """B2 on a pre-release network: the first founder record to arrive must not win."""
    f, _b, _c = three
    legacy = hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()
    v = machine(tmp_path, "v", inviter="ee" * 32)
    x = machine(tmp_path, "x")
    x.found()  # x signs a founder record bound to itself
    assert x.network_id() != legacy
    v.learn(x.client.public_key, x.wire())
    assert v.network_id() == legacy and not v.data.get("founder") and v.view().founder == ""
    meet(f, v)  # the real founder's (bound) record changes nothing either
    assert v.network_id() == legacy
    # and on a network that already has its founder, the id and founder stay put
    original = f.network_id()
    f.learn(x.client.public_key, x.wire())
    assert f.network_id() == original and f.view().founder == f.machine_hex


def test_a_founder_claim_and_a_bootstrap_pick_cannot_lock_out_the_real_ones(tmp_path):
    """S4: a lying candidate first; the person names the right fingerprint; the real pick still counts."""
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)
    f.found()
    legacy = f.network_id()
    m = machine(tmp_path, "m", inviter="ee" * 32)
    liar = machine(tmp_path, "liar")
    liar.data["founder"] = nh.founder_record(liar.machine, legacy)
    meet(f, m, liar)  # the liar is a member machine too, so its claim is a real candidate
    assert m.view().founder == ""  # two candidates: nothing counts until a person names one
    with pytest.raises(nh.HaltError, match="name the founder"):
        m.halt_act("confirm_founder")
    m.halt_act("confirm_founder", {"founder": f.machine_hex})
    assert m.view().founder == f.machine_hex
    fake = nh.admission(liar.machine, legacy, m.machine_hex, bootstrap=True)
    m.learn(liar.client.public_key, {"bootstrap_picks": [fake]})  # the lying pick arrives first
    f.halt_act("make_bootstrap", {"machine": m.machine_hex})
    meet(f, m)
    assert m.view().counting == {f.machine_hex, m.machine_hex}


def test_a_member_cannot_rebind_another_members_folder_to_its_own_machine(three):
    """R2-2: the machine signs any folder key; the folder key's own consent is what binds it."""
    f, b, c = three
    net = f.network_id()
    row = _row(b.client._store.key, c.machine, net, "b", bind_with=c.client._store.key)
    f.learn(c.client.public_key, {"members": [row]})
    assert f.view().machine_of(b.client.public_key) == b.machine_hex and f.view().counts(b.machine_hex)


def test_junk_records_do_not_evict_real_ones_and_forty_honest_confirmations_all_stay(three):
    """R2-4: unsigned junk is dropped before storage; honest records are kept per signer, not by arrival."""
    f, b, _c = three
    net = f.network_id()
    real = nh.machine_confirmation(b.machine, net, "ab" * 32)
    f.learn(b.client.public_key, {"confirmations": [real]})
    junk = [{"sig": f"{i:0128x}", "machine": "cd" * 32, "by": "ef" * 32, "network": net} for i in range(100)]
    for chunk in range(0, 100, 20):
        f.learn(b.client.public_key, {"confirmations": junk[chunk : chunk + 20]})
    assert f.data["confirmations"] == [real]
    signers = [f.machine, b.machine, _c.machine]
    honest = [nh.machine_confirmation(k, net, _hex(SigningKey.generate())) for k in signers for _ in range(13)]
    for chunk in range(0, 39, 20):
        f.learn(b.client.public_key, {"confirmations": honest[chunk : chunk + 20]})
    assert len(f.data["confirmations"]) == 40
    assert real in f.data["confirmations"]


def test_no_count_after_a_local_lift_is_still_refused(three):
    """M2: `resume --local` must not open the opt-out while the halt is held network-wide."""
    f, b, c = three
    f.halt_act("halt", {"reason": "x"})
    meet(f, b, c)
    b.halt_merge(f.halt_payload())
    b.halt_act("resume_local")
    assert not b.halt_book().active() and b.halt_book().held()
    with pytest.raises(nh.HaltError, match="while a halt is active"):
        b.halt_act("no_count")


def test_a_garbage_machine_key_or_a_replayed_proof_gets_no_admission(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    garbage = {"machine": "cd" * 32, "machine_sig": "00" * 64, "bind_sig": "00" * 64}
    assert "admission" not in decide(f, b, proof=garbage)
    real = b.join_proof("offer", "round-1")
    assert "admission" in decide(f, b, round_id="round-1", proof=real)
    assert "admission" not in decide(f, b, round_id="round-2", proof=real)  # another round
    assert "admission" not in decide(f, b, round_id="round-1", proof={**real, "bind_sig": "00" * 64})
    other = b.join_proof("offer", "round")
    assert "admission" not in decide(f, b, proof={**other, "machine": _hex(SigningKey.generate())})
    assert "admission" not in decide(f, b, proof={k: v for k, v in other.items() if k != "machine_sig"})


def test_a_join_never_picks_the_bootstrap_machine_by_itself(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    assert join(f, b)["admission"]["bootstrap"] is False
    meet(f, b)
    assert f.view().counting == {f.machine_hex}
    assert f.halt_snapshot()["bootstrap_open"] is True
    with pytest.raises(nh.HaltError):
        f.halt_act("make_bootstrap", {"machine": "cd" * 32})  # not a member machine
    f.halt_act("make_bootstrap", {"machine": b.machine_hex})
    meet(f, b)
    assert f.view().counting == {f.machine_hex, b.machine_hex}


def test_a_joiner_only_keeps_a_founder_record_that_verifies_for_its_room(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    good = f.data["founder"]
    b = machine(tmp_path, "b")
    anchor = nh.join_anchor(f.network_id())
    assert b.take_join({"founder": good}, anchor=anchor) == f.machine_hex
    x = machine(tmp_path, "x")
    forged = {**good, "founder": _hex(SigningKey.generate())}  # signature no longer matches
    with pytest.raises(JoinRefused):
        x.take_join({"founder": forged}, anchor=anchor)
    assert not x.data.get("founder")
    wrong = nh.founder_record(SigningKey.generate(), "ee" * 32)  # signed, but not this room's id
    with pytest.raises(JoinRefused):
        x.take_join({"founder": wrong}, anchor=anchor)
    assert not x.data.get("founder")


# ------------------------------------------------ review fixes F4


def test_f4_1_the_network_id_is_fixed_at_join_not_recomputed_from_the_founder_record(three, tmp_path):
    """F4-1: swapping the held founder record cannot move the id or make another machine the founder."""
    f, b, _c = three
    original = b.network_id()
    x = machine(tmp_path, "x")
    b.data["founder"] = nh.founder_record(x.machine, nh.network_id_for(ROOM, x.machine_hex))
    assert b.network_id() == original
    assert b.view().founder == ""  # the record names a founder the stored id does not bind


def test_f4_2_nobody_auto_adopts_a_founder_not_even_the_inviters_machine(tmp_path):
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)
    f.found()
    direct = machine(tmp_path, "d", inviter=f.client.public_key)
    meet(f, direct)
    assert direct.view().founder == "" and direct.data.get("founder_candidates")
    direct.halt_act("confirm_founder", {"founder": f.machine_hex})  # the person confirms the fingerprint
    assert direct.view().founder == f.machine_hex
    # a held, unbound founder record from the join decision is not trusted either
    joiner = machine(tmp_path, "j")
    joiner.client.state.inviter = f.client.public_key
    joiner.take_join({"founder": f.data["founder"]}, anchor=nh.join_anchor(f.network_id()))
    joiner.learn(f.client.public_key, f.wire())
    meet(f, joiner)
    assert joiner.view().founder == ""


def test_f4_3_the_join_code_anchor_refuses_a_founder_record_for_another_network(tmp_path):
    """F4-3: a compromised inviter C hands out a founder record for itself; the code names the real network."""
    real = machine(tmp_path, "real")
    real.found()
    anchor = nh.join_anchor(real.network_id())
    c = machine(tmp_path, "c")
    c.found()  # C founded its own one-machine network in the same room
    j = machine(tmp_path, "j")
    j.client.state.inviter = c.client.public_key
    fields = decide(c, j)
    with pytest.raises(JoinRefused) as refused:
        j.take_join(fields, anchor=anchor)
    assert str(refused.value) == NETWORK_MISMATCH_LINE
    assert "new code" in NETWORK_MISMATCH_LINE and not j.data.get("founder")
    with pytest.raises(JoinRefused):
        j.take_join(decide(real, j), anchor="")  # a code with no anchor is refused too
    assert j.take_join(decide(real, j), anchor=anchor) == real.machine_hex


def test_f4_3_a_legacy_network_decision_without_a_founder_still_checks_the_network(tmp_path):
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)
    m = machine(tmp_path, "m", inviter="ee" * 32)  # an inviter that holds no founder record
    fields = decide(m, machine(tmp_path, "j"))
    assert fields["founder"] is None
    j = machine(tmp_path, "j2")
    legacy = hashlib.sha256(bytes.fromhex(ROOM)).hexdigest()
    assert j.take_join(fields, anchor=nh.join_anchor(legacy)) == ""
    with pytest.raises(JoinRefused):
        j.take_join(fields, anchor=nh.join_anchor("ab" * 32))


def test_f4_3_the_join_warns_when_one_machine_counts(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    j = machine(tmp_path, "j")
    join(f, j)
    assert j.count_warning() == ONE_COUNTING_LINE
    assert "one machine that counts" in ONE_COUNTING_LINE and "second one" in ONE_COUNTING_LINE
    assert j.halt_snapshot()["halt_allowed"] is False


def test_f4_4_eight_throwaway_picks_do_not_fill_the_slots_and_the_founders_pick_lands(three):
    f, b, c = three
    net = f.network_id()
    picks = [nh.admission(SigningKey.generate(), net, c.machine_hex, bootstrap=True) for _ in range(8)]
    c.learn(b.client.public_key, {"bootstrap_picks": picks})
    assert c.data["bootstrap_picks"] == [p for p in c.data["bootstrap_picks"] if p["admitter"] == f.machine_hex]
    assert len(c.data["bootstrap_picks"]) <= 1


def test_f4_4_eight_self_signed_founder_records_do_not_lock_out_auto_adopt_or_confirm(tmp_path):
    f = machine(tmp_path, "f")
    f.client.state.approvals.append("ee" * 32)
    f.found()
    legacy = f.network_id()
    m = machine(tmp_path, "m", inviter="ee" * 32)
    for _ in range(8):
        m.learn(f.client.public_key, {"founder": nh.founder_record(SigningKey.generate(), legacy)})
    assert not m.data.get("founder_candidates")
    meet(f, m)
    assert [c["founder"] for c in m.data["founder_candidates"]] == [f.machine_hex]
    m.halt_act("confirm_founder", {"founder": f.machine_hex})
    assert m.view().founder == f.machine_hex


def test_f4_5_records_signed_by_non_members_take_no_slot(three):
    f, b, _c = three
    net = f.network_id()
    for _ in range(16):
        junk = [nh.machine_confirmation(SigningKey.generate(), net, _hex(SigningKey.generate())) for _ in range(32)]
        f.learn(b.client.public_key, {"confirmations": junk})
    assert f.data["confirmations"] == []
    real = nh.machine_confirmation(b.machine, net, "ab" * 32)
    f.learn(b.client.public_key, {"confirmations": [real]})
    assert f.data["confirmations"] == [real]


def test_f4_6_one_member_alone_cannot_disable_the_kill_switch_for_a_victim(three):
    """S1+S4: 8 picks, 8 founder candidates, 16 fresh keys x 32 records, then honest acts still work."""
    f, b, c = three
    net = f.network_id()
    attack = {
        "bootstrap_picks": [nh.admission(SigningKey.generate(), net, c.machine_hex, bootstrap=True) for _ in range(8)],
        "founder": nh.founder_record(SigningKey.generate(), net),
    }
    for _ in range(16):
        flood = {
            "confirmations": [
                nh.machine_confirmation(SigningKey.generate(), net, _hex(SigningKey.generate())) for _ in range(32)
            ],
            "revocations": [
                nh.revocation(SigningKey.generate(), SigningKey.generate(), net, _hex(SigningKey.generate()), 1)
                for _ in range(32)
            ],
            **attack,
        }
        f.learn(c.client.public_key, flood)  # c is the attacker, a real member
    # honest acts afterwards
    f.halt_act("halt", {"reason": "after the flood"})
    assert f.halt_book().active()
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    b.sign_revocation(_hex(SigningKey.generate()))
    meet(f, b, c)
    assert any(r["machine"] == b.machine_hex for r in f.data["revocations"])
    assert any(r["by"] == b.machine_hex for r in f.data["confirmations"])
    assert f.view().counts(c.machine_hex)


def test_f4_6_make_bootstrap_still_works_after_the_flood(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    join(f, b)
    meet(f, b)
    net = f.network_id()
    b.learn(f.client.public_key, {"bootstrap_picks": [
        nh.admission(SigningKey.generate(), net, b.machine_hex, bootstrap=True) for _ in range(8)
    ]})
    f.halt_act("make_bootstrap", {"machine": b.machine_hex})
    meet(f, b)
    assert b.view().counting == {f.machine_hex, b.machine_hex}


def test_f4_7_an_opt_out_needs_a_signature_a_member_signer_and_the_newest_wins(three):
    f, b, c = three
    net = f.network_id()
    stranger = SigningKey.generate()
    f.learn(b.client.public_key, {"opt_outs": [nh.no_count_record(stranger, net, issued_at=5)]})
    assert f.data["opt_outs"] == []  # not a member machine
    old = nh.no_count_record(c.machine, net, issued_at=10)
    unsigned = {**old, "sig": "00" * 64}
    f.learn(c.client.public_key, {"opt_outs": [unsigned]})
    assert f.data["opt_outs"] == []
    f.learn(c.client.public_key, {"opt_outs": [old]})
    newer = nh.no_count_record(c.machine, net, issued_at=20, opted_out=False)  # Count Here again
    f.learn(c.client.public_key, {"opt_outs": [newer]})
    f.learn(b.client.public_key, {"opt_outs": [old]})  # a third party replays the old opt-out
    assert f.data["opt_outs"] == [newer]
    assert len(f.data["opt_outs"]) <= 1


def test_f4_7_count_here_again_is_a_signed_record_that_beats_an_older_opt_out(three):
    f, b, c = three
    b.halt_act("confirm_machine", {"machine": c.machine_hex})
    meet(f, b, c)
    c.halt_act("no_count")
    meet(f, b, c)
    assert not f.view().counts(c.machine_hex)
    c.halt_act("count")
    meet(f, b, c)
    stale = [r for r in c.data["opt_outs"]]
    f.learn(b.client.public_key, {"opt_outs": stale})
    assert f.view().counts(c.machine_hex) and c.view().counts(c.machine_hex)


def test_f4_5_a_stored_record_whose_signer_is_no_member_machine_is_dropped_on_the_next_merge(three):
    f, b, _c = three
    net = f.network_id()
    stranger = nh.machine_confirmation(SigningKey.generate(), net, "ab" * 32)
    f.data["confirmations"] = [stranger]
    f.learn(b.client.public_key, {})
    assert f.data["confirmations"] == []


def _trade_halt_books(*controls):
    """Only the halt exchange (what a halted machine runs): no member lists."""
    for _ in range(2):
        for a in controls:
            for b in controls:
                if a is not b:
                    b.halt_merge(a.halt_payload())


def test_revocation_quorum_completes_over_the_halt_exchange(three, tmp_path, monkeypatch):
    # F15a: no settle window — the second counting signature applies at once
    """D6: a halted network trades halt books only, and a revocation still reaches two signatures."""
    f, b, c = three
    d = machine(tmp_path, "d")
    join(f, d)
    meet(f, b, c, d)
    f.sign_revocation(d.client.public_key)
    _trade_halt_books(f, b, c)
    assert d.client.public_key not in c.client.state.revoked  # one machine's word
    b.sign_revocation(d.client.public_key)
    _trade_halt_books(f, b, c)
    assert d.client.public_key in c.client.state.revoked and d.client.public_key in f.client.state.revoked
    # a primary is never revoked on peers' word, over this path either
    b.sign_revocation(f.client.public_key)
    c.sign_revocation(f.client.public_key)
    c.client.state.inviter = f.client.public_key
    _trade_halt_books(b, c)
    assert f.client.public_key not in c.client.state.revoked

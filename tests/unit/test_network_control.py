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
    HaltSync,
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


def join(issuer, joiner):
    joiner.client.state.inviter = issuer.client.public_key
    fields = issuer.join_decision_fields(joiner.machine_hex)
    joiner.take_join(fields["network_control"])
    return fields["network_control"]


@pytest.fixture
def three(tmp_path):
    """A new network: founder F, bootstrap B, and C that F admitted after."""
    f = machine(tmp_path, "f")
    assert f.admit_refusal() == ""  # alone: F founds the network here
    b = machine(tmp_path, "b")
    c = machine(tmp_path, "c")
    first = join(f, b)
    second = join(f, c)
    assert first["admission"]["bootstrap"] is True and second["admission"]["bootstrap"] is False
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
    fields = f.join_decision_fields(b.machine_hex)["network_control"]
    fields["admission"] = {**fields["admission"], "machine": "cd" * 32}
    b.client.state.inviter = f.client.public_key
    b.take_join(fields)
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
    assert direct.view().founder == f.machine_hex  # accepted without asking
    with pytest.raises(nh.HaltError):
        m.halt_act("make_bootstrap", {"machine": direct.machine_hex})  # only the founder picks
    f.halt_act("make_bootstrap", {"machine": m.machine_hex})
    meet(f, m)
    assert m.view().counting == {f.machine_hex, m.machine_hex}
    with pytest.raises(nh.HaltError):
        f.halt_act("make_bootstrap", {"machine": direct.machine_hex})  # once


def test_revocations_apply_on_two_counting_machines_and_spare_the_inviter(three, tmp_path):
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
    b.halt_act("no_count")
    meet(f, b, c)
    assert not f.view().counts(b.machine_hex)
    b.halt_act("count")
    meet(f, b, c)
    assert f.view().counts(b.machine_hex) and c.view().counts(b.machine_hex)
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

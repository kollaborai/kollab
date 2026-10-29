"""Kill switch round 12 (F15a): the revocation outcome is a pure function of the set of signed
records (no arrival order, no wall clock, no local first-seen state), and presence never carries
a dead or reused tool-shell pid.

The convergence tests fail on the F14 rule, which ordered completed pairs by THIS machine's
first-seen clock (REVOKE_PAIR/REVOKE_SETTLE) and so kept opposite revocations on machines that
heard the same records in different orders.
"""

from __future__ import annotations

import itertools

from kollabor_agent import runtime as agent_runtime
from kollabor_agent import shell_executor as se
from plugins.hub import network_halt as nh
from tests.unit.test_kill_switch_f12 import _four, _recount


def _rec(m, net, key, t=0):
    return nh.revocation(m.folders[0], m.key, net.id, key, t)


def _who(view, keys):
    return {view.machine_of(k) or k for k in keys}


# ------------------------------------------------------------------ 1. revocation convergence


def test_the_same_records_in_any_order_or_batches_give_one_answer_on_every_machine():
    """The review's fixture: four machines, the same crossing records delivered in opposite orders,
    the second batch 300 s after the first. Every machine ends on the same set."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    recs = [
        _rec(b, net, a.fk()), _rec(c, net, a.fk()),  # b+c revoke a
        _rec(a, net, c.fk()), _rec(b, net, c.fk()),  # a+b revoke c
    ]
    final = nh.revocations_to_apply(recs, view, recount=recount)
    answers = {frozenset(final)}
    for order in itertools.permutations(recs):  # each order is a different machine's arrival order
        for cut in (1, 2, 3):
            applied = set()
            applied |= nh.revocations_to_apply(list(order[:cut]), view, recount=recount)  # batch one
            applied |= nh.revocations_to_apply(list(order), view, recount=recount)  # batch two, 300 s later
            answers.add(frozenset(applied))
            assert applied <= final  # a subset's answer never exceeds the full set's
            assert len(recount(_who(view, applied)).counting) >= 2
    assert answers == {frozenset(final)}
    assert _who(view, final) == {a.mk, c.mk}  # a crossing with two signers each side revokes both


def test_a_machine_that_heard_a_subset_converges_when_the_rest_arrives():
    """F14's documented divergence (the window bounds delay, not partitions) is gone: machine X heard
    only one side of a crossing, applied it, and lands on Y's full-set answer when the rest arrives."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    to_a = [_rec(b, net, a.fk()), _rec(c, net, a.fk())]
    to_c = [_rec(a, net, c.fk()), _rec(b, net, c.fk())]
    y = nh.revocations_to_apply(to_a + to_c, view, recount=recount)
    x = nh.revocations_to_apply(to_c, view, recount=recount)  # X heard only C's side
    x |= nh.revocations_to_apply(to_a + to_c, view, recount=recount)  # then everything, any delay later
    assert _who(view, x) == _who(view, y) == {a.mk, c.mk}


def test_the_second_signature_500_seconds_later_completes_the_revocation():
    """A person walks to another machine and co-signs 500 s later: no pair window, no expiry."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    one = [_rec(b, net, d.fk(), 1_000)]
    assert nh.revocations_to_apply(one, view, recount=recount) == set()
    late = one + [_rec(c, net, d.fk(), 1_500)]
    assert d.fk() in nh.revocations_to_apply(late, view, recount=recount)


def test_backdated_far_future_and_negative_stamps_change_nothing():
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())

    def pair(t1, t2):
        return nh.revocations_to_apply([_rec(b, net, d.fk(), t1), _rec(c, net, d.fk(), t2)], view,
                                       recount=recount)

    base = pair(9000, 9001)
    assert base  # honest stamps do revoke d
    for t1, t2 in ((0, -10**15), (10**30, 10**30), (-1, 1), (0, 10**30)):
        assert pair(t1, t2) == base


def test_a_hostile_member_alone_neither_revokes_the_founder_nor_blocks_an_honest_revocation():
    net, a, b, c, d = _four()
    i = d.add_folder()
    net.join(d, folder=i)
    recount = _recount(net)
    view = recount(set())
    alone = [_rec(d, net, a.fk()), nh.revocation(d.folders[i], d.key, net.id, a.fk()), _rec(d, net, a.mk)]
    assert nh.revocations_to_apply(alone, view, recount=recount) == set()
    honest = [_rec(b, net, d.fk()), _rec(c, net, d.fk())]
    stale = [_rec(d, net, c.fk())]  # d's lone old signature on c blocks nothing and completes nothing
    out = nh.revocations_to_apply(alone + stale + honest, view, recount=recount)
    assert _who(view, out) == {d.mk}


def test_a_signer_that_does_not_count_does_not_count_as_a_signer():
    """u joined but was never confirmed by a counting machine: u+b signing together is one signer."""
    net, a, b, c, d = _four()
    u = net.admit(a, "u")  # admitted, not confirmed: not counting
    recount = _recount(net)
    view = recount(set())
    assert u.mk not in view.counting
    recs = [_rec(u, net, d.fk()), _rec(b, net, d.fk())]
    assert nh.revocations_to_apply(recs, view, recount=recount) == set()
    recs += [_rec(c, net, d.fk())]
    assert d.fk() in nh.revocations_to_apply(recs, view, recount=recount)


def test_a_target_signing_its_own_revocation_does_not_count_as_a_signer():
    """d signs "revoke c" and compromised c co-signs its own revocation: one counting signer, not two."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    recs = [_rec(d, net, c.fk()), _rec(c, net, c.fk())]
    assert nh.revocations_to_apply(recs, view, recount=recount) == set()


def test_a_machine_key_revocation_takes_every_folder_key_bound_to_the_machine():
    net, a, b, c, d = _four()
    i = d.add_folder()
    net.join(d, folder=i)
    recount = _recount(net)
    view = recount(set())
    recs = [_rec(b, net, d.mk), _rec(c, net, d.mk)]
    out = nh.revocations_to_apply(recs, view, recount=recount)
    assert d.fk() in out and len(out) == 3  # the machine key plus both bound folder keys


def test_etime_parsing():
    assert se._etime_seconds("01:30") == 90
    assert se._etime_seconds("02:03:04") == 7384
    assert se._etime_seconds("1-02:03:04") == 93784
    assert se._etime_seconds("x") is None and se._etime_seconds("") is None


def test_a_revocation_that_would_uncount_others_below_two_is_still_skipped():
    """The F12 floor keeps its chain effects: revoking B uncounts C (confirmed only by B) and E."""
    from tests.unit.test_kill_switch_f12 import Net

    net = Net()
    net.prod = net.admit(net.founder, "prod", bootstrap=True)
    a, b = net.founder, net.prod
    c = net.admit(a, "c")
    e = net.admit(a, "e")
    net.confirm(b, c)
    net.confirm(c, e)
    recount = _recount(net)
    assert len(recount(set()).counting) == 4 and len(recount({b.mk}).counting) == 1
    from tests.unit.test_network_halt import rev

    recs = [rev(c, net, b.fk()), rev(e, net, b.fk())]
    assert nh.revocations_to_apply(recs, recount(set()), recount=recount) == set()


def test_a_late_joiner_replaying_the_full_history_matches_the_online_machines():
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    history = [_rec(b, net, d.fk()), _rec(c, net, d.fk()), _rec(a, net, c.fk()), _rec(b, net, c.fk())]
    online = set()
    heard = []
    for r in history:  # one record at a time, as the online machines heard it
        heard.append(r)
        online |= nh.revocations_to_apply(heard, view, recount=recount)
    late = nh.revocations_to_apply(history, view, recount=recount)
    assert _who(view, online) == _who(view, late) == {c.mk, d.mk}


def test_the_answer_applies_at_once_and_survives_a_restart(tmp_path):
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
    before = f.revocations_to_apply()  # no settle window: the second signature is enough
    assert dk in before
    restarted = NetworkControl(f.client, machine=f.machine, root=f._root)  # same persisted records
    assert restarted.revocations_to_apply() == before
    assert "revoke_seen" not in restarted.data  # no local clock state anywhere


# ------------------------------------------------------------------ 2. stale tool-shell rows


def test_presence_never_carries_a_dead_or_reused_tool_shell(monkeypatch):
    se.TOOL_SHELLS[:] = [[111, 100.0, 1], [222, 200.0, 1], [333, 300.0, 1], [444, 400.0, 1]]
    # 111: shell and group gone. 222: live, same start. 333: live, ps start drifts half a second.
    # 444: the pid was reused by a new process that started at 900.
    monkeypatch.setattr(se, "_group_alive", lambda pid: pid in (222, 333, 444))
    monkeypatch.setattr(se, "_pid_starts", lambda pids: {222: 200.0, 333: 300.5, 444: 900.0})
    rows = agent_runtime._tool_shells()
    assert [r[0] for r in rows] == [222, 333]


def test_a_live_shell_ps_cannot_see_the_start_of_is_kept(monkeypatch):
    """Only a proven start mismatch drops a row: ps failing (a container without it) keeps live shells."""
    se.TOOL_SHELLS[:] = [[222, 200.0, 1]]
    monkeypatch.setattr(se, "_group_alive", lambda pid: True)
    monkeypatch.setattr(se, "_pid_starts", lambda pids: {})
    assert agent_runtime._tool_shells() == [[222, 200.0, 1]]


def test_the_prune_keeps_the_cap(monkeypatch):
    se.TOOL_SHELLS[:] = [[float(pid), 1.0, 1] for pid in range(80)]
    monkeypatch.setattr(se, "_group_alive", lambda pid: True)
    monkeypatch.setattr(se, "_pid_starts", lambda pids: {})
    rows = agent_runtime._tool_shells()
    assert len(rows) == se.MAX_TOOL_SHELLS and [r[0] for r in rows[:5]] == [16.0, 17.0, 18.0, 19.0, 20.0]


def test_recording_a_new_shell_drops_reused_pids(monkeypatch):
    se.TOOL_SHELLS[:] = [[444, 400.0, 1]]
    monkeypatch.setattr(se, "_group_alive", lambda pid: True)
    monkeypatch.setattr(se, "_pid_starts", lambda pids: {444: 900.0})
    se.record_tool_shell(555, 500.0)
    assert se.TOOL_SHELLS == [[555, 500.0, se.os.getpid()]]


# ------------------------------------------------------------------ 3. the aggregate floor (F15b)


def _three():
    """Three counting machines: founder a, bootstrap b, member c (the smallest network that can halt)."""
    from tests.unit.test_kill_switch_f12 import _recount
    from tests.unit.test_network_halt import Net

    net = Net()
    net.prod = net.admit(net.founder, "prod", bootstrap=True)
    a, b = net.founder, net.prod
    c = net.admit(a, "c")
    net.confirm(b, c)
    return net, a, b, c, _recount(net)


def test_a_three_machine_crossing_keeps_two_counting_machines():
    """F15b (Amethyst's blocker): a+b revoke c, b+c revoke a — each alone passes the per-key floor,
    together they leave only b counting. The final answer must keep two counting machines."""
    net, a, b, c, recount = _three()
    view = recount(set())
    assert len(view.counting) == 3
    recs = [_rec(a, net, c.fk()), _rec(b, net, c.fk()), _rec(b, net, a.fk()), _rec(c, net, a.fk())]
    out = nh.revocations_to_apply(recs, view, recount=recount)
    gone = _who(view, out)
    assert len(recount(gone).counting) >= 2  # the floor holds over the FINAL applied set
    assert gone == {view.machine_of(min(c.fk(), a.fk()))}  # one winner: the lower key id, no clock


def test_four_machine_chains_keep_the_floor():
    """Three completed revocations on four machines can never all apply: exactly the two lowest keys do."""
    net, a, b, c, d = _four()
    recount = _recount(net)
    view = recount(set())
    recs = [
        _rec(a, net, c.fk()), _rec(b, net, c.fk()),  # a+b revoke c
        _rec(b, net, a.fk()), _rec(d, net, a.fk()),  # b+d revoke a
        _rec(b, net, d.fk()), _rec(c, net, d.fk()),  # b+c revoke d
    ]
    out = nh.revocations_to_apply(recs, view, recount=recount)
    gone = _who(view, out)
    assert len(gone) == 2 and len(recount(gone).counting) >= 2  # the third would leave only b
    lowest = sorted([(c.fk(), c.mk), (a.fk(), a.mk), (d.fk(), d.mk)])[:2]
    assert gone == {m for _, m in lowest}  # the two lowest key ids, no clock, no arrival order


def test_the_apply_guard_skips_what_this_machines_history_would_kill():
    """The F15b guard: a machine heard one side of a three-machine crossing and applied it; the
    full-set answer is the other side (the lower key). Applying that too would leave one counting
    machine, so the apply step skips it and the machine stays haltable."""
    net, a, b, c, recount = _three()
    view = recount(set())
    winner, loser = min(c.fk(), a.fk()), max(c.fk(), a.fk())
    recs = [_rec(b, net, a.fk()), _rec(c, net, a.fk()), _rec(a, net, c.fk()), _rec(b, net, c.fk())]
    candidates = nh.revocations_to_apply(recs, view, recount=recount)
    assert candidates == {winner}  # the full-set answer: the lower key only
    clean = nh.floor_safe_applies(view, set(), candidates, recount=recount)
    assert clean == candidates  # with nothing applied yet, it goes
    applied = nh.floor_safe_applies(view, {loser}, candidates, recount=recount)
    assert applied == set()  # this machine already revoked the loser: the winner stays out
    both = nh.floor_safe_applies(view, set(), {winner, loser}, recount=recount)
    assert both == {winner}  # offered both, it takes the floor-safe one and stops

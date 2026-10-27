"""Kill switch F8: the join warning tracks the real count, every act needs a person, chains name this machine."""

from __future__ import annotations

import pytest

from plugins.hub import network_halt as nh
from plugins.hub.network_control import ONE_COUNTING_LINE
from tests.unit.test_network_control import decide, join, machine, meet


def test_join_after_the_bootstrap_pick_gets_no_one_machine_warning(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    join(f, b)
    meet(f, b)
    f.halt_act("make_bootstrap", {"machine": b.machine_hex})
    meet(f, b)
    c = machine(tmp_path, "c")
    c.client.state.inviter = f.client.public_key
    fields = decide(f, c)
    c.take_join(fields, anchor=nh.join_anchor(f.network_id()))
    assert c.count_warning(fields) == ""


def test_join_while_one_machine_counts_still_warns(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    b.client.state.inviter = f.client.public_key
    fields = decide(f, b)
    b.take_join(fields, anchor=nh.join_anchor(f.network_id()))
    assert b.count_warning(fields) == ONE_COUNTING_LINE


@pytest.mark.parametrize(
    "action", ["make_bootstrap", "withdraw", "confirm_machine", "confirm_founder", "count", "no_count"]
)
def test_every_halt_act_is_gated_on_confirmed(action):
    with pytest.raises(nh.HaltError):
        nh.require_confirmed(action, {})
    nh.require_confirmed(action, {"confirmed": True})


def test_halt_members_names_the_admitter_even_when_it_is_this_machine(tmp_path):
    f = machine(tmp_path, "f")
    f.found()
    b = machine(tmp_path, "b")
    join(f, b)
    meet(f, b)
    f.client.state.device_name = "f-work"
    rows = {r["name"]: r for r in f.halt_members()}
    for r in rows.values():
        assert "this machine" not in r["chain"]

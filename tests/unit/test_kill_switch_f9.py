"""Kill switch round 6 (F9): group expansion, env-tag trust, resume --local ordering, listener []->machine.

Synthetic process tables only; nothing here halts, sweeps or signals a real process.
"""

from __future__ import annotations

import asyncio

from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

INSTALL = nh.Install(prefix="/opt/this-install")


def _sweep(tree, procs=None, agents=(100,)):
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    return {p.pid for p in nh.select_sweep(
        procs or [agent], install=INSTALL, scopes=["machine"], agent_pids=list(agents), protect=set(), tree=tree,
    )}


def test_r6_1_an_agent_sharing_the_login_shells_group_leaves_the_shell_and_vim():
    tree = {50: (1, 50, 50), 60: (50, 50, 50), 100: (50, 50, 50)}  # job control off: one group for all
    assert _sweep(tree) == {100}


def test_r6_1_a_launcher_that_makes_no_group_leaves_its_siblings():
    tree = {300: (1, 300, 300), 310: (300, 300, 300), 100: (300, 300, 300), 110: (100, 300, 300)}
    assert _sweep(tree) == {100, 110}  # not npm/engine 300 or its vite 310


def test_r6_1_a_group_the_agent_leads_is_still_swept():
    assert 130 in _sweep({100: (1, 100, 100), 130: (1, 100, 700)})


def test_r6_2_a_tmux_pane_that_inherited_the_tag_is_left_alone():
    tmux = nh.Proc(400, 1, ["tmux: server"], {"KOLLAB_AGENT_PID": "100"}, "/")
    pane = nh.Proc(500, 400, [], {"KOLLAB_AGENT_PID": "100"}, "/home")
    vim = nh.Proc(510, 500, ["vim"], {"KOLLAB_AGENT_PID": "100"}, "/home")
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    tree = {50: (1, 50, 50), 100: (50, 100, 50), 400: (1, 400, 400), 500: (400, 500, 500), 510: (500, 500, 500)}
    assert _sweep(tree, [agent, tmux, pane, vim]) == {100}


def test_r6_2_an_agent_started_tool_child_with_the_tag_is_still_swept():
    agent = nh.Proc(100, 50, ["/opt/this-install/bin/kollab"], {}, "/work")
    child = nh.Proc(120, 100, ["python", "srv.py"], {"KOLLAB_AGENT_PID": "100"}, "/work")
    orphan = nh.Proc(130, 1, ["python", "srv.py"], {"KOLLAB_AGENT_PID": "100"}, "/work")  # setsid daemon
    tree = {100: (50, 100, 50), 120: (100, 120, 120), 130: (1, 130, 130)}
    assert _sweep(tree, [agent, child, orphan]) == {100, 120, 130}


def test_r6_3_resume_local_clears_only_after_the_act_and_never_a_good_book(tmp_path, monkeypatch):
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine"}, "networks": {"n": {"halts": {}}}})
    assert hr.clear_malformed_halt(tmp_path) == ""
    assert "n" in hr.read_halt(tmp_path)["scopes"]  # a valid book is untouched, nothing moved aside
    assert not (tmp_path / "private" / "halt.json.bad").exists()


def test_r6_3_a_transient_read_error_moves_nothing(tmp_path, monkeypatch):
    nh.HaltFile(tmp_path).write({"scopes": {"n": []}, "networks": {}})

    def boom(self):
        raise OSError("EIO")

    monkeypatch.setattr(nh.HaltFile, "read", boom)
    assert hr.clear_malformed_halt(tmp_path) == ""
    assert (tmp_path / "private" / "halt.json").exists()
    assert not (tmp_path / "private" / "halt.json.bad").exists()


def test_r6_3_cli_resume_local_leaves_the_file_when_the_act_fails(tmp_path, monkeypatch):
    nh.HaltFile(tmp_path).write({"scopes": {"n": "machine"}, "networks": {"n": {"halts": {}}}})
    before = (tmp_path / "private" / "halt.json").read_bytes()

    async def failing_act(*a, **k):
        raise RuntimeError("listener refused")

    monkeypatch.setattr(hr, "_act", failing_act)
    assert hr.clear_malformed_halt(tmp_path) == ""  # what the CLI would call on the failure: a good book, no-op
    assert (tmp_path / "private" / "halt.json").read_bytes() == before


def test_r6_4_an_empty_folder_list_in_a_halt_order_is_recorded_as_machine(tmp_path, monkeypatch):
    async def fake_sweep(scopes, **kw):
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
        {"op": "halt", "network": "net1", "scope": [], "folder": 5, "protect": []}))
    assert reply["ok"]
    assert hr.read_halt(tmp_path)["scopes"]["net1"] == "machine"

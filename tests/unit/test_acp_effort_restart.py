"""A restart into another profile forgets the previous session's picks, so they cannot replay."""

from tests.unit.test_acp_effort_restore import _options, _orchestrator


def test_restart_forgets_the_live_effort_pick_and_user_picks(tmp_path):
    orch = _orchestrator(tmp_path, _options())
    orch._effort_pick = "max"
    orch._user_picks = {"effort": "max"}
    orch._forget_session_picks()
    assert orch._effort_pick is None
    assert orch._user_picks == {}

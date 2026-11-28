"""Status output includes both options when both automatic limits expire."""

from kollabor.commands.system_commands.handlers.goal import GoalCommandHandler
from kollabor.goals.service import BoundaryContext, GoalService, TurnOutcome
from kollabor.state.goal_store import GoalStore


def test_status_names_both_resume_options_when_both_limits_expire(tmp_path):
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test-daemon")
    record = service.create_goal(
        "conversation-1",
        "objective",
        "/repo",
        auto_turn_limit=1,
        auto_time_limit_s=7200,
    )
    store.update_fields(
        record.goal_id,
        record.record_version,
        turn_count=1,
        time_used_seconds=7200.0,
    )
    service.maybe_continue(
        record.goal_id,
        TurnOutcome(
            turn_id="turn-1",
            origin="goal",
            status="ok",
            goal_id=record.goal_id,
        ),
        BoundaryContext(
            queue_empty=True,
            pending_work=False,
            invalidated=False,
            provider_healthy=True,
        ),
    )

    handler = GoalCommandHandler(None, None)
    handler._service = service
    handler._conversation_uid = lambda: "conversation-1"

    result = handler._render_status()

    assert "next: /goal resume --turns N --time N" in result.message
    store.close()

"""Regression coverage for durable coordinator dispatch recovery."""

import asyncio
import subprocess
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_definitions.hub import hub_dispatch_verify, hub_msg
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin, HubWakeDecision
from plugins.hub.task_ledger import TaskLedger
from plugins.hub.vault import AgentVault


def _record_dispatch(ledger, **overrides):
    values = {
        "task_id": "task-636",
        "assigned_by": "koordinator",
        "assignee": "lapis",
        "directive": "Implement durable dispatch recovery.",
        "message_id": "dispatch-message-1",
        "thread_id": "dispatch-thread-1",
        "worktree": "/tmp/kollab-worktree",
        "commit": "abc1234",
    }
    values.update(overrides)
    return ledger.record_dispatch(**values)


def test_dispatch_record_is_durable_idempotent_and_separate_from_reply_ttl(tmp_path):
    ledger_dir = tmp_path / "tasks"
    ledger = TaskLedger(str(ledger_dir))
    dispatch = _record_dispatch(ledger)

    assert dispatch["status"] == "dispatching"
    assert dispatch["task_id"] == "task-636"
    assert dispatch["assigned_by"] == "koordinator"
    assert dispatch["assignee"] == "lapis"
    assert dispatch["directive"] == "Implement durable dispatch recovery."
    assert dispatch["worktree"] == "/tmp/kollab-worktree"
    assert dispatch["commit"] == "abc1234"

    replay = _record_dispatch(ledger)
    assert replay == dispatch

    reloaded = TaskLedger(str(ledger_dir))
    assert reloaded.get_dispatch("dispatch-message-1") == dispatch
    assert reloaded.get_dispatches_for("koordinator") == [dispatch]
    assert reloaded.get_dispatches_for("lapis") == []
    assert reloaded.get("task-636") is None

    pending_path = ledger_dir / "_pending_replies.json"
    pending_path.write_text(
        '[{"task_id":"task-636","assignee":"lapis",'
        '"requested_by":"koordinator","message_id":"dispatch-message-1",'
        '"created_at":0,"deadline_seconds":120,"status":"pending"}]',
        encoding="utf-8",
    )
    assert reloaded.pending_replies() == []
    assert reloaded.get_dispatch("dispatch-message-1") == dispatch


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"task_id": "other-task"}, id="task-id"),
        pytest.param({"assigned_by": "peridot"}, id="assigner"),
        pytest.param({"assignee": "peridot"}, id="assignee"),
        pytest.param({"directive": "A different directive."}, id="directive"),
        pytest.param({"thread_id": "other-thread"}, id="thread-id"),
        pytest.param({"worktree": "/different/worktree"}, id="worktree"),
        pytest.param({"commit": "different-commit"}, id="commit"),
    ],
)
def test_conflicting_dispatch_replay_is_rejected(tmp_path, override):
    ledger = TaskLedger(str(tmp_path))
    original = _record_dispatch(ledger)

    with pytest.raises(ValueError, match="different immutable payload"):
        _record_dispatch(ledger, **override)

    assert ledger.get_dispatch("dispatch-message-1") == original


def test_dispatch_report_is_assignee_and_thread_bound_until_verified(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    _record_dispatch(ledger)
    assert ledger.update_dispatch("dispatch-message-1", status="in_flight")

    assert not ledger.record_dispatch_report(
        task_id="task-636", assignee="peridot", thread_id="dispatch-thread-1",
        message_id="wrong-agent-report", report="Task complete",
    )
    assert not ledger.record_dispatch_report(
        task_id="task-636", assignee="lapis", thread_id="another-thread",
        message_id="wrong-thread-report", report="Task complete",
    )
    assert not ledger.mark_dispatch_verified(
        "dispatch-message-1", verified_by="koordinator",
        note="Cannot verify before a report exists.",
    )

    for message_id, progress_report in (
        ("progress-message", "I am not done yet."),
        ("negative-completion", "Task not complete; tests are still running."),
        ("unresolved-progress", "Unresolved; implementation still needs tests."),
        ("no-blockers-progress", "No blockers, but this task is still in progress."),
        ("ambiguous-completion", "Task complete? Tests are still pending."),
        ("comma-progress", "Task complete, but acceptance tests are pending."),
    ):
        assert not ledger.record_dispatch_report(
            task_id="task-636", assignee="lapis", thread_id="dispatch-thread-1",
            message_id=message_id, report=progress_report,
        )
    assert ledger.get_dispatch("dispatch-message-1")["status"] == "in_flight"

    assert ledger.record_dispatch_report(
        task_id="task-636", assignee="lapis", thread_id="dispatch-thread-1",
        message_id="report-message-1", report="Task complete\ntests passed.",
        worktree="/tmp/kollab-worktree", commit="def5678",
    )
    reported = ledger.get_dispatch("dispatch-message-1")
    assert reported["status"] == "reported_unverified"
    assert reported["report_message_id"] == "report-message-1"
    assert reported["report"] == "Task complete\ntests passed."
    assert reported["reported_commit"] == "def5678"

    assert not ledger.mark_dispatch_verified(
        "dispatch-message-1", verified_by="koordinator", note="   "
    )
    assert not ledger.mark_dispatch_verified(
        "dispatch-message-1", verified_by="lapis",
        note="Self-report is not independent verification.",
    )
    assert ledger.mark_dispatch_verified(
        "dispatch-message-1", verified_by="koordinator",
        note="Inspected the diff and reran focused tests.",
    )
    reloaded = TaskLedger(str(tmp_path)).get_dispatch("dispatch-message-1")
    assert reloaded["status"] == "verified"
    assert reloaded["verified_by"] == "koordinator"
    assert reloaded["verification_note"] == "Inspected the diff and reran focused tests."


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        pytest.param({}, False, id="missing-reference"),
        pytest.param({"worktree": "/tmp/lane-636"}, True, id="worktree-reference"),
        pytest.param({"commit": "abc1234"}, True, id="commit-reference"),
    ],
)
def test_dispatch_report_requires_worktree_or_commit_reference(
    tmp_path, reference, expected
):
    ledger = TaskLedger(str(tmp_path))
    _record_dispatch(ledger)
    assert ledger.update_dispatch("dispatch-message-1", status="in_flight")

    accepted = ledger.record_dispatch_report(
        task_id="task-636",
        assignee="lapis",
        thread_id="dispatch-thread-1",
        reply_to="dispatch-message-1",
        message_id="report-with-reference",
        report="Task complete\nEvidence is ready for inspection.",
        **reference,
    )

    assert accepted is expected
    expected_status = "reported_unverified" if expected else "in_flight"
    assert ledger.get_dispatch("dispatch-message-1")["status"] == expected_status


@pytest.mark.asyncio
async def test_hub_assignment_is_persisted_before_routing(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator", identity="koordinator", agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = [
        AgentRuntime(name="coder", identity="lapis", agent_id="lapis-id")
    ]
    plugin._task_ledger = ledger
    plugin._route_message = AsyncMock()
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    plugin._resolve_scope = MagicMock(return_value=MessageScope.DIRECT.value)

    async def assert_durable_before_route(message):
        dispatch = ledger.get_dispatch(message.id)
        assert dispatch["status"] == "dispatching"
        assert dispatch["directive"] == "review Agent HUD and report back"
        assert dispatch["worktree"] == "/tmp/lane-636"
        assert dispatch["commit"] == "abc1234"
        assert message.metadata["task_id"] == "task-636"
        return []

    plugin._route_message.side_effect = assert_durable_before_route
    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-call-1", "_message_id": "dispatch-message-1",
            "to": "lapis", "content": "review Agent HUD and report back",
            "task_id": "task-636", "worktree": "/tmp/lane-636",
            "commit": "abc1234",
        }
    )

    assert result.success
    plugin._route_message.assert_awaited_once()
    assert ledger.get_dispatch("dispatch-message-1")["status"] == "in_flight"

    original_dispatch = ledger.get_dispatch("dispatch-message-1")
    plugin._route_message.reset_mock(side_effect=True)
    conflicting_replay = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-call-conflict",
            "_message_id": "dispatch-message-1",
            "to": "peridot",
            "content": "Task: inspect another file and report back.",
            "task_id": "task-conflict",
        }
    )
    assert not conflicting_replay.success
    assert "different immutable payload" in conflicting_replay.error
    plugin._route_message.assert_not_awaited()
    assert ledger.get_dispatch("dispatch-message-1") == original_dispatch

    async def assert_fallback_id_before_route(message):
        dispatch = ledger.get_dispatch(message.id)
        assert dispatch["task_id"] == message.id
        assert message.metadata["task_id"] == message.id
        assert any(
            item["task_id"] == message.id for item in ledger.pending_replies()
        )
        return []

    plugin._route_message.side_effect = assert_fallback_id_before_route
    fallback = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-call-fallback", "_message_id": "fallback-dispatch-id",
            "to": "lapis",
            "content": "Task: verify fallback task ID correlation and report back",
        }
    )
    assert fallback.success
    assert ledger.get_dispatch("fallback-dispatch-id")["status"] == "in_flight"

    async def reject_after_write(message):
        assert ledger.get_dispatch(message.id)["status"] == "dispatching"
        return [("lapis", "not connected")]

    plugin._route_message.side_effect = reject_after_write
    await plugin._handle_hub_msg_tool(
        {
            "id": "tool-call-2", "_message_id": "dispatch-message-2",
            "to": "lapis", "content": "review Agent HUD and report back with delivery status",
            "task_id": "task-637", "worktree": "/tmp/lane-637",
            "commit": "def5678",
        }
    )
    assert ledger.get_dispatch("dispatch-message-2")["status"] == "delivery_failed"

    plugin._route_message = AsyncMock(side_effect=RuntimeError("router unavailable"))
    failed_route = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-call-3", "_message_id": "dispatch-message-3",
            "to": "lapis", "content": "inspect dispatch route failure and report back",
            "task_id": "task-638", "worktree": "/tmp/lane-638",
            "commit": "feed123",
        }
    )
    assert not failed_route.success
    assert "router unavailable" in failed_route.error
    assert ledger.get_dispatch("dispatch-message-3")["status"] == "delivery_failed"

    plugin._route_message = AsyncMock()
    with patch.object(ledger, "record_dispatch", side_effect=OSError("disk full")):
        failed_write = await plugin._handle_hub_msg_tool(
            {
                "id": "tool-call-4", "_message_id": "dispatch-message-4",
                "to": "lapis", "content": "inspect dispatch lane and report back",
                "task_id": "task-639", "worktree": "/tmp/lane-639",
                "commit": "feed124",
            }
        )
    assert not failed_write.success
    plugin._route_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_dispatch_replay_is_not_routed(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator", identity="koordinator", agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = [
        AgentRuntime(name="coder", identity="lapis", agent_id="lapis-id")
    ]
    plugin._task_ledger = ledger
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    plugin._resolve_scope = MagicMock(return_value=MessageScope.DIRECT.value)

    payload = {
        "id": "dispatch-tool-call",
        "_message_id": "dispatch-replay-id",
        "_acp_hub": True,
        "to": "lapis",
        "content": "Task: review the patch and report back.",
        "task_id": "task-replay",
        "worktree": "/tmp/lane-replay",
        "commit": "abc1234",
    }
    sent = await plugin._handle_hub_msg_tool(payload)
    assert sent.success
    plugin._route_message.assert_awaited_once()

    dispatch = ledger.get_dispatch("dispatch-replay-id")

    plugin._route_message.reset_mock(side_effect=True)
    in_flight_replay = await plugin._handle_hub_msg_tool(
        {**payload, "id": "replay-in-flight", "thread_id": dispatch["thread_id"]}
    )
    assert in_flight_replay.success
    assert "not sent again" in in_flight_replay.output
    plugin._route_message.assert_not_awaited()
    assert ledger.get_dispatch("dispatch-replay-id")["status"] == "in_flight"

    assert ledger.record_dispatch_report(
        task_id="task-replay",
        assignee="lapis",
        thread_id=dispatch["thread_id"],
        reply_to="dispatch-replay-id",
        message_id="report-replay-id",
        report="Task complete\nInspected changes are ready.",
        worktree="/tmp/lane-replay",
        commit="abc1234",
    )

    for status in ("reported_unverified", "verified"):
        if status == "verified":
            assert ledger.mark_dispatch_verified(
                "dispatch-replay-id",
                verified_by="koordinator",
                note="Inspected the worktree and commit.",
            )
        plugin._route_message.reset_mock(side_effect=True)
        replay = await plugin._handle_hub_msg_tool(
            {**payload, "id": f"replay-{status}", "thread_id": dispatch["thread_id"]}
        )
        assert replay.success
        assert "not sent again" in replay.output
        plugin._route_message.assert_not_awaited()
        assert ledger.get_dispatch("dispatch-replay-id")["status"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wake_mode", "llm_available"),
    [("wake", True), ("queue", True), ("wake", False)],
)
async def test_inbound_report_updates_matching_dispatch_without_trusting_it(
    tmp_path, wake_mode, llm_available
):
    ledger = TaskLedger(str(tmp_path))
    _record_dispatch(ledger)
    ledger.update_dispatch("dispatch-message-1", status="in_flight")
    ledger.expect_reply(
        task_id="task-636", assignee="lapis", requested_by="koordinator",
        message_id="dispatch-message-1", deadline_seconds=120,
    )

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._task_ledger = ledger
    plugin._handle_stale_task_cron = AsyncMock(return_value=False)
    plugin._decide_hub_wake = lambda *_args, **_kwargs: HubWakeDecision(
        wake_mode, False, "test"
    )
    plugin._seen_messages = OrderedDict()
    plugin._presence = MagicMock()
    plugin._identity = MagicMock(identity="koordinator", agent_id="koordinator-id")
    plugin._vault = None
    llm = SimpleNamespace(
        conversation_history=[], is_processing=False, conversation_logger=None,
        current_parent_uuid=None,
    )

    class FakeEventBus:
        def get_service(self, name):
            return (
                llm
                if llm_available and name == "llm_service"
                else None
            )

        async def emit_with_hooks(self, *args, **kwargs):
            return None

    plugin.event_bus = FakeEventBus()
    plugin._history_lock = asyncio.Lock()
    plugin._seen_content_hashes = {}
    plugin._pending_hub_wake_ids = OrderedDict()
    plugin._display_hub_message = MagicMock()
    plugin._bridge = None
    plugin._change_feed = None
    plugin._route_message = AsyncMock(return_value=[])
    plugin._hub_buffer_retry_queued = False

    await plugin._on_message_received(
        HubMessage(
            action="message", id="report-message-1", from_agent="lapis-id",
            from_identity="lapis", to="koordinator",
            content="Task complete\ntests passed.",
            scope=MessageScope.DIRECT.value, thread_id="dispatch-thread-1",
            metadata={
                "task_id": "task-636", "worktree": "/tmp/lane-636",
                "commit": "def5678",
            },
        )
    )

    report = ledger.get_dispatch("dispatch-message-1")
    assert report["status"] == "reported_unverified"
    assert report["report_message_id"] == "report-message-1"
    assert report["reported_worktree"] == "/tmp/lane-636"
    assert report["reported_commit"] == "def5678"
    assert ledger.mark_dispatch_verified(
        "dispatch-message-1", verified_by="koordinator",
        note="Evidence is independently verified.",
    )


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, check=True, text=True,
    )


def test_fresh_coordinator_rebirth_recovers_dispatch_and_local_git_status(tmp_path):
    worktree = tmp_path / "worker-lane"
    worktree.mkdir()
    _git("init", "--quiet", cwd=worktree)
    (worktree / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=worktree)
    _git(
        "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "baseline", cwd=worktree,
    )
    (worktree / "tracked.txt").write_text("uncommitted edit\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text("new file\n", encoding="utf-8")

    ledger_dir = tmp_path / "hub-tasks"
    ledger = TaskLedger(str(ledger_dir))
    _record_dispatch(
        ledger, message_id="dispatch-in-flight", task_id="task-in-flight",
        assignee="lapis",
        directive="Finish the dispatch persistence implementation.",
        worktree=str(worktree), commit="abc1234",
    )
    ledger.update_dispatch("dispatch-in-flight", status="in_flight")

    _record_dispatch(
        ledger, message_id="dispatch-reported", task_id="task-reported",
        assignee="peridot",
        directive="Review the completed dispatch implementation.",
        worktree=str(tmp_path / "not-on-this-machine"), commit="123abcd",
    )
    ledger.update_dispatch("dispatch-reported", status="in_flight")
    ledger.record_dispatch_report(
        task_id="task-reported", assignee="peridot",
        thread_id="dispatch-thread-1", message_id="report-message-2",
        report="Task complete\nreview is ready.",
        worktree=str(worktree), commit="456def0",
    )
    _record_dispatch(
        ledger, message_id="dispatch-unavailable", task_id="task-unavailable",
        assignee="aquamarine",
        directive="Recover the remote lane after restart.",
        worktree=str(tmp_path / "not-on-this-machine"), commit="789abcd",
    )
    ledger.update_dispatch("dispatch-unavailable", status="in_flight")

    recovered_ledger = TaskLedger(str(ledger_dir))
    project_vaults = tmp_path / "project-vaults"
    global_vaults = tmp_path / "global-vaults"
    with (
        patch("plugins.hub.vault.get_vaults_dir", return_value=project_vaults),
        patch("plugins.hub.vault.get_global_vaults_dir", return_value=global_vaults),
    ):
        recovered_vault = AgentVault("koordinator")

    context = recovered_vault.get_rebirth_context(task_ledger=recovered_ledger)

    assert "task-in-flight" in context
    assert "lapis" in context
    assert "Finish the dispatch persistence implementation." in context
    assert str(worktree) in context
    assert "uncommitted files: 2" in context
    assert "task-reported" in context
    assert "reported_unverified" in context
    assert "reported commit: 456def0" in context
    assert f"reported worktree: {worktree}" in context
    assert f"live worktree: {worktree}" in context
    assert "worktree status unavailable" in context
    assert "verification checklist" in context.lower()
    assert "Do not trust a report" in context


def test_hub_msg_tool_exposes_lane_metadata():
    assert {"task_id", "worktree", "commit"}.issubset(set(hub_msg.xml_attributes))


def test_dispatch_verify_tool_has_explicit_dispatch_id():
    assert hub_dispatch_verify.xml_tag == "hub_dispatch_verify"
    assert hub_dispatch_verify.xml_attributes == ["dispatch_id"]


@pytest.mark.asyncio
async def test_dispatch_verify_handler_enforces_assigner_identity(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    _record_dispatch(ledger)
    ledger.update_dispatch("dispatch-message-1", status="in_flight")
    ledger.record_dispatch_report(
        task_id="task-636", assignee="lapis", thread_id="dispatch-thread-1",
        message_id="report-message-1", report="Task complete\ntests passed.",
        worktree="/tmp/lane-636",
    )

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._task_ledger = ledger
    plugin._identity = AgentRuntime(
        name="worker", identity="lapis", agent_id="lapis-id"
    )
    denied = await plugin._handle_hub_dispatch_verify_tool(
        {
            "id": "verify-call-1", "dispatch_id": "dispatch-message-1",
            "note": "Reviewed the diff and tests.",
        }
    )
    assert not denied.success

    plugin._identity = AgentRuntime(
        name="coordinator", identity="koordinator", agent_id="koordinator-id",
        is_coordinator=True,
    )
    accepted = await plugin._handle_hub_dispatch_verify_tool(
        {
            "id": "verify-call-2", "dispatch_id": "dispatch-message-1",
            "note": "Reviewed the diff and tests.",
        }
    )
    assert accepted.success
    assert ledger.get_dispatch("dispatch-message-1")["status"] == "verified"

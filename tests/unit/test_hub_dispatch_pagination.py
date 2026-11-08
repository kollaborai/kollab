"""Regression coverage for paginated coordinator dispatch recovery (#656).

Rebirth recovery expands only the newest dispatches. Beyond that, the
coordinator needs an opt-in, read-only way to list and read the rest.
"""

from unittest.mock import MagicMock, patch

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger
from plugins.hub.vault import AgentVault


def _record(ledger, index, assigned_by="koordinator", status="in_flight"):
    message_id = f"dispatch-{index:02d}"
    ledger.record_dispatch(
        task_id=f"task-{index:02d}",
        assigned_by=assigned_by,
        assignee="lapis",
        directive=f"Directive for lane {index:02d}.",
        message_id=message_id,
        worktree=f"/tmp/lane-{index:02d}",
        commit=f"c{index:04d}",
    )
    ledger.update_dispatch(message_id, status=status)
    return message_id


def _plugin(tmp_path, *, enabled, identity="koordinator"):
    config = {"plugins.hub.dispatch_list_enabled": enabled}
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.config = config
    plugin._task_ledger = TaskLedger(str(tmp_path))
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity=identity,
        agent_id=f"{identity}-id",
        is_coordinator=True,
    )
    return plugin


def _twelve_unresolved(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    for index in range(1, 13):
        _record(ledger, index)
    return ledger


@pytest.mark.asyncio
async def test_list_pages_through_all_unresolved_dispatches_without_repeats(tmp_path):
    _twelve_unresolved(tmp_path)
    plugin = _plugin(tmp_path, enabled=True)

    first = await plugin._handle_hub_dispatch_list_tool(
        {"id": "list-1", "page": "1", "per_page": "10"}
    )
    second = await plugin._handle_hub_dispatch_list_tool(
        {"id": "list-2", "page": "2", "per_page": "10"}
    )

    assert first.success and second.success
    assert "12 unresolved" in first.output
    assert "dispatch-12" in first.output and "dispatch-03" in first.output
    assert "dispatch-02" not in first.output
    assert "dispatch-02" in second.output and "dispatch-01" in second.output
    assert "dispatch-03" not in second.output
    assert "page 2 of 2" in second.output


@pytest.mark.asyncio
async def test_read_returns_full_record_for_dispatch_beyond_first_page(tmp_path):
    ledger = _twelve_unresolved(tmp_path)
    ledger.record_dispatch_report(
        task_id="task-01", assignee="lapis", thread_id="",
        message_id="report-01", report="Task complete\ntests passed.",
        worktree="/tmp/lane-01", commit="c0001",
    )
    plugin = _plugin(tmp_path, enabled=True)

    result = await plugin._handle_hub_dispatch_read_tool(
        {"id": "read-1", "dispatch_id": "dispatch-01"}
    )

    assert result.success
    assert "task-01" in result.output
    assert "Directive for lane 01." in result.output
    assert "/tmp/lane-01" in result.output
    assert "c0001" in result.output
    assert "tests passed" in result.output


@pytest.mark.asyncio
async def test_read_refuses_dispatch_assigned_by_another_coordinator(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    _record(ledger, 1, assigned_by="someone-else")
    plugin = _plugin(tmp_path, enabled=True)

    result = await plugin._handle_hub_dispatch_read_tool(
        {"id": "read-2", "dispatch_id": "dispatch-01"}
    )

    assert not result.success
    assert "dispatch-01" not in result.output
    assert "Directive" not in result.output


@pytest.mark.asyncio
async def test_list_and_read_are_read_only(tmp_path):
    ledger = _twelve_unresolved(tmp_path)
    before = ledger.get_dispatch("dispatch-05")
    plugin = _plugin(tmp_path, enabled=True)

    await plugin._handle_hub_dispatch_list_tool({"id": "l", "page": "1"})
    await plugin._handle_hub_dispatch_read_tool(
        {"id": "r", "dispatch_id": "dispatch-05"}
    )

    assert TaskLedger(str(tmp_path)).get_dispatch("dispatch-05") == before


@pytest.mark.asyncio
async def test_per_page_is_capped_and_out_of_range_page_is_explicit(tmp_path):
    _twelve_unresolved(tmp_path)
    plugin = _plugin(tmp_path, enabled=True)

    big = await plugin._handle_hub_dispatch_list_tool(
        {"id": "l", "page": "1", "per_page": "500"}
    )
    assert big.success
    assert big.output.count("dispatch-") == 12

    empty = await plugin._handle_hub_dispatch_list_tool(
        {"id": "l", "page": "9", "per_page": "10"}
    )
    assert empty.success
    assert "no dispatches on page 9" in empty.output


def test_pagination_tags_are_not_registered_when_disabled(tmp_path):
    response_parser = MagicMock()
    tool_executor = MagicMock()
    bus = MagicMock()
    bus.get_service.side_effect = lambda name: {
        "response_parser": response_parser,
        "tool_executor": tool_executor,
    }.get(name)

    for enabled, expected in ((False, False), (True, True)):
        response_parser.reset_mock()
        tool_executor.reset_mock()
        plugin = HubPlugin(event_bus=bus, config={
            "plugins.hub.dispatch_list_enabled": enabled,
        })
        plugin._register_pipeline_tools()
        registered = {c.args[0] for c in response_parser.register_plugin_tag.call_args_list}
        assert ("hub_dispatch_list" in registered) is expected
        assert ("hub_dispatch_read" in registered) is expected


def test_rebirth_overflow_names_paging_tools_only_when_enabled(tmp_path):
    ledger = _twelve_unresolved(tmp_path)
    with (
        patch("plugins.hub.vault.get_vaults_dir", return_value=tmp_path / "v"),
        patch("plugins.hub.vault.get_global_vaults_dir", return_value=tmp_path / "g"),
    ):
        vault = AgentVault("koordinator")

    off = vault.get_rebirth_context(task_ledger=ledger)
    assert "outbound dispatch reconciliation (12 unresolved)" in off
    assert "+2 older unresolved dispatches" in off
    assert "hub_dispatch_list" not in off

    on = vault.get_rebirth_context(task_ledger=ledger, dispatch_paging=True)
    assert "+2 older unresolved dispatches" in on
    assert "hub_dispatch_list" in on
    assert "hub_dispatch_read" in on

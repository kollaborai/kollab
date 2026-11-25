"""A proxy client cannot invent or select an option the harness did not offer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acp.schema import RequestPermissionResponse

from kollabor.acp_server import ClientSession, KollabACPAgent


def proxy(attached, selected):
    agent = KollabACPAgent()
    agent.connection = SimpleNamespace(request_permission=AsyncMock(return_value=
        RequestPermissionResponse.model_validate({"outcome": {"outcome": "selected", "optionId": selected}})))
    rpc = SimpleNamespace(call=AsyncMock(return_value={"ok": True}))
    engine = SimpleNamespace(daemon=SimpleNamespace(rpc=rpc))
    return agent, ClientSession("owned-client", engine, "/owned", attached), rpc


@pytest.mark.asyncio
@pytest.mark.parametrize("attached,options,source", [
    (False, [], "acp"),
    (False, [], None),
    (True, [{"optionId": "only-always", "name": "Always", "kind": "allow_always"}], "acp"),
])
async def test_empty_eligible_vendor_table_cancels_without_inventing_native_choices(attached, options, source):
    agent, session, rpc = proxy(attached, "once")
    details = {"permission_id": "owner-vendor-call", "acp_options": options}
    if source is not None:
        details["source"] = source
    await agent._permission(session, {"tool_id": "vendor-call", "permission_id": "owner-vendor-call", "details": details})
    agent.connection.request_permission.assert_not_awaited()
    assert rpc.call.call_args.args == ("permission.respond_owned", {"tool_id": "vendor-call", "permission_id": "owner-vendor-call", "control": "CANCEL"})


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["once", "session", "not-offered"])
async def test_non_offered_vendor_selection_cannot_fall_back_to_native_approval(selected):
    agent, session, rpc = proxy(False, selected)
    offered = [{"optionId": "opaque-yes", "name": "Allow", "kind": "allow_once"},
               {"optionId": "opaque-no", "name": "Deny", "kind": "reject_once"}]
    await agent._permission(session, {"tool_id": "vendor-call", "permission_id": "owner-vendor-call", "details": {
        "source": "acp", "permission_id": "owner-vendor-call", "acp_options": offered}})
    assert rpc.call.call_args.args == ("permission.respond_owned", {"tool_id": "vendor-call", "permission_id": "owner-vendor-call", "control": "CANCEL"})


@pytest.mark.asyncio
async def test_old_daemon_capability_failure_is_visible_and_cancels_the_exact_request():
    agent, session, rpc = proxy(False, "once")
    rpc.call.side_effect = RuntimeError("unknown RPC method permission.respond_owned")
    agent.connection.session_update = AsyncMock()
    await agent._permission(session, {
        "request_id": "request-owned",
        "tool_id": "vendor-call",
        "permission_id": "owner-vendor-call",
        "details": {"permission_id": "owner-vendor-call"},
    })
    assert rpc.call.call_args_list[0].args == (
        "permission.respond_owned",
        {"tool_id": "vendor-call", "permission_id": "owner-vendor-call", "control": "APPROVE_ONCE"},
    )
    assert rpc.call.call_args_list[1].args == (
        "state.acp_cancel", {"request_id": "request-owned"},
    )
    agent.connection.session_update.assert_awaited_once()

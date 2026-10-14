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
    rpc = SimpleNamespace(call=AsyncMock(return_value={}))
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
    details = {"acp_options": options}
    if source is not None:
        details["source"] = source
    await agent._permission(session, {"tool_id": "vendor-call", "details": details})
    agent.connection.request_permission.assert_not_awaited()
    assert rpc.call.call_args.args == ("permission.respond", {"tool_id": "vendor-call", "control": "CANCEL"})


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["once", "session", "not-offered"])
async def test_non_offered_vendor_selection_cannot_fall_back_to_native_approval(selected):
    agent, session, rpc = proxy(False, selected)
    offered = [{"optionId": "opaque-yes", "name": "Allow", "kind": "allow_once"},
               {"optionId": "opaque-no", "name": "Deny", "kind": "reject_once"}]
    await agent._permission(session, {"tool_id": "vendor-call", "details": {
        "source": "acp", "acp_options": offered}})
    assert rpc.call.call_args.args == ("permission.respond", {"tool_id": "vendor-call", "control": "CANCEL"})

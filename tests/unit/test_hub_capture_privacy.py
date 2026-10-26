"""Privacy gates for hub capture; fixtures use synthetic canary values only."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.permissions.risk_assessor import RiskAssessor
from kollabor_agent.tool_definitions.hub import hub_capture
from kollabor_events.permissions_models import RiskAssessmentRules, ToolRiskLevel
from plugins.hub.capture import normalize_records, render_transcript
from plugins.hub.messenger import AgentMessenger
from plugins.hub.plugin import HubPlugin

CANARY_ENV_SECRET = "SYNTHETIC_CANARY_ENV_VALUE_49c2"
CANARY_BEARER = "SYNTHETIC_CANARY_BEARER_7a10"
CANARY_PRIVATE_KEY = "SYNTHETIC_CANARY_PRIVATE_KEY_MATERIAL_235d"
REMOTE_HANDLE = "lapis@dev-device"
ATTACHED_SOCKET = "/tmp/kollab-privacy-test-attach.sock"


def _secret_events():
    return [
        {
            "type": "user_message",
            "text": f".env contents: APP_API_KEY={CANARY_ENV_SECRET}",
        },
        {"type": "token", "text": f"Authorization: Bearer {CANARY_BEARER}"},
        {
            "type": "tool_result",
            "tool_name": "terminal",
            "output": (
                "-----BEGIN PRIVATE KEY-----\n"
                f"{CANARY_PRIVATE_KEY}\n"
                "-----END PRIVATE KEY-----"
            ),
        },
    ]


def _minimal_plugin():
    return object.__new__(HubPlugin)


def test_transcript_redacts_env_auth_and_private_key_canaries_by_default():
    events = _secret_events()
    source = json.dumps(events)
    assert all(
        canary in source
        for canary in (CANARY_ENV_SECRET, CANARY_BEARER, CANARY_PRIVATE_KEY)
    )

    transcript = render_transcript(
        {"lines": normalize_records(events), "truncated": False},
        identity="lapis",
    )

    assert "REDACTED" in transcript.upper()
    assert CANARY_ENV_SECRET not in transcript
    assert CANARY_BEARER not in transcript
    assert CANARY_PRIVATE_KEY not in transcript


def test_plain_capture_redacts_sensitive_assignments_by_default():
    payload = {
        "format": "plain",
        "text": f"APP_SECRET={CANARY_ENV_SECRET}\nAuthorization: Bearer {CANARY_BEARER}",
        "truncated": False,
    }

    rendered = HubPlugin._render_plain_capture("lapis", payload)

    assert CANARY_ENV_SECRET not in rendered
    assert CANARY_BEARER not in rendered
    assert "REDACTED" in rendered.upper()


@pytest.mark.asyncio
async def test_explicit_terminal_raw_capture_preserves_synthetic_canary():
    plugin = _minimal_plugin()
    plugin._resolve_dial_target = MagicMock(
        return_value=("/tmp/kollab-privacy-test.sock", None)
    )
    peer = SimpleNamespace(
        identity="lapis", socket_path="/tmp/kollab-privacy-test.sock"
    )
    raw_line = f"APP_API_KEY={CANARY_ENV_SECRET}"
    request = AsyncMock(return_value=([raw_line], None))

    with patch.object(AgentMessenger, "request_output_diagnostic", request):
        outcome = await plugin._capture_peer_output(peer, 5, format="terminal")

    request.assert_awaited_once()
    assert CANARY_ENV_SECRET in outcome.text


@pytest.mark.asyncio
async def test_cross_device_capture_uses_trust_checked_network_attach_by_default():
    plugin = _minimal_plugin()
    plugin._relay_agent = SimpleNamespace(
        attach=AsyncMock(return_value=ATTACHED_SOCKET)
    )
    plugin._capture_peer_output = AsyncMock(
        return_value=SimpleNamespace(text="safe transcript", error=None, success=True)
    )

    outcome = await plugin._capture_target(REMOTE_HANDLE, 10, capture_format="json")

    assert outcome.text == "safe transcript"
    plugin._relay_agent.attach.assert_awaited_once_with(REMOTE_HANDLE)
    captured_peer = plugin._capture_peer_output.await_args.args[0]
    assert captured_peer.identity == REMOTE_HANDLE
    assert plugin._capture_peer_output.await_args.kwargs["socket_path_override"] == ATTACHED_SOCKET


@pytest.mark.asyncio
async def test_disallow_setting_refuses_cross_device_capture_before_attach():
    plugin = _minimal_plugin()
    plugin.config = SimpleNamespace(
        get=lambda key, default=None: key == "plugins.hub.disallow_cross_device_capture"
    )
    plugin._relay_agent = SimpleNamespace(attach=AsyncMock())

    outcome = await plugin._capture_target(REMOTE_HANDLE, 10, capture_format="json")

    assert not outcome.success
    assert "disallow_cross_device_capture" in outcome.text
    plugin._relay_agent.attach.assert_not_awaited()


@pytest.mark.parametrize(
    "refusal",
    [
        "network trust denied attach",
        "her-mac is a contact, not on this network: message its agents, "
        "opening them needs a join code",
    ],
)
@pytest.mark.asyncio
async def test_network_or_contact_refusal_stops_cross_device_capture(refusal):
    plugin = _minimal_plugin()
    plugin._relay_agent = SimpleNamespace(
        attach=AsyncMock(side_effect=PermissionError(refusal))
    )
    plugin._capture_peer_output = AsyncMock()

    outcome = await plugin._capture_target(REMOTE_HANDLE, 10, capture_format="json")

    assert not outcome.success
    assert refusal in outcome.text
    plugin._capture_peer_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_slash_capture_passes_raw_flag_for_a_remote_target():
    plugin = _minimal_plugin()
    plugin._capture_target = AsyncMock(
        return_value=SimpleNamespace(text="captured", error=None, success=True)
    )

    result = await plugin._handle_capture_command(f"{REMOTE_HANDLE} 7 --raw")

    assert result == "captured"
    assert plugin._capture_target.await_args.args == (REMOTE_HANDLE, 7)
    assert plugin._capture_target.await_args.kwargs == {"capture_format": "terminal"}


def test_hub_capture_tool_requires_permission_and_has_no_cross_device_option():
    assert hub_capture.requires_permission is True
    assert hub_capture.risk_level == "high"
    parameters = {parameter.name: parameter for parameter in hub_capture.parameters}
    assert "allow_cross_device" not in parameters
    assert parameters["raw"].type == "boolean"

    assessment = RiskAssessor(
        rules=RiskAssessmentRules(), config={}
    ).assess_tool({"type": "hub_capture", "name": "hub-capture"})
    assert assessment.level is ToolRiskLevel.HIGH
    assert assessment.requires_confirmation is True


@pytest.mark.asyncio
async def test_hub_capture_tool_captures_a_remote_target():
    plugin = _minimal_plugin()
    plugin._capture_target = AsyncMock(
        return_value=SimpleNamespace(text="captured", error=None, success=True)
    )
    tool_data = {
        "id": "privacy-tool-1",
        "input": {"name": REMOTE_HANDLE, "lines": 7, "raw": False},
    }

    result = await plugin._handle_hub_capture_tool(tool_data)

    assert result.success is True
    assert plugin._capture_target.await_args.args == (REMOTE_HANDLE, 7)
    assert plugin._capture_target.await_args.kwargs == {"capture_format": "json"}


def test_cli_disallow_flag_reads_global_then_local_config(tmp_path, monkeypatch):
    from kollabor import cli

    global_cfg = tmp_path / "global.json"
    global_cfg.write_text(json.dumps({"plugins": {"hub": {"disallow_cross_device_capture": True}}}))
    project = tmp_path / "project"
    (project / ".kollab").mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_existing_global_config_path", lambda: global_cfg
    )

    assert cli._hub_config_flag("disallow_cross_device_capture") is True
    (project / ".kollab" / "config.json").write_text(
        json.dumps({"plugins": {"hub": {"disallow_cross_device_capture": False}}})
    )
    assert cli._hub_config_flag("disallow_cross_device_capture") is False
    assert cli._hub_config_flag("missing_key") is False

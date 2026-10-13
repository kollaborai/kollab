import sys

import pytest

from kollabor.llm.acp_bridge import ACPError, ACPProfile, ACPRunner

FAKE_ADAPTER = """
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if req.get('method') == 'initialize':
        print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':{}}), flush=True)
    elif req.get('method') == 'session/new':
        print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':{'sessionId':'s1'}}), flush=True)
    elif req.get('method') == 'session/prompt':
        print(json.dumps({
            'jsonrpc': '2.0', 'method': 'session/update',
            'params': {'update': {'type': 'agent_message_chunk', 'text': 'hello '}},
        }), flush=True)
        print(json.dumps({
            'jsonrpc': '2.0', 'method': 'session/update',
            'params': {'update': {'type': 'tool_call', 'input': {'command': 'ignored'}}},
        }), flush=True)
        print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':{'stopReason':'end_turn'}}), flush=True)
    elif req.get('method') == 'session/cancel':
        break
"""


@pytest.mark.asyncio
async def test_profile_resolution_strips_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    command, env, cwd = profile.resolve()
    assert command[0] == sys.executable
    assert "OPENAI_API_KEY" not in env
    assert cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_runner_initializes_streams_answer_and_closes_process(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    result = await runner.prompt("hello")
    assert result.session_id == "s1"
    assert result.text == "hello "
    assert result.generation == 1
    assert result.end_frame["type"] == "turn_end"
    assert any(update["kind"] == "tool_call" for update in result.updates)
    await runner.close()
    assert runner.transport.process is None


def test_missing_adapter_is_a_hard_gate(tmp_path):
    profile = ACPProfile("missing", ("definitely-not-an-acp-adapter",), cwd=str(tmp_path))
    with pytest.raises(ACPError):
        profile.resolve()

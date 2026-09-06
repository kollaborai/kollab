import json
import subprocess
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _round_trip(proc: subprocess.Popen[str], message: dict) -> dict:
    assert proc.stdin is not None
    assert proc.stdout is not None

    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line
    return json.loads(line)


def test_mock_mcp_server_preserves_json_rpc_string_ids():
    proc = subprocess.Popen(
        [str(ROOT / "tests" / "tmux" / "mock_mcp_server.sh")],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        request_id = str(uuid.uuid4())
        response = _round_trip(
            proc,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {},
            },
        )

        assert response["id"] == request_id
        assert response["result"]["serverInfo"]["name"] == "mock-mcp-server"

        tool_id = str(uuid.uuid4())
        tool_response = _round_trip(
            proc,
            {
                "jsonrpc": "2.0",
                "id": tool_id,
                "method": "tools/call",
                "params": {
                    "name": "mock_echo",
                    "arguments": {"message": "runtime"},
                },
            },
        )

        assert tool_response["id"] == tool_id
        assert tool_response["result"]["content"][0]["text"] == (
            "Mock response from mock_echo"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)

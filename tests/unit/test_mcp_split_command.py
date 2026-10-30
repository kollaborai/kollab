"""split_command: POSIX unchanged; Windows keeps backslashes and finds .cmd launchers (#428)."""

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from kollabor_agent.mcp_integration import MCPServerConnection, split_command

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="Windows: PATHEXT and backslash paths"
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX split")
@pytest.mark.parametrize(
    "command",
    [
        "npx -y @modelcontextprotocol/server-memory",
        "node '/srv/my server.js' --port 1",
        'python -m "pkg.mod"',
    ],
)
def test_posix_split_is_shlex(command):
    assert split_command(command) == shlex.split(command)


@pytest.fixture
def shim(tmp_path, monkeypatch):
    path = tmp_path / "fakemcp.cmd"
    path.write_bytes(b"@echo off\r\necho %~1\r\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return path


@windows_only
def test_windows_finds_the_cmd_launcher_and_keeps_backslashes(shim):
    parts = split_command(r'fakemcp C:\srv\mcp\server.js "C:\Program Files\x y"')
    assert Path(parts[0]).name.lower() == "fakemcp.cmd"
    assert parts[1:] == [r"C:\srv\mcp\server.js", r"C:\Program Files\x y"]
    out = subprocess.run(parts[:2], capture_output=True, text=True, check=True).stdout
    assert out.strip() == r"C:\srv\mcp\server.js"


@windows_only
def test_windows_connect_starts_a_cmd_launcher(shim):
    async def start() -> int:
        connection = MCPServerConnection("fake", "fakemcp hello")
        assert await connection.connect()
        assert connection.process is not None
        return await connection.process.wait()

    assert asyncio.run(start()) == 0

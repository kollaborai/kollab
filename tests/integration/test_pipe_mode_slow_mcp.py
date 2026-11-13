"""Exercise pipe-mode startup against a real, deliberately slow MCP server."""

import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

import psutil
import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]
MCP_DISCOVERY_DELAY_SECONDS = 13
PACKAGE_SOURCE_DIRS = (
    "packages/kollabor-ai/src",
    "packages/kollabor-tui/src",
    "packages/kollabor-events/src",
    "packages/kollabor-config/src",
    "packages/kollabor-agent/src",
    "packages/kollabor-plugins/src",
    "packages/kollabor-engine/src",
    "packages/kollabor-rpc/src",
    "packages/kollabor-webui/src",
    "packages/kollabor-voice/src",
)


def _tool_names(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return set()

    names: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            continue
        function: object = item.get("function")
        if isinstance(function, dict):
            name: object = function.get("name")
        else:
            name = item.get("name", "")
        if isinstance(name, str):
            names.add(name)
    return names


def _terminate_process_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        pass
    else:
        children = root.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(children, timeout=5)


@pytest.mark.asyncio
async def test_pipe_mode_sends_builtin_tools_while_stdio_mcp_discovery_is_slow() -> (
    None
):
    home_root = tempfile.TemporaryDirectory(prefix="kollab-home-pr899-")
    home = Path(home_root.name)
    workspace_root = tempfile.TemporaryDirectory(prefix="kollab-pr899-")
    workspace = Path(workspace_root.name) / "workspace"
    marker_dir = home / "mcp-markers"
    config_dir = home / ".kollab"
    mcp_config_dir = config_dir / "mcp"
    for directory in (config_dir, mcp_config_dir, workspace, marker_dir):
        directory.mkdir(parents=True, exist_ok=True)

    first_request: asyncio.Future[dict[str, object]] = (
        asyncio.get_running_loop().create_future()
    )
    release_response = asyncio.Event()
    server_started = marker_dir / "server-process-started"
    discovery_started = marker_dir / "tools-list-started"
    discovery_finished = marker_dir / "tools-list-finished"

    async def list_models(_request: web.Request) -> web.Response:
        return web.json_response(
            {"object": "list", "data": [{"id": "pipe-test", "object": "model"}]}
        )

    async def complete_chat(request: web.Request) -> web.Response:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest()
        if not first_request.done():
            first_request.set_result(
                {
                    "payload": payload,
                    "server_started": server_started.is_file(),
                    "discovery_started": discovery_started.is_file(),
                    "discovery_finished": discovery_finished.is_file(),
                }
            )
        await release_response.wait()
        return web.json_response(
            {
                "id": "pipe-test-response",
                "object": "chat.completion",
                "created": 1,
                "model": "pipe-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "pipe test complete",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )

    api = web.Application()
    api.router.add_get("/v1/models", list_models)
    api.router.add_post("/v1/chat/completions", complete_chat)
    runner = web.AppRunner(api)
    await runner.setup()
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen()
    site = web.SockSite(runner, server_socket)
    await site.start()
    endpoint = f"http://127.0.0.1:{server_socket.getsockname()[1]}/v1"

    config = {
        "kollabor": {
            "llm": {
                "default_profile": "pipe-test",
                "active_profile": "pipe-test",
                "profiles": {
                    "pipe-test": {
                        "provider": "openai",
                        "model": "pipe-test",
                        "base_url": endpoint,
                        "api_key": "test-key",
                        "streaming": False,
                        "supports_tools": True,
                    }
                },
            }
        },
        "plugins": {"hub": {"enabled": False}, "mcp": {"enabled": True}},
    }
    (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    mcp_settings = {
        "servers": {
            "slow-test": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    str(ROOT / "tests" / "integration" / "slow_mcp_server.py"),
                    str(marker_dir),
                    str(MCP_DISCOVERY_DELAY_SECONDS),
                ],
                "enabled": True,
            }
        }
    }
    (mcp_config_dir / "mcp_settings.json").write_text(
        json.dumps(mcp_settings), encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "KOLLAB_NO_KEYRING": "1",
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT), *(str(ROOT / source) for source in PACKAGE_SOURCE_DIRS)]
            ),
            "TERM": "dumb",
        }
    )
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[bytes] | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(ROOT / "main.py"),
            "-p",
            "--agent",
            "coder",
            cwd=workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(process.stdout.read())
        stderr_task = asyncio.create_task(process.stderr.read())
        process.stdin.write(b"check tools during slow MCP discovery")
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

        try:
            observation = await asyncio.wait_for(first_request, timeout=20)
        except asyncio.TimeoutError as exc:
            release_response.set()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                _terminate_process_tree(process.pid)
                process.terminate()
                await process.wait()
            stdout = (await stdout_task).decode(errors="replace")
            stderr = (await stderr_task).decode(errors="replace")
            raise AssertionError(
                "pipe-mode CLI never reached the fake provider; "
                f"server_started={server_started.is_file()}, "
                f"discovery_started={discovery_started.is_file()}, "
                f"discovery_finished={discovery_finished.is_file()}, "
                f"returncode={process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            ) from exc
        assert (
            observation["server_started"] is True
        ), "configured MCP process never started before the first model request"
        assert observation["discovery_started"] is True
        assert observation["discovery_finished"] is False
        names = _tool_names(observation["payload"])
        assert "file_read" in names
        assert "slow_mcp_probe" not in names

    finally:
        release_response.set()
        if process is not None and process.returncode is None:
            _terminate_process_tree(process.pid)
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                await process.wait()
        if stdout_task is not None and not stdout_task.done():
            await stdout_task
        if stderr_task is not None and not stderr_task.done():
            await stderr_task
        await runner.cleanup()
        workspace_root.cleanup()
        home_root.cleanup()

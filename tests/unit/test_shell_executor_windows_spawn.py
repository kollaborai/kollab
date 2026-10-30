"""Unit tests for ShellExecutor._create_windows_process via the resolved agent shell.

No real Windows and no real processes: every spawn is a patched
``asyncio.create_subprocess_exec`` capturing the argv/kwargs it was called with.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kollabor_agent import shell_executor as se
from kollabor_agent.shell_utils import POWERSHELL_PREFIX, AgentShell, AgentShellError

PWSH = AgentShell(r"C:\Program Files\PowerShell\7\pwsh.exe", "pwsh")
BASH = AgentShell(r"C:\Program Files\Git\bin\bash.exe", "git-bash")


@pytest.fixture(autouse=True)
def _windows_creationflags(monkeypatch):
    """POSIX Pythons lack the Windows creationflags constants the executor reads."""
    if not hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        monkeypatch.setattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
        )
    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = 0
        self.stdin = None
        self.stdout = None
        self.stderr = None

    @staticmethod
    async def communicate():
        return b"hi", b""


def capture_exec(monkeypatch, shell, fail=False):
    seen = {}

    async def fake_exec(*argv, **kwargs):
        if fail:
            raise OSError("boom")
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(se.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(se, "agent_shell", lambda: shell)
    return seen


def windows_executor(**kwargs):
    executor = se.ShellExecutor(**kwargs)
    executor._is_windows = True
    return executor


def test_pwsh_routing_and_flags(monkeypatch):
    seen = capture_exec(monkeypatch, PWSH)
    executor = windows_executor()

    result = asyncio.run(executor.run("echo hi"))

    assert result.success is True
    argv = seen["argv"]
    assert argv[0] == r"C:\Program Files\PowerShell\7\pwsh.exe"
    assert argv[-2:] == ("-Command", POWERSHELL_PREFIX + "echo hi")
    assert "-NoProfile" in argv
    assert argv[argv.index("-ExecutionPolicy") + 1] == "Bypass"
    kwargs = seen["kwargs"]
    assert kwargs["creationflags"] == (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    )
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    assert "start_new_session" not in kwargs


def test_git_bash_routing(monkeypatch):
    seen = capture_exec(monkeypatch, BASH)
    executor = windows_executor()

    result = asyncio.run(executor.run("echo hi"))

    assert result.success is True
    assert seen["argv"] == (r"C:\Program Files\Git\bin\bash.exe", "-c", "echo hi")

    seen = capture_exec(monkeypatch, BASH)
    executor = windows_executor(interactive=True)

    result = asyncio.run(executor.run("echo hi"))

    assert result.success is True
    assert seen["argv"] == (r"C:\Program Files\Git\bin\bash.exe", "-i", "-c", "echo hi")


def test_windows_path_cleanup_without_mutation(monkeypatch):
    seen = capture_exec(monkeypatch, BASH)
    executor = windows_executor()
    env = {"PATH": r".;rel\C:\;C:\proj;C:\proj\bin;C:\Windows;C:\Tools"}

    result = asyncio.run(executor.run("echo hi", cwd=Path(r"C:\proj"), env=env))

    assert result.success is True
    assert seen["kwargs"]["env"]["PATH"] == r"C:\Windows;C:\Tools"
    assert env["PATH"] == r".;rel\C:\;C:\proj;C:\proj\bin;C:\Windows;C:\Tools"


def test_agent_shell_error_fails_the_command(monkeypatch):
    def broken_shell():
        raise AgentShellError(
            "KOLLAB_SHELL='C:/bad/shell.exe' cannot run agent commands: no such file"
        )

    monkeypatch.setattr(se, "agent_shell", broken_shell)
    executor = windows_executor()

    result = asyncio.run(executor.run("echo hi"))

    assert result.success is False
    assert result.exit_code == -1
    assert "KOLLAB_SHELL" in result.error


def test_spawn_oserror_wrapped_with_kollab_shell_hint(monkeypatch):
    capture_exec(monkeypatch, PWSH, fail=True)
    executor = windows_executor()

    result = asyncio.run(executor.run("echo hi"))

    assert result.success is False
    assert r"C:\Program Files\PowerShell\7\pwsh.exe" in result.error
    assert "KOLLAB_SHELL" in result.error


def test_posix_spawn_unchanged(monkeypatch):
    seen = capture_exec(monkeypatch, BASH)
    executor = se.ShellExecutor()
    executor._is_windows = False

    proc = asyncio.run(
        executor._create_unix_process("echo hi", Path("/tmp"), {"SHELL": "/bin/zsh"})
    )

    assert proc.pid == 4242
    assert seen["argv"] == ("/bin/zsh", "-c", "echo hi")
    assert seen["kwargs"]["start_new_session"] is True

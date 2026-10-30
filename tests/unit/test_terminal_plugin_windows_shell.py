"""Windows shell routing for /terminal sessions (issue #438, lane 5).

The two spawn sites and _kill_process must use the resolved Windows shell
(Git Bash / PowerShell, never cmd.exe) with proc_ctl.kill_tree, while POSIX
behavior stays byte-identical.  No real processes are started: subprocess.Popen
is monkeypatched with a recorder.
"""

import asyncio
import io
import signal
from types import SimpleNamespace

import pytest

from kollabor_agent.shell_utils import POWERSHELL_PREFIX, AgentShell, AgentShellError
from plugins import terminal_plugin as tp
from plugins.terminal_plugin import TmuxPlugin

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"

CREATE_NEW_PROCESS_GROUP = 512
CREATE_NO_WINDOW = 0x08000000


class _RecordingPopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return SimpleNamespace(
            pid=99,
            poll=lambda: 0,
            stdin=None,
            stdout=io.BytesIO(),
            stderr=None,
            wait=lambda timeout=None: 0,
            terminate=lambda: None,
            kill=lambda: None,
        )


def _patch_windows_popen_flags(monkeypatch):
    # CREATE_NEW_PROCESS_GROUP / CREATE_NO_WINDOW only exist on real Windows;
    # define them on the subprocess module for the duration of the test.
    monkeypatch.setattr(
        tp.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        CREATE_NEW_PROCESS_GROUP,
        raising=False,
    )
    monkeypatch.setattr(
        tp.subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False
    )


# ------------------------------------------------------------------
# 1. _windows_terminal_cmd
# ------------------------------------------------------------------


def test_windows_terminal_cmd_argv(monkeypatch):
    monkeypatch.setattr(tp, "agent_shell", lambda: AgentShell(GIT_BASH, "git-bash"))
    assert tp._windows_terminal_cmd(None) == [GIT_BASH, "-i"]

    monkeypatch.setattr(tp, "agent_shell", lambda: AgentShell(PWSH, "pwsh"))
    assert tp._windows_terminal_cmd(None) == [PWSH, "-NoLogo"]

    argv = tp._windows_terminal_cmd("git status")
    assert argv == [
        PWSH,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        POWERSHELL_PREFIX + "git status",
    ]


# ------------------------------------------------------------------
# 2. Spawn site routing (both sites, both platforms)
# ------------------------------------------------------------------


def test_execute_background_windows_routes_to_resolved_shell(monkeypatch):
    plugin = TmuxPlugin()
    popen = _RecordingPopen()
    monkeypatch.setattr(tp, "agent_shell", lambda: AgentShell(GIT_BASH, "git-bash"))
    monkeypatch.setattr(tp.subprocess, "Popen", popen)
    _patch_windows_popen_flags(monkeypatch)
    monkeypatch.setattr(tp.os, "name", "nt")

    result = asyncio.run(plugin.execute_background(command=None, name="win"))

    assert result["success"] is True
    argv, kwargs = popen.calls[0]
    assert argv == tp._windows_terminal_cmd(None) == [GIT_BASH, "-i"]
    assert kwargs["start_new_session"] is False
    assert kwargs["creationflags"] == CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def test_execute_background_posix_unchanged(monkeypatch):
    plugin = TmuxPlugin()
    popen = _RecordingPopen()
    monkeypatch.setattr(tp.subprocess, "Popen", popen)
    monkeypatch.setattr(tp.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    result = asyncio.run(plugin.execute_background(command="echo hi", name="posix"))

    assert result["success"] is True
    argv, kwargs = popen.calls[0]
    assert argv == ["echo", "hi"]  # shlex.split(command), as before
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_handle_new_session_windows_routes_to_resolved_shell(monkeypatch):
    plugin = TmuxPlugin()
    popen = _RecordingPopen()
    monkeypatch.setattr(tp, "agent_shell", lambda: AgentShell(GIT_BASH, "git-bash"))
    monkeypatch.setattr(tp.subprocess, "Popen", popen)
    _patch_windows_popen_flags(monkeypatch)
    monkeypatch.setattr(tp.os, "name", "nt")

    result = asyncio.run(plugin._handle_new_session(["win"]))

    assert result.success is True
    argv, kwargs = popen.calls[0]
    assert argv == [GIT_BASH, "-i"]  # the resolver, not $SHELL / cmd.exe
    assert kwargs["start_new_session"] is False
    assert kwargs["creationflags"] == CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def test_handle_new_session_posix_uses_shell_env(monkeypatch):
    plugin = TmuxPlugin()
    popen = _RecordingPopen()
    monkeypatch.setattr(tp.subprocess, "Popen", popen)
    monkeypatch.setattr(tp.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    result = asyncio.run(plugin._handle_new_session(["only"]))

    assert result.success is True
    argv, kwargs = popen.calls[0]
    assert argv == ["/bin/zsh"]  # shlex.split($SHELL), as before
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


# ------------------------------------------------------------------
# 3. Kill path
# ------------------------------------------------------------------


def _fake_session():
    proc = SimpleNamespace(
        pid=99,
        poll=lambda: None,
        stdin=None,
        wait=lambda timeout=None: 0,
        terminate=lambda: None,
        kill=lambda: None,
    )
    return SimpleNamespace(proc=proc, name="s")


def test_kill_process_windows_uses_kill_tree(monkeypatch):
    plugin = TmuxPlugin()
    recorded = []
    monkeypatch.setattr(tp.os, "name", "nt")
    monkeypatch.setattr(
        tp.proc_ctl, "kill_tree", lambda pid, sig: recorded.append((pid, sig))
    )

    def _no_killpg(*args, **kwargs):
        raise AssertionError("os.killpg must not be called on Windows")

    def _no_getpgid(pid):
        raise AssertionError("os.getpgid must not be called on Windows")

    monkeypatch.setattr(tp.os, "killpg", _no_killpg)
    monkeypatch.setattr(tp.os, "getpgid", _no_getpgid)

    assert plugin._kill_process(_fake_session()) is True
    assert recorded == [(99, signal.SIGTERM)]


def test_kill_process_posix_killpg(monkeypatch):
    plugin = TmuxPlugin()
    kills = []
    monkeypatch.setattr(tp.os, "name", "posix")
    monkeypatch.setattr(
        tp.proc_ctl, "kill_tree", lambda pid, sig: kills.append(("tree", pid))
    )
    monkeypatch.setattr(tp.os, "getpgid", lambda pid: 99)
    monkeypatch.setattr(tp.os, "killpg", lambda pgid, sig: kills.append((pgid, sig)))

    assert plugin._kill_process(_fake_session()) is True
    assert ("tree", 99) not in kills
    assert (99, signal.SIGTERM) in kills


# ------------------------------------------------------------------
# 4. Resolver failure surfaces (never a silent cmd.exe fallback)
# ------------------------------------------------------------------


def _agent_shell_error():
    raise AgentShellError(
        "KOLLAB_SHELL='nope' cannot run agent commands: it must be the absolute "
        "path of an existing bash.exe, pwsh.exe or powershell.exe, and not WSL's "
        "System32 bash.exe."
    )


def test_windows_resolver_failure_surfaces(monkeypatch):
    monkeypatch.setattr(tp, "agent_shell", _agent_shell_error)
    with pytest.raises(AgentShellError) as exc:
        tp._windows_terminal_cmd(None)
    assert "KOLLAB_SHELL" in str(exc.value)

    plugin = TmuxPlugin()
    popen = _RecordingPopen()
    monkeypatch.setattr(tp.subprocess, "Popen", popen)
    monkeypatch.setattr(tp.os, "name", "nt")

    result = asyncio.run(plugin.execute_background(command=None, name="bad"))

    assert result["success"] is False
    assert "KOLLAB_SHELL" in result["message"]
    assert popen.calls == []  # nothing was started, let alone cmd.exe

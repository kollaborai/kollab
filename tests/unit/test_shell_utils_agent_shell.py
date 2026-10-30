"""Unit tests for the Windows agent-shell resolver in kollabor_agent.shell_utils."""

import pytest

from kollabor_agent.shell_utils import (
    POWERSHELL_PREFIX,
    AgentShell,
    AgentShellError,
    agent_shell,
    windows_path_entries,
)

CWD = r"C:\proj"
PS51 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def files(*paths):
    """An is_file stub accepting exactly the given paths."""
    known = set(paths)
    return lambda p: p in known


def win(environ, is_file):
    """Run agent_shell as a Windows resolve with a stubbed environment."""
    return agent_shell(environ=environ, cwd=CWD, system="Windows", is_file=is_file)


def test_posix_returns_shell_env_with_bin_sh_fallback():
    shell = agent_shell(environ={"SHELL": "/bin/zsh"}, system="Darwin")
    assert shell == AgentShell("/bin/zsh", "posix")
    assert agent_shell(environ={}, system="Linux") == AgentShell("/bin/sh", "posix")


def test_kollab_shell_wins_over_program_files_git_bash():
    environ = {
        "SystemRoot": r"C:\Windows",
        "KOLLAB_SHELL": r"C:\Tools\pwsh.exe",
        "ProgramFiles": r"C:\Program Files",
    }
    shell = win(
        environ,
        files(r"C:\Tools\pwsh.exe", r"C:\Program Files\Git\bin\bash.exe"),
    )
    assert shell == AgentShell(r"C:\Tools\pwsh.exe", "pwsh")


@pytest.mark.parametrize(
    ("value", "exists"),
    [
        ("pwsh.exe", False),
        (r"C:\Windows\System32\cmd.exe", True),
        (r"C:\Windows\System32\bash.exe", True),
        (r"C:\Tools\pwsh.exe", False),
    ],
)
def test_kollab_shell_refused_names_itself_and_value(value, exists):
    environ = {"SystemRoot": r"C:\Windows", "KOLLAB_SHELL": value}
    with pytest.raises(AgentShellError) as excinfo:
        win(environ, files(value) if exists else files())
    message = str(excinfo.value)
    assert "KOLLAB_SHELL" in message
    assert f"{value!r}" in message


def test_program_files_git_bash():
    environ = {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"}
    shell = win(environ, files(r"C:\Program Files\Git\bin\bash.exe"))
    assert shell == AgentShell(r"C:\Program Files\Git\bin\bash.exe", "git-bash")


def test_localappdata_git_bash_beats_powershell_7():
    environ = {
        "SystemRoot": r"C:\Windows",
        "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
    }
    shell = win(
        environ,
        files(
            r"C:\Users\me\AppData\Local\Programs\Git\bin\bash.exe",
            r"C:\Program Files\PowerShell\7\pwsh.exe",
        ),
    )
    assert shell.kind == "git-bash"


def test_git_bash_next_to_git_exe_on_path():
    environ = {"SystemRoot": r"C:\Windows", "PATH": r"C:\Tools\Git\cmd"}
    shell = win(
        environ, files(r"C:\Tools\Git\cmd\git.exe", r"C:\Tools\Git\bin\bash.exe")
    )
    assert shell == AgentShell(r"C:\Tools\Git\bin\bash.exe", "git-bash")


def test_windows_path_entries_drops_empty_relative_cwd_and_quoted():
    value = r';.;rel\dir;C:\proj;C:\proj\bin;"C:\Tools";C:\Other'
    assert windows_path_entries(value, CWD) == [r"C:\Tools", r"C:\Other"]


def test_entries_inside_cwd_are_never_searched():
    environ = {"SystemRoot": r"C:\Windows", "PATH": r"C:\proj\bin"}
    shell = win(
        environ,
        files(
            r"C:\proj\bin\git.exe",
            r"C:\proj\bin\bash.exe",
            PS51,
        ),
    )
    assert shell == AgentShell(PS51, "powershell")


def test_pwsh_on_path():
    apps = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps"
    environ = {"SystemRoot": r"C:\Windows", "PATH": apps}
    assert win(environ, files(apps + r"\pwsh.exe")).kind == "pwsh"


def test_windows_powershell_51_is_the_last_resort():
    environ = {"SystemRoot": r"C:\Windows"}
    assert win(environ, files(PS51)) == AgentShell(PS51, "powershell")


def test_no_shell_anywhere_raises():
    with pytest.raises(AgentShellError):
        win({"SystemRoot": r"C:\Windows"}, files())


def test_argv_posix_and_git_bash():
    posix = AgentShell("/bin/zsh", "posix")
    assert posix.argv("ls", interactive=True) == ["/bin/zsh", "-i", "-c", "ls"]
    assert posix.argv("ls") == ["/bin/zsh", "-c", "ls"]
    bash = AgentShell(r"C:\Program Files\Git\bin\bash.exe", "git-bash")
    assert bash.argv("ls") == [r"C:\Program Files\Git\bin\bash.exe", "-c", "ls"]


def test_argv_powershell_flags_and_prefix():
    pwsh = AgentShell(r"C:\Program Files\PowerShell\7\pwsh.exe", "pwsh")
    argv = pwsh.argv("Write-Output hi")
    assert "-NoProfile" in argv
    assert argv[-2:] == ["-Command", POWERSHELL_PREFIX + "Write-Output hi"]


def test_labels_and_rules():
    assert "&& and || stop on a failure" in AgentShell("", "pwsh").rule
    assert "if ($?) { cmd2 }" in AgentShell("", "powershell").rule
    posix = AgentShell("/bin/zsh", "posix")
    assert posix.rule == ""
    assert posix.label == "zsh"
    bash = AgentShell(r"C:\Program Files\Git\bin\bash.exe", "git-bash")
    assert bash.label == "Git Bash"

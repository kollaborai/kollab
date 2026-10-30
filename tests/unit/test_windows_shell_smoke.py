"""Unit tests for the pure helpers of scripts/windows_shell_smoke.py."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows_shell_smoke.py"
_SPEC = importlib.util.spec_from_file_location("windows_shell_smoke", _SCRIPT)
smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke)

PWSH_PROMPT = (
    "  shell:             PowerShell 7 (pwsh): write PowerShell, not bash. ...\n"
)
POSIX_PROMPT = "  shell:             /bin/zsh\n"


def test_parse_args_accepts_expect_and_command():
    args = smoke.parse_args(["--expect", "pwsh", "--command", "Write-Output hi"])
    assert args.expect == "pwsh"
    assert args.command == "Write-Output hi"


def test_parse_args_rejects_a_bogus_expect(capsys):
    with pytest.raises(SystemExit) as excinfo:
        smoke.parse_args(["--expect", "bogus", "--command", "x"])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_shell_line_names_accepts_the_matching_label():
    assert smoke.shell_line_names(PWSH_PROMPT, "pwsh") is True


def test_shell_line_names_rejects_the_wrong_windows_shell():
    assert smoke.shell_line_names(PWSH_PROMPT, "powershell") is False


def test_shell_line_names_rejects_a_posix_path_line():
    assert smoke.shell_line_names(POSIX_PROMPT, "git-bash") is False


def test_find_trender_errors_flags_an_error_line():
    rendered = "fine\n[trender error: command exited with code 1]\nalso fine\n"
    assert smoke.find_trender_errors(rendered) == [
        "[trender error: command exited with code 1]"
    ]


def test_find_trender_errors_passes_a_clean_prompt():
    assert smoke.find_trender_errors("a clean prompt\n") == []

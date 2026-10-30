"""The `session` trender renders session facts in Python on every OS.

Lane 3 of the Windows shell work: <trender type="session" field="..." /> replaces
the five bash trenders at the top of 01-session-context.md, and raw bash trenders
route through Git Bash (or render nothing under PowerShell) on Windows. Run with
PYTHONPATH=packages/kollabor-ai/src so the edited copy of prompt_renderer.py is
imported instead of the canonical dev install's.
"""

from __future__ import annotations

import getpass
import os
import platform
import socket
from pathlib import Path
from types import SimpleNamespace

import kollabor_ai.prompt_renderer as prompt_renderer_module
from kollabor_ai.prompt_renderer import PromptRenderer

LANE_ROOT = Path(__file__).resolve().parents[2]
BASE_BUNDLE = LANE_ROOT / "bundles" / "agents" / "_base"


def _renderer(shell_resolver=None):
    return PromptRenderer(
        timeout=5,
        base_path=BASE_BUNDLE,
        shell_resolver=shell_resolver,
    )


def _posix_shell():
    return SimpleNamespace(path="/bin/zsh", kind="posix", label="zsh", rule="")


def test_posix_shell_line_is_byte_compatible():
    renderer = _renderer(shell_resolver=lambda: _posix_shell())

    out = renderer.render('<trender type="session" field="shell" />')

    assert out == "/bin/zsh"


def test_git_bash_line_carries_label_and_rule():
    rule = (
        "Git Bash on Windows: write bash. Quote Windows paths and use forward "
        "slashes (C:/Users/...). Native Windows switches need a doubled slash."
    )
    shell = SimpleNamespace(
        path="C:/Program Files/Git/bin/bash.exe",
        kind="git-bash",
        label="Git Bash",
        rule=rule,
    )
    renderer = _renderer(shell_resolver=lambda: shell)

    out = renderer.render('<trender type="session" field="shell" />')

    assert out.startswith("Git Bash\n")
    assert rule in out


def test_full_posix_block_equals_todays_output(monkeypatch):
    """The new session block renders exactly what the bash trenders printed."""
    monkeypatch.setattr(
        prompt_renderer_module.time, "strftime", lambda fmt: "2026-10-30 MST"
    )
    monkeypatch.setenv("PWD", os.getcwd())
    renderer = _renderer(shell_resolver=lambda: _posix_shell())

    section_path = BASE_BUNDLE / "sections" / "01-session-context.md"
    section = section_path.read_text(encoding="utf-8")
    rendered = renderer.render(section)

    expected_block = (
        "session context:\n"
        "  date:              2026-10-30 MST\n"
        f"  system:            {platform.system()} {platform.machine()}\n"
        f"  user:              {getpass.getuser()} @ {socket.gethostname()}\n"
        "  shell:             /bin/zsh\n"
        f"  working directory: {os.getcwd()}"
    )
    assert expected_block in rendered


def test_pwsh_renders_bash_blocks_as_nothing():
    shell = SimpleNamespace(
        path="C:/Program Files/PowerShell/7/pwsh.exe",
        kind="pwsh",
        label="PowerShell 7",
        rule="",
    )
    renderer = _renderer(shell_resolver=lambda: shell)

    assert renderer.render("<trender>uname -s</trender>") == ""
    assert renderer.render("<trender>date '+%Y-%m-%d %Z'</trender>") == ""
    assert "[trender error" not in renderer.render("<trender>uname -s</trender>")


def test_git_bash_routes_raw_trender_through_argv(monkeypatch):
    captured = {}

    def fake_run(cmd_args, **kwargs):
        captured["argv"] = tuple(cmd_args)
        return SimpleNamespace(stdout="Darwin\n", stderr="", returncode=0)

    monkeypatch.setattr(prompt_renderer_module.subprocess, "run", fake_run)

    shell = SimpleNamespace(
        path="C:/Program Files/Git/bin/bash.exe",
        kind="git-bash",
        label="Git Bash",
        rule="",
        argv=lambda command, interactive=False: ["/bin/bash", "-c", command],
    )
    renderer = _renderer(shell_resolver=lambda: shell)

    out = renderer.render("<trender>uname -s</trender>")

    assert out == "Darwin"
    assert captured["argv"] == ("/bin/bash", "-c", "uname -s")


def test_missing_or_unknown_field_renders_empty_without_resolving_the_shell():
    def _exploding_resolver():
        raise AssertionError("the session shell must not resolve for non-shell fields")

    renderer = _renderer(shell_resolver=_exploding_resolver)

    assert renderer.render('<trender type="session" />') == ""
    assert renderer.render('<trender type="session" field="bogus" />') == ""

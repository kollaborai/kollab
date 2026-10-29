"""Accident guard wiring: tool children carry the markers, /terminal strips them."""

from __future__ import annotations

import asyncio
import os

from kollabor_agent.shell_executor import ShellExecutor
from kollabor_events.permissions_models import RiskAssessmentRules
from plugins.terminal_plugin import _filter_env


def _run(env):
    result = asyncio.run(ShellExecutor().run("env", timeout=10, env=env))
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def test_tool_children_are_marked_with_or_without_caller_env():
    for env in (None, {"PATH": os.environ["PATH"]}):
        child = _run(env)
        assert child["KOLLAB_TOOL_SHELL"] == "1"
        assert child["KOLLAB_AGENT_PID"] == str(os.getpid())


def test_terminal_sessions_strip_the_markers():
    env = _filter_env({"KOLLAB_TOOL_SHELL": "1", "KOLLAB_AGENT_PID": "9", "PATH": "/bin"})
    # A person's pane may run network controls, but a halt can find the pane by its owner.
    assert env == {"PATH": "/bin", "KOLLAB_OWNER_PID": str(os.getpid())}


def test_network_controls_are_high_risk():
    patterns = RiskAssessmentRules().high_risk_patterns
    risky = ("kollab --hub halt", "kollab --hub resume --local", "/connect revoke p", "rm ~/.kollab/private/halt.json")
    risky += (
        ">~/.kollab/private/halt.json",
        "sed -i '' s/x/y/ ~/.kollab/private/halt.json",
        "dd if=/dev/zero of=~/.kollab/private/halt.json",
    )
    for cmd in risky:
        assert any(p.search(cmd) for p in patterns), cmd
    assert not any(p.search("kollab --hub status") for p in patterns)
    assert not any(p.search("cat ~/.kollab/private/halt.json") for p in patterns)

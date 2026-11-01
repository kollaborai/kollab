"""The real ACP resolver keeps existing installs while adopting an installed pin."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_bridge
from kollabor.llm.acp_bridge import ACPError, ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.acp_state import ACPBinding, ACPBindingStore
from kollabor.state.local import LocalStateService
from kollabor.state.refresher import WidgetStateRefresher
from kollabor_ai.profile_manager import LLMProfile
from kollabor_tui.status.core_widgets import render_stats

OLD = "codex-2.1.1"
NEW = "codex-2.5.0"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(acp_bridge.shutil, "which", lambda command: None)
    return home, tmp_path / "config"


def managed(home, version, contents="#!/bin/sh\n"):
    path = home / ".local/share/kollab/acp-adapters" / version / "node_modules/.bin/codex-acp"
    path.parent.mkdir(parents=True, exist_ok=True)
    package = path.parent.parent / "@agentclientprotocol/codex-acp"
    package.mkdir(parents=True, exist_ok=True)
    target = package / "adapter"
    (package / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": version.removeprefix("codex-"),
        "bin": {"codex-acp": "adapter"},
    }))
    target.write_text(contents)
    target.chmod(0o755)
    path.symlink_to(target)
    return path


def cache(config, executable, command="codex-acp"):
    path = config / "acp/resolved.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"codex-cli": {"command": command, "path": str(executable)}}))


def test_old_cached_managed_switches_to_installed_new_pin(environment):
    home, config = environment
    old, new = managed(home, OLD), managed(home, NEW)
    cache(config, old)
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    assert profile.resolve()[0][0] == str(new)
    assert json.loads((config / "acp/resolved.json").read_text())["codex-cli"]["path"] == str(new)
    assert old.exists()


@pytest.mark.parametrize("cached", [False, True], ids=["cold", "cached"])
def test_old_only_managed_install_remains_usable(environment, cached):
    home, config = environment
    old = managed(home, OLD)
    if cached:
        cache(config, old)
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(old)


def test_cold_cache_prefers_new_managed_when_both_installed(environment):
    home, _ = environment
    managed(home, OLD)
    new = managed(home, NEW)
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(new)


def test_invalid_new_path_keeps_old_and_invalid_old_is_not_executed(environment):
    home, config = environment
    old, new = managed(home, OLD), managed(home, NEW)
    cache(config, old)
    new.chmod(0o644)
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    assert profile.resolve()[0][0] == str(old)
    old.chmod(0o644)
    with pytest.raises(ACPError, match="adapter executable not found"):
        profile.resolve()


def test_explicit_custom_or_cached_global_is_not_replaced(environment, monkeypatch):
    home, config = environment
    managed(home, OLD)
    managed(home, NEW)
    global_path = home / "bin/codex-acp"
    global_path.parent.mkdir()
    global_path.write_text("#!/bin/sh\n")
    global_path.chmod(0o755)
    cache(config, global_path)
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(global_path)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(global_path))
    custom = ACPProfile.builtin("codex-cli", command=[str(global_path), "--custom"], cwd=str(home))
    assert custom.resolve()[0] == (str(global_path), "--custom")


def test_old_path_explicitly_on_shell_path_is_not_replaced(environment, monkeypatch):
    home, config = environment
    old = managed(home, OLD)
    managed(home, NEW)
    cache(config, old)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(old))
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(old)


def test_explicit_global_replaces_old_managed_cache_before_new_pin(environment, monkeypatch):
    home, config = environment
    old = managed(home, OLD)
    managed(home, NEW)
    global_path = home / "bin/codex-acp"
    global_path.parent.mkdir()
    global_path.write_text("#!/bin/sh\n")
    global_path.chmod(0o755)
    cache(config, old)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(global_path))
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(global_path)


@pytest.mark.parametrize("name,version,expected", [
    ("@agentclientprotocol/codex-acp", "2.1.1", False),
    ("@agentclientprotocol/codex-acp", "2.5.0", True),
    ("@agentclientprotocol/codex-acp", "2.6.0", False),
    ("@agentclientprotocol/claude-agent-acp", "0.89.0", True),
    ("custom-adapter", "2.5.0", False),
    ("@agentclientprotocol/codex-acp", [2, 5, 0], False),
])
def test_prompt_usage_requires_recognized_resolved_package(tmp_path, name, version, expected):
    package = tmp_path / "node_modules" / (name if isinstance(name, str) else "custom-adapter")
    executable = package / "dist/adapter"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    binary_name = "claude-agent-acp" if name == "@agentclientprotocol/claude-agent-acp" else "codex-acp"
    (package / "package.json").write_text(json.dumps({
        "name": name, "version": version, "bin": {binary_name: "dist/adapter"},
    }))
    assert acp_bridge._known_prompt_usage(str(executable)) is expected


def test_claimed_official_manifest_beside_arbitrary_wrapper_is_not_trusted(tmp_path):
    executable = tmp_path / "codex-acp"
    executable.write_text("#!/bin/sh\n")
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": "2.5.0",
        "bin": {"codex-acp": "codex-acp"},
    }))
    assert acp_bridge._adapter_package_identity(str(executable)) is None
    assert not acp_bridge._known_prompt_usage(str(executable))


def test_prompt_usage_does_not_trust_missing_or_invalid_metadata(tmp_path):
    executable = tmp_path / "adapter"
    executable.write_text("#!/bin/sh\n")
    assert acp_bridge._known_prompt_usage(str(executable)) is False


def test_prompt_usage_does_not_trust_a_wrapper_next_to_a_known_package(tmp_path):
    executable = tmp_path / "wrapper"
    executable.write_text("#!/bin/sh\n")
    (tmp_path / "other").write_text("#!/bin/sh\n")
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": "2.5.0",
        "bin": {"codex-acp": "other"},
    }))
    assert acp_bridge._known_prompt_usage(str(executable)) is False
    (tmp_path / "package.json").write_text("not-json")
    assert acp_bridge._known_prompt_usage(str(executable)) is False


def test_prompt_usage_rejects_absolute_package_bin_target(tmp_path):
    executable = tmp_path / "adapter"
    executable.write_text("#!/bin/sh\n")
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": "2.5.0",
        "bin": {"codex-acp": str(executable)},
    }))
    assert acp_bridge._known_prompt_usage(str(executable)) is False


def test_resolving_old_install_does_not_run_installer(environment, monkeypatch):
    home, _ = environment
    old = managed(home, OLD)
    def unexpected(*args, **kwargs):
        raise AssertionError("resolution must not start an installer")
    monkeypatch.setattr(acp_bridge.subprocess, "run", unexpected)
    assert ACPProfile.builtin("codex-cli", cwd=str(home)).resolve()[0][0] == str(old)


def test_canonical_setup_uses_current_pin_and_names_fallback():
    spec = (Path(__file__).resolve().parents[2] / "docs/specs/acp-bridge.md").read_text()
    assert "codex-acp@2.5.0" in spec
    assert "codex-acp@2.1.1" not in spec
    assert "does not install an adapter in the background" in spec
    assert "its last request even on the prompt" in spec


ADAPTER = '''import asyncio
import acp
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse, Usage, UsageUpdate
class Agent:
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, **kwargs):
        return NewSessionResponse(sessionId="isolated")
    async def prompt(self, session_id, prompt, **kwargs):
        await self.conn.session_update(session_id, UsageUpdate(sessionUpdate="usage_update", used=27905, size=200000))
        return PromptResponse(stopReason="end_turn", usage=USAGE)
    def on_connect(self, conn):
        self.conn = conn
asyncio.run(acp.run_agent(Agent()))
'''

RESUME_ADAPTER = '''import asyncio
import acp
from acp.schema import (AgentCapabilities, InitializeResponse, NewSessionResponse,
                        ResumeSessionResponse, SessionCapabilities, SessionResumeCapabilities)
class Agent:
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1, agentCapabilities=AgentCapabilities(
            sessionCapabilities=SessionCapabilities(resume=SessionResumeCapabilities())))
    async def new_session(self, cwd, **kwargs):
        return NewSessionResponse(sessionId="fresh-session")
    async def resume_session(self, session_id, cwd, **kwargs):
        if SHOULD_FAIL:
            raise RuntimeError("injected resume failure")
        return ResumeSessionResponse()
    def on_connect(self, conn):
        pass
asyncio.run(acp.run_agent(Agent(), use_unstable_protocol=True))
'''


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["old-only", "old-cached-plus-new", "old-global", "custom-unknown"])
async def test_real_resolver_launches_selected_adapter_and_projects_its_usage(environment, scenario):
    home, config = environment
    # The real 2.1.1 adapter returns a last-request usage in the prompt
    # response; it is not absent and must not be labeled as a turn total.
    old_usage = "Usage(totalTokens=27905, inputTokens=27900, outputTokens=5)"
    old = managed(home, OLD, f"#!{sys.executable}\n" + ADAPTER.replace("USAGE", old_usage))
    if scenario == "old-cached-plus-new":
        expected = managed(home, NEW, f"#!{sys.executable}\n" + ADAPTER.replace(
            "USAGE", "Usage(totalTokens=83575, inputTokens=2000, outputTokens=3777, cachedReadTokens=77798)"))
        cache(config, old)
    elif scenario in {"old-global", "custom-unknown"}:
        managed(home, NEW)
        expected = home / "global/codex-acp"
        expected.parent.mkdir()
        usage = old_usage if scenario == "old-global" else (
            "Usage(totalTokens=83575, inputTokens=2000, outputTokens=3777, cachedReadTokens=77798)"
        )
        if scenario == "old-global":
            package = home / "global/node_modules/@agentclientprotocol/codex-acp"
            package.mkdir(parents=True)
            target = package / "adapter"
            target.write_text(f"#!{sys.executable}\n" + ADAPTER.replace("USAGE", usage))
            target.chmod(0o755)
            expected.symlink_to(target)
            (package / "package.json").write_text(json.dumps({
                "name": "@agentclientprotocol/codex-acp", "version": "2.1.1",
                "bin": {"codex-acp": "adapter"},
            }))
            assert acp_bridge._adapter_package_identity(str(expected)) == (
                "@agentclientprotocol/codex-acp", "2.1.1")
        else:
            expected.write_text(f"#!{sys.executable}\n" + ADAPTER.replace("USAGE", usage))
            expected.chmod(0o755)
        cache(config, expected)
    else:
        expected = old
    stats = {}
    llm = SimpleNamespace(session_stats=stats, conversation_manager=None)
    bus = SimpleNamespace(get_service=lambda name: llm if name == "llm_service" else None)
    orch = ACPOrchestrator(bus)
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    runner = ACPRunner(profile, update_callback=orch._on_runner_update)
    orch._runner = runner
    try:
        await runner.start()
        output = await asyncio.wait_for(runner.prompt("hello"), 10)
        assert runner.resolved_command[0] == str(expected)
        orch._settle_turn_usage(output)
        ctx = SimpleNamespace(remote_state={}, llm_service=None)
        await WidgetStateRefresher(ctx, LocalStateService(llm, None)).refresh_once()
        text = render_stats(60, ctx)
        if scenario == "old-cached-plus-new":
            assert output.usage["total_tokens"] == 83575
            assert "83K tok/turn" in text
        else:
            assert output.usage is None
            assert "last req" in text
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_managed_upgrade_resumes_saved_session_without_changing_other_bindings(environment):
    home, config = environment
    old = managed(home, OLD)
    new = managed(home, NEW, f"#!{sys.executable}\n" + RESUME_ADAPTER.replace("SHOULD_FAIL", "False"))
    cache(config, old)
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old),), {})
    runner = ACPRunner(profile)
    try:
        await runner.start(binding=binding)
        assert runner.resolved_command[0] == str(new)
        assert runner.resume_mode == "resumed"
        assert runner.session_id == "saved-session"
    finally:
        await runner.close()
    assert not profile._matches_managed_upgrade(replace(binding, workspace="/other"), str(home), (str(new),))
    assert not profile._matches_managed_upgrade(replace(binding, profile="other"), str(home), (str(new),))
    assert not profile._matches_managed_upgrade(
        replace(binding, command=(str(old), "--different")), str(home), (str(new),)
    )
    package = home / "global/node_modules/@agentclientprotocol/codex-acp"
    global_path = package / "dist/codex-acp"
    global_path.parent.mkdir(parents=True)
    global_path.write_text("#!/bin/sh\n")
    global_path.chmod(0o755)
    (package / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": "2.5.0",
        "bin": {"codex-acp": "dist/codex-acp"},
    }))
    assert profile._matches_managed_upgrade(binding, str(home), (str(global_path),))


@pytest.mark.asyncio
async def test_managed_upgrade_resume_failure_does_not_overwrite_binding(environment):
    home, config = environment
    old = managed(home, OLD)
    managed(home, NEW, f"#!{sys.executable}\n" + RESUME_ADAPTER.replace("SHOULD_FAIL", "True"))
    cache(config, old)
    store = ACPBindingStore(config / "bindings")
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old),), {})
    store.save("conversation", binding)
    before = store._path("conversation").read_bytes()
    llm = SimpleNamespace(session_stats={}, conversation_manager=SimpleNamespace(current_session_id="conversation"))
    services = {"llm_service": llm}
    bus = SimpleNamespace(get_service=lambda name: services.get(name),
                          register_service=lambda name, value: services.update({name: value}))
    orch = ACPOrchestrator(bus)
    orch._bindings = store
    with pytest.raises(ACPError, match="existing binding was kept"):
        await orch.start(LLMProfile(name="codex-cli", provider="acp", command=("codex-acp",), cwd=str(home)),
                         resume_conversation="conversation")
    assert store._path("conversation").read_bytes() == before
    assert store.load("conversation") == binding


@pytest.mark.asyncio
async def test_managed_upgrade_without_resume_capability_preserves_binding(environment):
    home, config = environment
    old = managed(home, OLD)
    managed(home, NEW, f"#!{sys.executable}\n" + ADAPTER.replace("USAGE", "None"))
    cache(config, old)
    store = ACPBindingStore(config / "bindings")
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old),), {})
    store.save("conversation", binding)
    before = store._path("conversation").read_bytes()
    llm = SimpleNamespace(session_stats={}, conversation_manager=SimpleNamespace(current_session_id="conversation"))
    services = {"llm_service": llm}
    bus = SimpleNamespace(get_service=lambda name: services.get(name),
                          register_service=lambda name, value: services.update({name: value}))
    orch = ACPOrchestrator(bus)
    orch._bindings = store
    with pytest.raises(ACPError, match="existing binding was kept"):
        await orch.start(LLMProfile(name="codex-cli", provider="acp", command=("codex-acp",), cwd=str(home)),
                         resume_conversation="conversation")
    assert store._path("conversation").read_bytes() == before
    assert store.load("conversation") == binding


@pytest.mark.asyncio
async def test_managed_upgrade_cancel_does_not_overwrite_binding(environment):
    home, config = environment
    old = managed(home, OLD)
    blocked = RESUME_ADAPTER.replace("if SHOULD_FAIL:", "await asyncio.Event().wait()\n        if False:")
    managed(home, NEW, f"#!{sys.executable}\n" + blocked)
    cache(config, old)
    store = ACPBindingStore(config / "bindings")
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old),), {})
    store.save("conversation", binding)
    before = store._path("conversation").read_bytes()
    llm = SimpleNamespace(session_stats={}, conversation_manager=SimpleNamespace(current_session_id="conversation"))
    services = {"llm_service": llm}
    bus = SimpleNamespace(get_service=lambda name: services.get(name),
                          register_service=lambda name, value: services.update({name: value}))
    orch = ACPOrchestrator(bus)
    orch._bindings = store
    task = asyncio.create_task(orch.start(
        LLMProfile(name="codex-cli", provider="acp", command=("codex-acp",), cwd=str(home)),
        resume_conversation="conversation",
    ))
    try:
        for _ in range(2000):
            if orch._runner is not None and orch._runner.session_id == "saved-session":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("managed resume did not reach the saved session")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._path("conversation").read_bytes() == before
        assert store.load("conversation") == binding
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unrecognized_global_keeps_old_binding_but_still_runs_for_new_sessions(environment, monkeypatch):
    home, config = environment
    old = managed(home, OLD)
    managed(home, NEW)
    global_path = home / "custom/codex-acp"
    global_path.parent.mkdir()
    global_path.write_text(f"#!{sys.executable}\n" + RESUME_ADAPTER.replace("SHOULD_FAIL", "False"))
    global_path.chmod(0o755)
    cache(config, old)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(global_path))
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old),), {})
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    runner = ACPRunner(profile)
    try:
        with pytest.raises(ACPError, match="existing binding was kept"):
            await runner.start(binding=binding)
        assert runner.session_id is None
    finally:
        await runner.close()
    fresh = ACPRunner(profile)
    try:
        await fresh.start()
        assert fresh.resolved_command[0] == str(global_path)
        assert fresh.resume_mode == "fresh"
    finally:
        await fresh.close()

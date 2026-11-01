"""npm-generated launchers preserve ACP identity and saved managed sessions."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm import acp_bridge
from kollabor.llm.acp_bridge import ACPError, ACPProfile
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.acp_state import ACPBinding, ACPBindingStore
from kollabor_ai.profile_manager import LLMProfile

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/acp-npm-shims"
PACKAGE = "@agentclientprotocol/codex-acp"
NODE_EXECUTABLE = shutil.which("node")
NODE_AVAILABLE = NODE_EXECUTABLE is not None

# This small agent responds over the actual ACP stdio transport. It has no
# credentials, network calls, or dependency on the real provider.
AGENT = (FIXTURES / "fixture-agent.js").read_text()


def install_shims(prefix, template, version="2.5.0", *, global_layout=False, name="codex-acp"):
    """Place recorded cmd-shim output in npm's real package/bin layout."""
    package_name = "@agentclientprotocol/" + name
    modules = prefix / "node_modules"
    package = modules / package_name
    target = package / "dist/index.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(AGENT)
    (package / "package.json").write_text(json.dumps({
        "name": package_name, "version": version, "bin": {name: "dist/index.js"},
    }))
    bin_dir = prefix if global_layout else modules / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(target, bin_dir).replace("\\", "/")
    for suffix in ("", ".cmd"):
        text = (FIXTURES / template / ("codex-acp" + suffix)).read_text()
        if template == "v7" and not suffix:
            # cmd-shim 7 emits a trailing space here; restore it from the
            # whitespace-clean checked-in fixture before hashing/execution.
            text = text.replace("\nelse\n", "\nelse \n")
        old = "../@agentclientprotocol/codex-acp/dist/index.js"
        new = relative
        if suffix:
            old, new = old.replace("/", "\\"), new.replace("/", "\\")
        text = text.replace(old, new)
        path = bin_dir / (name + suffix)
        path.write_text(text, newline="")
        path.chmod(0o755)
    return bin_dir / name


@pytest.mark.parametrize("template", ["v7", "v9"])
@pytest.mark.parametrize("global_layout", [False, True], ids=["managed", "global"])
@pytest.mark.parametrize("name", ["codex-acp", "claude-agent-acp"])
def test_real_generated_npm_shim_identity(tmp_path, template, global_layout, name):
    version = "2.5.0" if name == "codex-acp" else "0.89.0"
    shim = install_shims(tmp_path, template, version, global_layout=global_layout, name=name)
    for path in (shim, Path(str(shim) + ".cmd")):
        assert acp_bridge._adapter_package_identity(str(path)) == ("@agentclientprotocol/" + name, version)
        assert acp_bridge._known_prompt_usage(str(path))


@pytest.mark.parametrize("suffix", ["", ".cmd"])
def test_modified_shim_is_not_trusted_even_beside_official_package(tmp_path, suffix):
    shim = Path(str(install_shims(tmp_path, "v9")) + suffix)
    shim.write_text(shim.read_text() + "\necho injected-command\n")
    assert acp_bridge._adapter_package_identity(str(shim)) is None
    assert not acp_bridge._known_prompt_usage(str(shim))


def test_wrong_target_or_outside_package_is_not_trusted(tmp_path):
    shim = install_shims(tmp_path, "v7")
    manifest = tmp_path / "node_modules" / PACKAGE / "package.json"
    data = json.loads(manifest.read_text())
    data["bin"]["codex-acp"] = "../../outside"
    manifest.write_text(json.dumps(data))
    (tmp_path / "node_modules/outside").write_text(AGENT)
    assert acp_bridge._adapter_package_identity(str(shim)) is None
    assert acp_bridge._adapter_package_identity(str(shim) + ".cmd") is None


@pytest.mark.parametrize("suffix", ["", ".cmd"])
def test_symlinked_package_bin_cannot_escape_package(tmp_path, suffix):
    if os.name == "nt":
        pytest.skip("symlink creation needs a Windows developer-mode privilege")
    shim = install_shims(tmp_path, "v9")
    target = tmp_path / "node_modules" / PACKAGE / "dist/index.js"
    target.unlink()
    outside = tmp_path / "outside.js"
    outside.write_text(AGENT)
    target.symlink_to(outside)
    assert acp_bridge._adapter_package_identity(str(shim) + suffix) is None


@pytest.mark.parametrize("template", ["v7", "v9"])
def test_old_global_npm_shim_is_identified_but_not_trusted_for_turn_usage(tmp_path, template):
    shim = install_shims(tmp_path, template, "2.1.1", global_layout=True)
    for suffix in ("", ".cmd"):
        assert acp_bridge._adapter_package_identity(str(shim) + suffix) == (PACKAGE, "2.1.1")
        assert not acp_bridge._known_prompt_usage(str(shim) + suffix)


def test_unrecognized_global_shim_stays_selected_but_untrusted(tmp_path, monkeypatch):
    shim = install_shims(tmp_path, "v9", global_layout=True)
    cmd = Path(str(shim) + ".cmd")
    cmd.write_text(cmd.read_text() + "\necho custom\n")
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    profile = ACPProfile.builtin("codex-cli", command=(str(cmd),), cwd=str(tmp_path))
    assert profile.resolve()[0][0] == str(cmd)
    assert not acp_bridge._known_prompt_usage(str(cmd))


@pytest.mark.parametrize("old_form", ["", ".cmd"])
@pytest.mark.parametrize("new_available", [False, True])
def test_windows_old_managed_resolution_and_binding_retarget(tmp_path, monkeypatch, old_form, new_available):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".local/share/kollab/acp-adapters"
    old = install_shims(root / "codex-2.1.1", "v7", "2.1.1")
    new = install_shims(root / "codex-2.5.0", "v9") if new_available else None
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setattr(acp_bridge, "_on_windows", lambda: True)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(acp_bridge.shutil, "which", lambda command: None)
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    config = tmp_path / "config/acp/resolved.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"codex-cli": {"command": "codex-acp", "path": str(old) + old_form}}))
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    selected = profile.resolve()[0]
    expected = str(new if new_available else old) + ".cmd"
    assert selected[0] == expected
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old) + old_form,), {})
    if selected[0] != binding.command[0]:
        assert profile._retargets_previous_binding(binding, str(home), selected)
        assert profile._matches_managed_upgrade(binding, str(home), selected)
    else:
        assert binding.matches(str(home), "codex-cli", selected)
    assert acp_bridge._known_prompt_usage(selected[0]) is new_available


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_command", [False, True], ids=["path-selection", "explicit-command"])
async def test_unknown_replacement_is_rejected_before_executing_it(tmp_path, monkeypatch, explicit_command):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".local/share/kollab/acp-adapters"
    old = install_shims(root / "codex-2.1.1", "v7", "2.1.1")
    install_shims(root / "codex-2.5.0", "v9")
    custom = home / "custom-codex"
    custom.write_text("not an identified npm shim")
    custom.chmod(0o755)
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(custom))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    spawn = AsyncMock(side_effect=AssertionError("ambiguous adapter must not start"))
    monkeypatch.setattr(acp_bridge.asyncio, "create_subprocess_exec", spawn)
    profile = ACPProfile.builtin("codex-cli", cwd=str(home), **(
        {"command": (str(custom), "--custom")} if explicit_command else {}
    ))
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old) + ".cmd",), {})
    runner = acp_bridge.ACPRunner(profile)
    with pytest.raises(ACPError, match="existing binding was kept"):
        await runner.start(binding=binding)
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_altered_managed_shim_is_rejected_before_executing_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".local/share/kollab/acp-adapters"
    old = install_shims(root / "codex-2.1.1", "v7", "2.1.1")
    new = install_shims(root / "codex-2.5.0", "v9")
    selected = Path(str(new) + ".cmd") if acp_bridge._on_windows() else new
    selected.write_text(selected.read_text() + "\necho injected-command\n")
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(acp_bridge.shutil, "which", lambda command: None)
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    spawn = AsyncMock(side_effect=AssertionError("modified adapter must not start"))
    monkeypatch.setattr(acp_bridge.asyncio, "create_subprocess_exec", spawn)
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old) + ".cmd",), {})
    runner = acp_bridge.ACPRunner(ACPProfile.builtin("codex-cli", cwd=str(home)))
    with pytest.raises(ACPError, match="existing binding was kept"):
        await runner.start(binding=binding)
    spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(not NODE_AVAILABLE or os.name == "nt", reason="POSIX generated-shim runtime")
@pytest.mark.parametrize("old_form", ["", ".cmd"])
@pytest.mark.parametrize("outcome", ["resume", "failure", "unsupported", "cancel"])
async def test_generated_shim_old_binding_resume_or_preservation(tmp_path, monkeypatch, old_form, outcome):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".local/share/kollab/acp-adapters"
    old = install_shims(root / "codex-2.1.1", "v7", "2.1.1")
    new = install_shims(root / "codex-2.5.0", "v9")
    # cmd-shim 9 checks a local node path in its POSIX wrapper.
    (new.parent / "node").symlink_to(NODE_EXECUTABLE)
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(acp_bridge.shutil, "which", lambda command: None)
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    store = ACPBindingStore(tmp_path / "bindings")
    binding = ACPBinding("saved-session", os.path.realpath(home), "codex-cli", (str(old) + old_form,), {})
    store.save("conversation", binding)
    before = store._path("conversation").read_bytes()
    llm = SimpleNamespace(session_stats={}, conversation_manager=SimpleNamespace(current_session_id="conversation"))
    services = {"llm_service": llm}
    bus = SimpleNamespace(get_service=lambda name: services.get(name),
                          register_service=lambda name, value: services.update({name: value}))
    orch = ACPOrchestrator(bus)
    orch._bindings = store
    env = {"TEST_FAIL_RESUME": "1"} if outcome == "failure" else (
        {"TEST_NO_RESUME": "1"} if outcome == "unsupported" else
        {"TEST_BLOCK_RESUME": "1"} if outcome == "cancel" else {})
    profile = LLMProfile(name="codex-cli", provider="acp", command=("codex-acp",), cwd=str(home), env=env)
    task = asyncio.create_task(orch.start(profile, resume_conversation="conversation"))
    try:
        if outcome == "cancel":
            for _ in range(2000):
                if orch._runner is not None and orch._runner.session_id == "saved-session":
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            else:
                pytest.fail("resume did not reach saved session")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"failure", "unsupported"}:
            with pytest.raises(ACPError, match="existing binding was kept"):
                await task
        else:
            await task
            assert orch._runner.resolved_command[0] == str(new)
            assert orch._runner.resume_mode == "resumed"
            assert orch._runner.session_id == "saved-session"
            assert (await orch._runner.prompt("hello")).usage["total_tokens"] == 13
        if outcome != "resume":
            assert store._path("conversation").read_bytes() == before
            assert store.load("conversation") == binding
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if orch._runner is not None:
            await orch.close()

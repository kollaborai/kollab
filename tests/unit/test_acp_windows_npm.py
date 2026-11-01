"""Native Windows npm shim launch, usage, and managed session upgrade."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kollabor.llm import acp_bridge
from kollabor.llm.acp_bridge import ACPProfile, ACPRunner

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows npm smoke")
AGENT = Path(__file__).resolve().parents[1] / "fixtures/acp-npm-shims/fixture-agent.js"


def install_local_adapter(tmp_path, prefix, version):
    """Ask npm to generate real shims from a local, dependency-free package."""
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for the Windows npm smoke")
    npm_cli = Path(node).resolve().parent / "node_modules/npm/bin/npm-cli.js"
    if not npm_cli.is_file():
        pytest.fail("Node's bundled npm CLI is unavailable")
    source = tmp_path / ("source-" + version)
    (source / "dist").mkdir(parents=True)
    shutil.copyfile(AGENT, source / "dist/index.js")
    (source / "package.json").write_text(json.dumps({
        "name": "@agentclientprotocol/codex-acp", "version": version,
        "bin": {"codex-acp": "dist/index.js"},
    }))
    prefix.mkdir(parents=True)
    env = dict(os.environ, npm_config_cache=str(tmp_path / "npm-cache"),
               npm_config_userconfig=str(tmp_path / "npmrc"), npm_config_update_notifier="false")
    subprocess.run([
        node, str(npm_cli), "install", "--prefix", str(prefix), "--no-save", "--no-package-lock",
        "--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--install-links", str(source),
    ], env=env, check=True, capture_output=True, text=True, timeout=60)
    shim = prefix / "node_modules/.bin/codex-acp.cmd"
    assert shim.is_file()
    return shim


@pytest.mark.asyncio
async def test_native_npm_shim_runs_and_resumes_old_managed_binding(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".local/share/kollab/acp-adapters"
    old = install_local_adapter(tmp_path, root / "codex-2.1.1", "2.1.1")
    monkeypatch.setattr(acp_bridge.Path, "home", lambda: home)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    original_which = shutil.which
    monkeypatch.setattr(acp_bridge.shutil, "which",
                        lambda name: None if name == "codex-acp" else original_which(name))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    profile = ACPProfile.builtin("codex-cli", cwd=str(home))
    old_runner = ACPRunner(profile)
    try:
        await old_runner.start()
        assert old_runner.resolved_command[0] == str(old)
        assert acp_bridge._adapter_package_identity(str(old)) == ("@agentclientprotocol/codex-acp", "2.1.1")
        assert (await old_runner.prompt("old adapter")).usage is None
        binding = old_runner.binding
    finally:
        await old_runner.close()
    # The new adapter is installed explicitly by the test, never by resolver.
    new = install_local_adapter(tmp_path, root / "codex-2.5.0", "2.5.0")
    new_runner = ACPRunner(profile)
    try:
        await new_runner.start(binding=binding)
        assert new_runner.resolved_command[0] == str(new)
        assert new_runner.session_id == binding.session_id
        assert new_runner.resume_mode == "resumed"
        assert acp_bridge._known_prompt_usage(str(new))
        assert (await new_runner.prompt("new adapter")).usage["total_tokens"] == 13
    finally:
        await new_runner.close()

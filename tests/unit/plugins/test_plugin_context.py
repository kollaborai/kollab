"""Engine plugin contexts enforce grants and expose declared defaults."""

import json
from pathlib import Path

import pytest

from kollabor_plugins.context import PluginContext


@pytest.mark.asyncio
async def test_plugin_context_grants_defaults_and_agent_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    context = PluginContext("hello", root, tmp_path / "data", manifest, grants=[])
    assert context.id == "hello"
    assert isinstance(context.settings.get(), dict)
    with pytest.raises(PermissionError):
        context.secrets.get("NOT_GRANTED")
    with pytest.raises(NotImplementedError, match="step 4"):
        await context.agents.run("hello")
    with pytest.raises(ValueError):
        context.lock("../escape")

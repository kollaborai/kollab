"""Engine plugin contexts enforce grants and expose declared defaults."""

import json
import shutil
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


def test_plugin_context_creates_private_lock_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    context = PluginContext("hello", root, tmp_path / "data", manifest)
    with context.lock("chain"):
        assert (tmp_path / "data" / "locks" / "chain.lock").is_file()


def test_plugin_context_refuses_lock_directory_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "locks").symlink_to(outside, target_is_directory=True)
    context = PluginContext("hello", root, data, manifest)
    with pytest.raises(ValueError, match="lock"):
        with context.lock("chain"):
            pass
    assert not (outside / "chain.lock").exists()


def test_plugin_context_reports_corrupt_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    data = tmp_path / "data"
    data.mkdir()
    (data / "values.json").write_text("{bad json", encoding="utf-8")
    context = PluginContext("hello", root, data, manifest)
    with pytest.raises(ValueError, match="Invalid plugin settings"):
        context.settings.get()


@pytest.mark.asyncio
async def test_plugin_context_full_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    import kollabor_ai.profile_manager as profile_manager

    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    data = home / "plugin-data" / "hello"
    events = []
    notices = []
    monkeypatch.setattr(profile_manager, "_keyring_get", lambda name: "sample" if name == "TOKEN" else None)
    context = PluginContext(
        "hello", root, data, manifest, grants=["secret:TOKEN"],
        publish=lambda *args: events.append(args), notify=lambda *args: notices.append(args),
    )
    assert context.settings.get() == {"greeting": "Hello"}
    context.settings.save({"greeting": "Welcome"})
    assert context.settings.get() == {"greeting": "Welcome"}
    with pytest.raises(ValueError):
        context.settings.save({"enabled": False})
    assert context.secrets.get("TOKEN") == "sample"
    with pytest.raises(PermissionError):
        context.secrets.get("OTHER")
    context.events.publish("changed", {"count": 1})
    context.notify("Ready", {"label": "Open"})
    assert events == [("hello", "changed", {"count": 1})]
    assert notices == [("hello", "Ready", {"label": "Open"})]
    assert [service["name"] for service in context.services.status()] == ["hello"]
    with context.lock("chain"):
        assert (data / "locks" / "chain.lock").is_file()
    with pytest.raises(NotImplementedError, match="step 4"):
        await context.agents.run()
    # Old or manually edited values cannot turn the enable switch into a setting.
    (data / "values.json").write_text(json.dumps({"greeting": "Hi", "enabled": False, "old": 7}))
    assert context.settings.get() == {"greeting": "Hi"}

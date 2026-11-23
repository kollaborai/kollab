"""Exception text raised by plugin code never reaches plugin metadata (#1401)."""

import json

import pytest

SECRET = "tok_1401_do_not_show"


def _install(tmp_path, entry_source):
    from kollabor_plugins.packages import files_sha256

    root = tmp_path / ".kollab" / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "python": "entry.py:Hello"}))
    (root / "entry.py").write_text(entry_source)
    (tmp_path / ".kollab" / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root)}}, "dev": {}})
    )


@pytest.mark.parametrize(
    "entry_source",
    [
        f"raise RuntimeError({SECRET!r})\n",
        "from kollabor_plugins import BasePlugin\n"
        "class Hello(BasePlugin):\n"
        "    @staticmethod\n"
        f"    def get_default_config():\n        raise ValueError({SECRET!r})\n",
    ],
    ids=["import", "default-config"],
)
def test_plugin_raised_text_is_not_kept(tmp_path, monkeypatch, entry_source):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry

    (tmp_path / "bundled").mkdir()
    _install(tmp_path, entry_source)
    registry = PluginRegistry(tmp_path / "bundled")
    registry.load_all_plugins()
    error = registry.package_errors["hello"]
    assert SECRET not in error
    assert "kollab.log" in error

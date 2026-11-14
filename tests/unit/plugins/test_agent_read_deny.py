"""Plugin and engine token paths are not readable through file tools."""

import pytest

from kollabor_agent.file_operations_executor import FileOperationsExecutor, PathAccessMode


@pytest.mark.parametrize("operation", ["file_read", "file_grep", "file_copy", "file_copy_overwrite", "file_move"])
@pytest.mark.parametrize("protected", ["engine.token", "plugins/example/code.py", "plugin-data/example/run/token"])
def test_sensitive_paths_denied_for_all_file_reading_operations(tmp_path, monkeypatch, operation, protected):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = tmp_path / ".kollab"
    source = root / protected
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("private sentinel", encoding="utf-8")
    executor = FileOperationsExecutor(workspace=tmp_path)
    executor.set_path_access_mode(PathAccessMode.ANYWHERE)
    result = executor.execute_operation(
        {
            "type": operation,
            "file": str(source),
            "pattern": "sentinel",
            "from": str(source),
            "to": str(tmp_path / "out"),
        }
    )
    assert not result["success"]
    assert "private sentinel" not in str(result)


def test_symlink_to_sensitive_path_is_denied_but_normal_config_is_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    root = tmp_path / ".kollab"
    root.mkdir()
    token = root / "engine.token"
    token.write_text("private sentinel", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(token)
    config = root / "config.json"
    config.write_text("normal config", encoding="utf-8")
    executor = FileOperationsExecutor(workspace=tmp_path)
    executor.set_path_access_mode(PathAccessMode.ANYWHERE)
    assert not executor.execute_operation({"type": "file_read", "file": str(link)})["success"]
    assert executor.execute_operation({"type": "file_read", "file": str(config)})["success"]


def test_overridden_engine_token_directory_is_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "engine"))
    token = tmp_path / "engine" / "engine.token"
    token.parent.mkdir()
    token.write_text("private sentinel", encoding="utf-8")
    executor = FileOperationsExecutor(workspace=tmp_path)
    assert not executor.execute_operation({"type": "file_read", "file": str(token)})["success"]

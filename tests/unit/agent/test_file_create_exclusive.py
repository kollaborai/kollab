"""Normal file_create must never overwrite a concurrent local creation."""

import builtins

from kollabor_agent.file_operations_executor import FileOperationsExecutor


def test_create_refuses_concurrent_creation(tmp_path, monkeypatch):
    destination = tmp_path / "result.txt"
    executor = FileOperationsExecutor(workspace=tmp_path)
    original_open = builtins.open

    def racing_open(path, mode="r", *args, **kwargs):
        if str(path) == str(destination) and mode in {"w", "x"}:
            with original_open(destination, "w", encoding="utf-8") as handle:
                handle.write("created by another process")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", racing_open)
    result = executor._execute_create({"file": str(destination), "content": "remote overwrite"})
    assert not result["success"]
    assert destination.read_text() == "created by another process"


def test_create_and_explicit_overwrite_keep_distinct_contracts(tmp_path):
    destination = tmp_path / "result.txt"
    executor = FileOperationsExecutor(workspace=tmp_path)
    assert executor._execute_create({"file": str(destination), "content": "first"})["success"]
    assert not executor._execute_create({"file": str(destination), "content": "second"})["success"]
    assert destination.read_text() == "first"

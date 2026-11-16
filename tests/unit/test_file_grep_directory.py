from tempfile import TemporaryDirectory
from unittest.mock import patch

from kollabor_agent.file_operations_executor import FileOperationsExecutor


def test_file_grep_directory_returns_a_clear_tool_error():
    with TemporaryDirectory() as directory:
        result = FileOperationsExecutor(workspace=directory).execute_operation(
            {"type": "file_grep", "file": directory, "pattern": "needle"}
        )

    assert result["success"] is False
    assert "directory" in result["error"].lower()
    assert "Operation failed" not in result["error"]


def test_is_text_file_returns_false_for_directory_and_os_errors():
    executor = FileOperationsExecutor()

    with TemporaryDirectory() as directory:
        assert executor.is_text_file(directory) is False

    with patch("builtins.open", side_effect=PermissionError("denied")):
        assert executor.is_text_file("unreadable.txt") is False


def test_text_operations_identify_directory_inputs_as_directories():
    with TemporaryDirectory() as directory:
        executor = FileOperationsExecutor(workspace=directory)
        operations = [
            {"type": "file_read", "file": directory},
            {"type": "file_edit", "file": directory, "find": "x", "replace": "y"},
            {"type": "file_append", "file": directory, "content": "x"},
            {
                "type": "file_insert_after",
                "file": directory,
                "pattern": "x",
                "content": "y",
            },
            {
                "type": "file_insert_before",
                "file": directory,
                "pattern": "x",
                "content": "y",
            },
        ]

        for operation in operations:
            result = executor.execute_operation(operation)
            assert result["success"] is False
            assert "directory" in result["error"].lower(), (operation["type"], result)

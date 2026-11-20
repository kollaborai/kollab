"""Text operations must reject invalid file kinds without probing their content."""

import errno
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from kollabor_agent.file_operations_executor import FileOperationsExecutor

OPERATIONS = [
    {"type": "file_read"},
    {"type": "file_grep", "pattern": "needle"},
    {"type": "file_edit", "find": "needle", "replace": "changed"},
    {"type": "file_append", "content": "added"},
    {"type": "file_insert_after", "pattern": "needle", "content": "added"},
    {"type": "file_insert_before", "pattern": "needle", "content": "added"},
]


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op["type"])
@pytest.mark.parametrize("symlink", [False, True], ids=["directory", "directory-link"])
@pytest.mark.parametrize("size_limit", [10, 0], ids=["normal-limit", "zero-limit"])
def test_directory_errors_precede_size_and_content_checks(
    tmp_path, operation, symlink, size_limit, caplog
):
    directory = tmp_path / "directory"
    directory.mkdir()
    target = directory
    if symlink:
        target = tmp_path / "link"
        target.symlink_to(directory, target_is_directory=True)
    executor = FileOperationsExecutor(workspace=tmp_path)
    executor.max_read_size_mb = executor.max_edit_size_mb = size_limit

    with patch("builtins.open", side_effect=AssertionError("directory was opened")) as opened:
        result = executor.execute_operation({**operation, "file": str(target)})

    assert result["success"] is False
    assert "directory" in result["error"].lower(), result
    assert "binary" not in result["error"].lower()
    assert "Operation failed" not in result["error"]
    opened.assert_not_called()
    assert not [record for record in caplog.records if record.levelname == "ERROR"]
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op["type"])
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_text_probe_preserves_os_error_in_tool_result(tmp_path, operation, error_number):
    target = tmp_path / "example.txt"
    target.write_text("needle\n", encoding="utf-8")
    executor = FileOperationsExecutor(workspace=tmp_path)
    message = os.strerror(error_number)

    with patch("builtins.open", side_effect=OSError(error_number, message)):
        result = executor.execute_operation({**operation, "file": str(target)})

    assert result["success"] is False
    assert message in result["error"], result
    assert "binary" not in result["error"].lower()
    assert "Operation failed" not in result["error"]
    assert target.read_text(encoding="utf-8") == "needle\n"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX file kinds")
@pytest.mark.parametrize("operation", [None, *OPERATIONS], ids=lambda op: op["type"] if op else "is_text_file")
@pytest.mark.parametrize("file_kind", ["fifo", "socket"])
def test_non_regular_files_are_rejected_without_opening(operation, file_kind):
    # AF_UNIX addresses have a small length limit, unlike ordinary file paths.
    with TemporaryDirectory(dir="/tmp") as directory, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        target = Path(directory) / "special"
        if file_kind == "fifo":
            os.mkfifo(target)
        else:
            server.bind(str(target))
        executor = FileOperationsExecutor(workspace=directory)

        with patch("builtins.open", side_effect=AssertionError("special file was opened")) as opened:
            if operation is None:
                assert executor.is_text_file(str(target)) is False
            else:
                result = executor.execute_operation({**operation, "file": str(target)})
                assert result["success"] is False
                assert "non-regular file" in result["error"], result
                assert "Operation failed" not in result["error"]
            opened.assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op["type"])
@pytest.mark.parametrize("file_kind", ["text", "binary", "missing", "text-link"])
def test_regular_file_behavior_is_preserved(tmp_path, operation, file_kind):
    target = tmp_path / "example.txt"
    if file_kind == "binary":
        target.write_bytes(b"\xff\xfe")
    elif file_kind != "missing":
        target.write_text("needle\n", encoding="utf-8")
    if file_kind == "text-link":
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        target = link
    executor = FileOperationsExecutor(workspace=tmp_path)
    # Keep this input-validation test independent of backup storage locations.
    executor.automatic_backups = False

    result = executor.execute_operation({**operation, "file": str(target)})

    if file_kind in {"text", "text-link"}:
        assert result["success"] is True, result
        if operation["type"] == "file_read":
            assert "needle" in result["output"]
        elif operation["type"] == "file_grep":
            assert "Found 1 match" in result["output"]
            assert "1: needle" in result["output"]
        else:
            expected = {
                "file_edit": "changed\n",
                "file_append": "needle\nadded",
                "file_insert_after": "needle\nadded\n",
                "file_insert_before": "added\nneedle\n",
            }
            assert target.read_text(encoding="utf-8") == expected[operation["type"]]
    else:
        assert result["success"] is False
        assert ("binary file" if file_kind == "binary" else "File not found") in result["error"]


@pytest.mark.parametrize("operation", OPERATIONS[:3], ids=lambda op: op["type"])
def test_regular_file_size_limits_still_apply(tmp_path, operation):
    target = tmp_path / "example.txt"
    target.write_text("needle\n", encoding="utf-8")
    executor = FileOperationsExecutor(workspace=tmp_path)
    executor.max_read_size_mb = executor.max_edit_size_mb = 0

    with patch("builtins.open", side_effect=AssertionError("oversized file was opened")) as opened:
        result = executor.execute_operation({**operation, "file": str(target)})

    assert result["success"] is False
    assert "File too large" in result["error"]
    opened.assert_not_called()

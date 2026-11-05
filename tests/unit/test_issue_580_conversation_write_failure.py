"""Issue #580: a conversation record that is not on disk must not report success.

The logger used to catch the OSError from the session file's open or write,
log it, and return a message id anyway. These tests drive the real logger
(real files, real OS errors where the platform allows) and check what the
caller sees: an exception, no counted record, no half-written line, and a
clean retry.
"""

import asyncio
import errno
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_ai.conversation_logger import KollaborConversationLogger

try:
    import resource
except ImportError:  # Windows: no RLIMIT_FSIZE
    resource = None


def _logger(tmp_path):
    return KollaborConversationLogger(tmp_path / "conversations")


def _contents(log):
    """Parsed records in the session file (each line must be valid JSON)."""
    if not log.session_file.exists():
        return []
    return [json.loads(line) for line in log.session_file.read_text().splitlines()]


@pytest.fixture
def fail_session_open(monkeypatch):
    """Make os.open fail only for this logger's session file, as the issue's probe did."""

    def install(log):
        real_open = os.open

        def failing_open(path, flags, *args, **kwargs):
            if os.fspath(path) == os.fspath(log.session_file):
                raise OSError(errno.EIO, "injected open failure")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", failing_open)

    return install


def test_failed_open_raises_instead_of_returning_an_id(tmp_path):
    """A real open failure (the session path is a directory) must not return an id."""
    log = _logger(tmp_path)
    log.session_file.mkdir()  # os.open(O_WRONLY) on a directory: IsADirectoryError

    async def scenario():
        with pytest.raises(OSError):
            await log.log_user_message("lost")

    asyncio.run(scenario())
    assert log.message_count == 0


def test_failed_write_surfaces_through_a_task_boundary(tmp_path, fail_session_open):
    """The ACP caller runs log_user_message as a task and reads task.exception()."""
    log = _logger(tmp_path)
    fail_session_open(log)

    async def scenario():
        task = asyncio.create_task(log.log_user_message("lost"))
        await asyncio.wait([task])
        return task

    task = asyncio.run(scenario())
    assert isinstance(task.exception(), OSError)
    assert log.message_count == 0


@pytest.mark.parametrize(
    "writer",
    ["conversation_start", "user", "assistant", "system", "conversation_end"],
)
def test_every_event_writer_raises_when_the_record_is_not_written(
    tmp_path, fail_session_open, writer
):
    log = _logger(tmp_path)
    fail_session_open(log)
    calls = {
        "conversation_start": lambda: log.log_conversation_start(),
        "user": lambda: log.log_user_message("hi"),
        "assistant": lambda: log.log_assistant_message("hi", parent_uuid="root"),
        "system": lambda: log.log_system_message("note", parent_uuid="root"),
        "conversation_end": lambda: log.log_conversation_end(),
    }

    async def scenario():
        with pytest.raises(OSError, match="injected open failure"):
            await calls[writer]()

    asyncio.run(scenario())
    assert log.message_count == 0


def test_recovery_after_a_failed_open_drops_nothing_and_duplicates_nothing(
    tmp_path, fail_session_open, monkeypatch
):
    """Fail the first open only: the failed record is absent, the retry is present once."""
    log = _logger(tmp_path)
    real_open = os.open
    remaining = {"failures": 1}

    def flaky_open(path, flags, *args, **kwargs):
        if remaining["failures"] and os.fspath(path) == os.fspath(log.session_file):
            remaining["failures"] -= 1
            raise OSError(errno.EIO, "injected open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky_open)

    async def scenario():
        with pytest.raises(OSError):
            await log.log_user_message("first attempt")
        await log.log_user_message("second attempt")

    asyncio.run(scenario())
    assert [r["message"]["content"] for r in _contents(log)] == ["second attempt"]
    assert log.message_count == 1


@pytest.mark.skipif(resource is None, reason="RLIMIT_FSIZE is Unix-only")
def test_partial_write_is_rolled_back_and_retry_is_clean(tmp_path):
    """A real short write (40 bytes land, then EFBIG) must leave no half line."""
    log = _logger(tmp_path)

    async def scenario():
        await log.log_user_message("kept")
        before = log.session_file.read_bytes()
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        resource.setrlimit(resource.RLIMIT_FSIZE, (len(before) + 40, hard))
        try:
            with pytest.raises(OSError):
                await log.log_user_message("x" * 1000)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        assert log.session_file.read_bytes() == before
        assert log.message_count == 1
        await log.log_user_message("retry")

    asyncio.run(scenario())
    assert [r["message"]["content"] for r in _contents(log)] == ["kept", "retry"]
    assert log.message_count == 2


def test_shutdown_still_releases_resources_when_the_end_record_fails():
    """Callers that cannot absorb a logger failure keep their cleanup (llm_coordinator)."""
    from kollabor.llm.llm_coordinator import LLMService

    service = LLMService.__new__(LLMService)
    service.conversation_logger = MagicMock()
    service.conversation_logger.log_conversation_end = AsyncMock(
        side_effect=OSError("disk full")
    )
    service.cancel_all_tasks = AsyncMock()
    service._task_manager = SimpleNamespace(_monitoring_task=None)
    service.api_service = MagicMock(shutdown=AsyncMock())
    service._provider_registry = MagicMock(shutdown_all=AsyncMock())
    service.mcp_integration = MagicMock(shutdown=AsyncMock())

    asyncio.run(service.shutdown())

    service.cancel_all_tasks.assert_awaited_once()
    service.api_service.shutdown.assert_awaited_once()
    service.mcp_integration.shutdown.assert_awaited_once()


def test_hud_injection_still_queues_when_the_history_write_fails(monkeypatch):
    """The HUD entry is queued before the history write, so a failed write must not raise."""
    from kollabor.llm import llm_coordinator
    from kollabor.llm.llm_coordinator import LLMService

    monkeypatch.setattr(llm_coordinator, "format_agent_hud", lambda entries: "[hud] note")
    service = LLMService.__new__(LLMService)
    service.queue_agent_hud = MagicMock(return_value={"label": "injection"})
    service.conversation_logger = MagicMock()
    service.conversation_logger.log_system_message = AsyncMock(
        side_effect=OSError("disk full")
    )
    service.current_parent_uuid = "parent"

    asyncio.run(service.inject_system_message("note"))

    service.queue_agent_hud.assert_called_once()
    service.conversation_logger.log_system_message.assert_awaited_once()

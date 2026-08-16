"""Regression tests for legacy ContextService startup behavior."""

import asyncio

from kollabor_ai.context_injection import ContextService


class _Config:
    def get(self, key, default=None):
        return default


class _ConversationManager:
    def __init__(self):
        self.messages = []

    def add_message(self, **message):
        self.messages.append(message)


class _EventBus:
    def __init__(self, loop, services=None):
        self.loop = loop
        self.services = services or {}

    def get_service(self, name):
        return self.services.get(name)


def test_context_service_does_not_schedule_ready_coroutine_on_stopped_loop():
    """A configured but stopped loop must not receive an unawaited task."""
    loop = asyncio.new_event_loop()
    try:
        ContextService(_Config(), _ConversationManager(), _EventBus(loop))
        assert not asyncio.all_tasks(loop)
    finally:
        loop.close()


def test_keyword_context_is_queued_without_persisting_system_message(tmp_path):
    """Legacy keyword context must use the request-local ledger rail."""
    bookmarks = tmp_path / "BOOKMARKS.md"
    bookmarks.write_text("### the test\nrequest-local context\n")
    manager = _ConversationManager()

    class Config(_Config):
        def get(self, key, default=None):
            if key == "context.bookmarks_path":
                return str(bookmarks)
            if key == "context_triggers":
                return {"test": "test:the-test"}
            return default

    queued = []

    class LedgerService:
        def queue_ephemeral_injection(self, content):
            queued.append(content)

    service = ContextService(
        Config(),
        manager,
        _EventBus(None, {"context_service": LedgerService()}),
    )

    assert asyncio.run(service.trigger_context_injection("please test this"))
    assert manager.messages == []
    assert len(queued) == 1
    assert "request-local context" in queued[0]

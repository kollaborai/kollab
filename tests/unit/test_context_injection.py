"""Regression tests for legacy ContextService startup behavior."""

import asyncio

from kollabor_ai.context_injection import ContextService


class _Config:
    def get(self, key, default=None):
        return default


class _ConversationManager:
    pass


class _EventBus:
    def __init__(self, loop):
        self.loop = loop


def test_context_service_does_not_schedule_ready_coroutine_on_stopped_loop():
    """A configured but stopped loop must not receive an unawaited task."""
    loop = asyncio.new_event_loop()
    try:
        ContextService(_Config(), _ConversationManager(), _EventBus(loop))
        assert not asyncio.all_tasks(loop)
    finally:
        loop.close()

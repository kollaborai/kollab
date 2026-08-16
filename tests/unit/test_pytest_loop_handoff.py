"""Regression coverage for pytest event-loop ownership handoffs."""

import asyncio
import unittest

import pytest


@pytest.mark.asyncio
async def test_asyncio_fixture_seeds_isolated_handoff() -> None:
    """Leave pytest-asyncio's clean loop for the following isolated test."""
    await asyncio.sleep(0)


class TestIsolatedRunnerHandoff(unittest.IsolatedAsyncioTestCase):
    async def test_runner_does_not_orphan_pytest_clean_loop(self) -> None:
        await asyncio.sleep(0)


@pytest.fixture
def application_owned_loop():
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    policy.set_event_loop(loop)
    try:
        yield loop
    finally:
        assert not loop.is_closed()
        loop.close()
        policy.set_event_loop(None)


def test_asyncio_run_does_not_close_application_owned_loop(
    application_owned_loop,
) -> None:
    asyncio.run(asyncio.sleep(0))
    assert not application_owned_loop.is_closed()


def test_sync_hook_does_not_allocate_policy_loop() -> None:
    previous_policy = asyncio.get_event_loop_policy()
    fresh_policy = asyncio.DefaultEventLoopPolicy()
    asyncio.set_event_loop_policy(fresh_policy)
    try:
        assert not fresh_policy._local._set_called
        assert fresh_policy._local._loop is None
    finally:
        asyncio.set_event_loop_policy(previous_policy)

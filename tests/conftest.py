"""Shared pytest lifecycle safeguards for the test suite."""

import asyncio
from collections.abc import Generator

import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Close an ambient policy loop displaced during a test call.

    pytest-asyncio leaves a clean loop installed in the current event-loop
    policy after an async test. Both ``asyncio.run`` and
    ``IsolatedAsyncioTestCase`` can replace that loop without closing it, so
    its self-pipe can surface later as an unraisable ``ResourceWarning``.
    Keep an already-installed loop alive across every test call and close it
    only when the test displaced it. The private policy-local lookup avoids
    allocating a loop for ordinary synchronous tests.
    """
    policy = asyncio.get_event_loop_policy()
    policy_local = getattr(policy, "_local", None)
    ambient_loop = None
    if getattr(policy_local, "_set_called", False):
        ambient_loop = getattr(policy_local, "_loop", None)

    try:
        yield
    finally:
        current_loop = None
        if getattr(policy_local, "_set_called", False):
            current_loop = getattr(policy_local, "_loop", None)
        if (
            ambient_loop is not None
            and ambient_loop is not current_loop
            and not ambient_loop.is_closed()
            and not ambient_loop.is_running()
        ):
            ambient_loop.close()

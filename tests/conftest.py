"""Shared pytest lifecycle safeguards for the test suite."""

import asyncio
import threading
from collections.abc import Generator

import pytest

_state = threading.local()


def _policy_loop(policy: asyncio.AbstractEventLoopPolicy):
    """Return the current policy loop without allocating one."""
    policy_local = getattr(policy, "_local", None)
    if not getattr(policy_local, "_set_called", False):
        return None
    return getattr(policy_local, "_loop", None)


def pytest_fixture_post_finalizer(fixturedef, request) -> None:
    """Remember only the clean loop installed by pytest-asyncio."""
    del request
    if fixturedef.argname != "event_loop":
        return

    loop = _policy_loop(asyncio.get_event_loop_policy())
    if loop is not None and not loop.is_closed():
        _state.pytest_clean_loop = loop
    else:
        _state.pytest_clean_loop = None


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Close a pytest-owned loop displaced during a later test call.

    pytest-asyncio installs a fresh compatibility loop after each async test.
    Both ``asyncio.run`` and ``IsolatedAsyncioTestCase`` can replace that loop
    without closing it. Track its identity so the displaced pytest loop is
    closed while any application-owned replacement remains untouched.
    """
    del item
    owned_loop = getattr(_state, "pytest_clean_loop", None)

    try:
        yield
    finally:
        current_loop = _policy_loop(asyncio.get_event_loop_policy())
        if (
            owned_loop is not None
            and owned_loop is not current_loop
            and not owned_loop.is_closed()
            and not owned_loop.is_running()
        ):
            owned_loop.close()
            if getattr(_state, "pytest_clean_loop", None) is owned_loop:
                _state.pytest_clean_loop = None

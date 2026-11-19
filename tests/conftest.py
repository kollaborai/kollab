"""Shared pytest lifecycle safeguards for the test suite."""

import asyncio
import os
import subprocess
import threading
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from tests.process_scan_guard import (
    HOST_PROCESS_SCAN_MARKER,
    HOST_PROCESS_SCAN_OPTION,
    HOST_PROCESS_SCAN_SKIP_REASON,
    HOST_PROCESS_SCAN_TESTS,
    host_process_scan_environment_errors,
    host_process_scans_allowed,
    install_test_process_scan_guard,
)


@contextmanager
def _policy_api():
    """Read the asyncio policy API without tripping 3.14+ deprecation errors.

    The policy machinery is slated for removal in 3.16 and
    get_event_loop_policy() warns from 3.14 on. pytest-asyncio >= 1.4 manages
    its loops without the policy, so these reads are no-ops there; on
    3.12/3.13 they still drive the displaced-loop safeguard below.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield asyncio


_state = threading.local()


def pytest_addoption(parser) -> None:
    parser.addoption(
        HOST_PROCESS_SCAN_OPTION,
        action="store_true",
        default=False,
        help="allow marked host-wide process tests only in the approved CI job",
    )


def _host_process_scan_option_enabled(config) -> bool:
    return config.getoption("allow_host_process_enumeration", default=False)


def _host_process_scans_allowed(config) -> bool:
    return host_process_scans_allowed(_host_process_scan_option_enabled(config), os.environ)


def pytest_configure(config) -> None:
    """Reject an attempted opt-in unless every sanctioned CI identity matches."""
    if not _host_process_scan_option_enabled(config):
        return
    errors = host_process_scan_environment_errors(os.environ)
    if errors:
        raise pytest.UsageError(
            f"{HOST_PROCESS_SCAN_OPTION} requires the GitHub-hosted CI/tests job; "
            f"mismatched metadata: {', '.join(errors)}"
        )


def pytest_collection_modifyitems(config, items) -> None:
    """Skip approved live tests locally and reject known tests if unmarked."""
    allowed = _host_process_scans_allowed(config)
    tests_root = Path(__file__).resolve().parent
    for item in items:
        module_path = Path(str(item.path)).resolve().relative_to(tests_root).as_posix()
        original_name = getattr(item, "originalname", item.name)
        required_tests = HOST_PROCESS_SCAN_TESTS.get(module_path, frozenset())
        if original_name in required_tests and item.get_closest_marker(HOST_PROCESS_SCAN_MARKER) is None:
            item.add_marker(getattr(pytest.mark, HOST_PROCESS_SCAN_MARKER))
        if item.get_closest_marker(HOST_PROCESS_SCAN_MARKER) is not None and not allowed:
            item.add_marker(pytest.mark.skip(reason=HOST_PROCESS_SCAN_SKIP_REASON))


@pytest.fixture(autouse=True)
def _block_unmarked_host_process_enumeration(request, monkeypatch):
    """Block process discovery unless this marked test has CI authorization."""
    is_authorized_live_test = request.node.get_closest_marker(
        HOST_PROCESS_SCAN_MARKER
    ) is not None and _host_process_scans_allowed(request.config)
    if is_authorized_live_test:
        return
    install_test_process_scan_guard(psutil, subprocess, monkeypatch)


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path_factory, monkeypatch):
    """Point HOME at a scratch dir, so app startup in a test never migrates the developer's ~/.kollab.

    Building TerminalLLMChat runs initialize_system_prompt(), which moves seed
    skill copies aside; a unit run once did that to a real ~/.kollab/skills.
    A test that needs its own HOME still sets one.
    """
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture(autouse=True)
def _no_picker_warm(monkeypatch):
    """The model picker's background warm launches real harnesses (#953): a test that wants it sets 1."""
    monkeypatch.setenv("KOLLAB_PICKER_WARM", "0")


@pytest.fixture(autouse=True)
def _scratch_presence_roots(tmp_path, monkeypatch, request):
    """Keep presence scans away from the account home and shared Hub root."""
    from plugins.hub import presence

    # This test exercises the home resolver itself with a stubbed pwd record.
    if request.node.name != "test_system_home_directory_ignores_home_override":
        monkeypatch.setattr(presence, "_system_home_directory", lambda: tmp_path / "account")
    root = tmp_path / "hub-root"
    monkeypatch.setattr(presence, "hub_socket_root", lambda: root)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", root)


def _policy_loop(policy: asyncio.AbstractEventLoopPolicy):
    """Return the current policy loop without allocating one."""
    policy_local = getattr(policy, "_local", None)
    # 3.12/3.13: _set_called separates an implicit get_event_loop()
    # allocation from an explicit set_event_loop(). 3.14 removed implicit
    # allocation and the flag; there _loop is only ever set explicitly, so
    # treat a missing flag as "explicit" and trust the _loop read below.
    if not getattr(policy_local, "_set_called", True):
        return None
    return getattr(policy_local, "_loop", None)


def pytest_fixture_post_finalizer(fixturedef, request) -> None:
    """Remember only the clean loop installed by pytest-asyncio."""
    del request
    if fixturedef.argname != "event_loop":
        return

    with _policy_api() as _a:
        loop = _policy_loop(_a.get_event_loop_policy())
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
        with _policy_api() as _a:
            current_loop = _policy_loop(_a.get_event_loop_policy())
        if (
            owned_loop is not None
            and owned_loop is not current_loop
            and not owned_loop.is_closed()
            and not owned_loop.is_running()
        ):
            owned_loop.close()
            if getattr(_state, "pytest_clean_loop", None) is owned_loop:
                _state.pytest_clean_loop = None

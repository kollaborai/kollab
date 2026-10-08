"""kollab --web-ui reuses only an engine of its own version."""

import pytest

from kollabor.cli import _pick_engine_port


def test_reuses_an_engine_of_this_version():
    assert _pick_engine_port("0.12.0", {7433: "0.12.0"}.get) == (7433, True)


def test_starts_its_own_engine_when_none_runs():
    assert _pick_engine_port("0.12.0", {}.get) == (7433, False)


def test_leaves_a_stale_engine_running_and_takes_the_next_port():
    running = {7433: "0.10.2"}
    assert _pick_engine_port("0.12.0", running.get) == (7434, False)
    assert _pick_engine_port("0.12.0", {**running, 7434: "0.12.0"}.get) == (7434, True)


def test_gives_up_after_its_port_range():
    stale = {port: "0.10.2" for port in range(7433, 7443)}
    with pytest.raises(RuntimeError, match="7433-7442"):
        _pick_engine_port("0.12.0", stale.get)

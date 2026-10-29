"""L1: a finished join shows the founder, its fingerprint and the one-machine
warning in the terminal flow and the web Network panel; a mismatch shows its
plain line."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.panels.connect import ConnectJoinPanel
from kollabor.state.interface import enrollment_result
from plugins.altview import connect_altview
from plugins.altview.connect_altview import ConnectOutcome
from plugins.hub.connect_guide import JOIN_FAILURE_REASONS, join_check_text
from plugins.hub.network_control import NETWORK_MISMATCH_LINE, ONE_COUNTING_LINE

from .test_connect_attached_and_waiting import (
    _JOINED,
    _SYNTHETIC_CODE,
    _Bus,
    _daemon,
    _FakeRenderer,
    _key,
    _named,
    _settle,
)

FP = "4082c5d2abcd"
APPROVED = {"status": "approved", "founder": "ab" * 32, "founder_fingerprint": FP, "warning": ONE_COUNTING_LINE}


def test_check_text_names_the_fingerprint_and_the_warning():
    text = join_check_text(APPROVED)
    assert FP in text and ONE_COUNTING_LINE in text
    assert join_check_text({"status": "approved"}) == ""
    assert "ab" * 32 not in text


def test_the_mismatch_code_has_its_plain_line():
    assert JOIN_FAILURE_REASONS["network_mismatch"] == NETWORK_MISMATCH_LINE
    assert len(NETWORK_MISMATCH_LINE) <= 200


def test_enrollment_result_keeps_only_printable_bounded_check_strings():
    kept = enrollment_result(APPROVED)
    assert kept["founder_fingerprint"] == FP and kept["warning"] == ONE_COUNTING_LINE
    bad = enrollment_result({"status": "approved", "founder_fingerprint": "x" * 99, "warning": "a\x1bb"})
    assert bad == {"status": "approved"}


@pytest.mark.asyncio
async def test_the_daemon_passes_the_check_fields_through():
    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        return dict(APPROVED)

    plugin = _daemon(enroll)
    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    await _settle()
    result = started
    if started["status"] == "pending":
        result = await plugin._connect_enrollment_status(started["receipt_id"])
    assert result["founder_fingerprint"] == FP and result["warning"] == ONE_COUNTING_LINE


@pytest.mark.asyncio
async def test_a_mismatched_code_reads_back_as_the_plain_line():
    async def enroll(domain, code, on_submitted=None):
        return {"error": "network_mismatch"}

    plugin = _daemon(enroll)
    result = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    assert result == {"status": "failed", "reason": NETWORK_MISMATCH_LINE}


@pytest.mark.asyncio
async def test_the_terminal_form_shows_the_founder_and_the_warning(monkeypatch):
    monkeypatch.setattr(connect_altview, "_POLL_SECONDS", 0.0)
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return dict(APPROVED)

    plugin = _daemon(enroll)
    plugin._cli_args = SimpleNamespace(attach=False)
    stack = SimpleNamespace(push=AsyncMock())
    plugin.event_bus = _Bus(altview_stack_manager=stack)
    await plugin._open_connect_altview("kollabor.ai")
    view, _name = stack.push.await_args.args
    renderer = _FakeRenderer(size=(120, 30))
    await view.on_enter(renderer)
    for char in _SYNTHETIC_CODE.replace("-", ""):
        await view.handle_input(_key(char))
    await view.handle_input(_named("Enter"))
    gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)
    text = renderer.text()
    assert _JOINED in text and FP in text
    assert "a halt needs a second one" in text
    await view.on_complete()


def test_outcome_check_line_is_bounded():
    assert ConnectOutcome.approved("x", "", "a check").check == "a check"
    with pytest.raises(ValueError):
        ConnectOutcome.approved("x", "", "y" * 500)


def test_the_web_panel_shows_the_check_and_the_mismatch_line():
    ok = ConnectJoinPanel._outcome(dict(APPROVED, detail=_JOINED, note="n"), "kollabor.ai")
    assert FP in ok["message"] and ONE_COUNTING_LINE in ok["message"]
    bad = ConnectJoinPanel._outcome({"status": "failed", "reason": "network_mismatch"}, "kollabor.ai")
    assert bad["ok"] is False and NETWORK_MISMATCH_LINE in bad["message"]

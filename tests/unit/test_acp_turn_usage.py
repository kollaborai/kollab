"""Per-turn token usage from ACP prompt responses (#495).

codex-acp >= 2.3.0 reports the whole turn's token totals on the prompt
response. The bridge extracts them, the orchestrator settles them once per
(request, turn) into session stats, the engine snapshot carries them, and the
status widget labels last-request-only samples from older adapters instead of
letting them read as turn spend.
"""

import asyncio
from types import SimpleNamespace

import pytest
from acp.schema import PromptResponse, Usage

from kollabor.llm import acp_bridge as bridge_mod
from kollabor.llm import acp_orchestrator as orch_mod
from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor_tui.status.core_widgets import render_stats


USAGE = {
    "total_tokens": 83575,
    "input_tokens": 2000,
    "output_tokens": 3000,
    "thought_tokens": 777,
    "cached_read_tokens": 77798,
    "cached_write_tokens": 0,
}


def _usage():
    return Usage(
        totalTokens=83575, inputTokens=2000, outputTokens=3000,
        cachedReadTokens=77798, cachedWriteTokens=0, thoughtTokens=777,
    )


# === bridge: usage extraction ===

@pytest.mark.asyncio
async def test_prompt_response_usage_lands_on_result_and_end_frame(tmp_path):
    runner = ACPRunner(ACPProfile.builtin("codex-cli", cwd=str(tmp_path)))
    runner.session_id = "s1"
    runner.generation = 1
    runner._process = SimpleNamespace(returncode=None)

    class Connection:
        async def prompt(self, **kwargs):
            return PromptResponse(stopReason="end_turn", usage=_usage())

    runner._connection = Connection()
    future = asyncio.get_running_loop().create_future()
    admission = bridge_mod.ACPAdmission("request", "rpc", None, 1, 1, "prompt", future)
    result = await runner._run_prompt(admission, 1)
    assert result.usage == USAGE
    assert result.end_frame["acp_turn_usage"] == USAGE
    future.cancel()


@pytest.mark.asyncio
async def test_adapter_without_response_usage_reports_none(tmp_path):
    runner = ACPRunner(ACPProfile.builtin("claude-code", cwd=str(tmp_path)))
    runner.session_id = "s1"
    runner.generation = 1
    runner._process = SimpleNamespace(returncode=None)

    class Connection:
        async def prompt(self, **kwargs):
            return PromptResponse(stopReason="end_turn")

    runner._connection = Connection()
    future = asyncio.get_running_loop().create_future()
    admission = bridge_mod.ACPAdmission("request", "rpc", None, 1, 1, "prompt", future)
    result = await runner._run_prompt(admission, 1)
    assert result.usage is None
    assert result.end_frame["acp_turn_usage"] is None
    future.cancel()


# === orchestrator: one-shot settle ===

class Bus:
    def __init__(self):
        self.services = {}

    def get_service(self, name):
        return self.services.get(name)


@pytest.fixture(autouse=True)
def isolated_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir",
        lambda: tmp_path / "data",
    )


def _result(usage=None, request_id="r1", turn_id=1):
    return SimpleNamespace(
        request_id=request_id,
        turn_id=turn_id,
        usage=usage,
        end_frame={} if usage is None else {"acp_turn_usage": usage},
    )


def _orchestrator(stats=None):
    bus = Bus()
    if stats is not None:
        bus.services["llm_service"] = SimpleNamespace(session_stats=stats)
    return orch_mod.ACPOrchestrator(bus)


def test_settle_folds_whole_turn_totals_once():
    stats = {"total_input_tokens": 100, "total_output_tokens": 50}
    orchestrator = _orchestrator(stats)
    settled = orchestrator._settle_turn_usage(_result(dict(USAGE)))
    # input excludes cache, so the whole-turn input adds cached read/write.
    assert settled["input_total"] == 2000 + 77798 + 0
    assert settled["output_total"] == 3000 + 777
    assert stats["total_input_tokens"] == 100 + 2000 + 77798
    assert stats["total_output_tokens"] == 50 + 3000 + 777
    assert stats["total_cache_read_tokens"] == 77798
    assert stats["acp_turn_usage"] == USAGE


def test_settle_dedupes_replayed_terminal_events():
    stats = {"total_input_tokens": 0, "total_output_tokens": 0}
    orchestrator = _orchestrator(stats)
    assert orchestrator._settle_turn_usage(_result(dict(USAGE))) is not None
    assert orchestrator._settle_turn_usage(_result(dict(USAGE))) is None
    assert stats["total_input_tokens"] == 2000 + 77798
    # a different turn on the same request still settles.
    settled = orchestrator._settle_turn_usage(_result(dict(USAGE), turn_id=2))
    assert settled["input_total"] == 2000 + 77798
    assert stats["total_input_tokens"] == 2 * (2000 + 77798)


def test_settle_without_usage_touches_nothing():
    stats = {}
    orchestrator = _orchestrator(stats)
    assert orchestrator._settle_turn_usage(_result(None)) is None
    assert stats == {}


def test_settle_without_stats_service_still_reports_totals():
    orchestrator = _orchestrator()
    settled = orchestrator._settle_turn_usage(_result(dict(USAGE)))
    assert settled["input_total"] == 2000 + 77798
    assert settled["output_total"] == 3000 + 777


def test_settle_reads_end_frame_fallback_when_result_usage_missing():
    stats = {"total_input_tokens": 0, "total_output_tokens": 0}
    orchestrator = _orchestrator(stats)
    result = SimpleNamespace(request_id="r9", turn_id=1, usage=None,
                             end_frame={"acp_turn_usage": dict(USAGE)})
    assert orchestrator._settle_turn_usage(result)["input_total"] == 2000 + 77798


# === status widget: honesty labels ===

def _ctx(remote_state):
    return SimpleNamespace(remote_state=remote_state, llm_service=None)


def test_widget_labels_last_request_sample_on_old_adapters():
    state = {
        "messages": 5, "input_tokens": 10, "output_tokens": 20,
        "acp_usage": {"used": 27905, "size": 200000},
    }
    out = render_stats(60, _ctx(state))
    assert "last req" in out
    assert "tok/turn" not in out


def test_widget_shows_settled_turn_total_when_present():
    state = {
        "messages": 5, "input_tokens": 10, "output_tokens": 20,
        "acp_usage": {"used": 27905, "size": 200000},
        "acp_turn_usage": dict(USAGE),
    }
    out = render_stats(60, _ctx(state))
    assert "tok/turn" in out
    assert "last req" not in out


def test_widget_narrows_instead_of_lying_at_tiny_widths():
    state = {
        "messages": 5, "input_tokens": 10, "output_tokens": 20,
        "acp_usage": {"used": 27905, "size": 200000},
        "acp_turn_usage": dict(USAGE),
    }
    import re
    strip_ansi = re.compile(r"\x1b\[[0-9;]*m")
    for width in (30, 20, 8):
        out = strip_ansi.sub("", render_stats(width, _ctx(state)))
        # whatever fits, it never claims a per-turn total it cannot render
        assert len(out) <= width
        assert "last req" not in out

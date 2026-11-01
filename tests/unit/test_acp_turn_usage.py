"""Per-turn token usage from ACP prompt responses (#495).

codex-acp >= 2.3.0 reports the whole turn's token totals on the prompt
response. The bridge extracts them, the orchestrator settles them once per
(request, turn) into session stats, the engine snapshot carries them, and the
status widget labels last-request-only samples from older adapters instead of
letting them read as turn spend.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from acp.schema import PromptResponse, Usage

from kollabor.llm import acp_bridge as bridge_mod
from kollabor.llm import acp_orchestrator as orch_mod
from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.state.hub_client import HubStateClient
from kollabor.state.local import LocalStateService
from kollabor.state.refresher import WidgetStateRefresher
from kollabor.state.remote import RemoteStateService
from kollabor_ai.profile_manager import LLMProfile
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

    def register_service(self, name, service):
        self.services[name] = service


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


def test_missing_usage_clears_previous_turn_and_replays_do_not_clear_newer():
    stats = {"acp_turn_usage": dict(USAGE)}
    orchestrator = _orchestrator(stats)
    assert orchestrator._settle_turn_usage(_result(None)) is None
    assert "acp_turn_usage" not in stats
    newer = dict(USAGE, total_tokens=1234)
    orchestrator._settle_turn_usage(_result(newer, request_id="r2", turn_id=2))
    assert orchestrator._settle_turn_usage(_result(None)) is None
    assert stats["acp_turn_usage"] == newer


def test_delayed_old_completion_cannot_replace_new_turn_display(monkeypatch):
    monkeypatch.setattr(orch_mod, "publish_semantic", lambda *args, **kwargs: None)
    stats = {"acp_turn_usage": dict(USAGE)}
    orchestrator = _orchestrator(stats)
    orchestrator._on_runner_update({"kind": "turn_start", "request_id": "r1", "turn_id": 1, "generation": 1})
    assert "acp_turn_usage" not in stats
    orchestrator._on_runner_update({"kind": "turn_start", "request_id": "r2", "turn_id": 2, "generation": 1})
    orchestrator._settle_turn_usage(_result(dict(USAGE)))
    assert stats["total_input_tokens"] == 79798
    assert "acp_turn_usage" not in stats
    newer = dict(USAGE, total_tokens=1234)
    orchestrator._settle_turn_usage(_result(newer, request_id="r2", turn_id=2))
    assert stats["acp_turn_usage"] == newer


@pytest.mark.asyncio
async def test_harness_start_and_close_clear_previous_usage(monkeypatch):
    class FakeRunner:
        def __init__(self, profile, **kwargs):
            self.binding = None
            self.session_id = "test"

        async def start(self, binding=None):
            pass

        async def close(self):
            pass

        def advertised_config_options(self):
            return []

    monkeypatch.setattr(orch_mod, "ACPRunner", FakeRunner)
    monkeypatch.setattr(orch_mod, "publish_semantic", lambda *args, **kwargs: None)
    stats = {"acp_usage": {"used": 5}, "acp_turn_usage": dict(USAGE)}
    orchestrator = _orchestrator(stats)
    await orchestrator.start(LLMProfile(name="fake", provider="acp", command=("fake",)))
    assert "acp_usage" not in stats
    assert "acp_turn_usage" not in stats
    stats["acp_turn_usage"] = dict(USAGE)
    await orchestrator.close()
    assert "acp_turn_usage" not in stats


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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["local", "rpc", "hub"])
async def test_settlement_reaches_widget_through_real_state_pipeline(path):
    stats = {"acp_usage": {"used": 27905, "size": 200000}}
    llm = SimpleNamespace(session_stats=stats, conversation_manager=None)
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = orch_mod.ACPOrchestrator(bus)
    orchestrator._settle_turn_usage(_result(dict(USAGE)))
    local = LocalStateService(llm_service=llm, profile_manager=None)

    class RPC:
        async def call(self, method, params, timeout=None):
            if method != "state.get_session_stats":
                raise RuntimeError("not needed for this probe")
            snapshot = await local.get_session_stats()
            return json.loads(json.dumps(snapshot.to_dict()))

    state = {"local": local, "rpc": RemoteStateService(RPC()), "hub": HubStateClient(RPC())}[path]
    ctx = _ctx({})
    refresher = WidgetStateRefresher(ctx, state)
    await refresher.refresh_once()
    assert ctx.remote_state["acp_turn_usage"] == USAGE
    rendered = render_stats(60, ctx)
    assert "83K tok/turn" in rendered
    assert "27K/200K ctx" in rendered
    assert "last req" not in rendered

    # A later completion without response usage clears the actual snapshot,
    # including the previously populated remote widget, and labels the sample.
    orchestrator._settle_turn_usage(_result(None, request_id="r2", turn_id=2))
    await refresher.refresh_once()
    assert ctx.remote_state["acp_turn_usage"] == {}
    assert "last req" in render_stats(60, ctx)


def test_widget_shows_authoritative_usage_without_context_samples():
    out = render_stats(60, _ctx({"messages": 1, "acp_turn_usage": dict(USAGE)}))
    assert "83K tok/turn" in out
    assert "last req" not in out


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

"""Per-turn token usage from ACP prompt responses (#495).

codex-acp >= 2.3.0 reports prompt token totals on the response. The bridge
extracts them, the orchestrator settles them once per
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
    "output_tokens": 3777,
    "thought_tokens": 777,
    "cached_read_tokens": 77798,
    "cached_write_tokens": 0,
}


def _usage():
    return Usage(
        totalTokens=83575, inputTokens=2000, outputTokens=3777,
        cachedReadTokens=77798, cachedWriteTokens=0, thoughtTokens=777,
    )


# === bridge: usage extraction ===

@pytest.mark.asyncio
async def test_prompt_response_usage_lands_on_result_and_end_frame(tmp_path):
    runner = ACPRunner(ACPProfile.builtin("codex-cli", cwd=str(tmp_path)))
    # This test isolates response parsing; real launches set this from the
    # resolved adapter's recognized package identity and version.
    runner._prompt_usage_supported = True
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
    assert settled["output_total"] == 3777
    assert stats["total_input_tokens"] == 100 + 2000 + 77798
    assert stats["total_output_tokens"] == 50 + 3777
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


def test_codex_reasoning_is_already_in_output_and_replay_does_not_count_twice():
    # The 96,664-token response was observed on a real multi-tool Codex turn.
    usage = {
        "total_tokens": 96664, "input_tokens": 19337, "cached_read_tokens": 77184,
        "cached_write_tokens": 0, "output_tokens": 143, "thought_tokens": 66,
    }
    stats = {}
    orchestrator = _orchestrator(stats)
    settled = orchestrator._settle_turn_usage(_result(usage))
    assert settled["input_total"] == 96521
    assert settled["output_total"] == 143
    assert stats["total_input_tokens"] + stats["total_output_tokens"] == usage["total_tokens"]
    assert stats["total_cache_read_tokens"] == 77184
    assert orchestrator._settle_turn_usage(_result(usage)) is None
    assert stats["total_input_tokens"] + stats["total_output_tokens"] == usage["total_tokens"]


def test_claude_cache_read_and_write_are_distinct_and_accumulate_per_turn():
    # Claude's response has separate input, cache read, cache write and output.
    usage = {
        "total_tokens": 625, "input_tokens": 100, "cached_read_tokens": 200,
        "cached_write_tokens": 300, "output_tokens": 25,
    }
    stats = {}
    orchestrator = _orchestrator(stats)
    first = orchestrator._settle_turn_usage(_result(usage))
    second = orchestrator._settle_turn_usage(_result(usage, request_id="r2", turn_id=2))
    assert first["input_total"] == second["input_total"] == 600
    assert first["output_total"] == second["output_total"] == 25
    assert stats["total_input_tokens"] == 1200
    assert stats["total_output_tokens"] == 50
    assert stats["total_cache_read_tokens"] == 400
    assert stats["total_cache_creation_tokens"] == 600
    assert stats["total_input_tokens"] + stats["total_output_tokens"] == 2 * usage["total_tokens"]


@pytest.mark.parametrize("bad_field", [
    {"total_tokens": 1}, {"cached_read_tokens": -1}, {"output_tokens": "invalid"},
])
def test_inconsistent_adapter_breakdown_does_not_invent_spend(bad_field):
    stats = {"acp_turn_usage": dict(USAGE), "total_input_tokens": 5, "total_output_tokens": 6}
    orchestrator = _orchestrator(stats)
    inconsistent = dict(USAGE, **bad_field)
    assert orchestrator._settle_turn_usage(_result(inconsistent)) is None
    assert stats["total_input_tokens"] == 5
    assert stats["total_output_tokens"] == 6
    assert "acp_turn_usage" not in stats


def test_missing_required_component_does_not_imply_zero():
    stats = {"acp_turn_usage": dict(USAGE)}
    orchestrator = _orchestrator(stats)
    incomplete = dict(USAGE, total_tokens=77798 + 3777)
    del incomplete["input_tokens"]
    assert orchestrator._settle_turn_usage(_result(incomplete)) is None
    assert "total_input_tokens" not in stats
    assert "acp_turn_usage" not in stats


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
    newer = dict(USAGE, total_tokens=83576, output_tokens=3778)
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
    newer = dict(USAGE, total_tokens=83576, output_tokens=3778)
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


@pytest.mark.asyncio
async def test_pending_usage_settles_before_harness_replacement(monkeypatch):
    class FakeRunner:
        def __init__(self, profile, **kwargs):
            self.binding = None
            self.session_id = profile.name
            self.pending = None

        async def start(self, binding=None):
            pass

        def advertised_config_options(self):
            return []

        def admit(self, text, *, request_id, source, thread_id=None, origin=None):
            future = asyncio.get_running_loop().create_future()
            self.pending = bridge_mod.ACPAdmission(request_id, source, thread_id, 1, 1, text, future)
            return self.pending

        def finish(self):
            admission = self.pending
            admission.future.set_result(bridge_mod.ACPResult(
                1, self.session_id, "answer", "end_turn", (),
                {"type": "turn_end", "acp_turn_usage": dict(USAGE)},
                1, admission.request_id, admission.source, admission.thread_id, dict(USAGE),
            ))

        async def close(self):
            if self.pending is not None and not self.pending.future.done():
                self.finish()

    monkeypatch.setattr(orch_mod, "ACPRunner", FakeRunner)
    monkeypatch.setattr(orch_mod, "publish_semantic", lambda *args, **kwargs: None)
    stats = {}
    orchestrator = _orchestrator(stats)
    first = LLMProfile(name="first", provider="acp", command=("fake",))
    second = LLMProfile(name="second", provider="acp", command=("fake",))
    await orchestrator.start(first)
    try:
        old_runner = orchestrator.runner
        orchestrator.admit("first", request_id="first", source="rpc", record=False)
        await orchestrator.start(second)
        assert orchestrator.runner is not old_runner
        assert stats["total_input_tokens"] == 79798
        assert stats["total_output_tokens"] == 3777
        assert "acp_turn_usage" not in stats

        # The replacement starts turn numbering at 1 again; its usage must
        # still be counted and displayed instead of deduplicated as old work.
        orchestrator.admit("second", request_id="second", source="rpc", record=False)
        orchestrator.runner.finish()
        await asyncio.gather(*tuple(orchestrator._completion_tasks))
        assert stats["total_input_tokens"] == 2 * 79798
        assert stats["total_output_tokens"] == 2 * 3777
        assert stats["acp_turn_usage"] == USAGE
    finally:
        await orchestrator.close()


def test_settle_without_stats_service_still_reports_totals():
    orchestrator = _orchestrator()
    settled = orchestrator._settle_turn_usage(_result(dict(USAGE)))
    assert settled["input_total"] == 2000 + 77798
    assert settled["output_total"] == 3777


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

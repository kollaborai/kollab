"""Unit tests for the usage ledger core (issue #723). No providers, no network."""

import json
import os
import stat

import pytest

from kollabor_ai.pricing_registry import ModelPricing
from kollabor_ai.usage_ledger import (
    COST_NO_USAGE,
    COST_PRICED,
    COST_UNPRICED,
    KIND_BUDGET_ALERT,
    UsageLedger,
    budget_alerts,
    build_turn_record,
    price_usage,
    resolve_pricing,
    rollup,
)

DAY = 86400
UNKNOWN_PROVIDER = "acme"
UNKNOWN_MODEL = "widget-zeta-9"

# $2 / 1M prompt, $8 / 1M completion, cache reads at 10% of prompt.
TABLE = {
    "acme/known-model": {
        "prompt_per_million": 2.0,
        "completion_per_million": 8.0,
    }
}


def _turn(
    *,
    ts=1_000_000.0,
    agent="coder",
    lane="issue-723",
    pr="",
    provider=UNKNOWN_PROVIDER,
    model="known-model",
    usage_source="provider",
    tokens_in=1000,
    tokens_out=100,
    cache_read=0,
    cache_write=0,
    wall_ms=250,
    price_table=TABLE,
):
    return build_turn_record(
        ts=ts,
        agent=agent,
        lane=lane,
        pr=pr,
        provider=provider,
        model=model,
        usage_source=usage_source,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        wall_ms=wall_ms,
        price_table=price_table,
    )


# --- pricing -----------------------------------------------------------------


def test_unknown_price_is_unpriced_not_zero():
    """An unknown model must not be priced at 0.0 (the calculate_cost default)."""
    assert price_usage(None, 1000, 100, 0) is None

    row = _turn(provider=UNKNOWN_PROVIDER, model=UNKNOWN_MODEL, price_table={})
    assert row["cost_status"] == COST_UNPRICED
    assert row["cost_usd"] is None


def test_price_table_prices_a_known_model():
    row = _turn(tokens_in=1_000_000, tokens_out=0)
    assert row["cost_status"] == COST_PRICED
    assert row["cost_usd"] == pytest.approx(2.0)


def test_price_table_entry_without_model_key_is_resolved_by_bare_model():
    pricing = resolve_pricing(
        "other", "known-model", {"known-model": TABLE["acme/known-model"]}
    )
    assert pricing is not None
    assert pricing.prompt_per_token == pytest.approx(2.0 / 1_000_000)


def test_invalid_price_table_entry_is_ignored_and_stays_unpriced():
    bad = {"acme/known-model": {"prompt_per_million": "free-ish"}}
    row = _turn(price_table=bad)
    assert row["cost_status"] == COST_UNPRICED
    assert row["cost_usd"] is None


def test_negative_rate_is_rejected():
    bad = {"acme/known-model": {"prompt_per_million": -1, "completion_per_million": 1}}
    assert resolve_pricing(UNKNOWN_PROVIDER, "known-model", bad) is None


def test_cache_reads_bill_once_at_discount():
    """Provider prompt counts include cache reads; they bill once, discounted."""
    pricing = ModelPricing(
        prompt_per_token=2.0 / 1_000_000,
        completion_per_token=8.0 / 1_000_000,
        cache_discount=0.1,
    )
    # 1000 prompt of which 400 were cache reads, 100 completion.
    cost = price_usage(pricing, 1000, 100, 400)
    expected = 600 * 2e-6 + 100 * 8e-6 + 400 * 2e-6 * 0.1
    assert cost == pytest.approx(expected)


# --- missing usage is missing, not zero --------------------------------------


def test_turn_without_usage_has_null_tokens_and_no_cost():
    row = _turn(usage_source="none", tokens_in=0, tokens_out=0, cache_read=0)
    assert row["tokens_in"] is None
    assert row["tokens_out"] is None
    assert row["cache_read_tokens"] is None
    assert row["cost_usd"] is None
    assert row["cost_status"] == COST_NO_USAGE


def test_rollup_does_not_sum_missing_usage_as_zero():
    rows = [_turn(usage_source="none"), _turn(usage_source="none", ts=2.0)]
    (group,) = rollup(rows, ("agent",))
    assert group["turns"] == 2
    assert group["turns_without_usage"] == 2
    assert group["tokens_in"] is None
    assert group["cost_usd"] == 0.0


def test_rollup_sums_only_turns_that_reported_usage():
    rows = [_turn(tokens_in=1000), _turn(usage_source="none", ts=2.0)]
    (group,) = rollup(rows, ("agent",))
    assert group["tokens_in"] == 1000
    assert group["turns_without_usage"] == 1


def test_estimated_usage_is_kept_and_marked():
    row = _turn(usage_source="estimated")
    assert row["usage_source"] == "estimated"
    assert row["tokens_in"] == 1000
    assert row["cost_status"] == COST_PRICED


# --- rollups -------------------------------------------------------------------


def test_lane_total_per_agent_and_per_model():
    rows = [
        _turn(agent="coder", lane="issue-723", tokens_in=1000, tokens_out=0),
        _turn(agent="coder", lane="issue-723", tokens_in=500, tokens_out=0, ts=2.0),
        _turn(agent="tester", lane="issue-723", tokens_in=300, tokens_out=0, ts=3.0),
        _turn(agent="coder", lane="other", tokens_in=9999, tokens_out=0, ts=4.0),
    ]
    groups = {
        (g["lane"], g["agent"], g["model_key"]): g
        for g in rollup(rows, ("lane", "agent", "model_key"))
    }
    assert groups[("issue-723", "coder", "acme/known-model")]["tokens_in"] == 1500
    assert groups[("issue-723", "tester", "acme/known-model")]["tokens_in"] == 300

    by_lane = {g["lane"]: g for g in rollup(rows, ("lane",))}
    assert by_lane["issue-723"]["turns"] == 3
    assert by_lane["issue-723"]["tokens_in"] == 1800


def test_rollup_counts_unpriced_separately_and_excludes_from_cost():
    rows = [
        _turn(tokens_in=1_000_000, tokens_out=0),
        _turn(
            provider=UNKNOWN_PROVIDER,
            model=UNKNOWN_MODEL,
            tokens_in=1_000_000,
            tokens_out=0,
            price_table={},
            ts=2.0,
        ),
    ]
    (group,) = rollup(rows, ("agent",))
    assert group["unpriced_turns"] == 1
    assert group["cost_usd"] == pytest.approx(2.0)


def test_rollup_ignores_non_turn_rows():
    alert = {"kind": KIND_BUDGET_ALERT, "ts": 1.0, "key": "k"}
    assert rollup([alert], ("agent",)) == []


def test_rollup_groups_by_day_utc():
    rows = [_turn(ts=0.0), _turn(ts=DAY + 1.0)]
    days = {g["day"]: g["turns"] for g in rollup(rows, ("day",))}
    assert days == {"1970-01-01": 1, "1970-01-02": 1}


# --- storage -----------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_append_creates_owner_only_file(tmp_path):
    ledger = UsageLedger(tmp_path / "usage" / "ledger.jsonl")
    ledger.append(_turn())
    mode = stat.S_IMODE(os.stat(ledger.path).st_mode)
    assert mode == 0o600


def test_append_round_trips_and_stores_no_prompt_text(tmp_path):
    ledger = UsageLedger(tmp_path / "ledger.jsonl")
    ledger.append(_turn())
    (row,) = ledger.read()
    assert row["tokens_in"] == 1000
    assert "content" not in row and "prompt" not in row and "output" not in row


def test_read_skips_corrupt_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = UsageLedger(path)
    ledger.append(_turn())
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    ledger.append(_turn(ts=2.0))
    assert len(ledger.read()) == 2


def test_prune_drops_rows_past_retention(tmp_path):
    now = 100 * DAY
    ledger = UsageLedger(
        tmp_path / "ledger.jsonl", retention_days=30, clock=lambda: now
    )
    ledger.append(_turn(ts=now - 31 * DAY))
    ledger.append(_turn(ts=now - 29 * DAY))
    removed = ledger.prune(now)
    assert removed == 1
    (kept,) = ledger.read()
    assert kept["ts"] == pytest.approx(now - 29 * DAY)


def test_prune_on_missing_file_is_a_noop(tmp_path):
    ledger = UsageLedger(tmp_path / "absent.jsonl")
    assert ledger.prune(0) == 0
    assert not (tmp_path / "absent.jsonl").exists()


# --- budget alerts -------------------------------------------------------------


def _rule(**overrides):
    rule = {
        "scope": "lane",
        "name": "issue-723",
        "threshold_usd": 1.0,
        "window_hours": 24,
    }
    rule.update(overrides)
    return rule


def test_budget_alert_fires_when_threshold_crossed():
    now = 10 * DAY + 100.0
    rows = [_turn(ts=now - 10, tokens_in=1_000_000, tokens_out=0)]  # $2
    (alert,) = budget_alerts(rows, [_rule()], now)
    assert alert["kind"] == KIND_BUDGET_ALERT
    assert alert["cost_usd"] == pytest.approx(2.0)
    assert alert["scope"] == "lane"


def test_budget_alert_does_not_fire_below_threshold():
    now = 10 * DAY
    rows = [_turn(ts=now - 10, tokens_in=100_000, tokens_out=0)]  # $0.20
    assert budget_alerts(rows, [_rule()], now) == []


def test_budget_alert_fires_at_most_once_per_window():
    now = 10 * DAY + 100
    rows = [_turn(ts=now - 10, tokens_in=1_000_000, tokens_out=0)]
    first = budget_alerts(rows, [_rule()], now)
    assert len(first) == 1
    # The alert row is now in the ledger: a repeat in the same window is silent.
    assert budget_alerts(rows + first, [_rule()], now + 60) == []


def test_budget_alert_fires_again_in_the_next_window():
    day_one = 10 * DAY
    rows = [_turn(ts=day_one - 10, tokens_in=1_000_000, tokens_out=0)]
    first = budget_alerts(rows, [_rule()], day_one)
    rows.append(_turn(ts=day_one + DAY + 5, tokens_in=1_000_000, tokens_out=0))
    second = budget_alerts(rows + first, [_rule()], day_one + DAY + 10)
    assert len(second) == 1


def test_budget_alert_agent_scope_and_unpriced_not_counted():
    now = 10 * DAY + 100
    rows = [
        _turn(ts=now - 10, agent="coder", tokens_in=1_000_000, tokens_out=0),
        _turn(
            ts=now - 5,
            agent="coder",
            provider=UNKNOWN_PROVIDER,
            model=UNKNOWN_MODEL,
            tokens_in=50_000_000,
            tokens_out=0,
            price_table={},
        ),
    ]
    (alert,) = budget_alerts(rows, [_rule(scope="agent", name="coder")], now)
    assert alert["cost_usd"] == pytest.approx(2.0)
    assert alert["unpriced_turns"] == 1


def test_budget_alert_ignores_rows_outside_the_window():
    now = 10 * DAY
    rows = [_turn(ts=now - 2 * DAY, tokens_in=1_000_000, tokens_out=0)]
    assert budget_alerts(rows, [_rule()], now) == []


@pytest.mark.parametrize(
    "bad_rule",
    [
        {"scope": "model", "name": "x", "threshold_usd": 1},
        {"scope": "lane", "name": "", "threshold_usd": 1},
        {"scope": "lane", "name": "x", "threshold_usd": 0},
        {"scope": "lane", "name": "x", "threshold_usd": "many"},
        {"scope": "lane", "name": "x", "threshold_usd": 1, "window_hours": 0},
    ],
)
def test_malformed_budget_rules_are_ignored(bad_rule):
    now = 10 * DAY
    rows = [_turn(ts=now - 1, tokens_in=1_000_000, tokens_out=0)]
    assert budget_alerts(rows, [bad_rule], now) == []


def test_alerts_round_trip_through_json():
    now = 10 * DAY + 100
    rows = [_turn(ts=now - 1, tokens_in=1_000_000, tokens_out=0)]
    (alert,) = budget_alerts(rows, [_rule()], now)
    assert json.loads(json.dumps(alert)) == alert

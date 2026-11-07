"""Adversarial cross-agent review policy (#726).

The policy decides whether a PR head gets an adversarial review, who may
review it, and when the owner must step in. It performs no I/O: callers pass
the clock and the recorded state.
"""

from __future__ import annotations

import pytest

from kollabor.review.adversarial import (
    DEFAULT_CONFIG,
    AdversarialReviewConfig,
    Finding,
    ReviewLedger,
    assign_reviewer,
    decide,
    record_review,
    render_verdict,
)

HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40
HEAD_D = "d" * 40
BASE = "0" * 40


def cfg(**over):
    raw = {
        "enabled": True,
        "round_caps": {"low": 1, "medium": 2, "high": 3, "critical": 3},
        "cooldown": 0,
        "reviewer_pool": ["r1", "r2", "r3"],
    }
    raw.update(over)
    return AdversarialReviewConfig.from_config(raw)


def test_default_config_is_off_and_matches_loader_defaults():
    from kollabor_config.loader import ConfigLoader

    assert DEFAULT_CONFIG["enabled"] is False
    assert AdversarialReviewConfig.from_config({}).enabled is False
    base = ConfigLoader(config_manager=None, fast_mode=True).get_base_config()
    assert base["adversarial_review"] == DEFAULT_CONFIG


def test_disabled_never_reviews():
    ledger = ReviewLedger()
    d = decide(AdversarialReviewConfig.from_config({}), ledger, HEAD_A, now=0)
    assert d.action == "skip" and "disabled" in d.reason


def test_first_head_is_reviewed_and_same_sha_is_never_reviewed_twice():
    c, ledger = cfg(), ReviewLedger()
    assert decide(c, ledger, HEAD_A, now=0).action == "review"
    record_review(c, ledger, HEAD_A, [Finding("f1", "low")], now=0)
    again = decide(c, ledger, HEAD_A, now=10_000)
    assert again.action == "skip" and "already reviewed" in again.reason


def test_low_severity_gets_one_round_then_escalates():
    c, ledger = cfg(), ReviewLedger()
    record_review(c, ledger, HEAD_A, [Finding("f1", "low")], now=0)
    d = decide(c, ledger, HEAD_B, now=100)
    assert d.action == "escalate"
    # escalation sticks: it never silently turns back into a review
    assert decide(c, ledger, HEAD_C, now=200).action == "escalate"


def test_medium_gets_two_rounds_and_high_gets_three():
    c, ledger = cfg(), ReviewLedger()
    record_review(c, ledger, HEAD_A, [Finding("f1", "medium")], now=0)
    assert decide(c, ledger, HEAD_B, now=1).action == "review"
    record_review(c, ledger, HEAD_B, [Finding("f2", "medium")], now=1)
    assert decide(c, ledger, HEAD_C, now=2).action == "escalate"

    c2, l2 = cfg(), ReviewLedger()
    record_review(c2, l2, HEAD_A, [Finding("g1", "high")], now=0)
    record_review(c2, l2, HEAD_B, [Finding("g2", "high")], now=1)
    assert decide(c2, l2, HEAD_C, now=2).action == "review"
    record_review(c2, l2, HEAD_C, [Finding("g3", "critical")], now=2)
    assert decide(c2, l2, HEAD_D, now=3).action == "escalate"


def test_highest_severity_seen_sets_the_cap():
    c, ledger = cfg(), ReviewLedger()
    record_review(c, ledger, HEAD_A, [Finding("f1", "low")], now=0)
    record_review(c, ledger, HEAD_B, [Finding("f2", "critical")], now=1)
    # low alone would have escalated after one round; critical allows three
    assert decide(c, ledger, HEAD_C, now=2).action == "review"


def test_only_new_or_regressed_findings_use_a_round():
    c, ledger = cfg(), ReviewLedger()
    r1 = record_review(c, ledger, HEAD_A, [Finding("f1", "medium")], now=0)
    assert r1.reopened and ledger.rounds == 1
    # f1 still open, nothing new: not a new round
    r2 = record_review(c, ledger, HEAD_B, [Finding("f1", "medium")], now=1)
    assert not r2.reopened and ledger.rounds == 1
    # f1 fixed (absent), then back: regression reopens a round
    record_review(c, ledger, HEAD_C, [], now=2)
    r4 = record_review(c, ledger, HEAD_D, [Finding("f1", "medium")], now=3)
    assert r4.regressed == ["f1"] and r4.reopened and ledger.rounds == 2


def test_cooldown_blocks_until_elapsed():
    c, ledger = cfg(cooldown=600), ReviewLedger()
    record_review(c, ledger, HEAD_A, [Finding("f1", "high")], now=1000)
    waiting = decide(c, ledger, HEAD_B, now=1100)
    assert waiting.action == "wait" and waiting.retry_after == 500
    assert decide(c, ledger, HEAD_B, now=1600).action == "review"


def test_clean_review_passes_and_further_heads_still_get_one_review_each():
    c, ledger = cfg(), ReviewLedger()
    out = record_review(c, ledger, HEAD_A, [], now=0)
    assert out.verdict == "PASS" and not out.reopened and ledger.rounds == 0
    assert decide(c, ledger, HEAD_B, now=1).action == "review"


def test_reviewer_is_never_author_or_lane_or_fixer():
    pool = ["Ruby", "peridot", "lapis"]
    assert assign_reviewer(pool, author="ruby", lane=[]) == "peridot"
    assert assign_reviewer(pool, author="ruby", lane=["PERIDOT"]) == "lapis"
    assert (
        assign_reviewer(pool, author="ruby", lane=["peridot"], fixers=["lapis"]) is None
    )


def test_unknown_severity_and_bad_config_fail_loudly():
    c, ledger = cfg(), ReviewLedger()
    with pytest.raises(ValueError, match="severity"):
        record_review(c, ledger, HEAD_A, [Finding("f1", "urgent")], now=0)
    with pytest.raises(ValueError, match="round_caps"):
        AdversarialReviewConfig.from_config({"round_caps": {"low": 0}})
    with pytest.raises(ValueError, match="cooldown"):
        AdversarialReviewConfig.from_config({"cooldown": -1})


def test_verdict_records_shas_commands_result_and_no_reviewer_identity():
    text = render_verdict(
        head_sha=HEAD_A,
        base_sha=BASE,
        commands=["pytest tests/unit -q"],
        findings=[Finding("f1", "high")],
    )
    assert HEAD_A in text and BASE in text and "pytest tests/unit -q" in text
    assert "FAIL" in text and "f1" in text
    with pytest.raises(ValueError):
        render_verdict(head_sha=HEAD_A, base_sha=BASE, commands=[], findings=[])
    with pytest.raises(ValueError):
        render_verdict(head_sha="abc", base_sha=BASE, commands=["x"], findings=[])


def test_ledger_round_trips_through_dict():
    c, ledger = cfg(), ReviewLedger()
    record_review(c, ledger, HEAD_A, [Finding("f1", "low")], now=5)
    again = ReviewLedger.from_dict(ledger.to_dict())
    assert decide(c, again, HEAD_B, now=6).action == "escalate"
    assert decide(c, again, HEAD_A, now=6).action == "skip"

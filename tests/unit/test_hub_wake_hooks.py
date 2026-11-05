"""Event hooks that wake agents (#727): triggers, targets, dedupe, limits."""

from plugins.hub.wake_hooks import (
    HOOK_NAMES,
    WakeEvent,
    WakeHookEngine,
    default_hook_config,
)


class Cfg:
    """Dotted-path config reader with the same ``get`` shape as ConfigService."""

    def __init__(self, values=None):
        self.values = values or {}

    def get(self, path, default=None):
        return self.values.get(path, default)


def _on(*names, **extra):
    values = {}
    for name in names:
        values[f"plugins.hub.hooks.{name}.enabled"] = True
        values[f"plugins.hub.hooks.{name}.cooldown"] = 0
    values.update(extra)
    return WakeHookEngine(Cfg(values).get)


def _review(**overrides):
    fields = dict(
        name="review_ready",
        pr="42",
        sha="abc1234",
        actor="reviewer",
        coder="lapis",
        koordinator="koordinator",
    )
    fields.update(overrides)
    return WakeEvent(**fields)


def test_every_hook_is_off_by_default():
    engine = WakeHookEngine(Cfg().get)
    assert HOOK_NAMES == (
        "review_ready",
        "dependency_clear",
        "idle_nudge",
        "auto_rereview",
        "undelivered_messages",
        "koordinator_heartbeat",
    )
    assert engine.handle(_review(), now=1000.0) == []
    for name in HOOK_NAMES:
        assert default_hook_config()[name]["enabled"] is False


def test_review_ready_wakes_the_coder_and_koordinator_once_per_head():
    engine = _on("review_ready")
    first = engine.handle(_review(), now=1000.0)
    assert {a.target for a in first} == {"lapis", "koordinator"}
    assert {a.hook for a in first} == {"review_ready"}
    assert engine.handle(_review(), now=1001.0) == []
    # A new head is a new event key and wakes again.
    again = engine.handle(_review(sha="def5678"), now=1002.0)
    assert {a.target for a in again} == {"lapis", "koordinator"}


def test_events_the_target_caused_are_ignored():
    engine = _on("review_ready")
    actions = engine.handle(_review(actor="lapis"), now=1000.0)
    assert [a.target for a in actions] == ["koordinator"]


def test_a_hook_never_triggers_another_hook():
    engine = _on("review_ready")
    assert engine.handle(_review(from_hook=True), now=1000.0) == []


def test_dependency_clear_wakes_dependents_from_the_event():
    engine = _on("dependency_clear")
    event = WakeEvent(
        name="dependency_clear",
        pr="7",
        sha="s1",
        actor="koordinator",
        dependents=("peridot", "ruby"),
    )
    assert [a.target for a in engine.handle(event, now=1.0)] == ["peridot", "ruby"]


def test_configured_targets_replace_the_defaults():
    engine = _on(
        "review_ready",
        **{"plugins.hub.hooks.review_ready.targets": ["ruby", "@koordinator"]},
    )
    assert {a.target for a in engine.handle(_review(), now=1.0)} == {
        "ruby",
        "koordinator",
    }


def test_idle_nudge_waits_for_the_threshold_and_open_work():
    engine = _on(
        "idle_nudge",
        **{"plugins.hub.hooks.idle_nudge.thresholds": {"minutes": 10}},
    )
    quiet = WakeEvent(name="idle_nudge", agent="ruby", age_seconds=300, open_work=True)
    assert engine.handle(quiet, now=1.0) == []
    done = WakeEvent(name="idle_nudge", agent="ruby", age_seconds=900, open_work=False)
    assert engine.handle(done, now=2.0) == []
    stuck = WakeEvent(name="idle_nudge", agent="ruby", age_seconds=900, open_work=True)
    assert [a.target for a in engine.handle(stuck, now=3.0)] == ["ruby"]


def test_auto_rereview_needs_open_findings_and_configured_reviewers():
    engine = _on(
        "auto_rereview",
        **{"plugins.hub.hooks.auto_rereview.targets": ["reviewer-agent"]},
    )
    clean = WakeEvent(name="auto_rereview", pr="9", sha="n1", actor="lapis")
    assert engine.handle(clean, now=1.0) == []
    dirty = WakeEvent(
        name="auto_rereview", pr="9", sha="n1", actor="lapis", open_findings=True
    )
    actions = engine.handle(dirty, now=2.0)
    assert [(a.target, a.kind) for a in actions] == [("reviewer-agent", "rereview")]
    # No reviewer configured: nothing is guessed.
    assert _on("auto_rereview").handle(dirty, now=2.0) == []


def test_undelivered_message_alerts_koordinator_and_owner_after_threshold():
    engine = _on(
        "undelivered_messages",
        **{"plugins.hub.hooks.undelivered_messages.targets": ["@koordinator", "@owner"]},
    )
    fresh = WakeEvent(
        name="undelivered_messages",
        agent="ruby",
        ref="m1",
        age_seconds=30,
        koordinator="koordinator",
        owner="operator",
    )
    assert engine.handle(fresh, now=1.0) == []
    late = WakeEvent(
        name="undelivered_messages",
        agent="ruby",
        ref="m1",
        age_seconds=600,
        koordinator="koordinator",
        owner="operator",
    )
    actions = engine.handle(late, now=2.0)
    assert {a.target for a in actions} == {"koordinator", "operator"}
    assert "ruby" in actions[0].text


def test_missed_heartbeat_raises_an_alert_to_configured_targets():
    engine = _on(
        "koordinator_heartbeat",
        **{"plugins.hub.hooks.koordinator_heartbeat.targets": ["operator"]},
    )
    event = WakeEvent(
        name="koordinator_heartbeat", agent="koordinator", ref="t1", age_seconds=1800
    )
    actions = engine.handle(event, now=1.0)
    assert [(a.target, a.kind) for a in actions] == [("operator", "alert")]


def test_cooldown_blocks_a_second_wake_of_the_same_target():
    engine = _on(
        "review_ready",
        **{"plugins.hub.hooks.review_ready.cooldown": 60},
    )
    assert len(engine.handle(_review(), now=1000.0)) == 2
    assert engine.handle(_review(sha="n2"), now=1030.0) == []
    assert len(engine.handle(_review(sha="n3"), now=1061.0)) == 2


def test_hourly_cap_limits_wakes_and_recovers_after_the_hour():
    engine = _on(
        "review_ready",
        **{"plugins.hub.hooks.review_ready.max_per_hour": 3},
    )
    sent = 0
    for i in range(4):
        sent += len(engine.handle(_review(sha=f"s{i}"), now=100.0 + i))
    assert sent == 3
    assert len(engine.handle(_review(sha="late"), now=100.0 + 3700)) == 2


def test_malformed_config_falls_back_to_safe_defaults():
    engine = _on(
        "review_ready",
        **{
            "plugins.hub.hooks.review_ready.cooldown": "soon",
            "plugins.hub.hooks.review_ready.max_per_hour": None,
            "plugins.hub.hooks.review_ready.targets": "lapis",
        },
    )
    assert {a.target for a in engine.handle(_review(), now=1.0)} == {"lapis"}


def test_unknown_event_name_is_ignored():
    engine = _on("review_ready")
    assert engine.handle(WakeEvent(name="nope"), now=1.0) == []

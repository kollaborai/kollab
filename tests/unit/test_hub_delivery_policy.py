from plugins.hub.delivery import DeliveryDecision, DeliveryPolicy, SenderContext


def test_local_self_sender_delivers_even_when_dns_unknown():
    policy = DeliveryPolicy(strict_local_unknown=False)

    decision = policy.decide_sender(
        SenderContext(
            sender="lapis",
            is_self=True,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision == DeliveryDecision(
        mode="deliver",
        reason="local self sender",
        wake_allowed=True,
        trace_level="info",
    )


def test_local_unknown_same_project_warns_but_delivers_by_default():
    policy = DeliveryPolicy(strict_local_unknown=False)

    decision = policy.decide_sender(
        SenderContext(
            sender="sapphire",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "deliver"
    assert decision.reason == "local unknown same project"
    assert decision.trace_level == "warning"


def test_local_unknown_same_project_rejects_in_strict_mode():
    policy = DeliveryPolicy(strict_local_unknown=True)

    decision = policy.decide_sender(
        SenderContext(
            sender="sapphire",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="unknown",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "reject"
    assert decision.reason == "local unknown sender in strict mode"


def test_remote_unknown_quarantines_without_wake():
    policy = DeliveryPolicy(strict_local_unknown=False)

    decision = policy.decide_sender(
        SenderContext(
            sender="remote-lapis",
            is_self=False,
            is_coordinator=False,
            is_remote=True,
            approval_state="unknown",
            same_project=False,
            force=False,
        )
    )

    assert decision.mode == "quarantine"
    assert decision.reason == "remote unknown sender"
    assert decision.wake_allowed is False


def test_rejected_sender_fails_even_with_same_project():
    policy = DeliveryPolicy(strict_local_unknown=False)

    decision = policy.decide_sender(
        SenderContext(
            sender="aquamarine",
            is_self=False,
            is_coordinator=False,
            is_remote=False,
            approval_state="rejected",
            same_project=True,
            force=False,
        )
    )

    assert decision.mode == "reject"
    assert decision.reason == "sender rejected"

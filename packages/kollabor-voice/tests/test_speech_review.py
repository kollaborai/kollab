import pytest
from kollabor_voice.speech_review import SpeechDecision, format_decision


@pytest.mark.parametrize(
    "text",
    [
        "One. Two. Three.",
        "# Summary\nDone.",
        "- Done\n- Ready",
        "```python\npass\n```",
        "See https://example.com.",
        "Open /tmp/test.log.",
        "Run `pytest`.",
        "<terminal>pwd</terminal>",
        "| Status | Ready |",
        "word " * 51,
    ],
)
def test_unsuitable_formats_are_rejected_before_any_model_or_synthesis(text):
    assert format_decision(text).decision == "rewrite"


def test_short_spoken_text_still_requires_semantic_review_and_period_is_silent():
    assert format_decision("The tests passed. I will check playback next.") is None
    assert format_decision(" . \n").decision == "silent"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 2])
def test_invalid_model_confidence_is_rejected(value):
    from kollabor_voice.control import VoiceError

    with pytest.raises(VoiceError):
        SpeechDecision.from_wire(SpeechDecision("speak", confidence=value).to_wire())


@pytest.mark.parametrize("choice", ["speak", "rewrite"])
@pytest.mark.parametrize("confidence", [0.56, 0.8, 0.95])
def test_uncertain_style_decisions_defer_instead_of_rejecting(choice, confidence):
    from types import SimpleNamespace

    from kollabor_voice.laya_worker import predict_speech

    agent = SimpleNamespace(
        predict=lambda *_: {
            "answers": {
                "spoken_style": {
                    "choice": choice,
                    "probabilities": {choice: confidence},
                }
            }
        }
    )
    decision = predict_speech(agent, "The code example is on your screen.")
    assert decision.decision == (choice if confidence >= 0.8 else "defer")
    assert SpeechDecision.from_wire(decision.to_wire()) == decision

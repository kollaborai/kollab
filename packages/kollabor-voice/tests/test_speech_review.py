import pytest
from kollabor_voice.speech_review import SpeechDecision, format_decision, speakable


@pytest.mark.parametrize(
    "text",
    [
        "# Summary\nDone.",
        "- Done\n- Ready",
        "```python\npass\n```",
        "See https://example.com.",
        "Open /tmp/test.log.",
        "Run `pytest`.",
        "<terminal>pwd</terminal>",
        "| Status | Ready |",
    ],
)
def test_unsuitable_formats_are_rejected_before_any_model_or_synthesis(text):
    assert format_decision(text).decision == "rewrite"


def test_length_is_preferred_never_a_reason_to_rewrite():
    long = "The build passed on every platform. " * 6 + "word " * 60
    assert format_decision(long) is None


def test_speakable_takes_out_what_cannot_be_said():
    assert speakable("Run `pytest` now.") == "Run now."
    assert speakable("Open [the docs](https://x.y) now.") == "Open the docs now."
    assert speakable("I edited ~/dev/app.py and it passes.") == "I edited and it passes."
    assert speakable("| Status | Ready |") == "Status Ready"
    assert speakable("```python\npass\n```") == ""
    assert format_decision(speakable("# Done\n- Ready\n<b>Set</b>")) is None


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

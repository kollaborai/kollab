"""spoken_form: what reaches TTS once a reply's markup is gone (#voice-speakable)."""

import pytest
from kollabor_voice.spoken_form import spoken_form


@pytest.mark.parametrize(
    ("reply", "spoken"),
    [
        # Markdown escapes the model writes inside <spoken_text> were read as "backslash".
        (r"I fixed it \- the parser now strips tags\.", "I fixed it, the parser now strips tags."),
        (r"Step 1\. Open it\.", "Step 1. Open it."),
        (r"Step 2 \(optional\): save it .", "Step 2 (optional): save it."),
        (r"Sure \*thing\*.", "Sure thing."),
        # Arrows were read as symbols, or cut to "-" / "=".
        ("Version 0.18.4 → 0.18.5 shipped.", "Version 0.18.4 to 0.18.5 shipped."),
        ("Run tests -> ship => done.", "Run tests to ship to done."),
        ("Rename old ← new.", "Rename old from new."),
        # The bracketed voice context echoed back, and channel tags left over.
        ('[you already said: "On it."] Sure.', "Sure."),
        ("<spoken_text>Hi there</spoken_text>", "Hi there"),
        # Markdown and symbols.
        ("**Done.** ## Heading", "Done. Heading"),
        ("- one\n- two", "one two"),
        ("Use `kollab --hub status` now.", "Use kollab hub status now."),
        ("See [the docs](https://x.y/z) or https://a.b", "See the docs or link"),
        ("and/or 3/4 pass & x = 5", "and or 3/4 pass and x equals 5"),
        ("Fixed #345 ~ok~", "Fixed 345 ok"),
        ("Wait... really?", "Wait... really?"),
        ("e.g. this, i.e. that.", "e.g. this, i.e. that."),
        ("```py\nprint(1)\n``` Done.", "Done."),
    ],
)
def test_reply_becomes_speakable(reply, spoken):
    assert spoken_form(reply) == spoken


@pytest.mark.parametrize("reply", [".", r"\.", " . ", "*", "→", "```code```", ""])
def test_nothing_to_say_is_empty_never_a_spoken_period(reply):
    assert spoken_form(reply) == ""


def test_spoken_form_is_stable():
    once = spoken_form(r"Done \- a → b, see `x/y`.")
    assert spoken_form(once) == once

"""spoken_form: what reaches TTS once a reply's markup is gone (#voice-speakable)."""

import pytest
from kollabor_voice.spoken_form import spoken_form


@pytest.mark.parametrize(
    ("reply", "spoken"),
    [
        # Markdown escapes the model writes inside <spoken_text> were read as "backslash".
        (
            r"I fixed it \- the parser now strips tags\.",
            "I fixed it, the parser now strips tags.",
        ),
        (r"Step 1\. Open it\.", "Step one. Open it."),
        (r"Step 2 \(optional\): save it .", "Step two (optional): save it."),
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
        ("and/or 3/4 pass & x = 5", "and or 3/4 pass and x equals five"),
        ("Fixed #345 ~ok~", "Fixed 345 ok"),
        ("Wait... really?", "Wait... really?"),
        ("e.g. this, i.e. that.", "e.g. this, i.e. that."),
        ("```py\nprint(1)\n``` Done.", "Done."),
        # A standalone whole number is said as words; versions, decimals, times,
        # fractions, money, grouped digits and tokens like x86 keep their digits.
        ("I changed 3 files.", "I changed three files."),
        ("374 tests passed", "three hundred seventy-four tests passed"),
        ("1, 2 and 3", "one, two and three"),
        ("1000000 rows", "one million rows"),
        ("2026", "two thousand twenty-six"),
        ("0 errors", "zero errors"),
        ("version 0.18.5", "version 0.18.5"),
        ("at 10:30", "at 10:30"),
        ("3/4 done", "3/4 done"),
        ("2.5 GB", "2.5 GB"),
        ("12,000 rows", "12,000 rows"),
        ("$5 or 50%", "$5 or 50%"),
        ("1st try on x86 with v2", "1st try on x86 with v2"),
        ("007", "007"),
        # Digit groups joined into a run keep their digits: dates, phones, ranges.
        ("on 2026-10-29", "on 2026-10-29"),
        ("call (555) 123-4567", "call (555) 123-4567"),
        ("+1 555 123 4567", "+1 555 123 4567"),
        ("3-5 files", "3-5 files"),
        ("file_2 and step_10", "file 2 and step 10"),
        ("refs #374 and #123456", "refs 374 and 123456"),
        ("-5 degrees", "-5 degrees"),
        ("1234567890 bytes", "1234567890 bytes"),
    ],
)
def test_reply_becomes_speakable(reply, spoken):
    assert spoken_form(reply) == spoken


@pytest.mark.parametrize("reply", [".", r"\.", " . ", "*", "→", "```code```", ""])
def test_nothing_to_say_is_empty_never_a_spoken_period(reply):
    assert spoken_form(reply) == ""


def test_spoken_form_is_stable():
    for reply in (
        r"Done \- a → b, see `x/y`.",
        "I changed 3 files, see v2.txt on 2026-10-29.",
    ):
        once = spoken_form(reply)
        assert spoken_form(once) == once

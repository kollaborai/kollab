"""The still-working lines voice says while the agent has not answered."""

from kollabor_voice.acks import LINES, pick


def test_only_still_working_lines_remain():
    assert set(LINES) == {"still_working"}


def test_pick_never_repeats_the_previous_line():
    first = pick("still_working")
    assert all(pick("still_working", first) != first for _ in range(30))

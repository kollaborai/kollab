"""Request markers must match whole words, not substrings.

Substring matching turned ordinary status reports into task assignments:
"permission UI fixes landed" matched the "fix" marker, so the receiver
auto-minted a TaskCard and the task-cron reminded it every 5 minutes.
"""

import pytest

from plugins.hub.plugin import _compile_marker_pattern


@pytest.mark.parametrize(
    "text,expected",
    [
        # substrings must no longer fire
        ("prefix and suffix handling", False),
        ("permission UI fixes landed", False),
        ("budget hit 40k tokens", False),
        ("rerun the suite", False),
        ("reviewed and validated", False),
        # real requests still fire
        ("fix the parser", True),
        ("please check the logs", True),
        ("run the tests", True),
        ("is that ok?", True),
        # markers starting with a non-word char keep working
        ("[work assignment] do the thing", True),
    ],
)
def test_word_boundaries(text, expected):
    pattern = _compile_marker_pattern(
        ("?", "please", "run ", "check ", "fix", "get ", "review", "[work assignment")
    )
    assert bool(pattern.search(text)) is expected


def test_markers_are_case_insensitive_via_caller_normalization():
    """The matcher itself is case-sensitive; callers lowercase first."""
    pattern = _compile_marker_pattern(("fix",))
    assert pattern.search("Fix it") is None
    assert pattern.search("Fix it".lower()) is not None

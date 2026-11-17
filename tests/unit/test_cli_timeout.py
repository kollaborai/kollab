import pytest

from kollabor.cli import parse_timeout


@pytest.mark.parametrize(
    ("timeout", "expected_seconds"),
    [("0.5s", 0.5), ("0.5min", 30), ("0.5h", 1800)],
)
def test_parse_timeout_preserves_fractional_durations(timeout, expected_seconds):
    assert parse_timeout(timeout) == pytest.approx(expected_seconds)

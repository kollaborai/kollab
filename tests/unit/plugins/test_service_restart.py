"""Background-service restart and crash-loop decisions."""

import pytest


@pytest.mark.parametrize(
    ("policy", "code", "crashes", "now", "expected"),
    [
        ("never", 1, [], 10, (None, False)),
        ("on-failure", 0, [], 10, (None, False)),
        ("always", 0, [], 10, (2, False)),
        ("on-failure", 1, [1], 10, (2, False)),
        ("on-failure", 1, [1, 2, 3, 4], 10, (16, False)),
        ("on-failure", 1, [1, 2, 3, 4, 5], 10, (None, True)),
        ("on-failure", 1, [1, 2, 3, 4, 605], 606, (2, False)),
    ],
)
def test_restart_policy(policy, code, crashes, now, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.services import restart_decision

    assert restart_decision(policy, code, crashes, now) == expected

"""Plugin package Git source validation (issue #1028)."""

import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/example/plugin", ("https://github.com/example/plugin", None)),
        ("https://github.com/example/plugin#release/1.2", ("https://github.com/example/plugin", "release/1.2")),
        ("ssh://github.com/example/plugin#main", ("ssh://github.com/example/plugin", "main")),
        ("git@github.com:example/plugin#v1", ("git@github.com:example/plugin", "v1")),
        ("/tmp/plugin", None),
        ("file:///tmp/plugin", None),
        ("https://user:secret@example.com/repo", None),
        ("https://user@example.com/repo", None),
        ("ssh://user@example.com/repo", None),
        ("https://example.com/repo#-bad", None),
        ("https://example.com/repo#bad ref", None),
        ("https://example.com/repo#", None),
        ("https://example.com/repo#main#other", None),
        ("https://example.com/repo?token=secret", None),
    ],
)
def test_parse_git_source(source, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import parse_git_source

    if expected is None:
        with pytest.raises(ValueError):
            parse_git_source(source)
    else:
        assert parse_git_source(source) == expected

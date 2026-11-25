import pytest


@pytest.mark.parametrize(
    ("path", "query", "expected"),
    [
        ("/plugins/voice/api/items", b"kind=note", "voice"),
        ("/plugins/voice/settings", b"kind=installed", "voice"),
        ("/plugins/voice/settings", b"kind=built-in", None),
        ("/plugins/voice/settings", b"kind=installed&kind=built-in", None),
    ],
)
def test_plugin_token_kind_filter_only_applies_to_settings(path, query, expected):
    from kollabor_engine.routes.plugins import _plugin_token_target

    assert _plugin_token_target("GET", path, query) == expected

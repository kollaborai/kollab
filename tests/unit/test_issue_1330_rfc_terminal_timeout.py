from pathlib import Path


def test_unified_tool_loading_rfc_uses_configured_foreground_timeout() -> None:
    rfc = (
        Path(__file__).parents[2]
        / "docs/architecture/rfcs/RFC-2026-04-11-unified-tool-loading.md"
    ).read_text()

    assert "Foreground commands use the configured terminal timeout" in rfc
    assert "Foreground terminal has a default timeout of 60 seconds." not in rfc

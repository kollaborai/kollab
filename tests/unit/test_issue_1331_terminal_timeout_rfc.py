from pathlib import Path


def test_unified_tool_loading_rfc_reports_foreground_terminal_default() -> None:
    rfc = Path(__file__).parents[2] / "docs/architecture/rfcs/RFC-2026-04-11-unified-tool-loading.md"

    text = rfc.read_text(encoding="utf-8")

    assert "Foreground terminal has a default timeout of 120 seconds." in text
    assert "Foreground terminal has a default timeout of 60 seconds." not in text

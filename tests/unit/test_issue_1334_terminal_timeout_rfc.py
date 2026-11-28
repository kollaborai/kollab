from pathlib import Path


def test_unified_tool_loading_rfc_documents_foreground_terminal_default():
    repository_root = Path(__file__).resolve().parents[2]
    rfc = (
        repository_root
        / "docs/architecture/rfcs/RFC-2026-04-11-unified-tool-loading.md"
    )

    content = rfc.read_text()

    assert "Foreground terminal has a default timeout of 120 seconds." in content
    assert "Foreground terminal has a default timeout of 60 seconds." not in content

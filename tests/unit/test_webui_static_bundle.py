"""The source Web UI must serve the build just verified in this checkout."""

from fastapi.testclient import TestClient
from kollabor_webui import server


def configure_bundles(monkeypatch, tmp_path):
    source = tmp_path / "frontend" / "dist"
    packaged = tmp_path / "static" / "assistant"
    monkeypatch.setattr(server, "SOURCE_ASSISTANT_STATIC_DIR", source)
    monkeypatch.setattr(server, "ASSISTANT_STATIC_DIR", packaged)
    return source, packaged


def test_fresh_source_bundle_wins_over_stale_packaged_copy(monkeypatch, tmp_path):
    source, packaged = configure_bundles(monkeypatch, tmp_path)
    (source / "assets").mkdir(parents=True)
    packaged.mkdir(parents=True)
    (source / "index.html").write_text('<script src="/assets/current.js"></script>')
    (source / "assets" / "current.js").write_text("/* current ACP cards */")
    (packaged / "index.html").write_text("stale bundle")
    with TestClient(server.create_app()) as client:
        assert "current.js" in client.get("/").text
        assert client.get("/assets/current.js").text == "/* current ACP cards */"


def test_installed_package_uses_its_bundle_without_source_output(monkeypatch, tmp_path):
    _, packaged = configure_bundles(monkeypatch, tmp_path)
    packaged.mkdir(parents=True)
    (packaged / "index.html").write_text("packaged ACP UI")
    with TestClient(server.create_app()) as client:
        assert client.get("/").text == "packaged ACP UI"

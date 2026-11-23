"""The entry page's import map is the one inline script the CSP allows, by its hash (#1028)."""

import base64
import hashlib

from fastapi.testclient import TestClient
from kollabor_webui import server

IMPORT_MAP = '{"imports":{"react":"./assets/plugin-runtime-react-abc.js"}}'


def serve(monkeypatch, tmp_path, index: str) -> TestClient:
    monkeypatch.delenv("KOLLAB_FRONTEND_DIST", raising=False)
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(index)
    (dist / "assets" / "app.js").write_text("export {}")
    monkeypatch.setattr(server, "SOURCE_ASSISTANT_STATIC_DIR", dist)
    monkeypatch.setattr(server, "ASSISTANT_STATIC_DIR", tmp_path / "static" / "assistant")
    return TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


def test_entry_page_csp_allows_its_import_map_by_hash(monkeypatch, tmp_path):
    digest = base64.b64encode(hashlib.sha256(IMPORT_MAP.encode()).digest()).decode()
    index = f'<html><head><script type="importmap">{IMPORT_MAP}</script></head><body></body></html>'
    with serve(monkeypatch, tmp_path, index) as client:
        # The root and every SPA route serve the same page, so each carries the hash.
        for path in ("/", "/plugin", "/learn"):
            policy = client.get(path).headers["content-security-policy"]
            assert policy == f"script-src 'self' 'sha256-{digest}'", path
        # Assets keep the plain policy: only the page holds the import map.
        assert client.get("/assets/app.js").headers["content-security-policy"] == "script-src 'self'"


def test_entry_page_without_import_map_keeps_plain_policy(monkeypatch, tmp_path):
    with serve(monkeypatch, tmp_path, "<html><head></head><body></body></html>") as client:
        assert client.get("/").headers["content-security-policy"] == "script-src 'self'"


def test_other_inline_scripts_never_gain_a_hash(monkeypatch, tmp_path):
    index = '<html><head><script>alert(1)</script><script type="module">import "x"</script></head></html>'
    with serve(monkeypatch, tmp_path, index) as client:
        assert client.get("/").headers["content-security-policy"] == "script-src 'self'"

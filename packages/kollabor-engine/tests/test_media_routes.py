"""Route tests for the media and artifact endpoints and the history overlay (#1088)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.media_store import list_for, save
from kollabor_engine.server import create_app

from kollabor_config.config_utils import get_project_data_dir

PNG = "data:image/png;base64,AQID"
JPEG = "data:image/jpeg;base64,BAUF"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


def conversations_dir() -> Path:
    return get_project_data_dir() / "conversations"


class TestMediaRoutes:
    async def test_serves_stored_image(self, client: AsyncClient) -> None:
        record = save(conversations_dir(), "s1", PNG)
        response = await client.get(f"/media/{record.ref}")
        assert response.status_code == 200
        assert response.content == bytes([1, 2, 3])
        assert response.headers["content-type"].startswith("image/png")
        assert "immutable" in response.headers.get("cache-control", "")

    async def test_lists_a_session_media(self, client: AsyncClient) -> None:
        refs = [save(conversations_dir(), "s2", url).ref for url in (PNG, JPEG)]
        response = await client.get("/media", params={"session_id": "s2"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == "s2"
        assert [entry["ref"] for entry in payload["media"]] == refs

    async def test_unknown_and_bad_refs_404(self, client: AsyncClient) -> None:
        assert (await client.get("/media/20261114-211500-00000000")).status_code == 404
        assert (await client.get("/media/../etc/passwd")).status_code in (400, 404)

    async def test_sessions_are_isolated(self, client: AsyncClient) -> None:
        save(conversations_dir(), "s3", PNG)
        response = await client.get("/media", params={"session_id": "other"})
        assert response.json()["media"] == []


class TestArtifactRoutes:
    async def test_distinct_long_sessions_do_not_share_artifacts(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        import kollabor_engine.routes.artifacts as artifacts

        first = "a" * 80 + "x"
        second = "a" * 80 + "y"
        monkeypatch.setattr(artifacts, "_conversations_dir", conversations_dir)
        root = artifacts._artifacts_root(first)
        root.mkdir(parents=True, exist_ok=True)
        (root / "private.output").write_text("first session only")

        listed = await client.get(f"/sessions/{second}/artifacts")

        assert listed.status_code == 200
        assert listed.json()["artifacts"] == []

    async def test_list_and_read(self, client: AsyncClient) -> None:
        root = conversations_dir() / "tool_outputs" / "s4"
        root.mkdir(parents=True, exist_ok=True)
        (root / "shell-exec-call1-aaa.output").write_text("line one\nline two")
        (root / "notes.txt").write_text("ignored")

        listed = await client.get("/sessions/s4/artifacts")
        assert listed.status_code == 200
        entries = listed.json()["artifacts"]
        assert [entry["ref"] for entry in entries] == ["shell-exec-call1-aaa.output"]
        assert entries[0]["bytes"] == len("line one\nline two")
        assert entries[0]["name"] == "shell-exec-call1-aaa"

        read = await client.get("/sessions/s4/artifacts/shell-exec-call1-aaa.output")
        assert read.status_code == 200
        assert read.json()["content"] == "line one\nline two"
        assert read.json()["truncated"] is False

    async def test_rejects_bad_refs(self, client: AsyncClient) -> None:
        root = conversations_dir() / "tool_outputs" / "s5"
        root.mkdir(parents=True, exist_ok=True)
        (root / "real.output").write_text("x")
        assert (
            await client.get("/sessions/s5/artifacts/../../secrets.txt")
        ).status_code in (400, 404)
        assert (await client.get("/sessions/s5/artifacts/notes.txt")).status_code in (
            400,
            404,
        )
        assert (
            await client.get("/sessions/s5/artifacts/20261114-000000-00000000.output")
        ).status_code == 404

    async def test_unknown_session_lists_empty(self, client: AsyncClient) -> None:
        response = await client.get("/sessions/never-was/artifacts")
        assert response.status_code == 200
        assert response.json()["artifacts"] == []


class TestHistoryOverlay:
    # The route is called directly (the established pattern in
    # test_history_route_*): the overlay logic is under test, not the
    # HTTP auth layer.
    @staticmethod
    def stub_registry(monkeypatch, session_id, history):
        import kollabor_engine.routes.sessions as sessions_routes

        session = SimpleNamespace(
            get_history_nonblocking=AsyncMock(return_value={"history": history})
        )
        monkeypatch.setattr(
            sessions_routes, "get_session_registry", lambda: SimpleNamespace(
                get=lambda sid: session if sid == session_id else None
            )
        )

    async def test_rows_carry_stored_media(self, monkeypatch):
        from kollabor_engine.routes.sessions import get_history

        refs = [save(conversations_dir(), "s6", url).ref for url in (PNG, JPEG)]
        history = [
            {"role": "user", "content": "look [image1] and [image2]"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "plain"},
        ]
        self.stub_registry(monkeypatch, "s6", history)
        page = await get_history("s6")
        rows = page["history"]
        assert [m["ref"] for m in rows[0]["media"]] == refs
        assert "media" not in rows[1] and "media" not in rows[2]

    async def test_compacted_history_binds_surviving_rows_to_latest_media(self, monkeypatch):
        from kollabor_engine.routes.sessions import get_history

        refs = [save(conversations_dir(), "s10", url).ref for url in (PNG, JPEG)]
        # The daemon replaced its history with a compacted snapshot, dropping
        # the first sender while retaining the later sender.
        history = [{"role": "user", "content": "later [image1]"}]
        self.stub_registry(monkeypatch, "s10", history)
        page = await get_history("s10")
        assert [m["ref"] for m in page["history"][0]["media"]] == [refs[1]]

    async def test_older_page_skips_bound_images(self, monkeypatch):
        from kollabor_engine.routes.sessions import get_history

        refs = [save(conversations_dir(), "s7", url).ref for url in (PNG, JPEG)]
        history = [
            {"role": "user", "content": "first [image1]"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second [image2]"},
        ]
        self.stub_registry(monkeypatch, "s7", history)
        page = await get_history("s7", limit=1)
        assert page["start_index"] == 2
        rows = page["history"]
        assert [m["ref"] for m in rows[0]["media"]] == [refs[1]]

    async def test_page_without_images_is_untouched(self, monkeypatch):
        from kollabor_engine.routes.sessions import get_history

        save(conversations_dir(), "s9", PNG)
        history = [{"role": "user", "content": "plain words"}]
        self.stub_registry(monkeypatch, "s9", history)
        page = await get_history("s9")
        assert all("media" not in row for row in page["history"])

    async def test_unknown_session_404(self):
        from fastapi import HTTPException
        from kollabor_engine.routes.sessions import get_history

        with pytest.raises(HTTPException) as excinfo:
            await get_history("never-was")
        assert excinfo.value.status_code == 404


def test_list_endpoint_matches_store(tmp_path: Path) -> None:
    refs = [save(tmp_path, "s8", url).ref for url in (PNG, JPEG)]
    listed = list_for(tmp_path, "s8")
    assert [entry["ref"] for entry in listed] == refs
    assert listed[0]["mime"] == "image/png"

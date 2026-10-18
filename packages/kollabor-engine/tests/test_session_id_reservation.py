"""A generated session name can collide within a minute: redraw it, never fail the launch."""

import kollabor_engine.routes.sessions as sessions_routes  # type: ignore[import-not-found]
import pytest
from fastapi import HTTPException


def test_a_generated_name_that_is_taken_is_redrawn(monkeypatch):
    names = iter(["taken", "pending", "free"])
    monkeypatch.setattr(sessions_routes, "generate_session_name", lambda: next(names))
    monkeypatch.setattr(sessions_routes, "_pending_session_ids", {"pending"})

    reserved = sessions_routes._reserve_session_id({"taken": object()}, None)

    assert reserved == "free"
    assert "free" in sessions_routes._pending_session_ids


def test_generated_names_that_are_all_taken_end_in_409(monkeypatch):
    monkeypatch.setattr(sessions_routes, "generate_session_name", lambda: "taken")

    with pytest.raises(HTTPException) as refused:
        sessions_routes._reserve_session_id({"taken": object()}, None)

    assert refused.value.status_code == 409


def test_a_requested_id_that_is_taken_is_a_409_without_a_redraw(monkeypatch):
    monkeypatch.setattr(
        sessions_routes,
        "generate_session_name",
        lambda: pytest.fail("a requested id is never replaced by a generated one"),
    )

    with pytest.raises(HTTPException) as refused:
        sessions_routes._reserve_session_id({"mine": object()}, "mine")

    assert refused.value.status_code == 409 and "mine" in refused.value.detail

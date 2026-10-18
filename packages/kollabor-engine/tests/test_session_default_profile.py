"""POST /sessions that names no profile starts on the active one, as New Session does.

A team launch sent the profile literally named "default" for a member with none of its
own. That built-in auto-detect profile has no provider on a computer whose saved profile
is the active one, so every member answered "LLM provider not available" while chats
started from New Session worked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kollabor_engine.routes import sessions
from starlette.requests import Request

import kollabor_ai


class _Profiles:
    """ProfileManager with one saved profile that is active, one more, and the built-in "default"."""

    active_profile_name = "fake"

    def get_profile(self, name):
        return SimpleNamespace(name=name) if name in ("fake", "fast", "default") else None


@pytest.fixture
def started(monkeypatch):
    """The profile name each POST /sessions would start a daemon on."""
    names: list[str] = []

    class _Session:
        def __init__(self, **kwargs):
            names.append(kwargs["profile"].name)
            self.session_id = kwargs["session_id"]

        async def initialize(self):
            pass

        def to_dict(self):
            return {"session_id": self.session_id}

    monkeypatch.setattr(kollabor_ai, "ProfileManager", _Profiles)
    monkeypatch.setattr(sessions, "get_session_registry", dict)
    monkeypatch.setattr(sessions, "EngineSession", _Session)
    return names


def seat_agent(monkeypatch, preferred):
    """The bundle POST /sessions finds for ``agent`` names ``preferred`` in its agent.json."""
    found = {"system_prompt": "", "profile": preferred, "mcp_servers": []}
    monkeypatch.setattr(sessions, "_resolve_agent", lambda name, workspace: found)


async def create(**body):
    request = Request({"type": "http", "method": "POST", "path": "/sessions", "headers": [], "query_string": b""})
    return await sessions.create_session(sessions.CreateSessionRequest(**body), request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, "fake"),  # nothing named: the active profile
        ({"profile": "fast"}, "fast"),  # what the caller named wins
        ({"profile": "default"}, "default"),  # an explicit pick of the built-in stays that
    ],
)
async def test_the_profile_is_the_callers_else_the_active_one(started, body, expected):
    await create(**body)
    assert started == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [({}, "fast"), ({"profile": "default"}, "fast"), ({"profile": "fake"}, "fake")],
)
async def test_an_agents_own_profile_applies_only_when_the_caller_names_none(monkeypatch, started, body, expected):
    seat_agent(monkeypatch, "fast")
    await create(agent="coder", **body)
    assert started == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [({}, "fake"), ({"profile": "default"}, "default"), ({"profile": "fast"}, "fast")],
)
async def test_an_agent_that_prefers_default_has_no_preference(monkeypatch, started, body, expected):
    # The coder bundle's agent.json says "profile": "default"; engineering and startup teams seat it.
    seat_agent(monkeypatch, "default")
    await create(agent="coder", **body)
    assert started == [expected]


@pytest.mark.asyncio
async def test_an_unknown_profile_is_a_404(started):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await create(profile="nope")
    assert raised.value.status_code == 404 and started == []

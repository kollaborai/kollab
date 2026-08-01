"""ChatGPT codex backend model catalog: request shape, parsing, ranking.

Both halves of the /model-shows-nothing-for-openai bug live here:

  * ``GET /models`` 400s without a ``client_version`` query param (and returns
    an empty list for versions below 1.0.0)
  * the backend answers ``{"models": [{"slug": ...}]}``, not the plain OpenAI
    ``{"data": [{"id": ...}]}``

Plus ``pick_best_model``, which used to prefer any slug containing "codex" --
that now selects a spark tier or the internal auto-review model over the
frontier.
"""

import asyncio
import json

import pytest

from kollabor_ai.oauth import openai_oauth

# Trimmed real payload (GET /models?client_version=1.0.0, 2026-07-29).
CODEX_PAYLOAD = {
    "models": [
        {
            "slug": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "context_window": 272000,
            "default_reasoning_level": "low",
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
                {"effort": "high"},
                {"effort": "xhigh"},
                {"effort": "max"},
                {"effort": "ultra"},
            ],
        },
        {"slug": "gpt-5.6-terra", "context_window": 272000},
        {"slug": "gpt-5.4-mini", "context_window": 272000},
        {"slug": "gpt-5.3-codex-spark", "context_window": 128000},
        {"slug": "codex-auto-review", "context_window": 272000},
    ]
}


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return json.loads(json.dumps(self._payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Records the requested URL, replays a canned response."""

    calls: list = []
    status = 200
    payload = CODEX_PAYLOAD

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url):
        type(self).calls.append(url)
        return _FakeResponse(self.status, self.payload)


@pytest.fixture
def fake_session(monkeypatch):
    _FakeSession.calls = []
    _FakeSession.status = 200
    _FakeSession.payload = CODEX_PAYLOAD
    monkeypatch.setattr(openai_oauth.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def test_query_sends_client_version(fake_session):
    models = asyncio.run(openai_oauth.query_codex_model_details("tok", "acct"))

    url = fake_session.calls[0]
    assert f"client_version={openai_oauth.CODEX_CLIENT_VERSION}" in url
    # below 1.0.0 the backend answers 200 with an empty catalog
    assert tuple(int(p) for p in openai_oauth.CODEX_CLIENT_VERSION.split(".")) >= (
        1,
        0,
        0,
    )
    assert [m["slug"] for m in models][0] == "gpt-5.6-sol"


def test_parses_slug_shape_and_keeps_metadata(fake_session):
    models = asyncio.run(openai_oauth.query_codex_model_details("tok"))

    assert [m["slug"] for m in models] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.4-mini",
        "gpt-5.3-codex-spark",
        "codex-auto-review",
    ]
    # effort levels survive -- the picker and effort plumbing both read them
    efforts = [level["effort"] for level in models[0]["supported_reasoning_levels"]]
    assert efforts == ["low", "medium", "high", "xhigh", "max", "ultra"]
    assert models[0]["context_window"] == 272000


def test_parses_plain_openai_shape(fake_session):
    fake_session.payload = {"data": [{"id": "gpt-5.6"}, {"id": ""}]}
    models = asyncio.run(openai_oauth.query_codex_model_details("tok"))
    assert [m["slug"] for m in models] == ["gpt-5.6"]


def test_ids_wrapper_and_error_paths(fake_session):
    assert asyncio.run(openai_oauth.query_codex_models("tok"))[0] == "gpt-5.6-sol"

    fake_session.status = 400
    assert asyncio.run(openai_oauth.query_codex_models("tok")) == []


@pytest.mark.parametrize(
    "models,expected",
    [
        # frontier beats the spark/auto-review slugs that "codex" matching hit
        ([m["slug"] for m in CODEX_PAYLOAD["models"]], "gpt-5.6-sol"),
        # flagship beats a lighter tier of the same version
        (["gpt-5.4-mini", "gpt-5.4"], "gpt-5.4"),
        # a dated snapshot must not out-rank the flagship: matching every number
        # in the id turned 2026-07-09 into extra version components, and the
        # longer vector won
        (["gpt-5.6-sol", "gpt-5.6-mini-2026-07-09"], "gpt-5.6-sol"),
        (["gpt-5.4-2026-01-01", "gpt-5.6-sol"], "gpt-5.6-sol"),
        # ...but a dated id still beats a genuinely older version
        (["gpt-5.4", "gpt-5.6-2026-07-09"], "gpt-5.6-2026-07-09"),
        # helper slug is never selected, even when it is the only alternative
        (["codex-auto-review", "gpt-5.3-codex-spark"], "gpt-5.3-codex-spark"),
        # ...unless it is all there is (better than a bogus fallback)
        (["codex-auto-review"], "codex-auto-review"),
        ([], "codex"),
    ],
)
def test_pick_best_model(models, expected):
    assert openai_oauth.pick_best_model(models) == expected

"""PUT /profiles/{name} against a real ProfileManager: every field the body can carry applies."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile, ProfileManager


class _Manager(ProfileManager):
    """Real update logic, no config file."""

    def __init__(self):
        self._profiles = {
            "edit-me": LLMProfile(name="edit-me", provider="openai", model="gpt-4")
        }
        self._active_profile_name = "other"

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True


@pytest_asyncio.fixture
async def client(monkeypatch):
    import kollabor_engine.routes.profiles as profile_routes
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    monkeypatch.setattr(profile_routes, "_get_profile_manager", _Manager)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_update_applies_streaming_tools_and_the_other_body_fields(client):
    response = await client.put(
        "/profiles/edit-me",
        json={
            "streaming": False,
            "supports_tools": False,
            "timeout": 30,
            "top_p": 0.5,
            "extra_headers": {"x-probe": "1"},
            "temperature": 1.2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["streaming"], body["supports_tools"]) == (False, False)
    assert (body["timeout"], body["top_p"], body["temperature"]) == (30, 0.5, 1.2)
    assert body["extra_headers"] == {"x-probe": "1"}

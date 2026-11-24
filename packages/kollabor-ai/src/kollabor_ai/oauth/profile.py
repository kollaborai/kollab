"""Shared OpenAI OAuth profile creation for /login and Setup."""

from typing import Any


def save_openai_oauth_profile(profile_manager: Any, tokens: Any, model: str) -> str:
    """Create or update the profile used by ChatGPT device-code sign-in."""
    from kollabor_ai.oauth.openai_oauth import CODEX_API_BASE_URL
    from kollabor_ai.profile_manager import LLMProfile

    extra_headers = {"originator": "kollab"}
    if tokens.account_id:
        extra_headers["ChatGPT-Account-Id"] = tokens.account_id

    profile_name = "openai-oauth"
    existing = profile_manager.get_profile(profile_name)
    if existing:
        profile_manager.update_profile(
            profile_name,
            api_key=tokens.access_token,
            base_url=CODEX_API_BASE_URL,
            model=model,
            provider="openai_responses",
        )
        existing.auth_type = "oauth"
        existing.extra_headers = extra_headers
    else:
        profile_manager._profiles[profile_name] = LLMProfile(
            name=profile_name,
            provider="openai_responses",
            model=model,
            api_key=tokens.access_token,
            base_url=CODEX_API_BASE_URL,
            extra_headers=extra_headers,
            description="OpenAI OAuth (ChatGPT account)",
            auth_type="oauth",
        )
    return profile_name

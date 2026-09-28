"""Managed profile behavior over the private enrollment state overlay."""

from unittest.mock import patch

from nacl.signing import SigningKey

from kollabor_ai import profile_manager as profile_manager_module
from kollabor_ai.profile_manager import ProfileManager
from kollabor_config import provisioned_state as provisioned_state_module
from kollabor_config.provisioned_state import ProvisionedStateFile
from plugins.hub.provisioning import (
    NetworkPreferences,
    ProfilePreferences,
    ProvisioningCredential,
    ProvisioningExpectation,
    ProvisioningPayload,
    ProvisioningScope,
    SafeAgentSettings,
    install_provisioning_bundle,
    seal_provisioning_bundle,
)
from plugins.hub.provisioning_store import FilesystemProvisioningStore


def _install_profile(state_file: ProvisionedStateFile) -> tuple[str, str]:
    issuer = SigningKey.generate()
    recipient = SigningKey.generate()
    profile_name = "private-managed-profile"
    api_key = "synthetic-provisioned-api-key-DO-NOT-LOG"
    category = "provider:openai:api_key"
    scope = ProvisioningScope(
        enrollment_id="profile-management-install",
        network_ids=("network:profile-management",),
        audience="workspace:" + "a" * 32,
        profile_name=profile_name,
        allowed_credential_categories=frozenset({category}),
    )
    payload = ProvisioningPayload(
        profile=ProfilePreferences(
            name=profile_name,
            provider="openai",
            model="gpt-test-model",
        ),
        networks=(
            NetworkPreferences(
                network_id="network:profile-management",
                discovery_domain="relay.example.org",
            ),
        ),
        settings=SafeAgentSettings(),
        credentials=(
            ProvisioningCredential(
                category=category,
                profile_name=profile_name,
                secret=api_key,
            ),
        ),
    )
    ciphertext = seal_provisioning_bundle(
        payload,
        scope=scope,
        issuer_key=issuer,
        recipient_public_key=bytes(recipient.verify_key),
        revision=1,
        expires_at=1_700_000_120,
        now=1_700_000_000,
    )
    install_provisioning_bundle(
        ciphertext,
        recipient_key=recipient,
        expectation=ProvisioningExpectation(bytes(issuer.verify_key), scope),
        store=FilesystemProvisioningStore(state_file),
        now=1_700_000_000,
    )
    return profile_name, api_key


def _manager_for_state(monkeypatch, tmp_path, state_file):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    config = home / ".kollab" / "config.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda _self: None)
    monkeypatch.setattr(
        profile_manager_module,
        "get_global_config_path",
        lambda: config,
    )
    monkeypatch.setattr(
        profile_manager_module,
        "get_global_config_path_candidates",
        lambda: [config],
    )
    monkeypatch.setattr(
        profile_manager_module,
        "get_local_config_path_candidates",
        lambda: [],
    )
    monkeypatch.setattr(
        provisioned_state_module,
        "default_provisioned_state_path",
        lambda: state_file.path,
    )
    return ProfileManager(), config


def test_provisioned_key_stays_private_and_ignores_unrelated_global_key(
    tmp_path, monkeypatch
):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    profile_name, installed_key = _install_profile(state_file)
    manager, _config = _manager_for_state(monkeypatch, tmp_path, state_file)
    profile = manager.get_profile(profile_name)

    assert profile is not None and profile.is_provisioned
    monkeypatch.setenv("KOLLAB_API_KEY", "synthetic-unrelated-global-key")
    with patch.object(profile_manager_module, "_keyring_set") as keyring_set:
        assert profile.get_api_key() == installed_key
        keyring_set.assert_not_called()

    monkeypatch.setenv("KOLLAB_PRIVATE_MANAGED_PROFILE_API_KEY", "explicit-local-key")
    assert profile.get_api_key() == "explicit-local-key"


def test_provisioned_profile_edits_and_api_key_stay_in_private_state(
    tmp_path, monkeypatch
):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    profile_name, _installed_key = _install_profile(state_file)
    manager, config = _manager_for_state(monkeypatch, tmp_path, state_file)

    with patch.object(profile_manager_module, "_keyring_set") as keyring_set:
        assert manager.update_profile(
            profile_name,
            model="gpt-updated-model",
            api_key="synthetic-updated-private-key-DO-NOT-LOG",
            save_to_config=True,
        )
        keyring_set.assert_not_called()

    assert not config.exists()
    state = state_file.read()["installs"]["profile-management-install"]
    assert state["profile_overrides"]["model"] == "gpt-updated-model"
    assert state["api_key_override"] == "synthetic-updated-private-key-DO-NOT-LOG"

    reloaded, _config = _manager_for_state(monkeypatch, tmp_path, state_file)
    profile = reloaded.get_profile(profile_name)
    assert profile is not None
    assert profile.model == "gpt-updated-model"
    assert profile.get_api_key() == "synthetic-updated-private-key-DO-NOT-LOG"


def test_deleting_provisioned_profile_survives_profile_manager_reload(
    tmp_path, monkeypatch
):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    profile_name, _ = _install_profile(state_file)
    manager, config = _manager_for_state(monkeypatch, tmp_path, state_file)

    assert manager.delete_profile(profile_name)
    assert manager.get_profile(profile_name) is None
    assert not config.exists()
    assert state_file.read()["installs"]["profile-management-install"][
        "profile_disabled"
    ]

    reloaded, _config = _manager_for_state(monkeypatch, tmp_path, state_file)
    assert reloaded.get_profile(profile_name) is None

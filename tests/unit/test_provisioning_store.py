from __future__ import annotations

import json
import logging
import os
import uuid

import pytest
from nacl.signing import SigningKey

from kollabor_config.provisioned_state import (
    ProvisionedStateError,
    ProvisionedStateFile,
)
from kollabor_ai.oauth.openai_oauth import OAuthError, OAuthTokens
from kollabor_ai.oauth import token_storage as token_storage_module
from kollabor_ai.oauth.token_storage import OAuthTokenStorage
from plugins.hub import provisioning_store as store_module
from plugins.hub.provisioning import (
    NetworkPreferences,
    OpenAIOAuthCredential,
    ProfilePreferences,
    ProvisioningCredential,
    ProvisioningError,
    ProvisioningExpectation,
    ProvisioningPayload,
    ProvisioningScope,
    SafeAgentSettings,
    install_provisioning_bundle,
    seal_provisioning_bundle,
)
from plugins.hub.provisioning_store import FilesystemProvisioningStore

_NOW = 1_800_000_000
_API_KEY = "synthetic-private-key-DO-NOT-LOG"


def _bundle(
    *,
    enrollment_id: str | None = None,
    profile_name: str | None = None,
    revision: int = 1,
    model: str = "gpt-4o-mini",
    network_id: str = "net-alpha",
    domain: str = "kollabor.ai",
    oauth: bool = False,
):
    enrollment = enrollment_id or f"enrollment-{uuid.uuid4().hex}"
    profile = profile_name or f"provisioned-{uuid.uuid4().hex[:16]}"
    issuer = SigningKey(b"i" * 32)
    recipient = SigningKey(b"r" * 32)
    scope = ProvisioningScope(
        enrollment_id=enrollment,
        network_ids=(network_id,),
        audience="kollab-device",
        profile_name=profile,
        allowed_credential_categories=frozenset(
            {
                "provider:openai:oauth_tokens"
                if oauth
                else "provider:openai:api_key"
            }
        ),
    )
    profile_preferences = (
        ProfilePreferences(
            name=profile,
            provider="openai_responses",
            model="gpt-5.6-luna",
            auth_type="oauth",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        if oauth
        else ProfilePreferences(
            name=profile,
            provider="openai",
            model=model,
            context_window=65536,
            organization="synthetic-org",
        )
    )
    credential = (
        ProvisioningCredential(
            category="provider:openai:oauth_tokens",
            profile_name=profile,
            secret=OpenAIOAuthCredential(
                access_token="synthetic-scoped-access-DO-NOT-LOG",
                refresh_token="synthetic-scoped-refresh-DO-NOT-LOG",
                expires_at=1.0,
                account_id="synthetic-scoped-account",
            ),
        )
        if oauth
        else ProvisioningCredential(
            category="provider:openai:api_key",
            profile_name=profile,
            secret=_API_KEY,
        )
    )
    payload = ProvisioningPayload(
        profile=profile_preferences,
        networks=(NetworkPreferences(network_id, domain),),
        settings=SafeAgentSettings(),
        credentials=(credential,),
    )
    ciphertext = seal_provisioning_bundle(
        payload,
        scope=scope,
        issuer_key=issuer,
        recipient_public_key=bytes(recipient.verify_key),
        revision=revision,
        expires_at=_NOW + 120,
        now=_NOW,
    )
    expectation = ProvisioningExpectation(bytes(issuer.verify_key), scope)
    return ciphertext, recipient, expectation, enrollment, profile


def _install(state_file: ProvisionedStateFile, **kwargs):
    ciphertext, recipient, expectation, enrollment, profile = _bundle(**kwargs)
    receipt = install_provisioning_bundle(
        ciphertext,
        recipient_key=recipient,
        expectation=expectation,
        store=FilesystemProvisioningStore(state_file),
        now=_NOW,
    )
    return receipt, enrollment, profile


def test_state_file_starts_empty_and_edits_publish_only_on_success(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    assert state_file.read() == {"version": 1, "installs": {}}
    assert not state_file.path.exists()

    with pytest.raises(RuntimeError):
        with state_file.edit() as state:
            state["installs"]["enrollment-a"] = {"partial": True}
            raise RuntimeError("abort before publish")
    assert not state_file.path.exists()

    with state_file.edit() as state:
        assert state == {"version": 1, "installs": {}}
    assert state_file.path.stat().st_mode & 0o777 == 0o600
    assert state_file.path.parent.stat().st_mode & 0o777 == 0o700


def test_install_is_durable_idempotent_and_rejects_same_revision_replay(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    ciphertext, recipient, expectation, enrollment, profile = _bundle()
    store = FilesystemProvisioningStore(state_file)

    first = install_provisioning_bundle(
        ciphertext,
        recipient_key=recipient,
        expectation=expectation,
        store=store,
        now=_NOW,
    )
    assert first.status == "installed"
    assert first.revision == 1
    assert state_file.read()["installs"][enrollment]["profile"]["name"] == profile

    after_restart = FilesystemProvisioningStore(ProvisionedStateFile(state_file.path))
    replay = install_provisioning_bundle(
        ciphertext,
        recipient_key=recipient,
        expectation=expectation,
        store=after_restart,
        now=_NOW,
    )
    assert replay.status == "already_installed"

    replacement, recipient2, expectation2, _, _ = _bundle(
        enrollment_id=enrollment,
        profile_name=profile,
        model="gpt-4.1-mini",
    )
    with pytest.raises(ProvisioningError, match="replayed"):
        install_provisioning_bundle(
            replacement,
            recipient_key=recipient2,
            expectation=expectation2,
            store=after_restart,
            now=_NOW,
        )


def test_staging_is_invisible_until_commit_and_rollback_discards_it(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    ciphertext, recipient, expectation, enrollment, _ = _bundle()
    from plugins.hub.provisioning import _open_bundle

    payload, revision, digest = _open_bundle(
        ciphertext, recipient_key=recipient, expectation=expectation, now=_NOW
    )
    transaction = FilesystemProvisioningStore(state_file).begin()
    assert transaction.installed_revision(enrollment) is None
    transaction.ensure_targets_available(payload)
    transaction.stage_payload(payload)
    transaction.stage_revision(enrollment, revision, digest)
    assert state_file.read()["installs"] == {}
    transaction.rollback()
    assert state_file.read()["installs"] == {}
    assert not state_file.path.exists()


def test_failed_atomic_publish_preserves_previous_install(tmp_path, monkeypatch):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    first, enrollment, profile = _install(state_file)
    assert first.status == "installed"
    before = state_file.read()

    ciphertext, recipient, expectation, _, _ = _bundle(
        enrollment_id=enrollment,
        profile_name=profile,
        revision=2,
        model="gpt-4.1-mini",
    )

    def fail_publish(_state):
        raise OSError("synthetic publish failure")

    monkeypatch.setattr(state_file, "_write_unlocked", fail_publish)
    with pytest.raises(ProvisioningError, match="storage_failure"):
        install_provisioning_bundle(
            ciphertext,
            recipient_key=recipient,
            expectation=expectation,
            store=FilesystemProvisioningStore(state_file),
            now=_NOW,
        )
    monkeypatch.undo()
    assert state_file.read() == before


def test_profile_name_collision_across_enrollments_fails_closed(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    first, _, profile = _install(state_file)
    assert first.status == "installed"
    ciphertext, recipient, expectation, _, _ = _bundle(profile_name=profile)

    with pytest.raises(ProvisioningError, match="storage_conflict"):
        install_provisioning_bundle(
            ciphertext,
            recipient_key=recipient,
            expectation=expectation,
            store=FilesystemProvisioningStore(state_file),
            now=_NOW,
        )


def test_config_profile_collision_is_detected_without_overwriting_config(
    tmp_path, monkeypatch
):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    monkeypatch.setattr(store_module, "_configured_profile_exists", lambda _name: True)
    ciphertext, recipient, expectation, _, _ = _bundle()
    with pytest.raises(ProvisioningError, match="storage_conflict"):
        install_provisioning_bundle(
            ciphertext,
            recipient_key=recipient,
            expectation=expectation,
            store=FilesystemProvisioningStore(state_file),
            now=_NOW,
        )
    assert not state_file.path.exists()


def test_profile_manager_loads_installed_profiles_but_keeps_user_config_precedence(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "private" / "state.json"
    state_file = ProvisionedStateFile(state_path)
    _, _, profile_name = _install(state_file)
    monkeypatch.setattr(
        "kollabor_config.provisioned_state.default_provisioned_state_path",
        lambda: state_path,
    )

    from kollabor_ai.profile_manager import LLMProfile, ProfileManager

    loaded = ProfileManager._add_provisioned_profiles({})
    installed = LLMProfile.from_dict(profile_name, loaded[profile_name])
    assert installed.api_key == _API_KEY
    assert installed.organization == "synthetic-org"
    assert installed.context_window == 65536
    assert installed.to_dict()["organization"] == "synthetic-org"
    assert installed.to_dict()["context_window"] == 65536

    user_profile = {"provider": "anthropic", "model": "user-choice", "api_key": "user-key"}
    merged = ProfileManager._add_provisioned_profiles({profile_name: user_profile})
    assert merged[profile_name] is user_profile


def test_provisioned_network_target_is_resolved_without_reusing_private_origin_scopes(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "private" / "state.json"
    state_file = ProvisionedStateFile(state_path)
    network_id = f"network-{uuid.uuid4().hex[:12]}"
    _install(state_file, network_id=network_id, domain="relay.example.org")
    monkeypatch.setattr(
        "kollabor_config.provisioned_state.default_provisioned_state_path",
        lambda: state_path,
    )

    from plugins.hub.relay_commands import RelayCommands

    assert RelayCommands._resolve_network_target(network_id) == "https://relay.example.org"
    assert RelayCommands._resolve_network_target("https://custom.example.org") == (
        "https://custom.example.org"
    )


def test_store_checks_actual_config_profile_collision_without_rewriting_it(
    tmp_path, monkeypatch
):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    config = tmp_path / "config.json"
    profile_name = f"configured-{uuid.uuid4().hex[:12]}"
    original = {
        "kollabor": {
            "llm": {
                "profiles": {
                    profile_name: {
                        "provider": "openai",
                        "model": "user-choice",
                        "api_key": "user-key",
                    }
                }
            }
        }
    }
    config.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(
        store_module,
        "get_global_config_path_candidates",
        lambda: [config],
    )
    monkeypatch.setattr(store_module, "get_local_config_path_candidates", lambda: [])
    ciphertext, recipient, expectation, _, _ = _bundle(profile_name=profile_name)

    with pytest.raises(ProvisioningError, match="storage_conflict"):
        install_provisioning_bundle(
            ciphertext,
            recipient_key=recipient,
            expectation=expectation,
            store=FilesystemProvisioningStore(state_file),
            now=_NOW,
        )
    assert json.loads(config.read_text(encoding="utf-8")) == original
    assert not state_file.path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission assertions")
def test_state_file_rejects_broad_permissions_symlinks_and_digest_tampering(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "private" / "state.json")
    _, enrollment, _ = _install(state_file)

    state_file.path.chmod(0o644)
    with pytest.raises(ProvisionedStateError, match="mode 0600"):
        state_file.read()
    state_file.path.chmod(0o600)

    outside = tmp_path / "outside.json"
    outside.write_text(state_file.path.read_text(encoding="utf-8"), encoding="utf-8")
    state_file.path.unlink()
    state_file.path.symlink_to(outside)
    with pytest.raises(ProvisionedStateError):
        state_file.read()
    state_file.path.unlink()
    state_file.path.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
    state_file.path.chmod(0o600)

    state = json.loads(state_file.path.read_text(encoding="utf-8"))
    state["installs"][enrollment]["profile"]["model"] = "tampered"
    state_file.path.write_text(json.dumps(state), encoding="utf-8")
    state_file.path.chmod(0o600)
    with pytest.raises(ProvisionedStateError, match="digest"):
        state_file.read()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink and mode assertions")
def test_state_file_rejects_symlink_parent_without_chmodding_target(tmp_path):
    target = tmp_path / "shared-target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    parent = tmp_path / "private-link"
    parent.symlink_to(target, target_is_directory=True)
    state_file = ProvisionedStateFile(parent / "state.json")

    with pytest.raises(ProvisionedStateError, match="parent must be a directory"):
        state_file.read()

    assert target.stat().st_mode & 0o777 == 0o755
    assert not (target / "state.json").exists()


@pytest.mark.asyncio
async def test_profile_scoped_oauth_refresh_stays_bound_and_never_uses_global_tokens(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "private" / "state.json"
    state_file = ProvisionedStateFile(state_path)
    _, enrollment, profile = _install(state_file, oauth=True)
    monkeypatch.setattr(
        "kollabor_config.provisioned_state.default_provisioned_state_path",
        lambda: state_path,
    )
    oauth_dir = tmp_path / "oauth"

    def _isolated_oauth_dir():
        oauth_dir.mkdir(exist_ok=True)
        return oauth_dir

    monkeypatch.setattr(token_storage_module, "_get_oauth_dir", _isolated_oauth_dir)
    storage = OAuthTokenStorage(expiry_buffer=0)
    monkeypatch.setattr(
        storage,
        "_token_path_candidates",
        lambda provider: [storage._token_path(provider)],
    )

    global_tokens = OAuthTokens("global-access", "global-refresh", 4_000_000_000.0)
    await storage.store_tokens("openai", global_tokens)
    scoped_initial = await storage.load_tokens(
        "openai", auto_refresh=False, profile_name=profile
    )
    assert scoped_initial is not None
    assert scoped_initial.access_token == "synthetic-scoped-access-DO-NOT-LOG"
    assert await storage.load_tokens(
        "openai", auto_refresh=False, profile_name="missing-profile"
    ) is None

    refreshed = OAuthTokens(
        "synthetic-refreshed-access-DO-NOT-LOG",
        "synthetic-refreshed-refresh-DO-NOT-LOG",
        4_000_000_000.0,
        "synthetic-refreshed-account",
    )

    class _FakeOAuthClient:
        async def refresh_access_token(self, _refresh_token, *, previous_account_id):
            assert previous_account_id == "synthetic-scoped-account"
            return refreshed

    monkeypatch.setattr(token_storage_module, "OpenAIOAuthClient", _FakeOAuthClient)
    loaded = await storage.load_tokens(
        "openai", auto_refresh=True, profile_name=profile
    )
    assert loaded == refreshed
    assert await storage.load_tokens("openai", auto_refresh=False) == global_tokens
    assert await storage.has_tokens("openai", profile_name=profile)

    scoped_state = state_file.read()["installs"][enrollment]
    assert scoped_state["oauth_tokens_override"] == refreshed.to_dict()
    assert scoped_state["digest"]
    assert await storage.clear_tokens("openai", profile_name=profile)
    assert await storage.load_tokens(
        "openai", auto_refresh=False, profile_name=profile
    ) is None
    assert await storage.load_tokens("openai", auto_refresh=False) == global_tokens


@pytest.mark.asyncio
async def test_oauth_refresh_logging_redacts_provider_exception(caplog, monkeypatch):
    secret_echo = "synthetic-provider-error-token-DO-NOT-LOG"

    class _FailingOAuthClient:
        async def refresh_access_token(self, *_args, **_kwargs):
            raise OAuthError(f"provider echoed {secret_echo}")

    monkeypatch.setattr(
        token_storage_module, "OpenAIOAuthClient", _FailingOAuthClient
    )
    storage = object.__new__(OAuthTokenStorage)
    storage._expiry_buffer = 0
    tokens = OAuthTokens("synthetic-access", "synthetic-refresh", _NOW + 1000)

    with caplog.at_level(logging.ERROR, logger="kollabor_ai.oauth.token_storage"):
        result = await storage._try_refresh(
            "openai", tokens, profile_name="synthetic-profile"
        )

    assert result is None
    assert "synthetic-profile" in caplog.text
    assert secret_echo not in caplog.text
    assert "provider echoed" not in caplog.text

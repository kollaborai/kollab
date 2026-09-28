from __future__ import annotations

import hashlib
import json

import pytest
from nacl.public import SealedBox
from nacl.signing import SigningKey

from plugins.hub import provisioning as provisioning_module
from plugins.hub.dns.private_directory import public_key_id
from plugins.hub.provisioning import (
    InstalledRevision,
    InstallReceipt,
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
    provisioning_payload_digest,
    seal_provisioning_bundle,
)

_NOW = 1_800_000_000
_TOKEN_ACCESS = "synthetic-access-token-DO-NOT-LOG"
_TOKEN_REFRESH = "synthetic-refresh-token-DO-NOT-LOG"
_API_KEY = "synthetic-api-key-DO-NOT-LOG"


def _key(seed: int) -> SigningKey:
    return SigningKey(bytes([seed]) * 32)


def _scope(
    *,
    enrollment_id: str = "enrollment-001",
    profile_name: str | None = "server-openai",
    categories: frozenset[str] = frozenset({"provider:openai:api_key"}),
    networks: tuple[str, ...] = ("net-alpha",),
) -> ProvisioningScope:
    return ProvisioningScope(
        enrollment_id=enrollment_id,
        network_ids=networks,
        audience="kollab-device",
        profile_name=profile_name,
        allowed_credential_categories=categories,
        allowed_agent_names=frozenset({"kollabor", "default"}),
        allowed_skill_names=frozenset({"kollab-development", "research"}),
    )


def _payload(
    *,
    oauth: bool = False,
    credentials: tuple[ProvisioningCredential, ...] | None = None,
) -> ProvisioningPayload:
    profile = ProfilePreferences(
        name="server-openai",
        provider="openai_responses" if oauth else "openai",
        model="gpt-5.6-luna" if oauth else "gpt-4o-mini",
        auth_type="oauth" if oauth else "api_key",
        base_url="https://chatgpt.com/backend-api/codex" if oauth else None,
    )
    if credentials is None:
        credentials = (
            (
                ProvisioningCredential(
                    category="provider:openai:oauth_tokens",
                    profile_name=profile.name,
                    secret=OpenAIOAuthCredential(
                        access_token=_TOKEN_ACCESS,
                        refresh_token=_TOKEN_REFRESH,
                        expires_at=1_800_003_600.0,
                        account_id="synthetic-account",
                    ),
                ),
            )
            if oauth
            else (
                ProvisioningCredential(
                    category="provider:openai:api_key",
                    profile_name=profile.name,
                    secret=_API_KEY,
                ),
            )
        )
    return ProvisioningPayload(
        profile=profile,
        networks=(NetworkPreferences("net-alpha", "kollabor.ai"),),
        settings=SafeAgentSettings(
            default_agent="kollabor", active_skills=("kollab-development",)
        ),
        credentials=credentials,
    )


def _expectation(
    issuer: SigningKey | None = None,
    *,
    scope: ProvisioningScope | None = None,
) -> ProvisioningExpectation:
    return ProvisioningExpectation(
        issuer_public_key=bytes((issuer or _key(1)).verify_key),
        scope=scope or _scope(),
    )


def _seal(
    payload: ProvisioningPayload | None = None,
    *,
    issuer: SigningKey | None = None,
    recipient: SigningKey | None = None,
    scope: ProvisioningScope | None = None,
    revision: int = 1,
    expires_at: int = _NOW + 120,
) -> bytes:
    return seal_provisioning_bundle(
        payload or _payload(),
        scope=scope or _scope(),
        issuer_key=issuer or _key(1),
        recipient_public_key=bytes((recipient or _key(2)).verify_key),
        revision=revision,
        expires_at=expires_at,
        now=_NOW,
    )


class _FakeStore:
    def __init__(self) -> None:
        self.revisions: dict[str, InstalledRevision] = {}
        self.installed: dict[str, object] = {}
        self.occupied = False
        self.fail_commit = False
        self.begin_count = 0
        self.rollback_count = 0

    def begin(self) -> "_FakeTransaction":
        self.begin_count += 1
        return _FakeTransaction(self)


class _FakeTransaction:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store
        self.before_installed = dict(store.installed)
        self.before_revisions = dict(store.revisions)
        self.pending_payload: ProvisioningPayload | None = None
        self.pending_revision: tuple[str, int, str] | None = None

    def installed_revision(self, enrollment_id: str) -> InstalledRevision | None:
        return self.store.revisions.get(enrollment_id)

    def ensure_targets_available(self, payload: ProvisioningPayload) -> None:
        if self.store.occupied:
            raise ProvisioningError("storage_conflict")

    def stage_payload(self, payload: ProvisioningPayload) -> None:
        self.pending_payload = payload

    def stage_revision(self, enrollment_id: str, revision: int, digest: str) -> None:
        self.pending_revision = (enrollment_id, revision, digest)

    def commit(self) -> None:
        assert self.pending_payload is not None
        assert self.pending_revision is not None
        self.store.installed[self.pending_revision[0]] = self.pending_payload
        self.store.revisions[self.pending_revision[0]] = InstalledRevision(
            self.pending_revision[1], self.pending_revision[2]
        )
        if self.store.fail_commit:
            raise OSError(f"failed while writing {_TOKEN_REFRESH}")

    def rollback(self) -> None:
        self.store.rollback_count += 1
        self.store.installed = dict(self.before_installed)
        self.store.revisions = dict(self.before_revisions)


def _forge_bundle(
    content: dict,
    *,
    issuer: SigningKey | None = None,
    recipient: SigningKey | None = None,
    scope: ProvisioningScope | None = None,
    content_digest: str | None = None,
    issuer_id: str | None = None,
    now: int = _NOW,
    expires_at: int = _NOW + 120,
) -> bytes:
    issuer = issuer or _key(1)
    recipient = recipient or _key(2)
    scope = scope or _scope()
    signed = {
        "type": provisioning_module._BUNDLE_TYPE,
        "version": provisioning_module._BUNDLE_VERSION,
        "issuer": issuer_id or public_key_id(bytes(issuer.verify_key)),
        "recipient": public_key_id(bytes(recipient.verify_key)),
        "enrollment_id": scope.enrollment_id,
        "network_ids": sorted(scope.network_ids),
        "audience": scope.audience,
        "revision": 1,
        "issued_at": now,
        "expires_at": expires_at,
        "content_digest": content_digest
        or hashlib.sha256(provisioning_module._canonical_json(content)).hexdigest(),
        "content": content,
    }
    signature = issuer.sign(
        provisioning_module._BUNDLE_DOMAIN + provisioning_module._canonical_json(signed)
    ).signature.hex()
    cleartext = json.dumps(
        {**signed, "signature": signature},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return SealedBox(recipient.verify_key.to_curve25519_public_key()).encrypt(cleartext)


def _content_dict(
    payload: ProvisioningPayload, scope: ProvisioningScope | None = None
) -> dict:
    return provisioning_module._validate_payload(payload, scope or _scope())


def _install(
    ciphertext: bytes,
    *,
    store: _FakeStore | None = None,
    recipient: SigningKey | None = None,
    issuer: SigningKey | None = None,
    scope: ProvisioningScope | None = None,
    now: int = _NOW,
) -> tuple[InstallReceipt, _FakeStore]:
    store = store or _FakeStore()
    receipt = install_provisioning_bundle(
        ciphertext,
        recipient_key=recipient or _key(2),
        expectation=_expectation(issuer, scope=scope),
        store=store,
        now=now,
    )
    return receipt, store


def test_seal_installs_device_bound_api_key_bundle_transactionally() -> None:
    ciphertext = _seal()
    receipt, store = _install(ciphertext)

    assert receipt.status == "installed"
    assert receipt.enrollment_id == "enrollment-001"
    assert receipt.revision == 1
    assert receipt.digest == provisioning_payload_digest(_payload(), scope=_scope())
    assert store.begin_count == 1
    assert store.installed["enrollment-001"].credentials[0].secret == _API_KEY
    assert store.revisions["enrollment-001"].digest == receipt.digest


def test_oauth_bundle_round_trips_existing_openai_token_schema_without_receipt_leak() -> (
    None
):
    scope = _scope(
        categories=frozenset({"provider:openai:oauth_tokens"}),
    )
    ciphertext = _seal(_payload(oauth=True), scope=scope)
    receipt, store = _install(ciphertext, scope=scope)
    credential = store.installed["enrollment-001"].credentials[0]

    assert receipt.status == "installed"
    assert credential.category == "provider:openai:oauth_tokens"
    assert credential.secret.access_token == _TOKEN_ACCESS
    assert credential.secret.refresh_token == _TOKEN_REFRESH
    assert credential.secret.expires_at == 1_800_003_600.0
    assert credential.secret.account_id == "synthetic-account"
    assert _TOKEN_ACCESS not in repr(receipt)
    assert _TOKEN_REFRESH not in repr(receipt)
    serialized_secret = provisioning_module._validate_payload(
        _payload(oauth=True), scope
    )["credentials"][0]["secret"]
    assert set(serialized_secret) == {
        "access_token",
        "refresh_token",
        "expires_at",
        "account_id",
    }


def test_secret_bearing_dataclass_repr_is_redacted() -> None:
    api_credential = ProvisioningCredential(
        "provider:openai:api_key", "server-openai", _API_KEY
    )
    oauth = OpenAIOAuthCredential(
        _TOKEN_ACCESS, _TOKEN_REFRESH, 1_800_003_600.0, "synthetic-account"
    )
    oauth_credential = ProvisioningCredential(
        "provider:openai:oauth_tokens", "server-openai", oauth
    )
    payload = _payload()
    expectation = _expectation()

    for rendered in (
        repr(api_credential),
        repr(oauth),
        repr(oauth_credential),
        repr(payload),
        repr(expectation),
    ):
        assert _API_KEY not in rendered
        assert _TOKEN_ACCESS not in rendered
        assert _TOKEN_REFRESH not in rendered
    assert "<redacted>" in repr(api_credential)


def test_secret_values_do_not_appear_in_bundle_ciphertext() -> None:
    ciphertext = _seal(
        _payload(oauth=True),
        scope=_scope(categories=frozenset({"provider:openai:oauth_tokens"})),
    )
    assert _TOKEN_ACCESS.encode() not in ciphertext
    assert _TOKEN_REFRESH.encode() not in ciphertext
    assert _API_KEY.encode() not in _seal()


def test_seal_rejects_credential_category_outside_delegation() -> None:
    scope = _scope(categories=frozenset())
    with pytest.raises(ProvisioningError) as error:
        _seal(scope=scope)
    assert str(error.value) == "unauthorized_credential"
    assert _API_KEY not in str(error.value)


def test_scope_binds_exact_enrollment_networks_and_audience() -> None:
    ciphertext = _seal()
    wrong_id = _scope(enrollment_id="enrollment-other")
    with pytest.raises(ProvisioningError, match="wrong_enrollment"):
        _install(ciphertext, scope=wrong_id)

    wrong_network = _scope(networks=("net-beta",))
    with pytest.raises(ProvisioningError, match="wrong_scope"):
        _install(ciphertext, scope=wrong_network)

    wrong_audience = ProvisioningScope(
        enrollment_id="enrollment-001",
        network_ids=("net-alpha",),
        audience="other-audience",
        profile_name="server-openai",
        allowed_credential_categories=frozenset({"provider:openai:api_key"}),
        allowed_agent_names=frozenset({"kollabor"}),
        allowed_skill_names=frozenset({"kollab-development"}),
    )
    with pytest.raises(ProvisioningError, match="wrong_scope"):
        _install(ciphertext, scope=wrong_audience)


def test_bundle_cannot_be_opened_by_another_device_key() -> None:
    ciphertext = _seal()
    with pytest.raises(ProvisioningError, match="wrong_recipient"):
        _install(ciphertext, recipient=_key(3))


def test_wrong_issuer_and_modified_signature_are_rejected() -> None:
    ciphertext = _seal(issuer=_key(4))
    with pytest.raises(ProvisioningError, match="wrong_issuer"):
        _install(ciphertext)

    bundle = bytearray(_seal())
    bundle[-1] ^= 0x01
    with pytest.raises(ProvisioningError):
        _install(bytes(bundle))


def test_expired_bundle_is_rejected_before_opening_store_transaction() -> None:
    ciphertext = _seal(expires_at=_NOW + 60)
    store = _FakeStore()
    with pytest.raises(ProvisioningError, match="expired"):
        _install(ciphertext, store=store, now=_NOW + 61)
    assert store.begin_count == 0


def test_rejects_digest_mismatch_and_unknown_profile_fields() -> None:
    content = _content_dict(_payload())
    forged_digest = _forge_bundle(content, content_digest="0" * 64)
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _install(forged_digest)

    content["profile"]["shell_command"] = "synthetic-dangerous-hook"
    forged_unknown_field = _forge_bundle(content)
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _install(forged_unknown_field)

    content["profile"].pop("shell_command")
    content["profile"]["provider"] = []
    malformed_field = _forge_bundle(content)
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _install(malformed_field)


def test_rejects_arbitrary_paths_and_custom_provider_profiles() -> None:
    content = _content_dict(_payload())
    content["profile"]["path"] = "/tmp/credentials"
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _install(_forge_bundle(content))

    custom = _payload()
    custom_profile = ProfilePreferences(
        name="server-openai", provider="custom", model="local-model"
    )
    custom_payload = ProvisioningPayload(
        profile=custom_profile,
        networks=custom.networks,
        settings=custom.settings,
        credentials=custom.credentials,
    )
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _seal(custom_payload)


def test_custom_api_key_requires_https_endpoint_and_exact_delegation_category() -> None:
    payload = ProvisioningPayload(
        profile=ProfilePreferences(
            name="server-openai",
            provider="custom",
            model="remote-model",
            base_url="https://llm.example/v1",
        ),
        networks=_payload().networks,
        credentials=(
            ProvisioningCredential(
                "provider:custom:api_key", "server-openai", _API_KEY
            ),
        ),
    )
    scope = _scope(categories=frozenset({"provider:custom:api_key"}))
    assert _install(_seal(payload, scope=scope), scope=scope)[0].status == "installed"

    loopback_payload = ProvisioningPayload(
        profile=ProfilePreferences(
            name="server-openai",
            provider="custom",
            model="remote-model",
            base_url="https://127.0.0.2/v1",
        ),
        networks=payload.networks,
        credentials=payload.credentials,
    )
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _seal(loopback_payload, scope=scope)

    with pytest.raises(ProvisioningError, match="unauthorized_credential"):
        _seal(payload, scope=_scope(categories=frozenset()))


def test_provider_specific_fields_are_typed_and_retained_for_existing_adapters() -> (
    None
):
    scope = _scope(categories=frozenset({"provider:azure_openai:api_key"}))
    payload = ProvisioningPayload(
        profile=ProfilePreferences(
            name="server-openai",
            provider="azure_openai",
            model="deployment-west",
            azure_endpoint="https://sample.openai.azure.com",
            api_version="2024-02-15-preview",
            deployment_id="deployment-west",
        ),
        networks=_payload().networks,
        credentials=(
            ProvisioningCredential(
                "provider:azure_openai:api_key", "server-openai", _API_KEY
            ),
        ),
    )
    receipt, store = _install(_seal(payload, scope=scope), scope=scope)

    assert receipt.status == "installed"
    profile = store.installed["enrollment-001"].profile
    assert profile.azure_endpoint == "https://sample.openai.azure.com"
    assert profile.api_version == "2024-02-15-preview"
    assert profile.deployment_id == "deployment-west"


def test_openai_oauth_cannot_be_redirected_to_an_issuer_chosen_endpoint() -> None:
    scope = _scope(categories=frozenset({"provider:openai:oauth_tokens"}))
    oauth = _payload(oauth=True)
    unsafe_profile = ProfilePreferences(
        name="server-openai",
        provider="openai_responses",
        model="gpt-5.6-luna",
        auth_type="oauth",
        base_url="https://attacker.example/token-collector",
    )
    payload = ProvisioningPayload(
        profile=unsafe_profile,
        networks=oauth.networks,
        settings=oauth.settings,
        credentials=oauth.credentials,
    )
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _seal(payload, scope=scope)


def test_api_key_and_oauth_category_are_bound_to_profile_provider_and_auth_type() -> (
    None
):
    wrong_provider = ProvisioningPayload(
        profile=ProfilePreferences("server-openai", "anthropic", "claude-sonnet"),
        networks=_payload().networks,
        credentials=_payload().credentials,
    )
    scope = _scope(categories=frozenset({"provider:openai:api_key"}))
    with pytest.raises(ProvisioningError, match="unauthorized_credential"):
        _seal(wrong_provider, scope=scope)

    oauth_scope = _scope(categories=frozenset({"provider:openai:oauth_tokens"}))
    oauth_payload = _payload(oauth=True)
    wrong_oauth_profile = ProvisioningPayload(
        profile=ProfilePreferences(
            "server-openai", "openai", "gpt-5.6-luna", auth_type="oauth"
        ),
        networks=oauth_payload.networks,
        credentials=oauth_payload.credentials,
    )
    with pytest.raises(ProvisioningError):
        _seal(wrong_oauth_profile, scope=oauth_scope)


def test_agents_and_skills_are_names_only_and_must_be_delegated() -> None:
    outside_scope = _scope()
    unsafe_settings = ProvisioningPayload(
        profile=_payload().profile,
        networks=_payload().networks,
        settings=SafeAgentSettings(default_agent="unapproved-agent"),
        credentials=_payload().credentials,
    )
    with pytest.raises(ProvisioningError, match="wrong_scope"):
        _seal(unsafe_settings, scope=outside_scope)


def test_same_revision_and_digest_retry_is_idempotent() -> None:
    ciphertext = _seal()
    first, store = _install(ciphertext)
    second, same_store = _install(ciphertext, store=store)

    assert first.status == "installed"
    assert second.status == "already_installed"
    assert second.digest == first.digest
    assert same_store.begin_count == 2
    assert same_store.revisions["enrollment-001"].revision == 1


def test_same_revision_with_different_digest_and_older_revision_are_replays() -> None:
    first, store = _install(_seal())
    changed = ProvisioningPayload(
        profile=ProfilePreferences(
            "server-openai", "openai", "gpt-4.1-mini", temperature=0.3
        ),
        networks=_payload().networks,
        settings=_payload().settings,
        credentials=_payload().credentials,
    )
    with pytest.raises(ProvisioningError, match="replayed"):
        _install(_seal(changed), store=store)

    newer, _ = _install(_seal(revision=2), store=store)
    assert newer.status == "installed"
    with pytest.raises(ProvisioningError, match="replayed"):
        _install(_seal(revision=1), store=store)
    assert first.digest == newer.digest


def test_store_conflict_preserves_unrelated_credentials_and_profile() -> None:
    store = _FakeStore()
    store.occupied = True
    store.installed["unrelated"] = "existing-profile-and-credential"
    with pytest.raises(ProvisioningError, match="storage_conflict"):
        _install(_seal(), store=store)
    assert store.installed == {"unrelated": "existing-profile-and-credential"}
    assert not store.revisions
    assert store.rollback_count == 1


def test_store_failure_is_redacted_and_rolls_back_partial_staging() -> None:
    store = _FakeStore()
    store.fail_commit = True
    store.installed["unrelated"] = "untouched"
    with pytest.raises(ProvisioningError) as error:
        _install(_seal(), store=store)

    assert str(error.value) == "storage_failure"
    assert _TOKEN_REFRESH not in str(error.value)
    assert store.installed == {"unrelated": "untouched"}
    assert not store.revisions
    assert store.rollback_count == 1


def test_credential_callback_never_returns_raw_failure_or_emits_logs(caplog) -> None:
    class FailingStore(_FakeStore):
        def begin(self):
            raise RuntimeError(f"private store rejected {_TOKEN_ACCESS}")

    with pytest.raises(ProvisioningError) as error:
        _install(
            _seal(
                _payload(oauth=True),
                scope=_scope(categories=frozenset({"provider:openai:oauth_tokens"})),
            ),
            store=FailingStore(),
            scope=_scope(categories=frozenset({"provider:openai:oauth_tokens"})),
        )

    assert str(error.value) == "storage_failure"
    assert _TOKEN_ACCESS not in str(error.value)
    assert _TOKEN_ACCESS not in caplog.text


def test_install_receipt_is_not_the_joined_or_complete_acknowledgment() -> None:
    receipt, _ = _install(_seal())
    assert receipt.status == "installed"
    assert receipt.status not in {"joined", "complete", "approved"}


def test_duplicate_json_keys_are_rejected_after_valid_sealing() -> None:
    content = _content_dict(_payload())
    duplicate_content = json.dumps(content, separators=(",", ":"))
    raw = (
        '{"content":' + duplicate_content + ',"content":' + duplicate_content + "}"
    ).encode()
    ciphertext = SealedBox(_key(2).verify_key.to_curve25519_public_key()).encrypt(raw)
    with pytest.raises(ProvisioningError, match="invalid_bundle"):
        _install(ciphertext)

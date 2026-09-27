import pytest
from nacl.signing import SigningKey

from plugins.hub.dns.discovery import DiscoveryError, normalize_target, verify_manifest
from plugins.hub.dns.service_locator import build_a2a_service_locator


def test_locator_renews_without_duplicating_card_fields(tmp_path, monkeypatch):
    key = SigningKey.generate()
    state = tmp_path / "locator.json"
    monkeypatch.setattr("plugins.hub.dns.service_locator.time.time", lambda: 10000)
    first = build_a2a_service_locator("https://example.com", key, state)
    assert build_a2a_service_locator("https://example.com", key, state) == first
    assert set(first["endpoints"]) == {"registry", "agent_card"}
    assert "capabilities" not in first and "skills" not in first
    assert first["discovery"]["roles"] == []
    monkeypatch.setattr("plugins.hub.dns.service_locator.time.time", lambda: 10060)
    renewed = build_a2a_service_locator("https://example.com", key, state)
    assert renewed["revision"] == first["revision"] + 1
    assert renewed["expires_at"] == 10360
    assert verify_manifest(renewed, normalize_target("example.com")).membership_state == "none"


def test_locator_rejects_changed_signer_and_corrupt_state(tmp_path):
    state = tmp_path / "locator.json"
    key = SigningKey.generate()
    build_a2a_service_locator("https://example.com", key, state)
    with pytest.raises(DiscoveryError, match="key_changed"):
        build_a2a_service_locator("https://example.com", SigningKey.generate(), state)
    state.write_text("{}")
    with pytest.raises((DiscoveryError, KeyError)):
        build_a2a_service_locator("https://example.com", key, state)

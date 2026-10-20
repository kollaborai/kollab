import logging

from plugins.hub.dns.identity import IdentityManager


class _Storage:
    def load_public_key(self, designation):
        return "not-a-valid-ed25519-key"

    def load_coordinator_pub(self):
        return "also-invalid"


def test_invalid_persisted_designation_key_is_logged(caplog):
    manager = IdentityManager(_Storage())
    with caplog.at_level(logging.WARNING, logger="plugins.hub.dns.identity"):
        assert manager._get_public_key_hex("peer") is None
    assert "Invalid persisted public key for designation peer" in caplog.text


def test_invalid_persisted_coordinator_key_is_logged(caplog):
    manager = IdentityManager(_Storage())
    with caplog.at_level(logging.WARNING, logger="plugins.hub.dns.identity"):
        assert manager._get_coordinator_public_key_hex() is None
    assert "Invalid persisted coordinator public key" in caplog.text

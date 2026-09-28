from __future__ import annotations

import pytest

from plugins.hub.peer_locator import (
    PeerLocatorError,
    validate_peer_designation,
    validate_peer_locator_endpoint,
)


@pytest.mark.parametrize(
    "designation",
    ["a", "agent_1", "agent-name", "A" * 64],
)
def test_peer_designation_matches_hub_dns_label_boundary(designation: str) -> None:
    assert validate_peer_designation(designation) == designation


@pytest.mark.parametrize("designation", ["", "a" * 65, "has.dot", "has space", "é"])
def test_peer_designation_rejects_values_outside_dns_label_boundary(
    designation: str,
) -> None:
    with pytest.raises(PeerLocatorError):
        validate_peer_designation(designation)


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("kollab+tls://peer.example:7443", ("peer.example", 7443)),
        ("kollab+tls://127.0.0.1:7443", ("127.0.0.1", 7443)),
        ("kollab+tls://[2001:db8::1]:7443", ("2001:db8::1", 7443)),
    ],
)
def test_direct_peer_locator_accepts_canonical_authorities(endpoint, expected) -> None:
    assert validate_peer_locator_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://peer.example:7443",
        "KOLLAB+TLS://peer.example:7443",
        "kollab+tls://Peer.example:7443",
        "kollab+tls://peer.example:07443",
        "kollab+tls://peer.example:7443/",
        "kollab+tls://peer.example:7443/path",
        "kollab+tls://peer.example:7443?x=1",
        "kollab+tls://user@peer.example:7443",
        "kollab+tls://010.0.0.1:7443",
        "kollab+tls://[2001:0db8::1]:7443",
    ],
)
def test_direct_peer_locator_rejects_noncanonical_or_wrong_carrier(endpoint) -> None:
    with pytest.raises(PeerLocatorError):
        validate_peer_locator_endpoint(endpoint)


def test_literal_private_ip_needs_explicit_network_opt_in() -> None:
    endpoint = "kollab+tls://192.168.1.10:7443"
    with pytest.raises(PeerLocatorError, match="explicit opt-in"):
        validate_peer_locator_endpoint(endpoint, allow_private_network=False)
    assert validate_peer_locator_endpoint(endpoint, allow_private_network=True) == (
        "192.168.1.10",
        7443,
    )

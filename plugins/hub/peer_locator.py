"""Shared syntax validation for direct peer endpoint locators.

The locator names Kollab's raw TLS/newline-JSON carrier. RelayClient's
``wss://`` endpoints are a separate WebSocket protocol and are not valid here.
These helpers validate syntax and, when requested, literal-IP network scope;
they do not authenticate or approve a peer.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

PEER_LOCATOR_SCHEME = "kollab+tls"
PEER_LOCATOR_MAX_LENGTH = 300
PEER_DESIGNATION_PATTERN = r"[A-Za-z0-9_-]{1,64}"

_DESIGNATION = re.compile(PEER_DESIGNATION_PATTERN + r"\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class PeerLocatorError(ValueError):
    """A peer locator is malformed or violates its requested address policy."""


def validate_peer_designation(value: object) -> str:
    """Return a DNS-compatible designation or reject it.

    This exactly matches the Hub DNS label boundary: ASCII letters, digits,
    underscore, and hyphen, with a maximum of 64 characters.
    """
    if not isinstance(value, str) or not _DESIGNATION.fullmatch(value):
        raise PeerLocatorError("invalid peer designation")
    return value


def _canonical_ipv6(address: ipaddress.IPv6Address) -> str:
    """The one wire spelling of an IPv6 address, the same from every Python.

    Python 3.13 writes an IPv4-mapped address as ::ffff:127.0.0.1 and 3.12 as
    ::ffff:7f00:1, so `compressed` alone made devices disagree on what is canonical.
    """
    if address.ipv4_mapped is not None:
        return f"::ffff:{address.ipv4_mapped}"
    return address.compressed


def validate_peer_locator_endpoint(
    endpoint: object,
    *,
    allow_private_network: bool | None = None,
) -> tuple[str, int]:
    """Validate the canonical direct-carrier URI and return host and port.

    ``allow_private_network=None`` performs syntax-only validation. ``False``
    rejects non-global literal IP addresses, while ``True`` permits them. Host
    names are never resolved here; callers must pin DNS results before dialing.
    """
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or len(endpoint) > PEER_LOCATOR_MAX_LENGTH
        or not endpoint.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in endpoint)
    ):
        raise PeerLocatorError("invalid peer locator endpoint")

    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise PeerLocatorError("invalid peer locator endpoint") from exc

    if (
        not endpoint.startswith(f"{PEER_LOCATOR_SCHEME}://")
        or parsed.scheme != PEER_LOCATOR_SCHEME
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or host is None
        or port is None
        or not 1 <= port <= 65535
        or "%" in parsed.netloc
    ):
        raise PeerLocatorError("peer locator must bind a direct TLS origin")

    # Enforce a single wire spelling: lowercase ASCII hostname, canonical IP,
    # bracketed compressed IPv6, and a decimal port with no leading zeroes.
    authority = parsed.netloc
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0 or authority[closing + 1 : closing + 2] != ":":
            raise PeerLocatorError("invalid peer locator authority")
        raw_host = authority[1:closing]
        raw_port = authority[closing + 2 :]
        try:
            address = ipaddress.ip_address(raw_host)
        except ValueError as exc:
            raise PeerLocatorError("invalid peer locator address") from exc
        if not isinstance(address, ipaddress.IPv6Address):
            raise PeerLocatorError("brackets are reserved for IPv6 addresses")
        canonical_host = _canonical_ipv6(address)
        if raw_host != canonical_host:
            raise PeerLocatorError("IPv6 peer locator is not canonical")
    else:
        if authority.count(":") != 1:
            raise PeerLocatorError("invalid peer locator authority")
        raw_host, raw_port = authority.rsplit(":", 1)
        if not raw_host or raw_host != host or raw_host.lower() != raw_host:
            raise PeerLocatorError("peer locator host is not canonical lowercase ASCII")
        address = None
        try:
            address = ipaddress.ip_address(raw_host)
        except ValueError:
            if all(char in "0123456789." for char in raw_host):
                raise PeerLocatorError("ambiguous numeric peer locator hostname")
            if (
                len(raw_host) > 253
                or raw_host.endswith(".")
                or any(not _DNS_LABEL.fullmatch(label) for label in raw_host.split("."))
            ):
                raise PeerLocatorError("invalid peer locator hostname")
            canonical_host = raw_host
        else:
            if not isinstance(address, ipaddress.IPv4Address):
                raise PeerLocatorError("IPv6 peer locator must use brackets")
            canonical_host = str(address)
            if raw_host != canonical_host:
                raise PeerLocatorError("IPv4 peer locator is not canonical")

    if (
        not raw_port
        or not raw_port.isascii()
        or not raw_port.isdecimal()
        or (len(raw_port) > 1 and raw_port.startswith("0"))
        or str(port) != raw_port
    ):
        raise PeerLocatorError("peer locator port is not canonical")

    if authority != f"{f'[{canonical_host}]' if ':' in canonical_host else canonical_host}:{port}":
        raise PeerLocatorError("peer locator authority is not canonical")

    if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        if address.is_unspecified or address.is_multicast:
            raise PeerLocatorError("peer locator address is not dialable")
        if allow_private_network is False and not address.is_global:
            raise PeerLocatorError("private peer endpoint requires explicit opt-in")

    return canonical_host, port

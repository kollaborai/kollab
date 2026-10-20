"""Private-only bind, Host/Origin, and exact-client-subnet checks for WebUI.

Phone-access endpoints are intentionally supported through a direct private
WebUI binding, not a public or arbitrary reverse-proxy deployment.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable, Sequence
from urllib.parse import unquote, urlsplit

import psutil

_RFC1918 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_ULA = ipaddress.ip_network("fc00::/7")
_V4_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
_V6_LINK_LOCAL = ipaddress.ip_network("fe80::/10")
_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_LOOPBACK_V6 = ipaddress.ip_network("::1/128")
_FORWARDING_HEADERS = frozenset(
    {
        "forwarded",
        "x-real-ip",
        "x-client-ip",
        "true-client-ip",
        "cf-connecting-ip",
        "x-original-for",
        "x-original-host",
        "x-original-proto",
    }
)


def _parse_ip(value: str):
    text = unquote(value.strip()).split("%", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _allowed_bind_address(value: str) -> bool:
    address = _parse_ip(value)
    if address is None or address.is_unspecified or address.is_multicast:
        return False
    if address.is_loopback or address.is_link_local:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _RFC1918)
    return address in _ULA


def validate_bind_hosts(hosts: Iterable[str]) -> list[str]:
    """Require literal local interface addresses; never bind a wildcard/public IP."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in hosts:
        host = raw.strip()
        if not host:
            continue
        address = _parse_ip(host)
        if address is None:
            raise ValueError(
                "KOLLAB_WEBUI_HOSTS accepts literal loopback, RFC1918, IPv6 ULA, "
                f"or link-local IP addresses only; rejected {host!r}"
            )
        if not _allowed_bind_address(host):
            raise ValueError(
                "KOLLAB_WEBUI_HOSTS cannot bind wildcard, unspecified, or public "
                f"addresses; rejected {host!r}. Use a private interface address."
            )
        canonical = str(address)
        if canonical not in seen:
            seen.add(canonical)
            result.append(host)
    if not result:
        raise ValueError("KOLLAB_WEBUI_HOSTS must include a private interface address")
    return result


def configured_bind_hosts() -> list[str]:
    extra = [
        value.strip()
        for value in os.environ.get("KOLLAB_WEBUI_HOSTS", "").split(",")
        if value.strip()
    ]
    return validate_bind_hosts(["127.0.0.1", *extra])


def allowed_hostnames(bind_hosts: Sequence[str]) -> frozenset[str]:
    hosts = {"localhost"}
    for host in bind_hosts:
        address = _parse_ip(host)
        if address is not None:
            hosts.add(str(address).lower())
    return frozenset(hosts)


def _private_network(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    if isinstance(network, ipaddress.IPv4Network):
        return (
            any(network.subnet_of(parent) for parent in _RFC1918)
            or network.subnet_of(_V4_LINK_LOCAL)
            or network.subnet_of(_LOOPBACK_V4)
        )
    return (
        network.subnet_of(_ULA)
        or network.subnet_of(_V6_LINK_LOCAL)
        or network.subnet_of(_LOOPBACK_V6)
    )


def interface_networks_for_hosts(bind_hosts: Sequence[str]):
    """Resolve the exact assigned CIDRs for the configured bind interfaces."""
    assigned: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    interfaces = psutil.net_if_addrs()
    for host in bind_hosts:
        address = _parse_ip(host)
        if address is None:
            raise ValueError(f"WebUI bind host {host!r} is not a literal IP address")
        if address.is_loopback:
            assigned.add(_LOOPBACK_V4 if isinstance(address, ipaddress.IPv4Address) else _LOOPBACK_V6)
            continue

        matched = False
        for entries in interfaces.values():
            for entry in entries:
                interface_address = _parse_ip(entry.address)
                if interface_address != address or not entry.netmask:
                    continue
                try:
                    network = ipaddress.ip_network(
                        f"{interface_address}/{entry.netmask}", strict=False
                    )
                except ValueError:
                    continue
                if not _private_network(network):
                    raise ValueError(
                        f"WebUI bind address {host!r} resolves to a non-private interface subnet"
                    )
                assigned.add(network)
                matched = True
        if not matched:
            raise ValueError(
                f"Cannot determine the private subnet for WebUI bind address {host!r}; "
                "bind an address assigned to a local interface."
            )
    return tuple(
        sorted(
            assigned,
            key=lambda network: (
                network.version,
                int(network.network_address),
                network.prefixlen,
            ),
        )
    )





def client_in_networks(
    host: str,
    networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    address = _parse_ip(host)
    return address is not None and any(address in network for network in networks)


def _host_and_port(value: str):
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    ip = _parse_ip(host)
    normalized = str(ip).lower() if ip is not None else host.rstrip(".").lower()
    return normalized, port


def host_is_allowed(host_header: str, allowed_hosts: Iterable[str]) -> bool:
    parsed = _host_and_port(host_header)
    if parsed is None:
        return False
    host, _port = parsed
    return host in allowed_hosts


def origin_is_same_origin(origin: str | None, host_header: str, scheme: str) -> bool:
    if origin is None:
        return True
    if origin.strip().lower() == "null":
        return False
    try:
        parsed = urlsplit(origin)
        origin_host_port = _host_and_port(parsed.netloc)
        request_host_port = _host_and_port(host_header)
        if (
            parsed.scheme.lower() != scheme.lower()
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or origin_host_port is None
            or request_host_port is None
        ):
            return False
        origin_host, origin_port = origin_host_port
        request_host, request_port = request_host_port
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        return (
            origin_host == request_host
            and (origin_port or default_port) == (request_port or default_port)
        )
    except ValueError:
        return False


def is_forwarded_client_header(name: str) -> bool:
    lower_name = name.lower()
    return lower_name in _FORWARDING_HEADERS or lower_name.startswith("x-forwarded-")



def has_forwarded_client_headers(headers) -> bool:
    """Reject proxy chains; this WebUI supports direct private binding only."""
    return any(is_forwarded_client_header(name) for name in headers.keys())



def request_origin_is_safe(request, allowed_hosts: Iterable[str]) -> bool:
    """Require the same origin for browser API access and reject cross-site fetches."""
    host_header = request.headers.get("host", "")
    if not host_is_allowed(host_header, allowed_hosts):
        return False

    origin = request.headers.get("origin")
    if origin is not None:
        return origin_is_same_origin(origin, host_header, request.url.scheme)

    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False
    referer = request.headers.get("referer")
    if not referer:
        return True
    try:
        parsed = urlsplit(referer)
        referer_host_port = _host_and_port(parsed.netloc)
        request_host_port = _host_and_port(host_header)
        if (
            parsed.scheme.lower() != request.url.scheme.lower()
            or referer_host_port is None
            or request_host_port is None
        ):
            return False
        referer_host, referer_port = referer_host_port
        request_host, request_port = request_host_port
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        return (
            referer_host == request_host
            and (referer_port or default_port) == (request_port or default_port)
        )
    except ValueError:
        return False


def networks_for_phone_peers(peers: Iterable[dict[str, object]]):
    """Trust only individual configured phone addresses, not the whole WG pool."""
    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for peer in peers:
        value = peer.get("address")
        if not isinstance(value, str):
            continue
        address = _parse_ip(value)
        if address is None or not _allowed_bind_address(str(address)):
            continue
        networks.add(
            ipaddress.ip_network(f"{address}/{address.max_prefixlen}", strict=True)
        )
    return tuple(
        sorted(
            networks,
            key=lambda network: (
                network.version,
                int(network.network_address),
                network.prefixlen,
            ),
        )
    )

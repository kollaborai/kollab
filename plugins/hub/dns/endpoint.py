"""Off-box transport for Agent DNS: TLS endpoint + federation bootstrap.

Adds a TCP/TLS listener alongside the per-agent unix socket so remote
agents can complete the SAME Ed25519 challenge-response handshake and
deliver messages over the network.

The handshake and message protocol in ``messenger.py`` are
transport-neutral — they operate on ``asyncio`` stream pairs, not on a
specific socket family. So this module only adds the *listener* (server
side) and the *dial* (client side); the wire protocol is unchanged.

Layering:
- TLS is the channel — privacy + server-certificate authentication.
- Ed25519 is the identity — proves *which agent* is on the wire.
  They are complementary, not redundant: TLS says "the pipe is private",
  the handshake says "the peer is peridot".

Standards alignment (see ``dns/__init__.py``): the advertised
``endpoint_uri`` is published in the agent's DNS record via
``AgentRecord.to_aid_txt`` / ``to_ardp_json`` / ``DNSStorage.write_well_known``
and resolved by peers through ``AgentRegistry.resolve_address``.
"""

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT_PORT = 8765

# Schemes that denote a remote endpoint URI rather than a unix socket path.
# wss/https carry TLS; ws/a2a are plaintext (trusted-network or loopback).
TLS_SCHEMES = ("wss://", "https://")
PLAINTEXT_SCHEMES = ("ws://", "a2a://")
REMOTE_SCHEMES = TLS_SCHEMES + PLAINTEXT_SCHEMES


def is_remote_uri(addr: str) -> bool:
    """True if ``addr`` is a remote endpoint URI, not a unix socket path."""
    return bool(addr) and addr.startswith(REMOTE_SCHEMES)


def uri_is_tls(addr: str) -> bool:
    """True if the URI scheme implies a TLS-wrapped transport."""
    return bool(addr) and addr.startswith(TLS_SCHEMES)


def parse_endpoint_uri(uri: str) -> Optional[Tuple[str, int]]:
    """Parse ``wss://host:port`` (or https/ws/a2a) into ``(host, port)``.

    Returns ``None`` if the URI is not a parseable remote endpoint.
    Port defaults to 443 for TLS schemes, else ``DEFAULT_ENDPOINT_PORT``.
    """
    if not is_remote_uri(uri):
        return None
    parsed = urlparse(uri)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port
    if port is None:
        port = 443 if uri_is_tls(uri) else DEFAULT_ENDPOINT_PORT
    return host, port


@dataclass
class EndpointConfig:
    """Resolved configuration for the off-box A2A endpoint.

    Built from flat ``plugins.hub.endpoint_*`` config keys so it matches
    the surrounding hub config style (``require_auth``, ``authority``).
    """

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = DEFAULT_ENDPOINT_PORT
    tls_cert: str = ""
    tls_key: str = ""
    tls_ca: str = ""
    advertise_host: str = ""
    allow_insecure: bool = False
    authority: str = "kollabor.ai"

    @classmethod
    def from_config(
        cls, config: Any, authority: str = "kollabor.ai"
    ) -> "EndpointConfig":
        """Read endpoint settings from a kollabor config object (dot keys)."""
        if config is None:
            return cls(authority=authority)
        g = config.get
        return cls(
            enabled=bool(g("plugins.hub.endpoint_enabled", False)),
            host=g("plugins.hub.endpoint_host", "0.0.0.0") or "0.0.0.0",
            port=int(g("plugins.hub.endpoint_port", DEFAULT_ENDPOINT_PORT)),
            tls_cert=g("plugins.hub.endpoint_tls_cert", "") or "",
            tls_key=g("plugins.hub.endpoint_tls_key", "") or "",
            tls_ca=g("plugins.hub.endpoint_tls_ca", "") or "",
            advertise_host=g("plugins.hub.endpoint_advertise_host", "") or "",
            allow_insecure=bool(g("plugins.hub.endpoint_allow_insecure", False)),
            authority=g("plugins.hub.authority", authority) or authority,
        )

    @property
    def advertised_host(self) -> str:
        """Public hostname peers should dial — falls back to the authority."""
        return self.advertise_host or self.authority

    @property
    def has_tls(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    @property
    def endpoint_uri(self) -> str:
        """The URI to advertise in this agent's DNS record."""
        scheme = "wss" if self.has_tls else "ws"
        return f"{scheme}://{self.advertised_host}:{self.port}"


def build_server_ssl_context(cert: str, key: str) -> Optional[ssl.SSLContext]:
    """Build a server-side TLS context from cert + key PEM paths.

    Returns ``None`` if cert/key are absent or fail to load — the caller
    decides whether to refuse to start (fail-closed) or run plaintext.
    """
    if not cert or not key:
        return None
    try:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx
    except (ssl.SSLError, OSError) as e:
        logger.error(f"endpoint: failed to build server TLS context: {e}")
        return None


def build_client_ssl_context(ca: str = "") -> ssl.SSLContext:
    """Build a client-side TLS context for dialing remote endpoints.

    Uses the system trust store by default; loads a custom CA bundle when
    provided (for self-signed mesh CAs).
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if ca:
        try:
            ctx.load_verify_locations(cafile=ca)
        except (ssl.SSLError, OSError) as e:
            logger.warning(f"endpoint: failed to load CA bundle {ca}: {e}")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# --- Federation bootstrap (AID/ANS discovery) ---------------------------------


def normalize_well_known_url(url: str) -> str:
    """Turn a bare authority or partial URL into a full well-known URL.

    ``example.com`` -> ``https://example.com/.well-known/agent-keys.json``
    A URL that already points at ``/.well-known/`` is returned unchanged.
    """
    base = url if url.startswith(("http://", "https://")) else f"https://{url}"
    if "/.well-known/" in base:
        return base
    return base.rstrip("/") + "/.well-known/agent-keys.json"


def fetch_well_known(url: str, ca: str = "", timeout: float = 10.0) -> Optional[dict]:
    """Fetch + parse a remote ``/.well-known/agent-keys.json`` (AID format).

    Network I/O — run via ``asyncio.to_thread`` if called from an event
    loop. Returns the parsed payload dict, or ``None`` on any failure.
    """
    fetch_url = normalize_well_known_url(url)
    ctx = build_client_ssl_context(ca)
    try:
        req = urllib.request.Request(fetch_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
        payload = json.loads(data)
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"endpoint: well-known fetch failed for {fetch_url}: {e}")
        return None


def register_well_known(
    payload: dict, registry: Any, identity_manager: Any = None
) -> Optional[str]:
    """Import a remote coordinator from an AID well-known payload.

    Builds an ``AgentRecord`` for the remote coordinator (designation,
    authority, public key, endpoint URI) and registers it locally so the
    inbound Ed25519 handshake can verify the remote's signature.

    When ``identity_manager`` is supplied and the payload carries a
    self-attestation, the attestation is verified before the key is
    trusted; a failed verification aborts the import (returns ``None``).

    Returns the imported designation, or ``None``.
    """
    from .models import AgentRecord, Attestation

    coord = payload.get("coordinator", {}) if isinstance(payload, dict) else {}
    designation = coord.get("designation", "")
    public_key = coord.get("public_key", "")
    if not designation or not public_key:
        logger.warning("endpoint: well-known payload missing designation/public_key")
        return None

    endpoints = payload.get("endpoints", {}) or {}
    endpoint_uri = coord.get("endpoint_uri", "") or endpoints.get("endpoint", "")

    record = AgentRecord(
        designation=designation,
        runtime=coord.get("runtime", "kollab"),
        authority=payload.get("authority", "") or designation,
        endpoint_uri=endpoint_uri,
        public_key=public_key,
        protocols=coord.get("protocols", ["a2a"]),
        is_coordinator=True,
        approval_state="approved",  # remote coordinators are trusted on import
    )

    att = coord.get("attestation")
    if att:
        attestation = Attestation(
            subject=designation,
            issuer=att.get("issuer", designation),
            public_key=public_key,
            signature=att.get("signature", ""),
            issued_at=att.get("issued_at", 0.0),
        )
        if identity_manager is not None and not identity_manager.verify_attestation(
            attestation
        ):
            logger.warning(
                f"endpoint: attestation verification failed for {designation} — "
                "refusing import"
            )
            return None
        record.attestation = attestation

    registry.register(record)
    logger.info(
        f"endpoint: imported remote coordinator {designation} "
        f"({endpoint_uri or 'no endpoint'}) from well-known"
    )
    return designation

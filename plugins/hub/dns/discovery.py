"""Explicit domain discovery. Nothing here admits a peer to the local Hub.

TXT is a locator. HTTPS binds the selected origin; Ed25519 authenticates the
complete descriptor. Group membership and messaging are separate protocols.
"""

import asyncio
import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
import dns.asyncresolver
import dns.exception
import dns.resolver
import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

SCHEMA = "kollab-discovery/2"
WELL_KNOWN = "/.well-known/agent-keys.json"
DOCUMENT_PATHS = {WELL_KNOWN, "/.well-known/agent-keys"}
MAX_DOCUMENT = 64 * 1024
MAX_INTEGER = 2**53 - 1
HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


class DiscoveryError(ValueError):
    """Stable error code plus a safe, human-readable explanation."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class DiscoveryTarget:
    authority: str
    origin: str
    url: str
    explicit: bool


@dataclass(frozen=True)
class DiscoveryResult:
    requested_authority: str
    origin: str
    discovery_url: str
    publisher_principal_id: str
    manifest: dict[str, Any]
    identity_evidence: str = "https-origin"
    discovery_state: str = "verified"
    membership_state: str = "none"
    reachability_state: str = "untested"
    message_authorization: str = "none"

    def summary(self) -> str:
        coord = self.manifest["coordinator"]
        service = "control advertised; untested" if self.manifest["endpoints"].get("control") else "identity only"
        if self.manifest["endpoints"].get("agent_card"):
            service = "A2A Card advertised; service reachability untested"
        return "\n".join(
            [
                f"discovery: verified ({self.identity_evidence})",
                f"authority: {self.requested_authority}",
                f"publisher: {coord['designation']}",
                f"document: {self.discovery_url}",
                f"service: {service}",
                "membership: none; messaging: not authorized; reachability: untested",
                "Saved discovery metadata. No agent connection or enrollment was made.",
            ]
        )


@dataclass(frozen=True)
class ResolvedAgentCard:
    """Signed wire document plus cryptographic verification; not admission."""

    document: dict[str, Any]
    verification: Any


def normalize_target(value: str, *, document: bool = True) -> DiscoveryTarget:
    """Normalize a domain/HTTPS origin or an explicitly selected document."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise DiscoveryError("invalid_target", "provide a domain or HTTPS discovery URL")
    if any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in "\\%?#"):
        raise DiscoveryError("invalid_target", "escaped hosts, whitespace, queries and fragments are unsupported")
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise ValueError("HTTPS without credentials required")
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        # aiohttp treats all-numeric hosts as IPs and bypasses its resolver,
        # including abbreviated forms that ipaddress.ip_address rejects.
        if host.replace(".", "").isdigit():
            raise ValueError("use a DNS hostname, not a numeric address")
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
        ):
            raise ValueError("invalid DNS hostname")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("use a DNS hostname, not an IP literal")
        port = parsed.port or 443
        if not 1 <= port <= 65535 or parsed.port == 0:
            raise ValueError("invalid port")
        origin = f"https://{host}" + (f":{port}" if port != 443 else "")
        explicit = parsed.path not in ("", "/")
        if document and explicit and parsed.path not in DOCUMENT_PATHS:
            raise ValueError("expected /.well-known/agent-keys.json")
        path = parsed.path if explicit else (WELL_KNOWN if document else "")
        return DiscoveryTarget(host, origin, origin + path, explicit)
    except (ValueError, UnicodeError) as exc:
        raise DiscoveryError("invalid_target", str(exc)) from exc


def parse_txt(records: list[tuple[bytes, ...]]) -> dict[str, str] | None:
    """Concatenate strings within an RR; never concatenate separate RRs."""
    candidates = []
    for chunks in records:
        raw = b"".join(chunks)
        if len(raw) > 4096:
            raise DiscoveryError("invalid_txt", "TXT record exceeds 4 KiB")
        try:
            value = raw.decode("utf-8")
        except UnicodeError as exc:
            raise DiscoveryError("invalid_txt", "TXT is not UTF-8") from exc
        fields = [part.strip() for part in value.split(";") if part.strip()]
        if not any(re.match(r"v\s*=\s*aid", field) for field in fields):
            continue
        parsed = {}
        for field in fields:
            key, sep, val = field.partition("=")
            key, val = key.strip(), val.strip()
            if not sep or not key or not val or key in parsed:
                raise DiscoveryError("invalid_txt", "empty or duplicate TXT field")
            parsed[key] = val
        if parsed.get("v") != "aid1":
            raise DiscoveryError("unsupported_version", "unsupported AID TXT version")
        if not parsed.get("u"):
            raise DiscoveryError("invalid_txt", "AID TXT requires u")
        if parsed not in candidates:
            candidates.append(parsed)
    if len(candidates) > 1:
        raise DiscoveryError("conflicting_txt", "multiple distinct AID records")
    return candidates[0] if candidates else None


def decode_document(raw: bytes) -> dict:
    if len(raw) > MAX_DOCUMENT:
        raise DiscoveryError("document_too_large", "discovery document exceeds 64 KiB")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON number")

    def depth(value, level=1):
        if level > 16:
            raise ValueError("JSON nesting exceeds 16 levels")
        if type(value) is int and not 0 <= value <= MAX_INTEGER:
            raise ValueError("JSON integer outside supported range")
        if isinstance(value, dict):
            for item in value.values():
                depth(item, level + 1)
        elif isinstance(value, list):
            for item in value:
                depth(item, level + 1)

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(payload, dict):
            raise ValueError("document must be an object")
        depth(payload)
        # Also rejects invalid Unicode and non-finite exponent notation.
        rfc8785.dumps(payload)
        return payload
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DiscoveryError("invalid_document", str(exc)) from exc


def verify_manifest(payload: dict, target: DiscoveryTarget, *, now: float | None = None) -> DiscoveryResult:
    """Verify the complete document using its explicit signer and origin."""
    if "schema" not in payload:
        raise DiscoveryError("legacy_document", "publisher must upgrade to kollab-discovery/2; no identity imported")
    if payload.get("schema") != SCHEMA or payload.get("v") != "aid1":
        raise DiscoveryError("unsupported_version", "expected kollab-discovery/2 and aid1")
    try:
        if payload["authority"] != target.authority:
            raise ValueError("authority does not match selected origin")
        coord, endpoints, discovery = (payload[key] for key in ("coordinator", "endpoints", "discovery"))
        designation, key = coord["designation"], coord["public_key"]
        if not LABEL.fullmatch(designation) or not HEX_KEY.fullmatch(key) or coord["key_type"] != "ed25519":
            raise ValueError("invalid designation or Ed25519 key")
        principal = "ed25519:" + key
        if coord["aid"] != f"agent:{designation}@{target.authority}" or discovery["principal_id"] != principal:
            raise ValueError("identity fields do not agree")
        protocols = coord["protocols"]
        if (
            not isinstance(protocols, list)
            or SCHEMA not in protocols
            or len(protocols) > 16
            or any(not isinstance(p, str) or not re.fullmatch(r"[a-zA-Z0-9./_-]{1,64}", p) for p in protocols)
        ):
            raise ValueError("invalid discovery protocols")
        if not endpoints["registry"].startswith("https://"):
            raise ValueError("registry requires an absolute HTTPS URL")
        registry = normalize_target(endpoints["registry"])
        if registry.origin != target.origin or registry.url != target.origin + WELL_KNOWN:
            raise ValueError("registry must be the canonical same-origin JSON URL")
        # The public descriptor cannot import socket paths or raw-stream routes.
        if set(endpoints) - {"registry", "control", "agent_card"}:
            raise ValueError("unsupported public endpoint field")
        if "agent_card" in endpoints and endpoints["agent_card"] != target.origin + "/.well-known/agent-card.json":
            raise ValueError("agent_card must be the canonical same-origin Agent Card URL")
        if "control" in endpoints:
            if (
                not endpoints["control"].startswith("https://")
                or normalize_target(endpoints["control"], document=False).origin != target.origin
            ):
                raise ValueError("control must use the same HTTPS origin")
        roles, bootstrap = discovery["roles"], discovery["bootstrap"]
        if (
            not isinstance(roles, list)
            or len(roles) > 3
            or any(r not in ("directory", "rendezvous", "relay") for r in roles)
            or len(set(roles)) != len(roles)
        ):
            raise ValueError("invalid service roles")
        if roles and "control" not in endpoints:
            raise ValueError("service roles require a control endpoint")
        if not isinstance(bootstrap, list) or len(bootstrap) > 8:
            raise ValueError("at most eight bootstrap referrals")
        for referral in bootstrap:
            if not isinstance(referral, str) or not referral.startswith("https://"):
                raise ValueError("bootstrap requires absolute HTTPS URLs")
            normalize_target(referral)
        revision, published, expires = (payload[k] for k in ("revision", "published_at", "expires_at"))
        if any(type(v) is not int or not 0 <= v <= MAX_INTEGER for v in (revision, published, expires)):
            raise ValueError("revision and times must be nonnegative safe integers")
        now = time.time() if now is None else now
        if not published < expires <= published + 300 or published > now + 60:
            raise ValueError("invalid publication/expiry window")
        if expires <= now:
            raise DiscoveryError("expired", "discovery document has expired")
        signature = payload["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{128}", signature):
            raise ValueError("invalid Ed25519 signature encoding")
        unsigned = {k: v for k, v in payload.items() if k != "signature"}
        VerifyKey(bytes.fromhex(key)).verify(rfc8785.dumps(unsigned), bytes.fromhex(signature))
        return DiscoveryResult(target.authority, target.origin, target.url, principal, payload)
    except DiscoveryError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, BadSignatureError) as exc:
        raise DiscoveryError("invalid_document", str(exc) or "signature verification failed") from exc


class _PublicResolver(aiohttp.abc.AbstractResolver):
    """Give the connector only validated numeric addresses; no second DNS lookup."""

    def __init__(self, resolver: dns.asyncresolver.Resolver, private_cidrs: tuple[str, ...]):
        self.resolver = resolver
        self.networks = tuple(ipaddress.ip_network(cidr, strict=True) for cidr in private_cidrs)

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        addresses = []
        for record_type in ("A", "AAAA"):
            try:
                answer = await self.resolver.resolve(host + ".", record_type, lifetime=5, search=False)
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            for record in answer:
                ip = ipaddress.ip_address(record.address)
                forbidden = ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
                if isinstance(ip, ipaddress.IPv6Address):
                    forbidden = forbidden or bool(ip.ipv4_mapped or ip.sixtofour or ip.teredo)
                allowed = ip.is_global or any(ip in net for net in self.networks)
                if forbidden or not allowed:
                    raise DiscoveryError("address_denied", "discovery resolves outside the permitted address scope")
                addresses.append(
                    {
                        "hostname": host,
                        "host": str(ip),
                        "port": port,
                        "family": socket.AF_INET if ip.version == 4 else socket.AF_INET6,
                        "proto": socket.IPPROTO_TCP,
                        "flags": socket.AI_NUMERICHOST,
                    }
                )
        if not addresses:
            raise DiscoveryError("dns_unavailable", "no usable address for discovery origin")
        return addresses

    async def close(self):
        pass


async def discover(value: str, *, ca: str = "", private_cidrs: tuple[str, ...] = ()) -> DiscoveryResult:
    """One explicit lookup, bounded to 30 seconds. No enrollment or dialing."""
    try:
        async with asyncio.timeout(30):
            return await _discover(value, ca=ca, private_cidrs=private_cidrs)
    except DiscoveryError:
        raise
    except TimeoutError as exc:
        raise DiscoveryError("timeout", "discovery exceeded its time limit") from exc
    except (aiohttp.ClientError, dns.exception.DNSException, OSError, ValueError) as exc:
        raise DiscoveryError("unavailable", "DNS, HTTPS or discovery configuration failed") from exc


async def _discover(value: str, *, ca: str, private_cidrs: tuple[str, ...]) -> DiscoveryResult:
    target = normalize_target(value)
    resolver = dns.asyncresolver.Resolver()
    hint = None
    if not target.explicit:
        try:
            answer = await resolver.resolve(f"_agent.{target.authority}.", "TXT", lifetime=5, search=False)
            hint = parse_txt([record.strings for record in answer])
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        if hint:
            selected = normalize_target(hint["u"])
            if not hint["u"].startswith("https://") or not selected.explicit or selected.origin != target.origin:
                raise DiscoveryError("invalid_txt", "u must select a same-origin HTTPS discovery document")
            target = selected
    target, payload = await _fetch_json(target, resolver, ca=ca, private_cidrs=private_cidrs)
    result = verify_manifest(payload, target)
    if hint and hint.get("k") and hint["k"] != result.manifest["coordinator"]["public_key"]:
        raise DiscoveryError("key_conflict", "TXT key hint differs from document signer")
    return result


async def _fetch_json(target, resolver, *, ca, private_cidrs, card=False):
    """Common bounded HTTPS transport; Cards prohibit redirects entirely."""
    context = ssl.create_default_context(cafile=ca or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connector = aiohttp.TCPConnector(
        resolver=_PublicResolver(resolver, private_cidrs), use_dns_cache=False, force_close=True, ssl=context
    )
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=False,
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
    ) as session:
        for redirects in range(3):
            async with session.get(target.url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    if card:
                        raise DiscoveryError("redirect_denied", "Agent Card redirects are not permitted")
                    redirected = normalize_target(urljoin(target.url, response.headers.get("Location", "")))
                    if redirects == 2 or redirected.origin != target.origin:
                        raise DiscoveryError("redirect_denied", "only two same-origin HTTPS redirects permitted")
                    target = redirected
                    continue
                if response.status != 200:
                    raise DiscoveryError("http_error", f"discovery returned HTTP {response.status}")
                if (
                    response.content_type != "application/json"
                    or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                ):
                    raise DiscoveryError("invalid_content_type", "expected uncompressed application/json")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    if len(raw) > MAX_DOCUMENT:
                        raise DiscoveryError("document_too_large", "discovery document exceeds 64 KiB")
                if card:
                    from .a2a_signing import decode_agent_card

                    return target, decode_agent_card(bytes(raw))
                return target, decode_document(bytes(raw))
    raise DiscoveryError("unavailable", "no discovery document returned")


async def fetch_agent_card(result: DiscoveryResult, *, ca: str = "", private_cidrs: tuple[str, ...] = ()):
    """Follow an advertised Card using the verified locator's signer.

    Call DiscoveryStore.accept before this method to enforce durable origin
    pins. Missing Card means identity-only; no speculative endpoint is probed.
    Generic capabilities/interfaces live exclusively in the signed A2A Card.
    """
    verified = verify_manifest(result.manifest, normalize_target(result.discovery_url))
    if not verified.manifest["endpoints"].get("agent_card"):
        return None
    from .a2a_signing import resolve_agent_card_url, verify_agent_card

    url = resolve_agent_card_url(verified.origin, verified.manifest["endpoints"].get("agent_card"))
    if url is None:
        return None
    target = normalize_target(url, document=False)
    try:
        async with asyncio.timeout(30):
            _, payload = await _fetch_json(
                target, dns.asyncresolver.Resolver(), ca=ca, private_cidrs=private_cidrs, card=True
            )
            verification = verify_agent_card(
                payload,
                origin=verified.origin,
                pinned_public_key=verified.manifest["coordinator"]["public_key"],
            )
            return ResolvedAgentCard(payload, verification)
    except DiscoveryError:
        raise
    except TimeoutError as exc:
        raise DiscoveryError("timeout", "Agent Card lookup exceeded its time limit") from exc
    except (aiohttp.ClientError, dns.exception.DNSException, OSError, ValueError) as exc:
        raise DiscoveryError("card_unavailable", "Agent Card transport or signature verification failed") from exc

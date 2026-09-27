"""Bounded HTTP orchestration checks; live HTTPS proof is recorded separately."""

import copy
import json
from types import SimpleNamespace

import dns.resolver
import pytest
import rfc8785
from nacl.signing import SigningKey

from plugins.hub.dns import discovery
from plugins.hub.dns.discovery_publish import publish


class Response:
    def __init__(self, raw, status=200, headers=None, content_type="application/json"):
        self.raw, self.status = raw, status
        self.headers, self.content_type = headers or {}, content_type
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for offset in range(0, len(self.raw), size):
            yield self.raw[offset : offset + size]


@pytest.fixture
def transport(tmp_path, monkeypatch):
    payload = publish("example.com", tmp_path / "state", tmp_path / "public" / "agent-keys.json")
    state = SimpleNamespace(payload=payload, responses=[], urls=[], txt=None, queries=[])
    state.key = SigningKey((tmp_path / "state" / "service.key").read_bytes())

    class Resolver:
        async def resolve(self, host, kind, **kwargs):
            state.queries.append((host, kind))
            if state.txt is None:
                raise dns.resolver.NoAnswer
            return [SimpleNamespace(strings=(state.txt,))]

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["auto_decompress"] is False
            assert kwargs["timeout"].total == 10

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, *, allow_redirects):
            assert allow_redirects is False
            state.urls.append(url)
            return state.responses.pop(0)

    monkeypatch.setattr(discovery.dns.asyncresolver, "Resolver", Resolver)
    monkeypatch.setattr(discovery.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(discovery.aiohttp, "TCPConnector", lambda **kwargs: None)
    return state


@pytest.mark.asyncio
async def test_txt_selection_and_missing_txt_canonical_fallback(transport):
    transport.responses = [Response(json.dumps(transport.payload).encode())]
    result = await discovery.discover("example.com")
    assert result.discovery_url == "https://example.com/.well-known/agent-keys.json"
    transport.txt = b"v=aid1;u=https://example.com/.well-known/agent-keys"
    transport.responses = [Response(json.dumps(transport.payload).encode())]
    result = await discovery.discover("example.com")
    assert result.discovery_url.endswith("/agent-keys")


@pytest.mark.asyncio
async def test_explicit_document_bypasses_txt_and_no_http_fallback(transport):
    transport.responses = [Response(b"{}", status=404)]
    with pytest.raises(discovery.DiscoveryError, match="http_error"):
        await discovery.discover("https://example.com/.well-known/agent-keys.json")
    assert transport.queries == []
    assert len(transport.urls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,code",
    [
        (Response(b"{}", content_type="text/html"), "invalid_content_type"),
        (Response(b"{}", headers={"Content-Encoding": "gzip"}), "invalid_content_type"),
        (Response(b"x" * (discovery.MAX_DOCUMENT + 1)), "document_too_large"),
        (
            Response(b"{}", status=302, headers={"Location": "https://evil.example/.well-known/agent-keys.json"}),
            "redirect_denied",
        ),
    ],
)
async def test_transport_rejections(transport, response, code):
    transport.responses = [copy.copy(response)]
    with pytest.raises(discovery.DiscoveryError, match=code):
        await discovery.discover("example.com")


@pytest.mark.asyncio
async def test_redirect_bound_and_txt_key_conflict(transport):
    transport.responses = [
        Response(b"", status=302, headers={"Location": "/.well-known/agent-keys.json"}) for _ in range(3)
    ]
    with pytest.raises(discovery.DiscoveryError, match="redirect_denied"):
        await discovery.discover("example.com")
    assert len(transport.urls) == 3
    transport.txt = b"v=aid1;u=https://example.com/.well-known/agent-keys.json;k=" + b"0" * 64
    transport.responses = [Response(json.dumps(transport.payload).encode())]
    with pytest.raises(discovery.DiscoveryError, match="key_conflict"):
        await discovery.discover("example.com")


@pytest.mark.asyncio
async def test_identity_only_never_probes_an_agent_card(transport):
    result = discovery.verify_manifest(transport.payload, discovery.normalize_target("example.com"))
    assert await discovery.fetch_agent_card(result) is None
    assert transport.urls == []


@pytest.mark.asyncio
async def test_signed_locator_to_pinned_jws_card_and_redirect_rejection(transport):
    from plugins.hub.dns.a2a_signing import sign_agent_card

    payload = copy.deepcopy(transport.payload)
    payload["endpoints"]["agent_card"] = "https://example.com/.well-known/agent-card.json"
    payload.pop("signature")
    payload["signature"] = transport.key.sign(rfc8785.dumps(payload)).signature.hex()
    result = discovery.verify_manifest(payload, discovery.normalize_target("example.com"))
    card = sign_agent_card({"name": "Example", "version": "1.0", "x-number": -1}, transport.key)
    transport.responses = [Response(json.dumps(card).encode())]
    resolved = await discovery.fetch_agent_card(result)
    assert resolved.verification.kid == result.publisher_principal_id
    assert resolved.document["x-number"] == -1
    assert transport.urls == [payload["endpoints"]["agent_card"]]
    transport.responses = [Response(b"", status=302, headers={"Location": payload["endpoints"]["agent_card"]})]
    with pytest.raises(discovery.DiscoveryError, match="redirect_denied"):
        await discovery.fetch_agent_card(result)
    changed = sign_agent_card({"name": "Example"}, SigningKey.generate())
    transport.responses = [Response(json.dumps(changed).encode())]
    with pytest.raises(discovery.DiscoveryError, match="card_unavailable"):
        await discovery.fetch_agent_card(result)

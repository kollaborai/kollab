"""Discovery validation never admits a peer or executes an agent."""

import copy
import json
import socket
from types import SimpleNamespace

import dns.resolver
import pytest
import rfc8785
from nacl.signing import SigningKey

from plugins.hub.dns.discovery import (
    MAX_DOCUMENT,
    DiscoveryError,
    _PublicResolver,
    decode_document,
    normalize_target,
    parse_txt,
    verify_manifest,
)
from plugins.hub.dns.discovery_publish import publish
from plugins.hub.dns.discovery_store import DiscoveryStore


@pytest.fixture
def published(tmp_path):
    payload = publish("https://example.com", tmp_path / "private", tmp_path / "public" / "agent-keys.json")
    key = SigningKey((tmp_path / "private" / "service.key").read_bytes())
    return payload, key


def resign(payload, key):
    payload = copy.deepcopy(payload)
    payload.pop("signature", None)
    payload["signature"] = key.sign(rfc8785.dumps(payload)).signature.hex()
    return payload


@pytest.mark.parametrize(
    "target",
    [
        "http://example.com",
        "https://user@example.com",
        "https://example.com/?secret=1",
        "https://example.com/#fragment",
        "https://example.com/not-discovery",
        "127.1",
        "2130706433",
        "127.0.0.1",
        "[::1]",
        "example.com:0",
        "example..com",
        "exa%6dple.com",
        "example.com\\evil",
        "example.com\n",
        "",
    ],
)
def test_invalid_targets(target):
    with pytest.raises(DiscoveryError):
        normalize_target(target)


def test_normalization_and_txt():
    assert normalize_target("EXAMPLE.COM.").origin == "https://example.com"
    assert normalize_target("https://example.com:443/").url.endswith("/agent-keys.json")
    assert normalize_target("https://example.com/.well-known/agent-keys").explicit
    record = (b"v=aid1;u=https://example.com/", b".well-known/agent-keys.json")
    assert parse_txt([record, record])["u"] == normalize_target("example.com").url
    assert parse_txt([(b"other=unrelated",)]) is None
    for records in (
        [(b"v=aid2;u=https://example.com",)],
        [(b"v=aid1;u=x;u=y",)],
        [(b"v=aid1;u=x",), (b"v=aid1;u=y",)],
        [(b"v=aid1",)],
        [(b"x" * 4097,)],
        [(b"\xff",)],
    ):
        with pytest.raises(DiscoveryError):
            parse_txt(records)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"v":1,"v":2}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":-1}',
        b'{"x":9007199254740992}',
        b"[]",
        b'{"x":"\\ud800"}',
        b'{"x":"\xff"}',
        b'{"x":' + b"[" * 20 + b"0" + b"]" * 20 + b"}",
        b"x" * (MAX_DOCUMENT + 1),
    ],
)
def test_bounded_strict_json(raw):
    with pytest.raises(DiscoveryError):
        decode_document(raw)


def test_identity_only_publisher_and_durable_revision(published, tmp_path):
    first, _ = published
    second = publish("example.com", tmp_path / "private", tmp_path / "public" / "agent-keys.json")
    assert first["coordinator"] == second["coordinator"]
    assert second["revision"] == first["revision"] + 1
    assert second["discovery"]["roles"] == []
    assert set(second["endpoints"]) == {"registry"}
    assert "a2a" not in second["coordinator"]["protocols"]
    assert (tmp_path / "private" / "service.key").stat().st_mode & 0o777 == 0o600
    result = verify_manifest(second, normalize_target("example.com"))
    assert result.membership_state == result.message_authorization == "none"
    assert "No agent connection" in result.summary()


def test_publisher_refuses_state_exposure_and_silent_identity_reset(published, tmp_path):
    with pytest.raises(DiscoveryError, match="private_state_exposed"):
        publish("example.com", tmp_path / "public" / "private", tmp_path / "public" / "agent-keys.json")
    with pytest.raises(DiscoveryError, match="publisher_conflict"):
        publish("different.example", tmp_path / "private", tmp_path / "public" / "agent-keys.json")
    state = tmp_path / "private" / "publisher.json"
    saved_state = state.read_bytes()
    state.unlink()
    with pytest.raises(DiscoveryError, match="state_missing"):
        publish("example.com", tmp_path / "private", tmp_path / "public" / "agent-keys.json")
    state.write_bytes(saved_state)
    (tmp_path / "private" / "service.key").unlink()
    with pytest.raises(DiscoveryError, match="key_missing"):
        publish("example.com", tmp_path / "private", tmp_path / "public" / "agent-keys.json")


def test_tampering_expiry_and_invalid_claims(published):
    original, key = published
    target = normalize_target("example.com")
    altered = copy.deepcopy(original)
    altered["revision"] += 1
    with pytest.raises(DiscoveryError, match="invalid_document"):
        verify_manifest(altered, target)
    with pytest.raises(DiscoveryError, match="expired"):
        verify_manifest(original, target, now=original["expires_at"])
    with pytest.raises(DiscoveryError, match="legacy_document"):
        verify_manifest({"v": "aid1"}, target)
    for mutate in (
        lambda p: p.update(authority="different.example"),
        lambda p: p.update(revision=True),
        lambda p: p.update(expires_at=p["published_at"] + 301),
        lambda p: p["endpoints"].update(socket="/private/socket"),
        lambda p: p["endpoints"].update(control="https://other.example/control"),
        lambda p: p["discovery"].update(roles=["relay"]),
        lambda p: p["discovery"].update(principal_id="ed25519:" + "0" * 64),
    ):
        payload = copy.deepcopy(original)
        mutate(payload)
        with pytest.raises(DiscoveryError):
            verify_manifest(resign(payload, key), target)


def test_pins_reject_rollback_changed_keys_and_conflicts(published, tmp_path):
    payload, key = published
    target = normalize_target("example.com")
    store = DiscoveryStore(tmp_path / "cache")
    result = verify_manifest(payload, target)
    assert store.accept(result).identity_evidence == "https-origin"
    assert store.accept(result).identity_evidence == "pinned-key"
    updated = copy.deepcopy(payload)
    updated["revision"] += 2
    store.accept(verify_manifest(resign(updated, key), target))
    with pytest.raises(DiscoveryError, match="rollback"):
        store.accept(result)
    changed = copy.deepcopy(updated)
    changed["coordinator"]["designation"] = "another"
    changed["coordinator"]["aid"] = "agent:another@example.com"
    with pytest.raises(DiscoveryError, match="revision_conflict"):
        store.accept(verify_manifest(resign(changed, key), target))
    replacement = SigningKey.generate()
    changed = copy.deepcopy(updated)
    changed["coordinator"]["public_key"] = replacement.verify_key.encode().hex()
    changed["discovery"]["principal_id"] = "ed25519:" + replacement.verify_key.encode().hex()
    with pytest.raises(DiscoveryError, match="key_changed"):
        store.accept(verify_manifest(resign(changed, replacement), target))
    cached = next((tmp_path / "cache").glob("*.json"))
    assert json.loads(cached.read_text())["revision"] == updated["revision"]
    cached.write_text("{}")
    with pytest.raises(DiscoveryError, match="cache_invalid"):
        store.accept(result)


class FakeDNS:
    def __init__(self, addresses):
        self.addresses = addresses

    async def resolve(self, host, kind, **kwargs):
        assert host == "example.com."
        assert kwargs == {"lifetime": 5, "search": False}
        if kind == "AAAA":
            raise dns.resolver.NoAnswer
        return [SimpleNamespace(address=address) for address in self.addresses]


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.1", "10.0.0.1", "224.0.0.1", "::1", "::ffff:8.8.8.8"])
async def test_resolver_rejects_nonpublic_address(address):
    resolver = _PublicResolver(FakeDNS([address]), ())
    with pytest.raises(DiscoveryError, match="address_denied"):
        await resolver.resolve("example.com", 443)


@pytest.mark.asyncio
async def test_resolver_uses_validated_addresses_and_scoped_private_opt_in():
    resolver = _PublicResolver(FakeDNS(["8.8.8.8"]), ())
    records = await resolver.resolve("example.com", 443)
    assert records[0]["host"] == "8.8.8.8"
    assert records[0]["flags"] == socket.AI_NUMERICHOST
    resolver = _PublicResolver(FakeDNS(["10.0.0.5"]), ("10.0.0.0/24",))
    assert (await resolver.resolve("example.com", 443))[0]["host"] == "10.0.0.5"
    resolver = _PublicResolver(FakeDNS(["127.0.0.1"]), ("127.0.0.0/8",))
    with pytest.raises(DiscoveryError, match="address_denied"):
        await resolver.resolve("example.com", 443)


def test_hub_plugin_still_discoverable():
    import inspect

    import plugins.hub

    classes = dict(inspect.getmembers(plugins.hub, inspect.isclass))
    assert "HubPlugin" in classes
    assert callable(classes["HubPlugin"].get_default_config)

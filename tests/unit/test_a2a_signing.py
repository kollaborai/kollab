"""Tests for the constrained A2A Agent Card signature profile."""

import base64
import json

import pytest
from nacl.signing import SigningKey

from plugins.hub.dns.a2a_signing import (
    AGENT_CARD_PATH,
    MAX_AGENT_CARD_BYTES,
    AgentCardSigningError,
    decode_agent_card,
    kid_for_public_key,
    resolve_agent_card_url,
    sign_agent_card,
    verify_agent_card,
)

ORIGIN = "https://agents.example"
SEED = bytes(range(32))


def unsigned_card():
    # Insertion order is intentionally not lexical; JCS controls signing.
    return {
        "name": "Café 🌲 東京",
        "version": "1.0.0",
        "provider": {"organization": "Kollab"},
        "description": "Unicode keys and values: \ue000 / 𐀀 / 😀",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "supportedInterfaces": [
            {
                "url": ORIGIN + "/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "skills": [{"id": "research", "name": "Research", "tags": ["é", "z", "𐀀"]}],
        "x-kollab-numbers": {
            "integer": 9007199254740991,
            "negativeInteger": -9007199254740991,
            "decimal": 333333333.3333333,
            "negativeZero": -0.0,
            "tiny": 1e-7,
        },
        "x-kollab-key-order": {"z": 1, "é": 2, "😀": 3, "a": 4, "𐀀": 5},
    }


def _decode_protected(card):
    encoded = card["signatures"][0]["protected"]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_sign_and_verify_unicode_numbers_and_key_order():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)

    assert _decode_protected(signed) == {
        "alg": "EdDSA",
        "kid": kid_for_public_key(key.verify_key),
        "typ": "JOSE",
    }
    result = verify_agent_card(
        signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
    )
    assert result.origin == ORIGIN
    assert result.kid == kid_for_public_key(key.verify_key)
    assert len(result.payload_sha256) == 64


def test_signatures_do_not_depend_on_python_mapping_order():
    key = SigningKey(SEED)
    card = unsigned_card()
    reversed_card = dict(reversed(list(card.items())))

    assert sign_agent_card(card, key) == sign_agent_card(reversed_card, key)


def test_signer_accepts_seed_and_valid_libsodium_secret_key_forms():
    key = SigningKey(SEED)
    card = unsigned_card()
    signed_seed = sign_agent_card(card, SEED.hex())
    signed_full = sign_agent_card(card, key.encode() + key.verify_key.encode())

    assert signed_seed == signed_full == sign_agent_card(card, key)


def test_signer_rejects_kid_that_does_not_identify_signing_key():
    with pytest.raises(AgentCardSigningError, match="kid_mismatch"):
        sign_agent_card(unsigned_card(), SigningKey(SEED), kid="ed25519:" + "0" * 64)


def test_tampered_payload_fails_verification():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    signed["description"] = "changed after signing"

    with pytest.raises(AgentCardSigningError, match="invalid_signature"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )


def test_other_pinned_key_fails_closed():
    signed = sign_agent_card(unsigned_card(), SigningKey(SEED))
    other_key = SigningKey(bytes(reversed(range(32))))

    with pytest.raises(AgentCardSigningError, match="key_changed"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=other_key.verify_key.encode()
        )


def test_algorithm_substitution_is_rejected_before_crypto():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    header = _decode_protected(signed)
    header["alg"] = "HS256"
    signed["signatures"][0]["protected"] = (
        base64.urlsafe_b64encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode()
    )

    with pytest.raises(AgentCardSigningError, match="unsupported_algorithm"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("jku", "https://agents.example/.well-known/jwks.json"),
        ("x5u", "https://agents.example/key.pem"),
        ("crit", ["alg"]),
    ],
)
def test_unknown_or_key_selecting_protected_headers_are_rejected(field, value):
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    header = _decode_protected(signed)
    header[field] = value
    signed["signatures"][0]["protected"] = (
        base64.urlsafe_b64encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode()
    )

    with pytest.raises(AgentCardSigningError, match="invalid_header"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )


def test_unprotected_signature_header_is_rejected():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    signed["signatures"][0]["header"] = {"kid": kid_for_public_key(key.verify_key)}

    with pytest.raises(AgentCardSigningError, match="unprotected"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )


def test_multiple_profile_signatures_are_rejected():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    signed["signatures"].append(dict(signed["signatures"][0]))

    with pytest.raises(AgentCardSigningError, match="exactly one"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=key.verify_key.encode()
        )


def test_signature_cannot_be_reused_after_key_rotation():
    key = SigningKey(SEED)
    signed = sign_agent_card(unsigned_card(), key)
    rotated_key = SigningKey(bytes((byte + 1) % 256 for byte in SEED))

    with pytest.raises(AgentCardSigningError, match="key_changed"):
        verify_agent_card(
            signed, origin=ORIGIN, pinned_public_key=rotated_key.verify_key.encode()
        )


def test_card_locator_is_optional_but_strict_when_present():
    assert resolve_agent_card_url(ORIGIN, None) is None
    assert (
        resolve_agent_card_url(ORIGIN, ORIGIN + AGENT_CARD_PATH)
        == ORIGIN + AGENT_CARD_PATH
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example" + AGENT_CARD_PATH,
        ORIGIN + "/.well-known/agent-keys.json",
        ORIGIN + AGENT_CARD_PATH + "?x=1",
        ORIGIN + AGENT_CARD_PATH + "#fragment",
        "https://user@agents.example" + AGENT_CARD_PATH,
        "http://agents.example" + AGENT_CARD_PATH,
        "https://agents.example:443" + AGENT_CARD_PATH,
        "https://127.1" + AGENT_CARD_PATH,
        "https://2130706433" + AGENT_CARD_PATH,
        ORIGIN + "/.well-known/%61gent-card.json",
    ],
)
def test_noncanonical_or_cross_origin_card_locators_are_rejected(url):
    with pytest.raises(AgentCardSigningError, match="invalid_card_url"):
        resolve_agent_card_url(ORIGIN, url)


def test_agent_card_decoder_rejects_duplicate_keys_and_nonfinite_numbers():
    with pytest.raises(AgentCardSigningError, match="duplicate JSON key"):
        decode_agent_card(b'{"name":"first","name":"second"}')
    with pytest.raises(AgentCardSigningError, match="non-finite JSON number"):
        decode_agent_card(b'{"name":"agent","x":NaN}')


def test_agent_card_decoder_enforces_document_size():
    with pytest.raises(AgentCardSigningError, match="card_too_large"):
        decode_agent_card(b" " * (MAX_AGENT_CARD_BYTES + 1))


def test_agent_card_decoder_returns_standard_object():
    card = decode_agent_card(json.dumps(unsigned_card(), ensure_ascii=False).encode())
    assert card["name"] == "Café 🌲 東京"

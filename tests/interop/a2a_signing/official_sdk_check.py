"""Verify the Kollab profile against a2a-sdk's v1 Agent Card signer/verifier.

Run with the integration environment, for example:
    .venv/bin/python tests/interop/a2a_signing/official_sdk_check.py
"""

from __future__ import annotations

from a2a.types import AgentCard
from a2a.utils.signing import create_agent_card_signer, create_signature_verifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToDict, ParseDict

from plugins.hub.dns.a2a_signing import (
    kid_for_public_key,
    sign_agent_card,
    verify_agent_card,
)

ORIGIN = "https://agents.example"
SEED = bytes(range(32))
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(SEED)
PUBLIC_KEY = PRIVATE_KEY.public_key()
PUBLIC_RAW = PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
KID = kid_for_public_key(PUBLIC_RAW)
PRIVATE_PEM = PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
PUBLIC_PEM = PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)

CARD = {
    "name": "Café 🌲 東京",
    "description": "Unicode: \ue000, 𐀀, 😀",
    "supportedInterfaces": [
        {"url": ORIGIN + "/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ],
    "version": "1.0.0",
    "capabilities": {
        "streaming": True,
        "pushNotifications": True,
        "extensions": [
            {
                "uri": "https://example.test/ext/jcs-vectors",
                "description": "Test-only canonicalization values",
                "required": False,
                "params": {
                    "numbers": {
                        "integer": 9007199254740991,
                        "negativeInteger": -9007199254740991,
                        "decimal": 333333333.3333333,
                        "negativeZero": -0.0,
                        "tiny": 1e-7,
                    },
                    "ordered": {"z": 1, "é": 2, "😀": 3, "a": 4, "𐀀": 5},
                },
            }
        ],
    },
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [
        {
            "id": "interop",
            "name": "Interop",
            "description": "JCS key ordering and number vector",
            "tags": ["é", "z", "𐀀"],
            "examples": ["Check a signed Card"],
        }
    ],
}


def key_provider(kid: str | None, jku: str | None):
    if kid != KID or jku is not None:
        raise AssertionError(
            f"unexpected official SDK key lookup: kid={kid!r}, jku={jku!r}"
        )
    return PUBLIC_PEM


def main() -> None:
    # Official SDK signer -> Kollab profile verifier.
    sdk_card = ParseDict(CARD, AgentCard())
    sdk_signer = create_agent_card_signer(
        PRIVATE_PEM,
        {"alg": "EdDSA", "kid": KID, "typ": "JOSE"},
    )
    sdk_signed_proto = sdk_signer(sdk_card)
    sdk_signed_dict = MessageToDict(sdk_signed_proto)
    verify_agent_card(sdk_signed_dict, origin=ORIGIN, pinned_public_key=PUBLIC_RAW)

    # Kollab profile signer -> official SDK verifier.
    # Use the SDK's ProtoJSON output as the exact presence-aware wire Card.
    # Passing the raw input mapping would preserve `required: false`, while
    # the non-optional protobuf field is omitted by the standard conversion.
    profile_unsigned_proto = ParseDict(CARD, AgentCard())
    profile_wire_dict = MessageToDict(profile_unsigned_proto)
    assert "required" not in profile_wire_dict["capabilities"]["extensions"][0]
    profile_signed_dict = sign_agent_card(profile_wire_dict, SEED)
    profile_signed_proto = ParseDict(profile_signed_dict, AgentCard())
    sdk_verifier = create_signature_verifier(key_provider, algorithms=["EdDSA"])
    sdk_verifier(profile_signed_proto)

    # Round-trip through protobuf JSON serialization and verify exact output.
    fetched_wire_dict = MessageToDict(profile_signed_proto)
    verify_agent_card(fetched_wire_dict, origin=ORIGIN, pinned_public_key=PUBLIC_RAW)
    print("official a2a-sdk 1.1.5 signer -> Kollab verifier: passed")
    print("Kollab signer -> official a2a-sdk 1.1.5 verifier: passed")
    print("signed Card protobuf JSON round-trip -> Kollab verifier: passed")


if __name__ == "__main__":
    main()

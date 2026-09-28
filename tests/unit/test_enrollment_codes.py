from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError

import pytest

from plugins.hub import enrollment_codes as codes

OFFER_ID = "00112233445566778899aabbccddeeff"
SECRET = "0123456789ABCDEFGHJK"
DISPLAY_CODE = f"K1-{OFFER_ID}-0123-4567-89AB-CDEF-GHJK"


def test_k1_verifier_and_stored_hash_match_deterministic_vector():
    code = codes.parse_enrollment_code(DISPLAY_CODE)

    verifier = codes.derive_enrollment_code_verifier(code)

    assert verifier.for_protocol() == "2GO0HHubpyw2yIa1hfcjyFzSdvcIK4X8PLMln7_CW1g"
    assert (
        codes.enrollment_verifier_hash(OFFER_ID, verifier)
        == "3fe8b5fe492ec526cd03d1942fe96e33c541c0a5fe27d15383fb4cdb7a7af78d"
    )


def test_envelope_key_matches_hkdf_vector_and_is_distinct_from_relay_verifier():
    code = codes.parse_enrollment_code(DISPLAY_CODE)

    envelope_key = codes.derive_enrollment_envelope_key(code)
    verifier = codes.derive_enrollment_code_verifier(code)
    verifier_bytes = base64.urlsafe_b64decode(verifier.for_protocol() + "=")

    assert (
        envelope_key.for_envelope_encryption().hex()
        == "da74cd8ccbd6dee29b783d12db0d8459820eff18081bcf00a27e448ac83e2f01"
    )
    assert envelope_key.for_envelope_encryption() != verifier_bytes


def test_hkdf_sha256_matches_rfc5869_extract_expand_vector():
    actual = codes._hkdf_sha256(
        bytes.fromhex("0b" * 22),
        salt=bytes.fromhex("000102030405060708090a0b0c"),
        info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        length=42,
    )

    assert actual.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a"
        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
        "34007208d5b887185865"
    )


def test_envelope_key_is_deterministic_and_does_not_derive_from_relay_scrypt(
    monkeypatch,
):
    code = codes.parse_enrollment_code(DISPLAY_CODE)

    def fail_if_relay_scrypt_is_used(*args, **kwargs):
        raise AssertionError("envelope derivation must use the raw code secret")

    monkeypatch.setattr(codes.hashlib, "scrypt", fail_if_relay_scrypt_is_used)
    first = codes.derive_enrollment_envelope_key(code)
    second = codes.derive_enrollment_envelope_key(code)
    another_offer = codes.parse_enrollment_code(
        f"K1-{'f' * 32}-0123-4567-89AB-CDEF-GHJK"
    )
    other_offer_key = codes.derive_enrollment_envelope_key(another_offer)

    assert first.for_envelope_encryption() == second.for_envelope_encryption()
    assert first.for_envelope_encryption() != other_offer_key.for_envelope_encryption()


def test_parse_normalizes_only_secret_letter_case_and_formats_canonically():
    entered = DISPLAY_CODE.rsplit("-", 1)[0] + "-ghjk"
    code = codes.parse_enrollment_code(entered)

    assert code.offer_id == OFFER_ID
    assert code.for_private_display() == DISPLAY_CODE
    with pytest.raises(FrozenInstanceError):
        code.offer_id = "f" * 32  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid",
    [
        "k1-" + DISPLAY_CODE[3:],
        DISPLAY_CODE.replace(OFFER_ID, OFFER_ID.upper()),
        DISPLAY_CODE.replace(OFFER_ID, OFFER_ID[:-1]),
        DISPLAY_CODE.replace(OFFER_ID, "g" + OFFER_ID[1:]),
        DISPLAY_CODE.replace("0123", "012"),
        DISPLAY_CODE.replace("0123", "01234"),
        DISPLAY_CODE.replace("AB", "AI"),
        DISPLAY_CODE.replace("AB", "AL"),
        DISPLAY_CODE.replace("AB", "AO"),
        DISPLAY_CODE.replace("AB", "AU"),
        DISPLAY_CODE.replace("0123", "01ß3"),
        DISPLAY_CODE.replace("-", "", 1),
    ],
)
def test_parse_rejects_noncanonical_or_invalid_codes(invalid: str):
    with pytest.raises(ValueError, match="^invalid enrollment code$"):
        codes.parse_enrollment_code(invalid)


@pytest.mark.parametrize(
    "offer_id",
    [
        "",
        "0" * 31,
        "0" * 33,
        "A" * 32,
        "g" + "0" * 31,
        "0" * 31 + "\n",
    ],
)
def test_generation_rejects_invalid_offer_ids(offer_id: str):
    with pytest.raises(ValueError, match="^invalid enrollment offer ID$"):
        codes.generate_enrollment_code(offer_id)


def test_generation_uses_100_bits_and_formats_secret_in_crockford_groups(monkeypatch):
    requested_lengths: list[int] = []

    def fixed_random_bytes(length: int) -> bytes:
        requested_lengths.append(length)
        return bytes(range(length))

    monkeypatch.setattr(codes.secrets, "token_bytes", fixed_random_bytes)
    code = codes.generate_enrollment_code(OFFER_ID)

    assert requested_lengths == [13]
    assert code.for_private_display() == (f"K1-{OFFER_ID}-000G-40R4-0M30-E209-185G")
    assert codes.parse_enrollment_code(
        code.for_private_display()
    ).for_private_display() == (code.for_private_display())


def test_code_and_verifier_string_representations_redact_values():
    code = codes.parse_enrollment_code(DISPLAY_CODE)
    verifier = codes.derive_enrollment_code_verifier(code)
    envelope_key = codes.derive_enrollment_envelope_key(code)
    code_secret = DISPLAY_CODE.rsplit("-", 5)[-5:]
    full_code = code.for_private_display()
    full_verifier = verifier.for_protocol()
    full_envelope_key = envelope_key.for_envelope_encryption()

    assert full_code not in repr(code)
    assert full_code not in str(code)
    assert "<redacted>" in repr(code)
    assert "<redacted>" in str(code)
    assert full_verifier not in repr(verifier)
    assert full_verifier not in str(verifier)
    assert "<redacted>" in repr(verifier)
    assert "<redacted>" in str(verifier)
    assert full_envelope_key.hex() not in repr(envelope_key)
    assert full_envelope_key.hex() not in str(envelope_key)
    assert "<redacted>" in repr(envelope_key)
    assert "<redacted>" in str(envelope_key)
    assert SECRET not in repr(code)
    assert SECRET not in str(code)
    assert all(group not in repr(code) for group in code_secret)


def test_verifier_hash_rejects_a_different_offer_id():
    verifier = codes.derive_enrollment_code_verifier(
        codes.parse_enrollment_code(DISPLAY_CODE)
    )

    with pytest.raises(ValueError, match="^verifier belongs to a different offer$"):
        codes.enrollment_verifier_hash("f" * 32, verifier)


def test_explicit_wipe_clears_code_and_verifier_buffers():
    code = codes.parse_enrollment_code(DISPLAY_CODE)
    verifier = codes.derive_enrollment_code_verifier(code)
    envelope_key = codes.derive_enrollment_envelope_key(code)
    code.wipe()
    verifier.wipe()
    envelope_key.wipe()

    with pytest.raises(ValueError, match="^enrollment code has been cleared$"):
        code.for_private_display()
    with pytest.raises(ValueError, match="^enrollment verifier has been cleared$"):
        verifier.for_protocol()
    with pytest.raises(ValueError, match="^enrollment envelope key has been cleared$"):
        envelope_key.for_envelope_encryption()

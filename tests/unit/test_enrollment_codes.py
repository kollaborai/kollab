from __future__ import annotations

import base64

import pytest

from plugins.hub import enrollment_codes as codes

OFFER_ID = "00112233445566778899aabbccddeeff"
SECRET = "01234567"  # gitleaks:allow (test fixture)
DISPLAY_CODE = "0123-4567"


def _code(offer_id: str = OFFER_ID, secret: str = SECRET) -> codes.EnrollmentCode:
    return codes.EnrollmentCode(offer_id, bytearray(secret.encode("ascii")))


def test_short_code_verifier_and_stored_hash_match_deterministic_vector():
    code = _code()

    verifier = codes.derive_enrollment_code_verifier(code)

    assert verifier.for_protocol() == "FDavhKFRSk45WkGeXKzPgMJ1Yn3sxZ87r4gXY58ccYQ"
    assert (
        codes.enrollment_verifier_hash(OFFER_ID, verifier)
        == "9d71c3663bef297b65dfe12bdb4652e337f84b04eec3d72c11d5b2228e4ca97c"
    )


def test_envelope_key_matches_hkdf_vector_and_is_distinct_from_relay_verifier():
    code = _code()

    envelope_key = codes.derive_enrollment_envelope_key(code)
    verifier = codes.derive_enrollment_code_verifier(code)
    verifier_bytes = base64.urlsafe_b64decode(verifier.for_protocol() + "=")

    assert (
        envelope_key.for_envelope_encryption().hex()
        == "b87de635b3ca1c5aaab847a17710d82a4c089f1a4f87daad063e4ff72f22b9d5"
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
    code = _code()

    def fail_if_relay_scrypt_is_used(*args, **kwargs):
        raise AssertionError("envelope derivation must use the raw code secret")

    monkeypatch.setattr(codes.hashlib, "scrypt", fail_if_relay_scrypt_is_used)
    first = codes.derive_enrollment_envelope_key(code)
    second = codes.derive_enrollment_envelope_key(code)
    other_offer_key = codes.derive_enrollment_envelope_key(_code(offer_id="f" * 32))

    assert first.for_envelope_encryption() == second.for_envelope_encryption()
    assert first.for_envelope_encryption() != other_offer_key.for_envelope_encryption()


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


def test_generation_uses_40_bits_and_formats_secret_as_a_short_code(monkeypatch):
    requested_lengths: list[int] = []

    def fixed_random_bytes(length: int) -> bytes:
        requested_lengths.append(length)
        return bytes(range(length))

    monkeypatch.setattr(codes.secrets, "token_bytes", fixed_random_bytes)
    code = codes.generate_enrollment_code(OFFER_ID)

    assert requested_lengths == [5]
    assert code.for_private_display() == "000G-40R4"
    assert codes.is_short_enrollment_code(code.for_private_display())
    assert (
        codes.parse_short_enrollment_code(code.for_private_display())
        == b"000G40R4"
    )


def test_code_and_verifier_string_representations_redact_values():
    code = _code()
    verifier = codes.derive_enrollment_code_verifier(code)
    envelope_key = codes.derive_enrollment_envelope_key(code)
    code_secret = DISPLAY_CODE.split("-")
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
    verifier = codes.derive_enrollment_code_verifier(_code())

    with pytest.raises(ValueError, match="^verifier belongs to a different offer$"):
        codes.enrollment_verifier_hash("f" * 32, verifier)


def test_explicit_wipe_clears_code_and_verifier_buffers():
    code = _code()
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


@pytest.mark.parametrize(
    "value",
    ["ABCD-1234", "abcd-1234", "AbCd-1234", "ABCD1234", "abcd1234", "  ABCD-1234  "],
)
def test_short_code_parses_either_case_dashless_and_padded(value: str):
    assert codes.is_short_enrollment_code(value)
    assert codes.parse_short_enrollment_code(value) == b"ABCD1234"


@pytest.mark.parametrize(
    "value",
    [
        "ABC-1234",  # too short
        "ABCDE-1234",  # too long
        "ABCI-1234",  # I is not in the alphabet
        "ABCD_1234",  # wrong separator
        "ABCD-1234-5678",  # a differently shaped code, not a short one
        "",
    ],
)
def test_short_code_rejects_malformed_input(value: str):
    assert not codes.is_short_enrollment_code(value)
    with pytest.raises(ValueError, match="^invalid enrollment code$"):
        codes.parse_short_enrollment_code(value)


def test_lookup_tag_is_deterministic_and_bound_to_the_origin():
    secret = codes.parse_short_enrollment_code("ABCD-1234")

    tag = codes.derive_enrollment_lookup_tag(secret, "https://relay.example")

    assert tag == codes.derive_enrollment_lookup_tag(secret, "https://relay.example")
    assert tag != codes.derive_enrollment_lookup_tag(secret, "https://other.example")
    assert tag != codes.derive_enrollment_lookup_tag(b"WXYZ5678", "https://relay.example")


def test_lookup_hash_is_deterministic_and_never_reverses_to_the_tag():
    secret = codes.parse_short_enrollment_code("ABCD-1234")
    tag = codes.derive_enrollment_lookup_tag(secret, "https://relay.example")

    hashed = codes.enrollment_lookup_hash(tag)

    assert hashed == codes.enrollment_lookup_hash(tag)
    assert tag not in hashed
    with pytest.raises(ValueError, match="^invalid lookup tag$"):
        codes.enrollment_lookup_hash("not-base64url-!!")


def test_code_for_lookup_tag_matches_the_free_function():
    code = codes.generate_enrollment_code(OFFER_ID)

    tag = code.for_lookup_tag("https://relay.example")

    assert tag == codes.derive_enrollment_lookup_tag(
        codes.parse_short_enrollment_code(code.for_private_display()),
        "https://relay.example",
    )


def test_looks_like_join_code_catches_codes_not_device_names():
    from plugins.hub.enrollment_codes import looks_like_join_code

    assert looks_like_join_code("7QK4-M2XP")
    assert not looks_like_join_code("mac-home")
    assert not looks_like_join_code("home-server")
    assert not looks_like_join_code("kollabor.ai")
    assert not looks_like_join_code("7qk4-m2xp")  # lower case is left to the private form

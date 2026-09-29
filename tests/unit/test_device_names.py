from pathlib import Path

import pytest

from plugins.hub.device_names import (
    contact_route_hex,
    default_device_name,
    device_key_fingerprint,
    format_handle,
    parse_handle,
    short_fingerprint,
    slug,
    validate_device_name,
    validate_trust,
)


def test_slug_and_validation():
    assert slug("Alzan Prod!!") == "alzan-prod"
    assert validate_device_name("mac-kollab") == "mac-kollab"
    with pytest.raises(ValueError):
        validate_device_name("Mac Kollab")
    with pytest.raises(ValueError):
        validate_device_name("-bad")


def test_default_device_name_home_and_folder(tmp_path):
    assert default_device_name(Path.home()).endswith("-home")
    assert default_device_name(tmp_path / "My Repo").endswith("-my-repo")


def test_handles():
    assert parse_handle("Infra@Alzan-Prod-Home") == ("infra", "alzan-prod-home")
    assert parse_handle("lapis") is None
    assert parse_handle("relay:abc") is None
    assert format_handle("infra", "alzan-prod-home") == "infra@alzan-prod-home"


def test_trust():
    assert validate_trust("open") == "open"
    with pytest.raises(ValueError):
        validate_trust("yolo")


def test_short_fingerprint_is_first_4_and_last_4_hex():
    assert short_fingerprint("4d04" + "0" * 56 + "9f2e") == "4d04\u20269f2e"


def test_contact_route_is_16_lowercase_hex_and_fingerprint_is_a_full_digest():
    key = "1" * 64
    route = contact_route_hex(key)
    assert len(route) == 16 and route == route.lower()
    assert route != contact_route_hex("2" * 64)
    assert len(device_key_fingerprint(key)) == 64
    for helper in (contact_route_hex, device_key_fingerprint):
        with pytest.raises(ValueError):
            helper("not-a-key")

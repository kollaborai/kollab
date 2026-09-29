from pathlib import Path

import pytest

from plugins.hub.device_names import (
    default_device_name,
    format_handle,
    parse_handle,
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

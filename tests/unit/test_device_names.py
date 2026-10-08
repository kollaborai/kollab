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
    assert slug("Home Server!!") == "home-server"
    assert validate_device_name("laptop-kollab") == "laptop-kollab"
    with pytest.raises(ValueError):
        validate_device_name("Mac Kollab")
    with pytest.raises(ValueError):
        validate_device_name("-bad")


def test_default_device_name_home_and_folder(tmp_path):
    assert default_device_name(Path.home()).endswith("-home")
    assert default_device_name(tmp_path / "My Repo").endswith("-my-repo")


def test_a_taken_name_gets_the_parent_folder_then_a_number(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "mac.local")
    worktree = Path("/w/buzz-completion/dsk")
    assert default_device_name(worktree) == "mac-dsk"
    assert default_device_name(worktree, {"mac-dsk"}) == "mac-buzz-completion-dsk"
    taken = {"mac-dsk", "mac-buzz-completion-dsk", "mac-dsk-2"}
    assert default_device_name(worktree, taken) == "mac-dsk-3"
    long = Path("/w/" + "p" * 70 + "/" + "d" * 70)
    name = default_device_name(long, {default_device_name(long)})
    assert name == validate_device_name(name) and len(name) == 63


def test_two_checkouts_in_same_named_folders_get_different_names(tmp_path, monkeypatch):
    from plugins.hub.relay_state import RelayStateStore

    monkeypatch.setattr("socket.gethostname", lambda: "mac")
    network = tmp_path / "network"
    first = RelayStateStore(tmp_path / "dev" / "dsk", network / "a")
    second = RelayStateStore(tmp_path / "wt" / "buzz" / "dsk", network / "b")
    assert first.state.device_name == "mac-dsk"
    assert second.state.device_name == "mac-buzz-dsk"
    # Pinned: a restart keeps the name, whatever starts first next time.
    assert RelayStateStore(tmp_path / "wt" / "buzz" / "dsk", network / "b").state.device_name == "mac-buzz-dsk"


def test_handles():
    assert parse_handle("Infra@Home-Server") == ("infra", "home-server")
    assert parse_handle("lapis") is None
    assert parse_handle("relay:abc") is None
    assert format_handle("infra", "home-server") == "infra@home-server"


def test_trust():
    assert validate_trust("open") == "open"
    with pytest.raises(ValueError):
        validate_trust("yolo")


def test_short_fingerprint_is_first_4_and_last_4_hex():
    assert short_fingerprint("abcd" + "0" * 56 + "ef01") == "abcd\u2026ef01"


def test_contact_route_is_16_lowercase_hex_and_fingerprint_is_a_full_digest():
    key = "1" * 64
    route = contact_route_hex(key)
    assert len(route) == 16 and route == route.lower()
    assert route != contact_route_hex("2" * 64)
    assert len(device_key_fingerprint(key)) == 64
    for helper in (contact_route_hex, device_key_fingerprint):
        with pytest.raises(ValueError):
            helper("not-a-key")

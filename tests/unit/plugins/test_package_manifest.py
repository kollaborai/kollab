"""Manifest validation for installable plugin packages."""

import json

import pytest


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("valid", None),
        ("id", "id"),
        ("unknown", "unknown"),
        ("old-version", "kollab"),
        ("format", "format"),
        ("path", "inside"),
        ("symlink", "inside"),
        ("missing-web", "web"),
        ("service-name", "service"),
        ("undeclared-secret", "secret"),
        ("contributes", "contributes"),
    ],
)
def test_manifest_checks(change, message, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import validate_manifest

    root = tmp_path / "package"
    (root / "python").mkdir(parents=True)
    (root / "python" / "entry.py").write_text("pass\n")
    manifest = {
        "format": 1,
        "id": "hello",
        "name": "Hello",
        "version": "1.0.0",
        "description": "Example",
        "kollab": ">=0.23.0",
        "python": "python/entry.py:Hello",
        "uses": [],
    }
    if change == "id":
        manifest["id"] = "../bad"
    elif change == "unknown":
        manifest["serivces"] = {}
    elif change == "old-version":
        manifest.update(kollab=">=99", format=999)
    elif change == "format":
        manifest["format"] = 999
    elif change == "path":
        manifest["settings"] = "../outside.json"
    elif change == "symlink":
        (tmp_path / "outside.json").write_text("{}")
        (root / "settings.json").symlink_to(tmp_path / "outside.json")
        manifest["settings"] = "settings.json"
    elif change == "missing-web":
        manifest["web"] = {"entry": "web/dist/index.js"}
    elif change == "service-name":
        manifest["services"] = {"../bad": {"command": ["python3"]}}
    elif change == "undeclared-secret":
        manifest["services"] = {"loop": {"command": ["python3"], "secrets": ["KEY"]}}
    elif change == "contributes":
        manifest["contributes"] = {"typo": []}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    if message:
        with pytest.raises(ValueError, match=message):
            validate_manifest(root, "0.23.0")
    else:
        assert validate_manifest(root, "0.23.0") == manifest


@pytest.mark.parametrize("key", ["stop_timeout_s", "heartbeat_s"])
@pytest.mark.parametrize("value", [10**1000, 86401, float("nan"), float("inf"), True])
def test_service_seconds_must_be_finite_and_bounded(key, value, tmp_path, monkeypatch):
    """A huge integer passed validation, then raised OverflowError when the service was stopped."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import validate_manifest

    root = tmp_path / "package"
    root.mkdir()
    manifest = {"format": 1, "id": "hello", "name": "Hello", "version": "1.0.0", "description": "Example",
                "kollab": ">=0.23.0", "uses": [], "services": {"loop": {"command": ["python3"], key: value}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=key):
        validate_manifest(root, "0.23.0")

"""The kollab wheel ships every file the voice manifest names."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "packages" / "kollabor-voice" / "src" / "kollabor_voice"


def test_wheel_package_data_ships_the_manifest():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    globs = config["tool"]["setuptools"]["package-data"]["kollabor_voice"]
    assert "manifest.json" in globs
    assert (VOICE / "manifest.json").is_file()
    assert json.loads((VOICE / "manifest.json").read_text())["bundles"]

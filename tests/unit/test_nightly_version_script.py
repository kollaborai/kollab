"""scripts/nightly_version.py: the nightly version and the files it is stamped into (#1349)."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from packaging.version import Version

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "nightly_version.py"
spec = importlib.util.spec_from_file_location("nightly_version", SCRIPT)
nightly_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly_version)

NIGHT = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def test_a_nightly_sorts_between_the_release_and_the_next_one():
    version = nightly_version.nightly_version("0.22.1", NIGHT)
    assert version == "0.22.2.dev202611200900"
    assert Version("0.22.1") < Version(version) < Version("0.22.2")
    later = nightly_version.nightly_version("0.22.1", NIGHT + timedelta(days=1))
    assert Version(version) < Version(later)


def test_the_time_is_utc_whatever_the_runner_zone():
    pacific = NIGHT.astimezone(timezone(timedelta(hours=-8)))
    assert nightly_version.nightly_version("0.22.1", pacific) == "0.22.2.dev202611200900"


def tree(root: Path, release: str = "0.22.1") -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "kollab"\nversion = "{release}"\n'
        f'dependencies = [\n    "aiohttp>=3.9",\n    "kollabor-ai>=0.22.1",\n    "kollabor-tui>=0.22.1",\n]\n'
        '[tool.black]\ntarget-version = ["py314"]\n',
        encoding="utf-8",
    )
    package = root / "packages" / "kollabor-ai"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text(
        f'[project]\nname = "kollabor-ai"\nversion = "{release}"\n'
        f'dependencies = [\n    "kollabor-config>=0.22.1",\n]\n',
        encoding="utf-8",
    )
    frontend = root / nightly_version.FRONTEND_PACKAGE_JSON
    frontend.parent.mkdir(parents=True)
    frontend.write_text(
        f'{{\n  "name": "kollab-webui",\n  "version": "{release}",\n'
        '  "dependencies": {\n    "react": "19.0.0"\n  }\n}\n',
        encoding="utf-8",
    )


def test_stamping_rewrites_every_version_and_kollabor_constraint(tmp_path):
    tree(tmp_path)
    nightly_version.stamp(tmp_path, "0.22.2.dev202611200900")
    root = (tmp_path / "pyproject.toml").read_text()
    assert 'version = "0.22.2.dev202611200900"' in root
    assert '"kollabor-ai>=0.22.2.dev202611200900"' in root
    assert '"kollabor-tui>=0.22.2.dev202611200900"' in root
    assert '"aiohttp>=3.9"' in root and 'target-version = ["py314"]' in root
    package = (tmp_path / "packages" / "kollabor-ai" / "pyproject.toml").read_text()
    assert 'version = "0.22.2.dev202611200900"' in package
    assert '"kollabor-config>=0.22.2.dev202611200900"' in package
    frontend = json.loads((tmp_path / nightly_version.FRONTEND_PACKAGE_JSON).read_text())
    assert frontend["version"] == "0.22.2.dev202611200900"
    assert frontend["dependencies"] == {"react": "19.0.0"}


def test_a_file_without_the_release_version_stops_the_stamp(tmp_path):
    tree(tmp_path)
    stray = tmp_path / "packages" / "kollabor-ai" / "pyproject.toml"
    stray.write_text(stray.read_text().replace("0.22.1", "0.21.0"))
    with pytest.raises(SystemExit, match="kollabor-ai"):
        nightly_version.stamp(tmp_path, "0.22.2.dev202611200900")

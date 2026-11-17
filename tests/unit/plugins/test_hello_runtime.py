"""The bundled template must load on this checkout's runtime."""
from pathlib import Path

from kollabor.version import get_runtime_version
from kollabor_plugins.packages import validate_manifest


def test_hello_supports_current_runtime():
    root = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    assert validate_manifest(root, get_runtime_version())["id"] == "hello"

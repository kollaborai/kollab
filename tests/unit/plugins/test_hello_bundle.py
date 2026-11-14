"""The bundled hello template is a usable, complete step-one fixture."""
import importlib.util
import json
from pathlib import Path

from kollabor_plugins import BasePlugin
from kollabor_plugins.packages import validate_manifest


ROOT = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"


def test_hello_manifest_and_parts():
    manifest = validate_manifest(ROOT, "0.23.0")
    assert manifest["id"] == "hello"
    assert len(manifest["services"]) == 1
    assert manifest["web"]["entry"] == "web/dist/index.js"
    assert manifest["web"]["style"] == "web/dist/index.css"
    section = json.loads((ROOT / manifest["settings"]).read_text())
    assert section["widgets"]
    path, symbol = manifest["python"].split(":")
    spec = importlib.util.spec_from_file_location("hello_fixture", ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert issubclass(getattr(module, symbol), BasePlugin)

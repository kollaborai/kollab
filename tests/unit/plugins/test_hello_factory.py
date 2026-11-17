"""The hello template must instantiate through the real plugin factory."""

import importlib.util
from pathlib import Path

from kollabor_plugins.factory import PluginFactory


def test_hello_instantiates_through_factory():
    path = Path(__file__).resolve().parents[3] / "bundles/plugins/hello/python/hello.py"
    spec = importlib.util.spec_from_file_location("hello_factory_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = PluginFactory()
    plugin = factory.instantiate_plugin(module.HelloPlugin, "HelloPlugin", None, None, None)
    assert plugin is not None

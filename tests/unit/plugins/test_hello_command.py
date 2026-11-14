"""The hello template supplies the terminal command used by acceptance."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import Mock


def test_hello_registers_command_and_answers(capsys):
    path = Path(__file__).resolve().parents[3] / "bundles/plugins/hello/python/hello.py"
    spec = importlib.util.spec_from_file_location("hello_command_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = module.HelloPlugin()
    registry = Mock()
    asyncio.run(plugin.initialize(None, None, command_registry=registry))
    definition = registry.register_command.call_args.args[0]
    assert definition.name == "hello"
    result = asyncio.run(definition.handler(Mock()))
    assert result.success and "hello" in result.message.lower()
    assert capsys.readouterr().out == ""

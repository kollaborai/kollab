"""Keep the bundled tool guide aligned with the actual registration paths.

This scans known production registration APIs and probes existing registrars
with fake services. It does not prove arbitrary dynamic Python enumerable:
new registration mechanisms need an explicit discovery strategy and test.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_agent.tool_registry import ToolRegistry, get_registry

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "bundles/skills/kollab-internal-tools/references"
FIELDS = ("Purpose", "Inputs", "Result", "Effects", "Permission", "Source")
HEADING = re.compile(r"^### `([^`]+)`\s*$", re.MULTILINE)
REGISTRATION_METHODS = {"register_plugin_tag", "register_plugin_handler", "register_custom_tool"}


@dataclass(frozen=True)
class Registration:
    kind: str
    name: str
    source: str
    tag: str = ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _scan_source(source: str, path: str) -> tuple[list[Registration], list[str]]:
    """Find direct registrations and reject opaque aliases of known entry points."""
    registrations: list[Registration] = []
    errors: list[str] = []
    tree = ast.parse(source, filename=path)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    registry_getters = {"get_registry"}
    registry_classes = {"ToolRegistry"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                if item.name == "get_registry":
                    registry_getters.add(item.asname or item.name)
                elif item.name == "ToolRegistry":
                    registry_classes.add(item.asname or item.name)

    def is_registry_factory(node):
        if not isinstance(node, ast.Call):
            return False
        return (isinstance(node.func, ast.Name) and node.func.id in registry_getters | registry_classes) or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_global"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in registry_classes
        )

    registry_vars: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if is_registry_factory(node.value):
            registry_vars.update(target.id for target in node.targets if isinstance(target, ast.Name))

    def is_registry(node):
        return (isinstance(node, ast.Name) and node.id in registry_vars) or is_registry_factory(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in registry_getters and isinstance(node.ctx, ast.Load):
            parent = parents.get(node)
            if not isinstance(parent, ast.Call) or parent.func is not node:
                errors.append(f"{path}:{node.lineno}: aliased registry getter needs discovery coverage")
        if isinstance(node, ast.Attribute):
            # Registration methods must be called directly. Passing a bound
            # method into a helper or storing it as an alias bypasses the
            # literal call-site inventory and requires an explicit new probe.
            is_registry_register = node.attr == "register" and is_registry(node.value)
            if node.attr in REGISTRATION_METHODS or is_registry_register:
                parent = parents.get(node)
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    errors.append(f"{path}:{node.lineno}: aliased registration needs discovery coverage")
                elif is_registry_register and not path.startswith(
                    "packages/kollabor-agent/src/kollabor_agent/tool_definitions/"
                ):
                    errors.append(f"{path}:{node.lineno}: extra registry producer needs runtime discovery coverage")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # hasattr checks availability without invoking or aliasing a method.
            if node.func.id == "getattr" and len(node.args) >= 2:
                name = _literal_string(node.args[1])
                if name in REGISTRATION_METHODS:
                    errors.append(f"{path}:{node.lineno}: reflective registration needs discovery coverage")
                if name == "register" and is_registry(node.args[0]):
                    errors.append(f"{path}:{node.lineno}: reflective registry needs discovery coverage")
        if isinstance(node, ast.Subscript) and _literal_string(node.slice) in REGISTRATION_METHODS:
            errors.append(f"{path}:{node.lineno}: indexed registration needs discovery coverage")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            if node.func.attr in {"__getattribute__", "methodcaller"}:
                name = _literal_string(node.args[0])
                if name in REGISTRATION_METHODS or (name == "register" and is_registry(node.func.value)):
                    errors.append(f"{path}:{node.lineno}: reflective registration needs discovery coverage")

        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in REGISTRATION_METHODS:
            continue
        if method == "register_custom_tool":
            definition = node.args[0] if node.args else None
            name = None
            if isinstance(definition, ast.Dict):
                for key, value in zip(definition.keys, definition.values):
                    if key is not None and _literal_string(key) == "name":
                        name = _literal_string(value)
            if name is None:
                errors.append(f"{path}:{node.lineno}: dynamic SDK tool needs discovery coverage")
            else:
                registrations.append(Registration("sdk", name, path))
            continue

        first = _literal_string(node.args[0]) if node.args else None
        if method == "register_plugin_handler":
            if first is None:
                errors.append(f"{path}:{node.lineno}: dynamic handler needs discovery coverage")
            else:
                registrations.append(Registration("handler", first, path))
            continue

        tool_type = _literal_string(node.args[2]) if len(node.args) > 2 else None
        if first is None or tool_type is None:
            # This single bridge iterates the central registry, which the test
            # independently reads at runtime. Any other dynamic call fails closed.
            is_registry_bridge = (
                path == "kollabor/llm/llm_coordinator.py"
                and len(node.args) >= 3
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "tag_name"
                and isinstance(node.args[2], ast.Name)
                and node.args[2].id == "tool_type_underscore"
            )
            if not is_registry_bridge:
                errors.append(f"{path}:{node.lineno}: dynamic tag needs discovery coverage")
        else:
            registrations.append(Registration("tag", tool_type, path, first))
    return registrations, errors


def _production_paths():
    for directory in ("plugins", "kollabor", "packages"):
        for path in (ROOT / directory).rglob("*.py"):
            relative = path.relative_to(ROOT)
            if any(part in {"tests", "test", ".venv", "__pycache__"} for part in relative.parts):
                continue
            yield path, relative.as_posix()


def _production_registrations() -> tuple[list[Registration], list[str]]:
    registrations: list[Registration] = []
    errors: list[str] = []
    for path, relative in _production_paths():
        found, problems = _scan_source(path.read_text(encoding="utf-8"), relative)
        registrations.extend(found)
        errors.extend(problems)
    return registrations, errors


def _scan_definitions(source: str, path: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Discover constructors even in conditionally loaded plugins."""
    sources: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    tree = ast.parse(source, filename=path)
    aliases = {"ToolDefinition"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(item.asname or item.name for item in node.names if item.name == "ToolDefinition")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_constructor = (isinstance(node.func, ast.Name) and node.func.id in aliases) or (
            isinstance(node.func, ast.Attribute) and node.func.attr == "ToolDefinition"
        )
        if not is_constructor:
            continue
        name = next(
            (_literal_string(kw.value) for kw in node.keywords if kw.arg == "name"),
            _literal_string(node.args[0]) if node.args else None,
        )
        overrides = [kw.value for kw in node.keywords if kw.arg == "_native_name_override"]
        override = _literal_string(overrides[0]) if overrides else None
        if name is None or (overrides and override is None):
            errors.append(f"{path}:{node.lineno}: dynamic definition needs discovery coverage")
        elif name in sources:
            errors.append(f"duplicate definition source for {name}")
        else:
            sources[name] = (path, override or name.replace("-", "_"))
    return sources, errors


def _registry_sources() -> tuple[dict[str, tuple[str, str]], list[str]]:
    sources: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for path, relative in _production_paths():
        found, problems = _scan_definitions(path.read_text(encoding="utf-8"), relative)
        errors.extend(problems)
        for name, definition in found.items():
            if name in sources:
                errors.append(f"duplicate definition source for {name}")
            sources[name] = definition
    return sources, errors


def _catalog() -> tuple[dict[str, dict[str, str]], list[str]]:
    entries: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for path in sorted(REFERENCES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        headings = list(HEADING.finditer(text))
        for index, match in enumerate(headings):
            name = match.group(1)
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            block = text[match.end() : end].split("\n## ", 1)[0]
            fields = {
                key: value.strip()
                for key, value in re.findall(
                    r"^(Purpose|Inputs|Result|Effects|Permission|Source):\s*(.*)$",
                    block,
                    re.MULTILINE,
                )
            }
            if name in entries:
                errors.append(f"duplicate catalog entry: {name}")
            entries[name] = fields
    return entries, errors


def _coverage_errors(tools, registrations: list[Registration], entries, registry_sources) -> list[str]:
    errors: list[str] = []
    expected = {tool.native_name for tool in tools}
    expected.update(native for _, native in registry_sources.values())
    expected.update(r.name for r in registrations if r.kind in {"handler", "sdk"})
    for name in sorted(expected - entries.keys()):
        errors.append(f"missing explanation: {name}")
    for name in sorted(entries.keys() - expected):
        errors.append(f"unregistered explanation: {name}")

    for name, fields in entries.items():
        for field in FIELDS:
            if len(fields.get(field, "").strip()) < 8:
                errors.append(f"{name}: missing or empty {field}")
        sources = re.findall(r"`([^`]+)`", fields.get("Source", ""))
        if not sources:
            errors.append(f"{name}: missing source path")
        for source in sources:
            if not (ROOT / source).is_file():
                errors.append(f"{name}: source does not exist: {source}")

    for definition_source, native_name in registry_sources.values():
        entry = entries.get(native_name)
        if entry and f"`{definition_source}`" not in entry.get("Source", ""):
            errors.append(f"{native_name}: missing definition source {definition_source}")

    for tool in tools:
        entry = entries.get(tool.native_name)
        if not entry:
            continue
        definition = registry_sources.get(tool.name)
        if not definition:
            errors.append(f"{tool.native_name}: registry definition source not found")
        for parameter in tool.parameters:
            if parameter.required and f"`{parameter.name}`" not in entry.get("Inputs", ""):
                errors.append(f"{tool.native_name}: missing required input {parameter.name}")
        if f"`{tool.risk_level}`" not in entry.get("Permission", ""):
            errors.append(f"{tool.native_name}: registry risk metadata differs")
        flag = str(tool.requires_permission).lower()
        if f"`{flag}`" not in entry.get("Permission", ""):
            errors.append(f"{tool.native_name}: registry confirmation metadata differs")
        if tool.xml_tag_name != tool.native_name and f"`{tool.xml_tag_name}`" not in entry.get("Purpose", ""):
            errors.append(f"{tool.native_name}: missing XML name {tool.xml_tag_name}")

    for registration in registrations:
        entry = entries.get(registration.name)
        # A tag label may name a separate handler entry, e.g. notifications_clear.
        if registration.kind == "tag" and registration.tag in entries:
            tag_entry = entries[registration.tag]
        else:
            tag_entry = entry
        if registration.kind == "tag":
            if not tag_entry:
                errors.append(f"missing tag explanation: {registration.tag} -> {registration.name}")
            elif registration.tag not in " ".join(tag_entry.values()):
                # The heading itself explains the tag when names are identical.
                if registration.tag != registration.name and registration.tag not in entries:
                    errors.append(f"{registration.name}: missing XML tag {registration.tag}")
        if entry and f"`{registration.source}`" not in entry.get("Source", ""):
            errors.append(f"{registration.name}: missing registration source {registration.source}")
    return errors


@pytest.fixture(scope="module")
def inventory():
    # Build the real built-in registry in isolation from earlier tests.
    original = ToolRegistry._instance
    ToolRegistry._instance = None
    try:
        tools = get_registry().list()
    finally:
        ToolRegistry._instance = original
    registrations, scan_errors = _production_registrations()
    registry_sources, source_errors = _registry_sources()
    entries, catalog_errors = _catalog()
    errors = scan_errors + source_errors + catalog_errors
    assert not errors, "\n".join(errors)
    return tools, registrations, entries, registry_sources


def test_every_shipped_tool_has_a_source_backed_explanation(inventory):
    tools, registrations, entries, sources = inventory
    errors = _coverage_errors(tools, registrations, entries, sources)
    assert not errors, "\n".join(errors)


def test_unexplained_new_registrations_and_missing_effects_fail(inventory):
    tools, registrations, entries, sources = inventory
    added, scan_errors = _scan_source(
        'executor.register_plugin_handler("new_tool", handler)\n'
        'parser.register_plugin_tag("new_tag", pattern, "new_tool", extract)\n'
        'sdk.register_custom_tool({"name": "new_service"})\n',
        "plugins/example.py",
    )
    assert not scan_errors
    errors = _coverage_errors(tools, registrations + added, entries, sources)
    assert "missing explanation: new_tool" in errors
    assert "missing explanation: new_service" in errors
    assert "missing tag explanation: new_tag -> new_tool" in errors
    changed = {name: dict(fields) for name, fields in entries.items()}
    changed[tools[0].native_name]["Effects"] = ""
    assert f"{tools[0].native_name}: missing or empty Effects" in _coverage_errors(
        tools, registrations, changed, sources
    )
    new_tool = replace(tools[0], name="new-registry-tool", _native_name_override=None)
    assert "missing explanation: new_registry_tool" in _coverage_errors(
        [*tools, new_tool], registrations, entries, sources
    )
    future_sources, errors = _scan_definitions(
        'from pkg import ToolDefinition as TD\nTD(name="conditional-tool", description="new")',
        "plugins/example.py",
    )
    assert not errors
    assert "missing explanation: conditional_tool" in _coverage_errors(
        tools, registrations, entries, {**sources, **future_sources}
    )


def test_unresolved_dynamic_registration_is_not_silently_skipped():
    _, errors = _scan_source(
        "executor.register_plugin_handler(make_name(), handler)\nsdk.register_custom_tool(make_definition())\n",
        "plugins/example.py",
    )
    assert len(errors) == 2


def test_new_registry_and_plugin_tools_require_complete_explanations(inventory):
    tools, registrations, entries, sources = inventory
    definition_source = "packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py"
    plugin_source = "plugins/hub/plugin.py"
    new_tool = ToolDefinition(
        name="canary-tool",
        description="Inspect the named fixture path",
        xml_tag="canary-tool",
        risk_level="high",
        requires_permission=True,
        parameters=[ToolParameter(name="path", type="string", description="Fixture path", required=True)],
    )
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    registry.register(new_tool)

    class Recorder:
        def __init__(self):
            self.registered = []

        def register_plugin_handler(self, name, handler):
            self.registered.append(Registration("handler", name, plugin_source))

    recorder = Recorder()
    recorder.register_plugin_handler("canary_plugin", lambda _: None)
    expanded_registrations = [*registrations, *recorder.registered]
    expanded_sources = {**sources, new_tool.name: (definition_source, new_tool.native_name)}
    errors = _coverage_errors(registry.list(), expanded_registrations, entries, expanded_sources)
    assert "missing explanation: canary_tool" in errors
    assert "missing explanation: canary_plugin" in errors

    expanded_entries = {
        **entries,
        "canary_tool": {
            "Purpose": "Inspect a fixture path using `canary-tool`.",
            "Inputs": "Required: `path`.",
            "Result": "The fixture status.",
            "Effects": "Reads fixture metadata.",
            "Permission": "Registry risk `high`, confirmation flag `true`.",
            "Source": f"`{definition_source}`",
        },
        "canary_plugin": {
            "Purpose": "Inspect a fixture plugin.",
            "Inputs": "No arguments are required.",
            "Result": "The fixture status.",
            "Effects": "Reads fixture metadata.",
            "Permission": "Current pipeline policy applies.",
            "Source": f"`{plugin_source}`",
        },
    }
    assert not _coverage_errors(registry.list(), expanded_registrations, expanded_entries, expanded_sources)
    for name in ("canary_tool", "canary_plugin"):
        for field in FIELDS:
            incomplete = {key: dict(value) for key, value in expanded_entries.items()}
            incomplete[name].pop(field)
            assert f"{name}: missing or empty {field}" in _coverage_errors(
                registry.list(), expanded_registrations, incomplete, expanded_sources
            )


def test_aliases_reflection_and_wrapper_registration_fail_closed():
    _, errors = _scan_source(
        "from registry import get_registry as get_tools\n"
        "registry = get_tools()\n"
        "register = executor.register_plugin_handler\n"
        "use_callback(parser.register_plugin_tag)\n"
        'getattr(executor, "register_plugin_handler")("hidden", handler)\n'
        'executor.__dict__["register_custom_tool"]({"name": "hidden"})\n'
        'operator.methodcaller("register_plugin_tag", "hidden")(parser)\n'
        'executor.__getattribute__("register_plugin_handler")("hidden", handler)\n'
        "wrapped = registry.register\n"
        "factory = get_tools\n"
        'getattr(registry, "register")(definition)\n'
        "def wrapper(parser, tag, kind):\n"
        "    parser.register_plugin_tag(tag, pattern, kind, extract)\n",
        "plugins/example.py",
    )
    assert sum("aliased registration" in e for e in errors) == 3
    assert any("reflective registration" in e for e in errors)
    assert any("reflective registry" in e for e in errors)
    assert any("indexed registration" in e for e in errors)
    assert any("aliased registry getter" in e for e in errors)
    assert any("dynamic tag" in e for e in errors)


@pytest.mark.asyncio
async def test_conditional_registrars_with_fake_services_match_source_inventory(inventory):
    """Exercise existing registrars without starting plugins or their services."""
    from kollabor.goals.tooling import register_goal_tools
    from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin
    from plugins.context_compaction_plugin import ContextCompactionPlugin
    from plugins.env_queue_plugin import EnvQueuePlugin
    from plugins.hook_monitoring_plugin import HookMonitoringPlugin
    from plugins.hub.plugin import HubPlugin
    from plugins.voice_plugin import VoicePlugin

    class Recorder:
        def __init__(self, source):
            self.source = source
            self.records = []

        def register_plugin_tag(self, tag, pattern, name, extract):
            self.records.append(Registration("tag", name, self.source, tag))

        def register_plugin_handler(self, name, handler):
            self.records.append(Registration("handler", name, self.source))

        def register_custom_tool(self, definition):
            self.records.append(Registration("sdk", definition["name"], self.source))

    class FakeOwner:
        def __init__(self, recorder):
            self.event_bus = SimpleNamespace(get_service=lambda name: recorder)
            self._event_bus = self.event_bus
            self.sdk = recorder
            self.config = {}
            self.name = "fixture"

        def __getattr__(self, name):
            if name.startswith(("_handle_", "_provide_")) or name == "_voice_tool":
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    probes = [
        ("plugins/hub/plugin.py", HubPlugin._register_pipeline_tools),
        ("plugins/agent_orchestrator/plugin.py", AgentOrchestratorPlugin._register_pipeline_tools),
        ("plugins/env_queue_plugin.py", EnvQueuePlugin._register_notification_tags),
        ("plugins/context_compaction_plugin.py", ContextCompactionPlugin._register_compact_tag),
        ("plugins/voice_plugin.py", VoicePlugin._register_tool),
    ]
    _, registrations, entries, _ = inventory
    for source, register in probes:
        recorder = Recorder(source)
        register(FakeOwner(recorder))
        assert set(recorder.records) == {r for r in registrations if r.source == source}
        assert all(record.name in entries for record in recorder.records)

    source = "plugins/hook_monitoring_plugin.py"
    recorder = Recorder(source)
    await HookMonitoringPlugin._register_monitoring_services(FakeOwner(recorder))
    assert set(recorder.records) == {r for r in registrations if r.source == source}
    assert all(record.name in entries for record in recorder.records)

    source = "kollabor/goals/tooling.py"
    recorder = Recorder(source)
    register_goal_tools(recorder, recorder, service=None)
    assert set(recorder.records) == {r for r in registrations if r.source == source}
    assert all(record.name in entries for record in recorder.records)


@pytest.mark.asyncio
async def test_external_mcp_discovery_uses_current_server_data(tmp_path):
    """The guide's search/load route works with dynamic, connected server data."""
    mcp = SimpleNamespace(
        tool_registry={
            "dynamic_lookup": {
                "server": "fixture_server",
                "enabled": True,
                "definition": {
                    "description": "fixture-specific capability",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        },
        server_connections={"fixture_server": object()},
    )
    executor = ToolExecutor(mcp_integration=mcp, event_bus=None, workspace=tmp_path)
    search = await executor._execute_tool_search({"query": "fixture-specific"})
    assert search.success
    assert "mcp:fixture_server:dynamic_lookup" in search.output

    loaded = await executor._execute_tool_load({"name": "mcp:fixture_server:dynamic_lookup"})
    assert loaded.success
    assert '"query"' in loaded.output
    wrong_server = await executor._execute_tool_load({"name": "mcp:other:dynamic_lookup"})
    assert not wrong_server.success
    assert "belongs to server 'fixture_server'" in wrong_server.error
    mcp.server_connections.clear()
    disconnected = await executor._execute_tool_load({"name": "mcp:fixture_server:dynamic_lookup"})
    assert not disconnected.success
    assert "not connected" in disconnected.error

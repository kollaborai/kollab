"""Keep the bundled tool guide aligned with the actual registration paths.

This scans production registration calls without initializing plugins or
starting processes. Unknown dynamic registration needs an explicit discovery
strategy instead of being silently omitted from coverage.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_agent.tool_executor import ToolExecutor
from kollabor_agent.tool_registry import ToolRegistry, get_registry

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "bundles/skills/kollab-internal-tools/references"
FIELDS = ("Purpose", "Inputs", "Result", "Effects", "Permission", "Source")
HEADING = re.compile(r"^### `([^`]+)`\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Registration:
    kind: str
    name: str
    source: str
    tag: str = ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _scan_source(source: str, path: str) -> tuple[list[Registration], list[str]]:
    """Find actual plugin and SDK registration calls, including conditional ones."""
    registrations: list[Registration] = []
    errors: list[str] = []
    tree = ast.parse(source, filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in {"register_plugin_tag", "register_plugin_handler", "register_custom_tool"}:
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


@pytest.fixture
def inventory(monkeypatch):
    # Build the real built-in registry in isolation from earlier tests.
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    tools = get_registry().list()
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

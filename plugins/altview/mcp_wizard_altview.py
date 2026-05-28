"""One-screen MCP manager as an AltView plugin."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


@dataclass
class McpEntry:
    """Single row in the MCP manager."""

    name: str
    description: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    source: str = "configured"
    enabled: bool = False
    connected: bool = False
    tool_count: int = 0
    tools: List[str] = field(default_factory=list)
    state: str = "disabled"
    badges: List[str] = field(default_factory=list)
    missing_env: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _servers(config: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(config, dict):
        return {}
    servers = config.get("servers", {})
    return servers if isinstance(servers, dict) else {}


def _snapshot_servers(runtime_state: Any) -> Dict[str, Dict[str, Any]]:
    if runtime_state is None:
        return {}

    raw_servers = getattr(runtime_state, "servers", None)
    if raw_servers is None and isinstance(runtime_state, dict):
        raw_servers = runtime_state.get("servers", [])

    if isinstance(raw_servers, dict):
        raw_servers = [
            {"name": name, **info}
            for name, info in raw_servers.items()
            if isinstance(info, dict)
        ]

    result: Dict[str, Dict[str, Any]] = {}
    for server in raw_servers or []:
        if isinstance(server, dict):
            name = str(server.get("name", ""))
            if not name:
                continue
            result[name] = dict(server)
        else:
            name = str(getattr(server, "name", ""))
            if not name:
                continue
            result[name] = {
                "name": name,
                "enabled": getattr(server, "enabled", False),
                "connected": getattr(server, "connected", False),
                "tool_count": getattr(server, "tool_count", 0),
                "tools": list(getattr(server, "tools", []) or []),
            }
    return result


def _is_missing_env_value(value: Any) -> bool:
    text = "" if value is None else str(value).strip()
    if not text:
        return True
    return (
        text.endswith("-here")
        or text.startswith("your-")
        or text in {"xxx", "TODO", "todo", "<token>", "<api-key>"}
    )


def _missing_env_keys(config: Dict[str, Any]) -> List[str]:
    env = config.get("env", {})
    if not isinstance(env, dict):
        return []
    return [key for key, value in env.items() if _is_missing_env_value(value)]


def build_mcp_entries(
    example_config: Optional[Dict[str, Any]],
    current_config: Optional[Dict[str, Any]],
    runtime_state: Any = None,
    errors: Optional[Dict[str, str]] = None,
) -> List[McpEntry]:
    """Merge configured servers, example templates, and runtime state."""

    example_servers = _servers(example_config)
    configured_servers = _servers(current_config)
    runtime_servers = _snapshot_servers(runtime_state)
    error_map = errors or {}
    names = sorted(set(example_servers) | set(configured_servers))
    entries: List[McpEntry] = []

    for name in names:
        configured = name in configured_servers
        config = copy.deepcopy(
            configured_servers.get(name) or example_servers.get(name) or {}
        )
        runtime = runtime_servers.get(name, {})
        source = "configured" if configured else "available"
        enabled = bool(config.get("enabled", False)) if configured else False
        connected = bool(runtime.get("connected", False))
        tools = list(runtime.get("tools", []) or [])
        tool_count = int(runtime.get("tool_count", len(tools)) or 0)
        missing_env = _missing_env_keys(config)
        error = error_map.get(name)

        if error:
            state = "error"
        elif not configured:
            state = "available"
        elif connected:
            state = "active"
        elif enabled:
            state = "enabled"
        else:
            state = "disabled"

        badges = [state]
        if missing_env:
            badges.append("needs env")

        entries.append(
            McpEntry(
                name=name,
                description=str(config.get("description", "No description")),
                command=str(config.get("command", "")),
                args=[str(arg) for arg in config.get("args", []) or []],
                env=dict(config.get("env", {}) or {}),
                source=source,
                enabled=enabled,
                connected=connected,
                tool_count=tool_count,
                tools=tools,
                state=state,
                badges=badges,
                missing_env=missing_env,
                error=error,
            )
        )

    return entries


class McpWizardAltView(AltView):
    """AltView for managing MCP servers from one `/mcp` screen."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="mcp",
            description="Open the MCP manager",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[MCP]",
            aliases=["mcps", "servers"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 15.0
        self._example_config: Dict[str, Any] = {}
        self._current_config: Dict[str, Any] = {"servers": {}}
        self._runtime_state: Any = None
        self._entries: List[McpEntry] = []
        self._filtered_entries: List[McpEntry] = []
        self._selected_index = 0
        self._scroll_offset = 0
        self._filter_active = False
        self._filter_query = ""
        self._status_message = ""
        self._last_errors: Dict[str, str] = {}
        self._mcp_manager: Any = None
        self._state_service: Any = None
        self._mcp_integration: Any = None
        self._config_service: Any = None
        self._event_bus: Any = None
        self._app: Any = None
        self._global_enabled = True
        self.selected_servers: Dict[str, Dict[str, Any]] = {}

    def set_config(
        self,
        example_config: Dict[str, Any],
        current_config: Dict[str, Any],
        runtime_state: Any = None,
    ) -> None:
        self._example_config = example_config or {}
        self._current_config = current_config or {"servers": {}}
        self._runtime_state = runtime_state

    def set_context(
        self,
        mcp_manager: Any = None,
        state_service: Any = None,
        mcp_integration: Any = None,
        config_service: Any = None,
        event_bus: Any = None,
        app: Any = None,
    ) -> None:
        self._mcp_manager = mcp_manager
        self._state_service = state_service
        self._mcp_integration = mcp_integration
        self._config_service = config_service or self._config_service
        self._event_bus = event_bus
        self._app = app

    def set_app(self, app: Any) -> None:
        self._app = app
        self._event_bus = getattr(app, "event_bus", self._event_bus)
        self._config_service = (
            getattr(app, "config_service", None)
            or getattr(app, "config", None)
            or self._config_service
        )
        llm_service = getattr(app, "llm_service", None)
        self._mcp_integration = (
            getattr(llm_service, "mcp_integration", None)
            or getattr(app, "mcp_integration", None)
            or self._mcp_integration
        )
        if self._event_bus and hasattr(self._event_bus, "get_service"):
            self._state_service = (
                self._event_bus.get_service("state_service") or self._state_service
            )

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        await self._load_data()
        self._status_message = "ready"

    async def on_resume(self) -> None:
        await super().on_resume()
        await self._load_data()

    async def on_complete(self) -> None:
        await super().on_complete()

    async def _load_data(self) -> None:
        self._global_enabled = self._read_global_enabled()
        if self._mcp_manager is not None:
            self._example_config = self._mcp_manager.load_example_config() or {}
            self._current_config = self._mcp_manager.load_config() or {"servers": {}}
        if self._state_service is not None and hasattr(
            self._state_service, "get_mcp_state"
        ):
            try:
                self._runtime_state = await self._state_service.get_mcp_state()
            except Exception as exc:
                logger.debug("MCP manager state refresh failed: %s", exc)
        self._refresh_entries()

    def _refresh_entries(self) -> None:
        self._entries = build_mcp_entries(
            self._example_config,
            self._current_config,
            self._runtime_state,
            self._last_errors,
        )
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._filter_query.strip().lower()
        if not query:
            self._filtered_entries = list(self._entries)
        else:
            self._filtered_entries = [
                entry
                for entry in self._entries
                if query in entry.name.lower()
                or query in entry.description.lower()
                or query in entry.command.lower()
                or query in entry.state.lower()
                or any(query in tool.lower() for tool in entry.tools)
            ]

        if not self._filtered_entries:
            self._selected_index = 0
            self._scroll_offset = 0
            return
        self._selected_index = min(self._selected_index, len(self._filtered_entries) - 1)
        self._scroll_offset = min(self._scroll_offset, self._selected_index)

    def _entry_index(self, name: str) -> int:
        for index, entry in enumerate(self._filtered_entries):
            if entry.name == name:
                return index
        raise ValueError(f"MCP entry not found: {name}")

    def _entry_by_name(self, name: str) -> McpEntry:
        for entry in self._entries:
            if entry.name == name:
                return entry
        raise ValueError(f"MCP entry not found: {name}")

    def _current_entry(self) -> Optional[McpEntry]:
        if not self._filtered_entries:
            return None
        return self._filtered_entries[
            min(self._selected_index, len(self._filtered_entries) - 1)
        ]

    async def handle_input(self, key_press: KeyPress) -> bool:
        if self._filter_active:
            return self._handle_filter_input(key_press)

        name = key_press.name
        char = key_press.char or ""

        if name == "Escape" or char == "\x1b":
            return True
        if char.lower() == "g":
            await self._toggle_global_enabled()
            return False
        if char == "/":
            self._filter_active = True
            self._filter_query = ""
            self._apply_filter()
            return False
        if name == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return False
        if name == "ArrowDown":
            self._selected_index = min(
                max(0, len(self._filtered_entries) - 1), self._selected_index + 1
            )
            return False
        if name == "PageUp":
            self._selected_index = max(0, self._selected_index - 8)
            return False
        if name == "PageDown":
            self._selected_index = min(
                max(0, len(self._filtered_entries) - 1), self._selected_index + 8
            )
            return False
        if name == "Home":
            self._selected_index = 0
            return False
        if name == "End":
            self._selected_index = max(0, len(self._filtered_entries) - 1)
            return False
        if char == " ":
            await self._toggle_selected()
            return False
        if char.lower() == "a":
            await self._add_selected()
            return False
        if char.lower() == "d":
            await self._delete_selected()
            return False
        if char.lower() == "t":
            await self._test_selected()
            return False
        if char.lower() == "r":
            await self._reload_servers()
            return False
        if name == "Enter" or char in ("\r", "\n"):
            entry = self._current_entry()
            self._status_message = (
                f"details: {entry.name}" if entry is not None else "no MCP selected"
            )
            return False
        return False

    def _handle_filter_input(self, key_press: KeyPress) -> bool:
        name = key_press.name
        char = key_press.char or ""
        if name in {"Escape", "Enter"} or char in ("\x1b", "\r", "\n"):
            self._filter_active = False
            return False
        if name in {"Backspace", "Delete"}:
            self._filter_query = self._filter_query[:-1]
            self._apply_filter()
            return False
        if char and len(char) == 1 and 32 <= ord(char) <= 126:
            self._filter_query += char
            self._apply_filter()
        return False

    def _read_global_enabled(self) -> bool:
        if self._config_service is None or not hasattr(self._config_service, "get"):
            return True
        return bool(self._config_service.get("plugins.mcp.enabled", True))

    def _save_global_enabled(self, enabled: bool) -> bool:
        if self._config_service is None:
            return False
        if hasattr(self._config_service, "save_key"):
            return bool(
                self._config_service.save_key(
                    "plugins.mcp.enabled",
                    enabled,
                    save_target="global",
                )
            )
        if hasattr(self._config_service, "set"):
            ok = bool(self._config_service.set("plugins.mcp.enabled", enabled))
            save_config = getattr(self._config_service, "save_config", None)
            if callable(save_config):
                try:
                    return bool(save_config(save_target="global"))
                except TypeError:
                    return bool(save_config())
            return ok
        return False

    def _notify_config_reload(self) -> None:
        notify = getattr(self._config_service, "_notify_reload_callbacks", None)
        if callable(notify):
            try:
                notify()
            except Exception as exc:
                logger.warning("MCP manager config reload notification failed: %s", exc)

    async def _toggle_global_enabled(self) -> None:
        next_enabled = not self._global_enabled
        if not self._save_global_enabled(next_enabled):
            self._status_message = "MCP global toggle failed"
            return

        self._global_enabled = next_enabled
        self._notify_config_reload()
        if next_enabled:
            try:
                summary = await self._reload_runtime()
                reconnected = int(summary.get("reconnected", 0) or 0)
                self._status_message = f"MCP enabled: {reconnected} reconnected"
            except Exception as exc:
                self._status_message = f"MCP enabled, reload failed: {exc}"
        else:
            try:
                await self._shutdown_runtime()
                self._status_message = "MCP disabled globally"
            except Exception as exc:
                self._status_message = f"MCP disabled, shutdown failed: {exc}"
            self._runtime_state = None

        await self._load_data()

    async def _add_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            self._status_message = "nothing to add"
            return
        if entry.source == "configured":
            self._status_message = f"{entry.name} already configured"
            return
        config = self._load_current_config()
        template = copy.deepcopy(_servers(self._example_config).get(entry.name, {}))
        config.setdefault("servers", {})[entry.name] = template
        self._save_config(config)
        self._status_message = f"added {entry.name}"
        await self._load_data()
        self._select_entry(entry.name)

    async def _delete_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            self._status_message = "nothing to delete"
            return
        if entry.source != "configured":
            self._status_message = f"{entry.name} is not configured"
            return
        config = self._load_current_config()
        config.setdefault("servers", {}).pop(entry.name, None)
        self._save_config(config)
        self._status_message = f"deleted {entry.name}"
        await self._load_data()
        self._select_entry(entry.name)

    async def _toggle_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            self._status_message = "nothing to toggle"
            return
        if entry.source != "configured":
            self._status_message = f"press a to add {entry.name} first"
            return
        if self._mcp_manager is not None:
            result = (
                self._mcp_manager.disable_server(entry.name)
                if entry.enabled
                else self._mcp_manager.enable_server(entry.name)
            )
            if not result.get("success", False):
                self._last_errors[entry.name] = str(result.get("error", "toggle failed"))
                self._status_message = f"toggle failed: {entry.name}"
                self._refresh_entries()
                return
        else:
            config = self._load_current_config()
            config["servers"][entry.name]["enabled"] = not entry.enabled
            self._save_config(config)
        self._status_message = (
            f"disabled {entry.name}" if entry.enabled else f"enabled {entry.name}"
        )
        await self._load_data()
        self._select_entry(entry.name)

    async def _test_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            self._status_message = "nothing to test"
            return
        try:
            if self._state_service is not None and hasattr(
                self._state_service, "test_mcp_server"
            ):
                result = await self._state_service.test_mcp_server(entry.name)
            elif self._mcp_manager is not None:
                result = self._mcp_manager.get_server_status(
                    entry.name, self._mcp_integration
                )
            else:
                result = {"found": False, "error": "MCP state service unavailable"}
        except Exception as exc:
            result = {"found": False, "error": str(exc)}

        if result.get("found", True) and not result.get("error"):
            self._last_errors.pop(entry.name, None)
            tools = int(result.get("tool_count", 0) or 0)
            self._status_message = f"test ok: {entry.name} ({tools} tools)"
        else:
            error = str(result.get("error", "test failed"))
            self._last_errors[entry.name] = error
            self._status_message = f"test failed: {entry.name}"
        await self._load_data()
        self._select_entry(entry.name)

    async def _reload_servers(self) -> None:
        try:
            summary = await self._reload_runtime()
        except Exception as exc:
            self._status_message = f"reload failed: {exc}"
            return

        reconnected = int(summary.get("reconnected", 0) or 0)
        self._status_message = f"reload ok: {reconnected} reconnected"
        await self._load_data()

    async def _reload_runtime(self) -> Dict[str, Any]:
        if self._state_service is not None and hasattr(
            self._state_service, "reload_mcp_servers"
        ):
            return await self._state_service.reload_mcp_servers()
        if self._mcp_integration is not None and hasattr(
            self._mcp_integration, "reload_mcp_servers"
        ):
            return await self._mcp_integration.reload_mcp_servers()
        if self._mcp_integration is not None:
            await self._mcp_integration.shutdown()
            self._mcp_integration.mcp_servers.clear()
            self._mcp_integration._load_mcp_config()
            discovered = await self._mcp_integration.discover_mcp_servers()
            return {
                "configured": len(self._mcp_integration.mcp_servers),
                "discovered": discovered,
                "reconnected": len(self._mcp_integration.server_connections),
            }
        return {"reconnected": 0}

    async def _shutdown_runtime(self) -> None:
        if self._state_service is not None and hasattr(
            self._state_service, "reload_mcp_servers"
        ):
            await self._state_service.reload_mcp_servers()
        elif self._mcp_integration is not None and hasattr(
            self._mcp_integration, "shutdown"
        ):
            await self._mcp_integration.shutdown()

        llm_service = getattr(self._app, "llm_service", None) if self._app else None
        native_tools = getattr(llm_service, "_native_tools", None)
        if native_tools is not None and hasattr(native_tools, "tools"):
            native_tools.tools = None

    def _load_current_config(self) -> Dict[str, Any]:
        if self._mcp_manager is not None:
            return self._mcp_manager.load_config() or {"servers": {}}
        return copy.deepcopy(self._current_config or {"servers": {}})

    def _save_config(self, config: Dict[str, Any]) -> None:
        if self._mcp_manager is not None:
            self._mcp_manager.save_config(config)
        self._current_config = copy.deepcopy(config)

    def _select_entry(self, name: str) -> None:
        try:
            self._selected_index = self._entry_index(name)
        except ValueError:
            self._selected_index = min(
                self._selected_index, max(0, len(self._filtered_entries) - 1)
            )

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False
        width, height = self._renderer.get_terminal_size()
        theme = T()
        self._renderer.clear_screen()
        self._render_header(width, theme)
        self._render_body(width, height, theme)
        self._render_footer(width, height, theme)
        return True

    def _render_header(self, width: int, theme: Any) -> None:
        configured = sum(1 for entry in self._entries if entry.source == "configured")
        active = sum(1 for entry in self._entries if entry.state == "active")
        disabled = sum(1 for entry in self._entries if entry.state == "disabled")
        available = sum(1 for entry in self._entries if entry.source == "available")
        path = getattr(self._mcp_manager, "config_path", "~/.kollab/mcp/mcp_settings.json")
        title = (
            f" MCP Manager  mcp:{'on' if self._global_enabled else 'off'} "
            f"cfg:{configured} active:{active} "
            f"disabled:{disabled} available:{available} "
        )
        self._renderer.write_at(
            0, 0, solid_fg(str(C["half_bottom"]) * width, theme.primary[0]), ""
        )
        self._renderer.write_at(
            0, 1, solid(title[:width].ljust(width), theme.primary[0], theme.text_dark, width), ""
        )
        path_line = f" {path}"
        self._renderer.write_at(
            0, 2, solid(path_line[:width].ljust(width), theme.dark[0], theme.text_dim, width), ""
        )

    def _render_body(self, width: int, height: int, theme: Any) -> None:
        left_width = max(34, min(54, width // 2))
        right_x = left_width + 1
        right_width = max(20, width - right_x)
        top = 4
        body_height = max(4, height - top - 3)

        self._render_list(0, top, left_width, body_height, theme)
        self._render_details(right_x, top, right_width, body_height, theme)

    def _render_list(
        self, x: int, y: int, width: int, height: int, theme: Any
    ) -> None:
        if self._filter_active:
            title = f" filter: {self._filter_query}_"
        elif self._filter_query:
            title = f" filter: {self._filter_query}"
        else:
            title = " servers"
        self._renderer.write_at(
            x, y, solid(title[:width].ljust(width), theme.dark[1], theme.text, width), ""
        )
        y += 1
        list_height = max(1, height - 1)
        total = len(self._filtered_entries)
        if self._selected_index < self._scroll_offset:
            self._scroll_offset = self._selected_index
        if self._selected_index >= self._scroll_offset + list_height:
            self._scroll_offset = self._selected_index - list_height + 1
        visible = self._filtered_entries[
            self._scroll_offset : self._scroll_offset + list_height
        ]
        for offset, entry in enumerate(visible):
            index = self._scroll_offset + offset
            cursor = ">" if index == self._selected_index else " "
            badges = ",".join(entry.badges)
            line = f"{cursor} {entry.name} [{badges}]"
            if len(line) > width - 1:
                line = line[: width - 2] + str(C["ellipsis"])
            bg = theme.primary[0] if index == self._selected_index else theme.dark[0]
            fg = theme.text_dark if index == self._selected_index else theme.text
            self._renderer.write_at(
                x, y + offset, solid(line.ljust(width), bg, fg, width), ""
            )
        for offset in range(len(visible), list_height):
            self._renderer.write_at(
                x, y + offset, solid(" " * width, theme.dark[0], theme.text, width), ""
            )
        if total:
            info = f" {self._scroll_offset + 1}-{min(total, self._scroll_offset + list_height)} of {total}"
        else:
            info = " no servers"
        self._renderer.write_at(x, y + list_height - 1, info[:width], "")

    def _render_details(
        self, x: int, y: int, width: int, height: int, theme: Any
    ) -> None:
        entry = self._current_entry()
        self._renderer.write_at(
            x, y, solid(" details".ljust(width), theme.dark[1], theme.text, width), ""
        )
        y += 1
        if entry is None:
            self._renderer.write_at(x, y, " no MCP server selected".ljust(width), "")
            return

        lines = [
            f"global MCP: {'enabled' if self._global_enabled else 'disabled'}",
            f"name: {entry.name}",
            f"state: {entry.state}",
            f"source: {entry.source}",
            f"enabled: {entry.enabled}",
            f"connected: {entry.connected}",
            f"tools: {entry.tool_count}",
            f"command: {entry.command or '-'}",
        ]
        if entry.args:
            lines.append(f"args: {' '.join(entry.args)}")
        if entry.env:
            lines.append(f"env: {', '.join(entry.env)}")
        if entry.missing_env:
            lines.append(f"missing env: {', '.join(entry.missing_env)}")
        if entry.tools:
            lines.append(f"tool names: {', '.join(entry.tools[:4])}")
        if entry.error:
            lines.append(f"error: {entry.error}")
        lines.append(f"status: {self._status_message}")

        for offset, line in enumerate(lines[: max(1, height - 1)]):
            if len(line) > width - 1:
                line = line[: width - 2] + str(C["ellipsis"])
            self._renderer.write_at(
                x,
                y + offset,
                solid(f" {line}".ljust(width), theme.dark[0], theme.text_dim, width),
                "",
            )

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        footer_y = height - 2
        self._renderer.write_at(
            0,
            footer_y,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        footer = " up/down move | g global | space toggle | a add | d delete | t test | r reload | / filter | esc exit "
        self._renderer.write_at(
            0,
            footer_y + 1,
            solid(footer[:width].ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )

"""Claude Code-compatible JSON hook system for Kollab.

Reads hooks.json files from project and global config directories,
creates subprocess-based Hook objects, and registers them on the
existing event bus. Zero changes to the event system itself.

JSON format (Claude Code compatible):
{
    "hooks": {
        "EventName": [
            {
                "matcher": "regex_pattern",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python script.py",
                        "timeout": 10,
                        "async": false
                    }
                ]
            }
        ]
    }
}
"""

import asyncio
import json
import logging
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from kollabor_config.config_utils import APP_CONFIG_DIR_NAME

from kollabor_events import EventBus
from kollabor_events.models import Event, EventType, Hook, HookPriority

logger = logging.getLogger(__name__)

# Claude Code event name → Kollabor EventType
_CLAUDE_CODE_ALIASES: Dict[str, EventType] = {
    "SessionStart": EventType.SYSTEM_STARTUP,
    "SessionEnd": EventType.SYSTEM_SHUTDOWN,
    "UserPromptSubmit": EventType.USER_INPUT_PRE,
    "PreToolUse": EventType.TOOL_CALL_PRE,
    "PostToolUse": EventType.TOOL_CALL_POST,
    "PostToolUseFailure": EventType.TOOL_CALL_POST,
    "PermissionRequest": EventType.PERMISSION_CHECK,
    "Stop": EventType.LLM_RESPONSE_POST,
}

# Kollabor-native PascalCase → EventType
_KOLLAB_ALIASES: Dict[str, EventType] = {
    "LlmRequestPre": EventType.LLM_REQUEST_PRE,
    "LlmRequestPost": EventType.LLM_REQUEST_POST,
    "LlmThinking": EventType.LLM_THINKING,
    "McpServerConnect": EventType.MCP_SERVER_CONNECT,
    "McpServerDisconnect": EventType.MCP_SERVER_DISCONNECT,
    "McpToolCallPre": EventType.MCP_TOOL_CALL_PRE,
    "McpToolCallPost": EventType.MCP_TOOL_CALL_POST,
    "SlashCommandExecute": EventType.SLASH_COMMAND_EXECUTE,
    "SlashCommandComplete": EventType.SLASH_COMMAND_COMPLETE,
    "ShellCommandPre": EventType.SHELL_COMMAND_PRE,
    "ShellCommandPost": EventType.SHELL_COMMAND_POST,
    "KeyPress": EventType.KEY_PRESS,
    "PasteDetected": EventType.PASTE_DETECTED,
    "CancelRequest": EventType.CANCEL_REQUEST,
    "RenderFrame": EventType.RENDER_FRAME,
    "ModalShow": EventType.MODAL_SHOW,
    "ModalHide": EventType.MODAL_HIDE,
}

# Matcher target field by event type
_MATCHER_FIELDS: Dict[EventType, str] = {
    EventType.TOOL_CALL_PRE: "tool_name",
    EventType.TOOL_CALL_POST: "tool_name",
    EventType.MCP_TOOL_CALL_PRE: "_mcp_compound",
    EventType.MCP_TOOL_CALL_POST: "_mcp_compound",
    EventType.SLASH_COMMAND_EXECUTE: "command",
    EventType.SLASH_COMMAND_COMPLETE: "command",
    EventType.SHELL_COMMAND_PRE: "command",
    EventType.SHELL_COMMAND_POST: "command",
    EventType.SYSTEM_STARTUP: "source",
    EventType.PERMISSION_CHECK: "tool_name",
    EventType.KEY_PRESS: "key",
}

# Priority name → int mapping
_PRIORITY_MAP: Dict[str, int] = {
    name: member.value for name, member in HookPriority.__members__.items()
}

DEFAULT_TIMEOUT = 30


class ConfigHookLoader:
    """Loads hooks from JSON config files and registers them on the event bus."""

    def __init__(self, event_bus: EventBus, config_service: Any) -> None:
        self.event_bus = event_bus
        self.config_service = config_service
        self._hooks_registered: List[str] = []
        self._pending_tasks: set = set()

    async def load_and_register(self) -> int:
        """Load hooks.json files and register all hooks on the event bus.

        Returns:
            Number of hooks registered.
        """
        hooks_config = self._load_hooks_config()
        if not hooks_config:
            return 0

        hooks_section = hooks_config.get("hooks", {})
        if not isinstance(hooks_section, dict):
            logger.warning("hooks.json 'hooks' field is not a dict, skipping")
            return 0

        count = 0
        for event_name, matcher_groups in hooks_section.items():
            event_type = self._resolve_event_type(event_name)
            if event_type is None:
                logger.warning(f"Unknown hook event name: {event_name}")
                continue

            if not isinstance(matcher_groups, list):
                logger.warning(f"hooks.json '{event_name}' should be a list, skipping")
                continue

            # PostToolUseFailure has special success=false filter
            failure_filter = event_name == "PostToolUseFailure"

            for group_idx, group in enumerate(matcher_groups):
                if not isinstance(group, dict):
                    continue

                matcher = group.get("matcher", "")
                hook_entries = group.get("hooks", [])
                if not isinstance(hook_entries, list):
                    continue

                for hook_idx, entry in enumerate(hook_entries):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type", "command") != "command":
                        logger.debug(
                            f"Skipping non-command hook type: {entry.get('type')}"
                        )
                        continue

                    command = entry.get("command")
                    if not command:
                        continue

                    timeout = entry.get("timeout", DEFAULT_TIMEOUT)
                    is_async = entry.get("async", False)
                    priority = self._resolve_priority(
                        entry.get("priority", "POSTPROCESSING")
                    )

                    hook_name = f"config_{event_name}_{group_idx}_{hook_idx}"
                    callback = self._build_hook_callback(
                        command=command,
                        event_type=event_type,
                        event_name=event_name,
                        matcher=matcher,
                        timeout=timeout,
                        is_async=is_async,
                        failure_filter=failure_filter,
                    )

                    hook = Hook(
                        name=hook_name,
                        plugin_name="config_hooks",
                        event_type=event_type,
                        priority=priority,
                        callback=callback,
                        timeout=timeout + 5,  # executor timeout > subprocess timeout
                        retry_attempts=0,
                        error_action="continue",
                    )

                    if await self.event_bus.register_hook(hook):
                        self._hooks_registered.append(hook_name)
                        count += 1
                        logger.debug(
                            f"Registered config hook: {hook_name} "
                            f"({event_name} -> {event_type.value})"
                        )

        if count > 0:
            logger.info(f"Loaded {count} config hooks from hooks.json")
        return count

    async def shutdown(self) -> None:
        """Wait for pending async hook tasks to finish before exit."""
        if self._pending_tasks:
            logger.debug(f"Waiting for {len(self._pending_tasks)} pending hook tasks")
            done, _ = await asyncio.wait(self._pending_tasks, timeout=5)
            # Cancel any that didn't finish in time
            for task in self._pending_tasks:
                if not task.done():
                    task.cancel()
            self._pending_tasks.clear()

    def _load_hooks_config(self) -> Optional[Dict[str, Any]]:
        """Load and merge hooks.json from project and global locations.

        Priority: project > global (project values override global).
        """
        project_path = Path.cwd() / APP_CONFIG_DIR_NAME / "hooks.json"
        global_path = Path.home() / APP_CONFIG_DIR_NAME / "hooks.json"

        merged: Dict[str, Any] = {}

        # Global first (base layer)
        global_config = self._read_json(global_path)
        if global_config:
            merged = global_config

        # Project overrides
        project_config = self._read_json(project_path)
        if project_config:
            # Merge hook lists: for same event name, project entries append
            if merged and "hooks" in merged and "hooks" in project_config:
                for event_name, groups in project_config["hooks"].items():
                    if event_name in merged["hooks"]:
                        # Project hooks come after global hooks for same event
                        if isinstance(merged["hooks"][event_name], list) and isinstance(
                            groups, list
                        ):
                            merged["hooks"][event_name].extend(groups)
                        else:
                            merged["hooks"][event_name] = groups
                    else:
                        merged["hooks"][event_name] = groups
            else:
                merged = project_config

        return merged if merged else None

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a JSON file, returning None on missing or parse error."""
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                logger.debug(f"Loaded hooks config from {path}")
                return data
            logger.warning(f"hooks.json at {path} is not a JSON object")
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read {path}: {e}")
            return None

    def _resolve_event_type(self, name: str) -> Optional[EventType]:
        """Resolve an event name (alias or enum value) to EventType."""
        # Check Claude Code aliases first
        if name in _CLAUDE_CODE_ALIASES:
            return _CLAUDE_CODE_ALIASES[name]

        # Check Kollabor native aliases
        if name in _KOLLAB_ALIASES:
            return _KOLLAB_ALIASES[name]

        # Try direct enum value match (e.g., "user_input_pre")
        try:
            return EventType(name)
        except ValueError:
            pass

        # Try uppercase snake_case (e.g., "USER_INPUT_PRE")
        try:
            return EventType[name]
        except KeyError:
            pass

        return None

    def _resolve_priority(self, value: Any) -> int:
        """Resolve a priority value to an integer."""
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            upper = value.upper()
            if upper in _PRIORITY_MAP:
                return _PRIORITY_MAP[upper]
        return HookPriority.POSTPROCESSING.value

    def _build_hook_callback(
        self,
        command: str,
        event_type: EventType,
        event_name: str,
        matcher: str,
        timeout: int,
        is_async: bool,
        failure_filter: bool,
    ) -> Callable:
        """Build an async callback that runs a subprocess for a hook entry."""
        compiled_matcher = re.compile(matcher) if matcher else None
        matcher_field = _MATCHER_FIELDS.get(event_type)

        async def _hook_callback(
            data: Dict[str, Any], event: Event
        ) -> Optional[Dict[str, Any]]:
            # Flatten nested tool_data/result for cleaner hook interface
            # Real events pass {"tool_data": {...}, "result": {...}}
            flat = dict(data)
            tool_data = data.get("tool_data", {})
            if isinstance(tool_data, dict):
                # Promote tool_data fields
                for k, v in tool_data.items():
                    if k not in flat:
                        flat[k] = v
                # tool_name: try "name" first, fall back to "type"
                # use `or` not default - name can exist but be None
                if not flat.get("tool_name"):
                    flat["tool_name"] = (
                        tool_data.get("name") or tool_data.get("type") or ""
                    )
            result_data = data.get("result", {})
            if isinstance(result_data, dict):
                for k, v in result_data.items():
                    if k not in flat:
                        flat[k] = v

            # PostToolUseFailure: only fire if success=false
            if failure_filter and flat.get("success", True):
                return None

            # Check matcher
            if compiled_matcher and matcher_field:
                if matcher_field == "_mcp_compound":
                    target = (
                        f"{flat.get('server_name', '')}"
                        f"__{flat.get('tool_name', '')}"
                    )
                else:
                    target = str(flat.get(matcher_field, ""))
                if not compiled_matcher.search(target):
                    return None

            # Build payload
            payload = {
                "session_id": flat.get("session_id", ""),
                "cwd": os.getcwd(),
                "hook_event_name": event_name,
                "timestamp": time.time(),
            }
            payload.update(flat)

            payload_json = json.dumps(payload, default=str)

            if is_async:
                # Fire and forget with cleanup tracking
                task = asyncio.create_task(
                    self._run_command(command, payload_json, timeout)
                )
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
                return None

            # Synchronous: wait for result
            try:
                returncode, stdout, stderr = await self._run_command(
                    command, payload_json, timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"Config hook timed out after {timeout}s: {command}")
                return None
            except Exception as e:
                logger.warning(f"Config hook failed: {command}: {e}")
                return None

            # Exit 2: block the event
            if returncode == 2:
                event.cancelled = True
                reason = stderr.strip() if stderr else "Blocked by config hook"
                logger.info(f"Config hook blocked event: {reason}")
                return {"data": {"_hook_blocked": True, "_hook_reason": reason}}

            # Non-zero, non-2: log and continue
            if returncode != 0:
                logger.warning(
                    f"Config hook exited {returncode}: {command}"
                    f"{(' stderr: ' + stderr.strip()) if stderr else ''}"
                )
                return None

            # Exit 0: parse stdout JSON response
            if not stdout or not stdout.strip():
                return None

            try:
                response = json.loads(stdout)
            except json.JSONDecodeError:
                logger.debug(
                    f"Config hook stdout not JSON, ignoring: " f"{stdout[:100]}"
                )
                return None

            if not isinstance(response, dict):
                return None

            # Handle systemMessage
            sys_msg = response.get("systemMessage")
            if sys_msg:
                logger.warning(f"Config hook message: {sys_msg}")

            # Handle continue=false or decision=deny
            if response.get("continue") is False or response.get("decision") == "deny":
                event.cancelled = True
                reason = response.get("reason", "Denied by config hook")
                logger.info(f"Config hook denied event: {reason}")
                return {"data": {"_hook_blocked": True, "_hook_reason": reason}}

            # Handle data transformation
            transform_data = response.get("data")
            if isinstance(transform_data, dict):
                return {"data": transform_data}

            return None

        return _hook_callback

    async def _run_command(
        self, command: str, payload: str, timeout: int
    ) -> Tuple[int, str, str]:
        """Run a shell command as a subprocess.

        CRITICAL: Uses asyncio.create_subprocess_exec, NEVER
        asyncio.to_thread + subprocess.run (causes SIGTTIN).

        Args:
            command: Shell command string.
            payload: JSON payload sent via stdin.
            timeout: Seconds before killing the process.

        Returns:
            (returncode, stdout, stderr)

        Raises:
            asyncio.TimeoutError: If command exceeds timeout.
        """
        args = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(payload.encode()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return (
            proc.returncode or 0,
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
        )

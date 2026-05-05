"""Native tool calling handler for Kollab LLM service.

Handles MCP server discovery, native tool loading, and execution
of native API tool calls. Extracted from LLMService as part of
the llm_service.py decomposition (Phase B).
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NativeToolsHandler:
    """Manages native tool calling via MCP integration.

    Responsibilities:
    - Background MCP server discovery
    - Loading tool definitions for native API function calling
    - Executing native tool calls from API responses
    - Handling malformed tool names (LLM confusion edge case)
    """

    def __init__(self, mcp_integration, profile_manager, api_service, config):
        """Initialize the native tools handler.

        Args:
            mcp_integration: MCPIntegration for server discovery and tool registry
            profile_manager: ProfileManager for checking profile tool support
            api_service: APICommunicationService for getting last tool calls
            config: ConfigService for reading tool configuration
        """
        self.mcp_integration = mcp_integration
        self.profile_manager = profile_manager
        self.api_service = api_service
        self.config = config

        # Native tool state
        self.tools: Optional[List[Dict[str, Any]]] = None
        self.tool_calling_enabled = config.get("kollabor.llm.native_tool_calling", True)

        # Synchronization for MCP discovery (prevent race condition on first API call)
        self.discovery_complete = asyncio.Event()

    async def background_discovery(self) -> None:
        """Discover MCP servers in background (non-blocking).

        Runs MCP server discovery asynchronously so the UI can start
        immediately. Updates tools when discovery completes.
        Sets discovery_complete event when finished (success or failure).
        """
        try:
            discovered_servers = await self.mcp_integration.discover_mcp_servers()
            logger.info(
                f"Background MCP discovery: found {len(discovered_servers)} servers"
            )

            # Load native tools now that MCP is ready
            await self.load_tools()
        except Exception as e:
            logger.warning(f"Background MCP discovery failed: {e}")
        finally:
            # Signal completion even on failure (prevents hang)
            self.discovery_complete.set()

    async def load_tools(self) -> None:
        """Load MCP tools for native API function calling.

        Populates self.tools with tool definitions from MCP integration
        for passing to API calls. This enables native tool calling where the
        LLM returns structured tool_calls instead of XML tags.

        Respects both:
        - Global config: core.llm.native_tool_calling (default: True)
        - Profile setting: profile.native_tool_calling (default: True)

        Both must be True for native tools to be loaded. When disabled,
        the LLM uses XML tags (<terminal>, <tool>, etc.) instead.
        """
        # Check global config setting
        if not self.tool_calling_enabled:
            logger.info("Native tool calling disabled in global config")
            self.tools = None
            return

        # Check profile-specific setting
        profile = self.profile_manager.get_active_profile()
        profile_supports_tools = profile.get_supports_tools()
        if not profile_supports_tools:
            logger.info(
                f"Native tool calling disabled for profile '{profile.name}' (using XML mode)"
            )
            self.tools = None
            return

        try:
            tools = self.mcp_integration.get_tool_definitions_for_api()
            if tools:
                self.tools = tools
                logger.info(f"Loaded {len(tools)} tools for native API calling")
            else:
                self.tools = None
                logger.debug("No MCP tools available for native calling")
        except Exception as e:
            logger.warning(f"Failed to load native tools: {e}")
            self.tools = None

    async def execute_tool_calls(self, tool_executor) -> List[Any]:
        """Execute tool calls from native API response.

        Processes tool calls returned by the API's native function calling
        and executes them through the tool executor.

        Handles edge case where LLM outputs malformed tool names containing XML.

        Args:
            tool_executor: ToolExecutor instance for executing tool calls

        Returns:
            List of ToolExecutionResult objects
        """
        results: List[Any] = []
        tool_calls = self.api_service.get_last_tool_calls()

        if not tool_calls:
            return results

        logger.info(f"Executing {len(tool_calls)} native tool calls")

        for tc in tool_calls:
            tool_name = tc.name

            # Handle malformed tool names that contain XML (LLM confusion)
            # Example: "read><file>path</file></read><tool_call>search_nodes"
            if "<" in tool_name or ">" in tool_name:
                logger.warning(f"Malformed tool name detected: {tool_name[:100]}")
                # Try to extract actual tool name from <tool_call>...</tool_call>
                match = re.search(r"<tool_call>([^<]+)", tool_name)
                if match:
                    tool_name = match.group(1).strip()
                    logger.info(f"Extracted tool name from malformed call: {tool_name}")
                else:
                    # Try to find any known MCP tool name in the string
                    available_tools = list(self.mcp_integration.tool_registry.keys())
                    for known_tool in available_tools:
                        if known_tool in tool_name:
                            tool_name = known_tool
                            logger.info(
                                f"Found known tool in malformed name: {tool_name}"
                            )
                            break
                    else:
                        logger.error(
                            f"Could not parse malformed tool name: {tool_name[:100]}"
                        )
                        continue

            # Convert ToolCallResult to tool_executor format
            # File operations use their name as type (file_create, file_edit, etc.)
            # Terminal commands use "terminal" as type
            # MCP tools use "mcp_tool" as type
            if tool_name.startswith("file_"):
                tool_type = tool_name
                tool_data = {
                    "type": tool_type,
                    "id": tc.id,
                    "name": tool_name,
                    **tc.input,
                }
            elif tool_name == "terminal":
                tool_type = "terminal"
                tool_data = {
                    "type": tool_type,
                    "id": tc.id,
                    "name": tool_name,
                    "command": tc.input.get("command", ""),
                }
            elif (
                tool_name in tool_executor.plugin_handlers
                or tool_name.replace("-", "_") in tool_executor.plugin_handlers
            ):
                # Route to registered plugin handler instead of MCP fallback.
                # Plugin handlers are registered with underscores (hub_msg)
                # but native tool names use hyphens (hub-msg) -- normalize.
                tool_type = (
                    tool_name
                    if tool_name in tool_executor.plugin_handlers
                    else tool_name.replace("-", "_")
                )
                tool_data = {
                    "type": tool_type,
                    "id": tc.id,
                    "name": tool_name,
                    **tc.input,
                }
            else:
                tool_type = "mcp_tool"
                tool_data = {
                    "type": tool_type,
                    "id": tc.id,
                    "name": tool_name,
                    "arguments": tc.input,
                }

            result = await tool_executor.execute_tool(tool_data)
            results.append(result)

            logger.debug(
                f"Native tool {tool_name}: {'success' if result.success else 'failed'}"
            )

        return results

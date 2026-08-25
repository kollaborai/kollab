"""System prompt building for Kollab LLM service.

Handles system prompt construction from files, agents, plugins,
and dynamic trender tags. Extracted from LLMService as part of
the llm_service.py decomposition (Phase B).

NOTE: This module is kept in kollabor-ai but requires kollabor-specific
utilities (config_utils) and kollabor_agent utilities (shell_utils) which are
not part of this package. This is a known dependency that should be resolved by
either:
1. Moving those utilities to a shared package
2. Making SystemPromptBuilder accept callbacks for these dependencies
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_events.data_models import ConversationMessage

logger = logging.getLogger(__name__)


class SystemPromptBuilder:
    """Builds and manages the system prompt for LLM conversations.

    Responsibilities:
    - Load base prompt from agent, env var, or file (priority order)
    - Render <trender> tags for dynamic content
    - Append project structure, attachment files, custom prompts
    - Collect plugin system prompt additions
    - Rebuild system prompt on demand (e.g. after skill load/unload)

    NOTE: This class has dependencies on kollabor.utils and kollabor_agent
    which are passed via dependency injection for testing and modular use.
    """

    def __init__(
        self,
        config,
        agent_manager=None,
        util_imports=None,
        profile_manager=None,
        conversation_logger=None,
        mcp_integration=None,
    ):
        """Initialize the system prompt builder.

        Args:
            config: ConfigService instance for reading prompt settings
            agent_manager: AgentManager for agent-specific system prompts (optional)
            util_imports: Dict of utility functions/modules for kollabor deps:
                - get_system_prompt_content: function to get prompt content
                - initialize_system_prompt: function to init prompts
                - render_system_prompt: function to render trender tags
                - get_system_prompt_path: function to get prompt path
                - format_aliases_for_prompt: function to format shell aliases (from kollabor_agent)
                - get_cached_aliases: function to read cached shell aliases (from kollabor_agent)
                If None, will import from kollabor_agent (default behavior)
            profile_manager: ProfileManager for reading active model/provider info (optional)
            mcp_integration: MCPIntegration instance for MCP tool discovery (optional)
        """
        self.config = config
        self.agent_manager = agent_manager
        self.profile_manager = profile_manager
        self.conversation_logger = conversation_logger
        self.mcp_integration = mcp_integration
        self._util_imports = util_imports or {}

        # Plugin instances reference (set after plugins are loaded)
        self._plugin_instances: Optional[Dict[str, Any]] = None

        # Build/rebuild counters
        self._build_count = 0
        self._rebuild_count = 0
        self._session_id: Optional[str] = None

        # Lazy shell-alias state (load only after first real submit)
        self._shell_alias_prompt: Optional[str] = None
        self._shell_aliases_loaded = False

    def _get_utils(self):
        """Lazy import kollabor utils if not provided."""
        if self._util_imports:
            return self._util_imports

        # Import from kollabor_config (kollabor-specific dependency)
        from kollabor_agent import format_aliases_for_prompt, get_cached_aliases
        from kollabor_ai.prompt_renderer import render_system_prompt
        from kollabor_config.config_utils import (
            get_system_prompt_content,
            get_system_prompt_path,
            initialize_system_prompt,
        )

        self._util_imports = {
            "get_system_prompt_content": get_system_prompt_content,
            "initialize_system_prompt": initialize_system_prompt,
            "render_system_prompt": render_system_prompt,
            "get_system_prompt_path": get_system_prompt_path,
            "format_aliases_for_prompt": format_aliases_for_prompt,
            "get_cached_aliases": get_cached_aliases,
        }
        return self._util_imports

    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID for logging context."""
        self._session_id = session_id


    def ensure_shell_aliases_loaded(self) -> bool:
        """Load shell aliases lazily for this session.

        Returns:
            True if alias prompt content was newly loaded and the system prompt
            should be rebuilt, False if aliases were already loaded, disabled,
            or produced no prompt content.
        """
        if self._shell_aliases_loaded:
            return False

        self._shell_aliases_loaded = True

        if not self.config.get("terminal.interactive_shell", False):
            return False

        try:
            utils = self._get_utils()
            aliases = utils["get_cached_aliases"]()
            alias_content = utils["format_aliases_for_prompt"](aliases)
        except Exception as e:
            logger.warning(f"Failed to load shell aliases for session: {e}")
            return False

        if not alias_content:
            return False

        self._shell_alias_prompt = alias_content
        logger.info("Loaded shell aliases for session")
        return True

    def set_mcp_integration(self, mcp_integration) -> None:
        """Set the MCP integration instance for lazy tool summaries.

        Called by the application after MCP integration is initialized.

        Args:
            mcp_integration: MCPIntegration instance
        """
        self.mcp_integration = mcp_integration
        logger.debug(f"MCP integration set: {mcp_integration is not None}")

    def set_plugin_instances(self, plugin_instances: Dict[str, Any]) -> None:
        """Set plugin instances reference for system prompt additions.

        Called by the application after plugins are loaded.

        Args:
            plugin_instances: Dictionary of plugin name to plugin instance
        """
        self._plugin_instances = plugin_instances
        logger.debug(f"Plugin instances set: {len(plugin_instances)} plugins")

    def build(self) -> str:
        """Build system prompt from file or agent.

        Priority:
        0. Active agent's system prompt (if agent is active)
        1. KOLLAB_SYSTEM_PROMPT environment variable (direct string)
        2. KOLLAB_SYSTEM_PROMPT_FILE environment variable (custom file path)
        3. Local .kollab/system_prompt/default.md (project override)
        4. Global ~/.kollab/system_prompt/default.md
        5. Fallback to minimal default

        Returns:
            Fully rendered system prompt with all <trender> tags executed.
        """
        self._build_count += 1
        utils = self._get_utils()

        # Stable-prefix mode: strip per-session-volatile trenders so the system
        # message is byte-identical across sessions (oMLX restores its on-disk
        # KV cache from the shared prefix). Volatiles are re-emitted per turn via
        # build_volatile_context() on the injection rail.
        stable = self.config.get(
            "kollabor.llm.system_prompt.stable_prefix", True
        )

        # Get event_bus from agent_manager for hub trender tags
        event_bus = (
            getattr(self.agent_manager, "event_bus", None)
            if self.agent_manager
            else None
        )

        # Check if we have an active agent with a system prompt
        if self.agent_manager:
            agent_prompt = self.agent_manager.get_system_prompt(skip_volatile=stable)
            if agent_prompt:
                # Render <trender> tags in agent prompt
                # Get agent's directory for correct section path resolution
                active_agent = self.agent_manager.get_active_agent()
                agent_path = active_agent.directory if active_agent else None
                base_prompt = utils["render_system_prompt"](
                    agent_prompt,
                    timeout=5,
                    base_path=agent_path,
                    event_bus=event_bus,
                    profile_manager=self.profile_manager,
                    conversation_logger=self.conversation_logger,
                    skip_volatile=stable,
                )
                logger.info(
                    f"System prompt build #{self._build_count} "
                    f"(rebuilds: {self._rebuild_count}, "
                    f"session: {self._session_id or 'unknown'}, "
                    f"source: agent:{self.agent_manager.active_agent_name})"
                )
                prompt_parts = [base_prompt]
                return self._finalize_system_prompt(prompt_parts)

        # Ensure system prompts are initialized (copies global to local if needed)
        utils["initialize_system_prompt"]()

        # Load base prompt (checks env vars and files in priority order)
        base_prompt = utils["get_system_prompt_content"]()

        # Render <trender> tags BEFORE building the full prompt
        # Set base_path to the system prompt's directory for correct section resolution
        prompt_path = utils["get_system_prompt_path"]()
        base_prompt = utils["render_system_prompt"](
            base_prompt,
            timeout=5,
            base_path=prompt_path.parent,
            event_bus=event_bus,
            profile_manager=self.profile_manager,
            conversation_logger=self.conversation_logger,
            skip_volatile=stable,
        )

        logger.info(
            f"System prompt build #{self._build_count} "
            f"(rebuilds: {self._rebuild_count}, "
            f"session: {self._session_id or 'unknown'}, "
            f"source: file:{prompt_path.name})"
        )

        prompt_parts = [base_prompt]
        return self._finalize_system_prompt(prompt_parts)

    def build_volatile_context(self) -> str:
        """Render the per-turn volatile context stripped from the stable prefix.

        Returns the session-context (date/git/cwd/probes), the live hub blocks
        (identity/roster/vault/work_queue) and active_llm — exactly what build()
        omits when stable_prefix is on — wrapped as one ``[context]`` block for
        the user-turn injection rail. Returns "" when stable_prefix is off (that
        content is already inline) or when nothing renders.
        """
        if not self.config.get(
            "kollabor.llm.system_prompt.stable_prefix", True
        ):
            return ""

        utils = self._get_utils()
        event_bus = (
            getattr(self.agent_manager, "event_bus", None)
            if self.agent_manager
            else None
        )

        # Re-emit the same installed session-context template the stable build
        # strips, so date/git/cwd/probes reach the model verbatim (and fresh).
        parts: List[str] = []
        base_path = None
        try:
            from kollabor_config.config_utils import get_global_agents_dir

            sess = (
                get_global_agents_dir()
                / "_base"
                / "sections"
                / "01-session-context.md"
            )
            if sess.exists():
                parts.append(
                    '<trender type="include" path="01-session-context.md" />'
                )
                base_path = sess.parent
        except Exception as e:
            logger.debug(f"Volatile session-context unavailable: {e}")

        # Live hub state + active model info (also stripped from the prefix).
        parts += [
            '<trender type="hub_identity" />',
            '<trender type="hub_roster" />',
            '<trender type="hub_vault" />',
            '<trender type="hub_work_queue" />',
            '<trender type="active_llm" />',
        ]

        # ponytail: re-runs the session-context shell probes every turn (5s cap
        # each). Fine at current turn rates; if latency shows, cache the probe
        # output per session and re-run only date/git.
        rendered = utils["render_system_prompt"](
            "\n".join(parts),
            timeout=5,
            base_path=base_path,
            event_bus=event_bus,
            profile_manager=self.profile_manager,
            conversation_logger=self.conversation_logger,
            skip_volatile=False,
        ).strip()

        return f"[context]\n{rendered}\n" if rendered else ""

    def rebuild(self, conversation_history: List[ConversationMessage]) -> bool:
        """Rebuild the system prompt and update conversation history.

        Call this after skills are loaded/unloaded to update the system message
        with the new prompt content including active skills.

        Args:
            conversation_history: The conversation history list to update in place.

        Returns:
            True if system prompt was rebuilt successfully.
        """
        self._rebuild_count += 1
        try:
            new_prompt = self.build()

            # Update the first message in conversation history (system message)
            if conversation_history:
                first_msg = conversation_history[0]
                if first_msg.role == "system":
                    conversation_history[0] = ConversationMessage(
                        role="system", content=new_prompt
                    )
                    logger.info(
                        f"System prompt rebuild #{self._rebuild_count} complete "
                        f"(total builds: {self._build_count}, "
                        f"session: {self._session_id or 'unknown'}, "
                        f"history_len: {len(conversation_history)})"
                    )
                    return True

            logger.warning(
                f"System prompt rebuild #{self._rebuild_count} — no system message found to update"
            )
            return False

        except Exception as e:
            logger.error(
                f"System prompt rebuild #{self._rebuild_count} failed: {e}", exc_info=True
            )
            return False

    def _get_tree_output(self) -> str:
        """Get project directory tree output."""
        try:
            result = subprocess.run(
                [
                    "tree",
                    "-I",
                    "__pycache__|*.pyc|.git|.venv|venv|node_modules",
                    "-L",
                    "3",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path.cwd(),
            )
            if result.returncode == 0:
                return result.stdout
            else:
                # Fallback to basic ls if tree is not available
                result = subprocess.run(
                    ["ls", "-la"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=Path.cwd(),
                )
                return (
                    result.stdout
                    if result.returncode == 0
                    else "Could not get directory listing"
                )
        except Exception as e:
            logger.warning(f"Failed to get tree output: {e}")
            return "Could not get directory listing"

    def _finalize_system_prompt(self, prompt_parts: List[str]) -> str:
        """Finalize system prompt by adding common sections.

        Args:
            prompt_parts: List of prompt parts (base prompt should be first)

        Returns:
            Complete system prompt string
        """
        # Add project structure if enabled
        include_structure = self.config.get(
            "kollabor.llm.system_prompt.include_project_structure", True
        )
        if include_structure:
            tree_output = self._get_tree_output()
            prompt_parts.append(f"## Project Structure\n```\n{tree_output}\n```")

        # Add attachment files
        attachment_files = self.config.get(
            "kollabor.llm.system_prompt.attachment_files", []
        )
        for filename in attachment_files:
            file_path = Path.cwd() / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    prompt_parts.append(f"## {filename}\n```markdown\n{content}\n```")
                    logger.debug(f"Attached file: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")

        # Auto-include AGENTS.md if it exists in project root
        agents_md = Path.cwd() / "AGENTS.md"
        if agents_md.exists():
            try:
                content = agents_md.read_text(encoding="utf-8")
                prompt_parts.append(f"## Agent Instructions\n{content}")
                logger.debug("Auto-included AGENTS.md from project root")
            except Exception as e:
                logger.warning(f"Failed to read AGENTS.md: {e}")

        # Add custom prompt files
        custom_files = self.config.get(
            "kollabor.llm.system_prompt.custom_prompt_files", []
        )
        for filename in custom_files:
            file_path = Path.cwd() / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    prompt_parts.append(
                        f"## Custom Instructions ({filename})\n{content}"
                    )
                    logger.debug(f"Added custom prompt: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to read custom prompt {filename}: {e}")

        # Add plugin system prompt additions
        plugin_additions = self._get_plugin_system_prompt_additions()
        for addition in plugin_additions:
            prompt_parts.append(addition)

        # Add registry-generated tool reference (coexistence mode)
        tool_ref = self._get_registry_tool_reference()
        if tool_ref:
            prompt_parts.append(tool_ref)

        # Add lazy MCP tool summaries (compact list, not full schemas)
        mcp_summaries = self._get_mcp_tool_summaries()
        if mcp_summaries:
            prompt_parts.append(mcp_summaries)

        # Add shell aliases only after lazy session load has populated them
        if self._shell_alias_prompt:
            prompt_parts.append(self._shell_alias_prompt)
            logger.info("Added cached shell aliases to system prompt")

        # Add closing statement
        prompt_parts.append(
            "This is the codebase and context for our session. You now have full project awareness."
        )

        return "\n\n".join(prompt_parts)

    def _get_registry_tool_reference(self) -> Optional[str]:
        """Generate tool reference markdown from the unified tool registry.

        Returns None if registry is disabled or unavailable.
        When enabled, generates docs from ToolDefinitions instead of
        relying on static markdown files in tool-reference/.

        Enabled by default. Set kollabor.tool_registry.use_registry
        to False to fall back to static markdown files.
        """
        try:
            use_registry = self.config.get(
                "kollabor.tool_registry.use_registry", True
            )
            if not use_registry:
                return None

            # Determine which tools the active agent has access to
            allowed_tools = None
            if self.agent_manager:
                active_agent = self.agent_manager.get_active_agent()
                if active_agent:
                    from kollabor_agent.runtime import get_agent_tool_scope

                    allowed_tools = get_agent_tool_scope(active_agent)

            from kollabor_agent.tool_generators.markdown import render_for_bundle
            from kollabor_agent.tool_registry import get_registry

            registry = get_registry()

            if allowed_tools:
                return render_for_bundle(allowed_tools, registry=registry)
            else:
                # No bundle scoping — render all tools
                all_names = [t.name for t in registry.list()]
                return render_for_bundle(all_names, registry=registry)
        except Exception as e:
            logger.debug(f"Registry tool reference unavailable: {e}")
            return None

    def _get_mcp_tool_summaries(self) -> Optional[str]:
        """Generate compact MCP tool summaries for the system prompt.

        When lazy MCP injection is enabled, MCP tools from external servers
        are listed as one-line summaries grouped by server, instead of having
        their full schemas injected. The agent can then use tool-search and
        tool-load (on-demand loading) to discover and activate specific tools.

        Returns None if:
        - Lazy MCP injection is disabled in config
        - MCP integration is not available
        - No MCP tools are registered

        Returns:
            Markdown section with compact MCP tool summaries, or None.
        """
        try:
            lazy_enabled = self.config.get(
                "kollabor.llm.lazy_mcp_tools", True
            )
            if not lazy_enabled:
                return None

            if not self.mcp_integration:
                return None

            tool_registry = getattr(self.mcp_integration, "tool_registry", {})
            if not tool_registry:
                return None

            # Group tools by server
            by_server: Dict[str, List[tuple]] = {}
            for tool_name, tool_info in tool_registry.items():
                if not tool_info.get("enabled", True):
                    continue
                server = tool_info.get("server", "unknown")
                definition = tool_info.get("definition", {})
                description = definition.get("description", "")
                # Truncate description to one line, max 80 chars
                if len(description) > 80:
                    description = description[:77] + "..."
                by_server.setdefault(server, []).append(
                    (tool_name, description)
                )

            if not by_server:
                return None

            total = sum(len(tools) for tools in by_server.values())
            lines = [
                f"## MCP Tools ({total} available, use tool-search/tool-load to activate)",
                "",
            ]

            for server in sorted(by_server.keys()):
                tools = by_server[server]
                lines.append(f"### {server} ({len(tools)} tools)")
                lines.append("")
                for tool_name, desc in sorted(tools):
                    if desc:
                        lines.append(f"- `{tool_name}` — {desc}")
                    else:
                        lines.append(f"- `{tool_name}`")
                lines.append("")

            # Remove trailing blank line
            while lines and lines[-1] == "":
                lines.pop()

            logger.info(
                f"Lazy MCP injection: {total} tools from {len(by_server)} servers "
                f"(summaries only — full schemas loaded on demand)"
            )
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"MCP tool summaries unavailable: {e}")
            return None

    def _get_plugin_system_prompt_additions(self) -> List[str]:
        """Get system prompt additions from all plugins.

        Queries each plugin that implements get_system_prompt_addition()
        and collects their additions.

        Returns:
            List of system prompt addition strings
        """
        additions: List[str] = []

        if not self._plugin_instances:
            return additions

        for plugin_name, plugin_instance in self._plugin_instances.items():
            if hasattr(plugin_instance, "get_system_prompt_addition"):
                try:
                    addition = plugin_instance.get_system_prompt_addition()
                    if addition:
                        additions.append(addition)
                        logger.debug(
                            f"Plugin '{plugin_name}' added system prompt content"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to get system prompt addition from '{plugin_name}': {e}"
                    )

        return additions

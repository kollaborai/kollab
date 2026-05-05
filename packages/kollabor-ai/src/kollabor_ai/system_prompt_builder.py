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
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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

    def __init__(self, config, agent_manager=None, util_imports=None, profile_manager=None, conversation_logger=None):
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
                If None, will import from kollabor_agent (default behavior)
            profile_manager: ProfileManager for reading active model/provider info (optional)
        """
        self.config = config
        self.agent_manager = agent_manager
        self.profile_manager = profile_manager
        self.conversation_logger = conversation_logger
        self._util_imports = util_imports or {}

        # Plugin instances reference (set after plugins are loaded)
        self._plugin_instances: Optional[Dict[str, Any]] = None

        # Build/rebuild counters
        self._build_count = 0
        self._rebuild_count = 0
        self._session_id: Optional[str] = None

    def _get_utils(self):
        """Lazy import kollabor utils if not provided."""
        if self._util_imports:
            return self._util_imports

        # Import from kollabor_config (kollabor-specific dependency)
        from kollabor_agent import format_aliases_for_prompt
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
        }
        return self._util_imports

    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID for logging context."""
        self._session_id = session_id

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

        # Get event_bus from agent_manager for hub trender tags
        event_bus = (
            getattr(self.agent_manager, "event_bus", None)
            if self.agent_manager
            else None
        )

        # Check if we have an active agent with a system prompt
        if self.agent_manager:
            agent_prompt = self.agent_manager.get_system_prompt()
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
        )

        logger.info(
            f"System prompt build #{self._build_count} "
            f"(rebuilds: {self._rebuild_count}, "
            f"session: {self._session_id or 'unknown'}, "
            f"source: file:{prompt_path.name})"
        )

        prompt_parts = [base_prompt]
        return self._finalize_system_prompt(prompt_parts)

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

        # Add shell aliases if interactive shell is enabled
        if self.config.get("terminal.interactive_shell", False):
            utils = self._get_utils()
            alias_content = utils["format_aliases_for_prompt"]()
            if alias_content:
                prompt_parts.append(alias_content)
                logger.info("Added shell aliases to system prompt")

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
                if active_agent and hasattr(active_agent, 'config'):
                    allowed_tools = active_agent.config.get("tools")

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

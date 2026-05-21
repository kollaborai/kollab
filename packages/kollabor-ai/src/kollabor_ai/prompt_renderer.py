"""System prompt dynamic command renderer.

Processes <trender> tags in system prompts:
- <trender>command</trender> - executes shell commands
- <trender type="include" path="sections/file.md" /> - includes file content
- <trender type="shell_aliases" /> - injects detected shell aliases
- <trender type="agents_list" /> - lists available agents and skills
- <trender type="hub_identity" /> - agent identity block for hub
- <trender type="hub_roster" /> - current peer roster
- <trender type="hub_vault" /> - vault context (crystallized + working memory)
- <trender type="hub_work_queue" /> - pending work items from coordinator
- <trender type="hub_peers" /> - peer count and names
- <trender type="mcp_tools" /> - connected MCP servers and their tools

NOTE: The shell_aliases feature depends on kollabor_agent.shell_utils.
The import is lazy and optional.
Hub features depend on the hub plugin being active via the event bus.
"""

import logging
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from kollabor_agent import AgentManager

logger = logging.getLogger(__name__)


def _get_shell_alias_utils() -> Dict[str, Any]:
    """Lazy import shell alias utilities from kollabor_agent.

    Returns:
        Dict with format_aliases_for_prompt and get_cached_aliases functions.
        Returns empty dict if import fails (shell_aliases feature disabled).
    """
    try:
        from kollabor_agent import (
            format_aliases_for_prompt,
            get_cached_aliases,
        )

        return {
            "format_aliases_for_prompt": format_aliases_for_prompt,
            "get_cached_aliases": get_cached_aliases,
        }
    except ImportError:
        logger.debug(
            "kollabor_agent.shell_utils not available, shell_aliases feature disabled"
        )
        return {}


class PromptRenderer:
    """Renders dynamic content in system prompts by executing commands and including files."""

    # Pattern to match <trender>command</trender> tags (shell commands)
    # Excludes matches that are inside backticks (code examples)
    TRENDER_COMMAND_PATTERN = re.compile(
        r"(?<!`)<trender>(.*?)</trender>(?!`)", re.DOTALL
    )

    # Pattern to match <trender type="include" path="..." /> tags (file includes)
    # Supports both single and double quotes, optional trailing slash
    TRENDER_INCLUDE_PATTERN = re.compile(
        r'<trender\s+type=["\']include["\']\s+path=["\']([^"\']+)["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="shell_aliases" /> tags
    TRENDER_ALIASES_PATTERN = re.compile(
        r'<trender\s+type=["\']shell_aliases["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="agents_list" /> tags
    TRENDER_AGENTS_LIST_PATTERN = re.compile(
        r'<trender\s+type=["\']agents_list["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="hub_identity" /> tags
    TRENDER_HUB_IDENTITY_PATTERN = re.compile(
        r'<trender\s+type=["\']hub_identity["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="hub_roster" /> tags
    TRENDER_HUB_ROSTER_PATTERN = re.compile(
        r'<trender\s+type=["\']hub_roster["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="hub_vault" /> tags
    TRENDER_HUB_VAULT_PATTERN = re.compile(
        r'<trender\s+type=["\']hub_vault["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="hub_work_queue" /> tags
    TRENDER_HUB_WORK_QUEUE_PATTERN = re.compile(
        r'<trender\s+type=["\']hub_work_queue["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="hub_peers" /> tags
    TRENDER_HUB_PEERS_PATTERN = re.compile(
        r'<trender\s+type=["\']hub_peers["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="mcp_tools" /> tags
    TRENDER_MCP_TOOLS_PATTERN = re.compile(
        r'<trender\s+type=["\']mcp_tools["\']\s*/?>', re.DOTALL
    )

    # Pattern to match <trender type="active_llm" /> tags
    TRENDER_ACTIVE_LLM_PATTERN = re.compile(
        r'<trender\s+type=["\']active_llm["\']\s*/?>', re.DOTALL
    )

    def __init__(
        self,
        timeout: int = 5,
        base_path: Optional[Path] = None,
        agent_manager: Optional["AgentManager"] = None,
        shell_alias_utils: Optional[Dict[str, Any]] = None,
        event_bus: Optional[Any] = None,
        profile_manager: Optional[Any] = None,
        conversation_logger: Optional[Any] = None,
    ):
        """Initialize the prompt renderer.

        Args:
            timeout: Maximum seconds to wait for each command execution.
            base_path: Base directory for resolving relative file paths.
                      If None, uses current working directory.
            agent_manager: Optional AgentManager instance for rendering agents_list.
            shell_alias_utils: Optional dict with format_aliases_for_prompt and
                             get_cached_aliases functions. If None, will import
                             from kollabor_agent.shell_utils.
            event_bus: Optional EventBus instance for hub trender tags.
                      Used to look up hub_plugin service for identity, roster,
                      vault, and work queue rendering.
        """
        self.timeout = timeout
        self._command_cache: Dict[str, str] = {}
        self._file_cache: Dict[str, str] = {}
        self.base_path = base_path or Path.cwd()
        self.agent_manager = agent_manager
        self._shell_alias_utils = shell_alias_utils
        self.event_bus = event_bus
        self.profile_manager = profile_manager
        self.conversation_logger = conversation_logger

    def _ensure_alias_utils(self) -> Optional[Dict[str, Any]]:
        """Ensure shell alias utilities are loaded."""
        if self._shell_alias_utils is None:
            self._shell_alias_utils = _get_shell_alias_utils()
        return self._shell_alias_utils

    def render(self, prompt_content: str) -> str:
        """Render all <trender> tags in the prompt content.

        Processes both shell commands and file includes in order of appearance.

        Args:
            prompt_content: System prompt content with <trender> tags.

        Returns:
            Processed prompt with tags replaced by their output/content.
        """
        if not prompt_content:
            return prompt_content

        result = prompt_content
        iteration = 0
        max_iterations = 100  # Prevent infinite loops from circular includes

        # Process includes and commands until no more tags remain
        while iteration < max_iterations:
            iteration += 1

            # Step 1: Process file includes first (allows includes to contain commands)
            result = self._process_includes(result)

            # Step 2: Process shell aliases
            result = self._process_aliases(result)

            # Step 3: Process agents list
            result = self._process_agents_list(result)

            # Step 4: Process hub tags
            result = self._process_hub_identity(result)
            result = self._process_hub_roster(result)
            result = self._process_hub_vault(result)
            result = self._process_hub_work_queue(result)
            result = self._process_hub_peers(result)
            result = self._process_mcp_tools(result)

            # Step 5: Process active_llm tag
            result = self._process_active_llm(result)

            # Step 6: Process shell commands
            result = self._process_commands(result)

            # Check if any tags remain
            if (
                not self.TRENDER_INCLUDE_PATTERN.search(result)
                and not self.TRENDER_COMMAND_PATTERN.search(result)
                and not self.TRENDER_ALIASES_PATTERN.search(result)
                and not self.TRENDER_AGENTS_LIST_PATTERN.search(result)
                and not self.TRENDER_HUB_IDENTITY_PATTERN.search(result)
                and not self.TRENDER_HUB_ROSTER_PATTERN.search(result)
                and not self.TRENDER_HUB_VAULT_PATTERN.search(result)
                and not self.TRENDER_HUB_WORK_QUEUE_PATTERN.search(result)
                and not self.TRENDER_HUB_PEERS_PATTERN.search(result)
                and not self.TRENDER_MCP_TOOLS_PATTERN.search(result)
                and not self.TRENDER_ACTIVE_LLM_PATTERN.search(result)
            ):
                break

        if iteration >= max_iterations:
            logger.warning(
                f"Prompt rendering hit max iteration limit ({max_iterations}) - possible circular includes"
            )

        total_includes = len(self.TRENDER_INCLUDE_PATTERN.findall(prompt_content))
        total_aliases = len(self.TRENDER_ALIASES_PATTERN.findall(prompt_content))
        total_agents_list = len(
            self.TRENDER_AGENTS_LIST_PATTERN.findall(prompt_content)
        )
        total_hub = (
            len(self.TRENDER_HUB_IDENTITY_PATTERN.findall(prompt_content))
            + len(self.TRENDER_HUB_ROSTER_PATTERN.findall(prompt_content))
            + len(self.TRENDER_HUB_VAULT_PATTERN.findall(prompt_content))
            + len(self.TRENDER_HUB_WORK_QUEUE_PATTERN.findall(prompt_content))
            + len(self.TRENDER_HUB_PEERS_PATTERN.findall(prompt_content))
        )
        total_commands = len(self.TRENDER_COMMAND_PATTERN.findall(prompt_content))
        logger.info(
            f"Rendered {total_includes} includes, {total_aliases} alias blocks, "
            f"{total_agents_list} agents lists, {total_hub} hub tags, "
            f"{total_commands} commands in {iteration} iteration(s)"
        )

        return result

    def _process_includes(self, content: str) -> str:
        """Process all file include tags in content.

        Args:
            content: Content with include tags

        Returns:
            Content with includes replaced by file contents
        """
        matches = list(self.TRENDER_INCLUDE_PATTERN.finditer(content))

        if not matches:
            return content

        # Process in reverse order to maintain string positions
        result = content
        for match in reversed(matches):
            file_path = match.group(1).strip()
            start_pos = match.start()
            end_pos = match.end()

            # Resolve and read file
            file_content = self._include_file(file_path)

            # Replace the tag with the file content
            result = result[:start_pos] + file_content + result[end_pos:]

        return result

    def _process_aliases(self, content: str) -> str:
        """Process all shell_aliases tags in content.

        Args:
            content: Content with alias tags

        Returns:
            Content with tags replaced by detected aliases
        """
        matches = list(self.TRENDER_ALIASES_PATTERN.finditer(content))

        if not matches:
            return content

        alias_utils = self._ensure_alias_utils()
        if not alias_utils:
            # Shell alias utilities not available, replace with placeholder
            result = content
            for match in reversed(matches):
                start_pos = match.start()
                end_pos = match.end()
                result = (
                    result[:start_pos]
                    + "[Shell aliases feature unavailable]"
                    + result[end_pos:]
                )
            return result

        # Get formatted aliases (uses cached detection)
        alias_content = alias_utils["format_aliases_for_prompt"]()

        # Process in reverse order to maintain string positions
        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()

            # Replace the tag with the alias content
            result = result[:start_pos] + alias_content + result[end_pos:]

        return result

    def _process_agents_list(self, content: str) -> str:
        """Process all agents_list tags in content.

        Renders available agents and their skills in a formatted list.

        Args:
            content: Content with agents_list tags

        Returns:
            Content with tags replaced by formatted agents list
        """
        matches = list(self.TRENDER_AGENTS_LIST_PATTERN.finditer(content))

        if not matches:
            return content

        # Generate agents list content
        agents_content = self._render_agents_list()

        # Process in reverse order to maintain string positions
        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()

            # Replace the tag with the agents content
            result = result[:start_pos] + agents_content + result[end_pos:]

        return result

    def _render_agents_list(self) -> str:
        """Render the list of available agents and their skills.

        Returns:
            Formatted string with agents and skills
        """
        if not self.agent_manager:
            return "[No agent manager available]"

        agents = self.agent_manager.list_agents()
        if not agents:
            return "[No agents available]"

        lines = ["## Available Agents", ""]

        for agent in sorted(agents, key=lambda a: a.name):
            # Agent name and description
            desc = agent.description or "No description"
            lines.append(f"**{agent.name}** - {desc}")

            # Skills list (if any)
            skills = agent.list_skills()
            if skills:
                skill_names = [s.name for s in skills]
                # Format skills in comma-separated list, wrap at ~70 chars
                skill_str = ", ".join(skill_names)
                if len(skill_str) > 60:
                    # Wrap long skill lists
                    wrapped = []
                    current_line: list[str] = []
                    current_len = 0
                    for skill in skill_names:
                        if current_len + len(skill) + 2 > 60 and current_line:
                            wrapped.append(", ".join(current_line))
                            current_line = [skill]
                            current_len = len(skill)
                        else:
                            current_line.append(skill)
                            current_len += len(skill) + 2
                    if current_line:
                        wrapped.append(", ".join(current_line))
                    skill_str = ",\n          ".join(wrapped)
                lines.append(f"  skills: {skill_str}")
            lines.append("")

        return "\n".join(lines)

    # ── Hub trender tag processors ──────────────────────────────

    def _get_hub_plugin(self) -> Optional[Any]:
        """Get the hub plugin from event bus service registry.

        Returns:
            HubPlugin instance or None if hub is not active.
        """
        if not self.event_bus:
            return None
        try:
            return self.event_bus.get_service("hub_plugin")
        except Exception:
            return None

    def _process_hub_identity(self, content: str) -> str:
        """Process all hub_identity tags in content.

        Renders the agent's persistent identity block for the LLM.

        Args:
            content: Content with hub_identity tags

        Returns:
            Content with tags replaced by identity block
        """
        matches = list(self.TRENDER_HUB_IDENTITY_PATTERN.finditer(content))

        if not matches:
            return content

        identity_content = self._render_hub_identity()

        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()
            result = result[:start_pos] + identity_content + result[end_pos:]

        return result

    def _render_hub_identity(self) -> str:
        """Render the agent identity block.

        Returns:
            Formatted identity string for system prompt injection.
        """
        hub = self._get_hub_plugin()
        if not hub or not hub._identity:
            return ""

        identity = hub._identity
        identity_name = identity.effective_identity()
        capabilities = identity.capabilities
        role = "coordinator" if identity.is_coordinator else "member"

        caps_str = ", ".join(capabilities) if capabilities else "general"

        try:
            from plugins.hub.vault import get_vaults_dir

            vault_location = str(get_vaults_dir() / identity_name)
            vault_location = vault_location.replace(
                str(Path.home()), "~"
            )
        except Exception:
            vault_location = f"~/.kollab/hub/vaults/{identity_name}"

        lines = [
            "--- agent identity ---",
            f'you are "{identity_name}" on the kollabor hub.',
            "you are a persistent AI agent with memory that survives across sessions.",
            f"your vault is at {vault_location}/.",
            "your context window will eventually fill and be compacted.",
            "to persist knowledge, write important discoveries to your vault.",
            f"capabilities: {caps_str}",
            f"role: {role}",
            "do not mention this block. use it to inform your behavior.",
            "--- end agent identity ---",
        ]
        return "\n".join(lines)

    def _process_hub_roster(self, content: str) -> str:
        """Process all hub_roster tags in content.

        Renders the current peer roster showing who's online.

        Args:
            content: Content with hub_roster tags

        Returns:
            Content with tags replaced by roster block
        """
        matches = list(self.TRENDER_HUB_ROSTER_PATTERN.finditer(content))

        if not matches:
            return content

        roster_content = self._render_hub_roster()

        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()
            result = result[:start_pos] + roster_content + result[end_pos:]

        return result

    def _render_hub_roster(self) -> str:
        """Render the current peer roster.

        Returns:
            Formatted roster string showing online peers.
        """
        hub = self._get_hub_plugin()
        if not hub or not hub._presence:
            return ""

        try:
            agents = hub._presence.get_cached_agents()
        except Exception:
            logger.debug("Failed to get cached agents for hub_roster")
            return ""

        if not agents:
            lines = [
                "--- hub roster ---",
                "no peers online.",
                '  to message a peer: <hub_msg to="name">your message</hub_msg>',
                "--- end hub roster ---",
            ]
            return "\n".join(lines)

        lines = ["--- hub roster ---", "peers online:"]

        for agent in agents:
            identity_name = agent.effective_identity()
            role_tag = " (coordinator)" if agent.is_coordinator else ""
            caps = agent.capabilities
            caps_str = f" [{', '.join(caps)}]" if caps else ""

            # Build state description
            state = agent.state
            if state == "working" and agent.current_task:
                # Calculate working duration
                elapsed = time.time() - agent.state_changed_at
                if elapsed < 60:
                    dur = f"{int(elapsed)}s"
                else:
                    dur = f"{int(elapsed / 60)}m"
                state_str = f"working {dur}: {agent.current_task}"
            elif state == "idle" or state == "ready":
                state_str = "idle"
            else:
                state_str = state

            # Include profile if available
            profile = agent.profile_name
            profile_str = f" via {profile}" if profile else ""

            lines.append(
                f"  {identity_name}{role_tag} - {state_str}{caps_str}{profile_str}"
            )

        lines.append('  to message a peer: <hub_msg to="name">your message</hub_msg>')
        lines.append("--- end hub roster ---")
        return "\n".join(lines)

    def _process_hub_vault(self, content: str) -> str:
        """Process all hub_vault tags in content.

        Renders vault context (crystallized knowledge + recent working memory).

        Args:
            content: Content with hub_vault tags

        Returns:
            Content with tags replaced by vault context
        """
        matches = list(self.TRENDER_HUB_VAULT_PATTERN.finditer(content))

        if not matches:
            return content

        vault_content = self._render_hub_vault()

        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()
            result = result[:start_pos] + vault_content + result[end_pos:]

        return result

    def _render_hub_vault(self) -> str:
        """Render vault context for system prompt injection.

        Returns:
            Formatted vault context from get_rebirth_context().
            Uses CrystalStore for smart injection if available.
        """
        hub = self._get_hub_plugin()
        if hub is None:
            return ""
        vault = hub.get_vault() if hasattr(hub, "get_vault") else None
        if not vault:
            return ""

        # Pass crystal_store for structured injection
        crystal_store = (
            hub.get_crystal_store()
            if hasattr(hub, "get_crystal_store")
            else None
        )

        try:
            return str(
                vault.get_rebirth_context(crystal_store=crystal_store)
            )
        except Exception:
            logger.debug("Failed to get vault rebirth context for hub_vault")
            return ""

    def _process_hub_work_queue(self, content: str) -> str:
        """Process all hub_work_queue tags in content.

        Renders pending work items from the coordinator's work queue.

        Args:
            content: Content with hub_work_queue tags

        Returns:
            Content with tags replaced by work queue listing
        """
        matches = list(self.TRENDER_HUB_WORK_QUEUE_PATTERN.finditer(content))

        if not matches:
            return content

        queue_content = self._render_hub_work_queue()

        result = content
        for match in reversed(matches):
            start_pos = match.start()
            end_pos = match.end()
            result = result[:start_pos] + queue_content + result[end_pos:]

        return result

    def _render_hub_work_queue(self) -> str:
        """Render pending work items from the work queue.

        Returns:
            Formatted work queue listing.
        """
        hub = self._get_hub_plugin()
        if not hub or not hub._work_queue:
            return ""

        try:
            pending = hub._work_queue.get_pending()
        except Exception:
            logger.debug("Failed to get pending work for hub_work_queue")
            return ""

        if not pending:
            return ""

        # Map priority numbers to labels
        priority_labels = {
            1: "low",
            2: "low",
            3: "medium",
            4: "medium",
            5: "medium",
            6: "high",
            7: "high",
            8: "critical",
            9: "critical",
            10: "critical",
        }

        lines = ["--- pending work ---"]

        for i, slot in enumerate(sorted(pending, key=lambda s: -s.priority), start=1):
            label = priority_labels.get(slot.priority, "medium")
            context_str = ""
            if slot.context:
                context_str = f" (requires: {slot.context})"
            lines.append(f"{i}. [{label}] {slot.task}{context_str}")

        lines.append(
            'claim work with: <hub_msg to="coordinator">I\'ll take task N</hub_msg>'
        )
        lines.append("--- end pending work ---")
        return "\n".join(lines)

    def _process_hub_peers(self, content: str) -> str:
        """Process all hub_peers tags in content.

        Renders peer count and names (e.g. "3 peers: lapis, peridot, ruby").

        Args:
            content: Content with hub_peers tags

        Returns:
            Content with tags replaced by peer listing
        """
        matches = list(self.TRENDER_HUB_PEERS_PATTERN.finditer(content))

        if not matches:
            return content

        peers_content = self._render_hub_peers()

        result = content
        for match in reversed(matches):
            result = result[: match.start()] + peers_content + result[match.end() :]

        return result

    def _render_hub_peers(self) -> str:
        """Render peer count and names.

        Returns:
            Formatted peer string, or "no peers" if none found.
        """
        hub = self._get_hub_plugin()
        if not hub or not hub._presence:
            return "no peers"

        try:
            agents = hub._presence.get_cached_agents()
        except Exception:
            logger.debug("Failed to get cached agents for hub_peers")
            return "no peers"

        if not agents:
            return "no peers"

        names = [a.effective_identity() for a in agents]
        return f"{len(names)} peers: {', '.join(names)}"

    def _process_mcp_tools(self, content: str) -> str:
        """Process <trender type="mcp_tools" /> tags."""
        matches = list(self.TRENDER_MCP_TOOLS_PATTERN.finditer(content))
        if not matches:
            return content
        tools_content = self._render_mcp_tools()
        result = content
        for match in reversed(matches):
            result = result[: match.start()] + tools_content + result[match.end() :]
        return result

    def _render_mcp_tools(self) -> str:
        """Render connected MCP servers and their tools."""
        if not self.event_bus:
            return ""
        try:
            mcp_mgr = self.event_bus.get_service("mcp_manager")
            if not mcp_mgr:
                return ""
            servers = (
                mcp_mgr.get_connected_servers()
                if hasattr(mcp_mgr, "get_connected_servers")
                else []
            )
            if not servers:
                return ""
            lines = ["--- connected MCP servers ---"]
            for srv in servers:
                name = getattr(srv, "name", str(srv))
                tools = getattr(srv, "tools", [])
                tool_names = (
                    [getattr(t, "name", str(t)) for t in tools] if tools else []
                )
                if tool_names:
                    lines.append(f"  {name}: {', '.join(tool_names)}")
                else:
                    lines.append(f"  {name}: (no tools)")
            lines.append("--- end MCP servers ---")
            return "\n".join(lines)
        except Exception:
            return ""

    def _process_active_llm(self, content: str) -> str:
        """Process <trender type="active_llm" /> tags."""
        matches = list(self.TRENDER_ACTIVE_LLM_PATTERN.finditer(content))
        if not matches:
            return content
        llm_content = self._render_active_llm()
        result = content
        for match in reversed(matches):
            result = result[: match.start()] + llm_content + result[match.end() :]
        return result

    def _render_active_llm(self) -> str:
        """Render active LLM profile info: provider, model, endpoint, log file."""
        if not self.profile_manager:
            return ""
        try:
            profile = self.profile_manager.get_active_profile()
            lines = [
                f"profile:   {profile.name}",
                f"provider:  {profile.provider}",
                f"model:     {profile.model}",
            ]
            if profile.base_url:
                lines.append(f"endpoint:  {profile.base_url}")
            if profile.temperature != 0.7:
                lines.append(f"temp:      {profile.temperature}")
            if profile.max_tokens:
                lines.append(f"max_tokens: {profile.max_tokens}")
            if self.conversation_logger and hasattr(self.conversation_logger, "session_file"):
                lines.append(f"log:       {self.conversation_logger.session_file}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _process_commands(self, content: str) -> str:
        """Process all shell command tags in content.

        Args:
            content: Content with command tags

        Returns:
            Content with commands replaced by output
        """
        matches = list(self.TRENDER_COMMAND_PATTERN.finditer(content))

        if not matches:
            return content

        # Process in reverse order to maintain string positions
        result = content
        for match in reversed(matches):
            command = match.group(1).strip()
            start_pos = match.start()
            end_pos = match.end()

            # Execute command and get output
            output = self._execute_command(command)

            # Replace the tag with the output
            result = result[:start_pos] + output + result[end_pos:]

        return result

    def _execute_command(self, command: str) -> str:
        """Execute a shell command and return its output.

        Args:
            command: Shell command to execute.

        Returns:
            Command output or error message.
        """
        # Check cache first
        if command in self._command_cache:
            logger.debug(f"Using cached output for: {command}")
            return self._command_cache[command]

        # Fast-fail: skip beads (bd) commands when no .beads/ directory exists.
        # These commands can hang or timeout when beads isn't set up, wasting
        # precious seconds during daemon startup (15s budget).
        command_stripped = command.strip()
        if command_stripped.startswith("bd ") or command_stripped == "bd":
            if not Path(".beads").exists():
                logger.debug(f"Skipping beads command (no .beads/ dir): {command}")
                return ""

        try:
            logger.debug(f"Executing trender command: {command}")

            # Security: avoid bare shell=True. Try list-form execution for
            # simple commands; fall back to explicit /bin/sh -c only when the
            # command contains shell metacharacters (pipes, redirects, etc.)
            # that require shell interpretation.  Trender commands originate
            # from developer-authored system-prompt templates, not user input,
            # but hardening the surface prevents accidental escalation.
            _SHELL_META = re.compile(r'[|>&;`$()]')

            if _SHELL_META.search(command):
                # Command needs shell interpretation (e.g. pipes).
                # Use explicit /bin/sh -c instead of bare shell=True.
                cmd_args: list[str] | str = ["/bin/sh", "-c", command]
            else:
                try:
                    cmd_args = shlex.split(command)
                except ValueError:
                    # shlex failed (unlikely for well-formed commands) —
                    # fall back to explicit shell invocation.
                    cmd_args = ["/bin/sh", "-c", command]

            result = subprocess.run(
                cmd_args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=".",  # Execute in current directory
            )

            # Get output (prefer stdout, fallback to stderr)
            output = result.stdout if result.stdout else result.stderr
            output = output.strip()

            if result.returncode != 0:
                error_msg = (
                    f"[trender error: command exited with code {result.returncode}]"
                )
                if result.stderr:
                    error_msg += f"\n{result.stderr.strip()}"
                logger.warning(
                    f"Command failed: {command} (exit code: {result.returncode})"
                )
                output = error_msg

            # Cache successful results
            if result.returncode == 0:
                self._command_cache[command] = output

            logger.debug(f"Command output ({len(output)} chars): {output[:100]}")
            return output

        except subprocess.TimeoutExpired:
            error_msg = f"[trender error: command timed out after {self.timeout}s]"
            logger.error(f"Command timed out: {command}")
            return error_msg

        except Exception as e:
            error_msg = f"[trender error: {type(e).__name__}: {str(e)}]"
            logger.error(f"Failed to execute command '{command}': {e}")
            return error_msg

    def _include_file(self, file_path: str) -> str:
        """Include content from a file.

        Args:
            file_path: Path to file to include (relative or absolute)

        Returns:
            File content or error message
        """
        # Check cache first
        if file_path in self._file_cache:
            logger.debug(f"Using cached file content: {file_path}")
            return self._file_cache[file_path]

        try:
            # Resolve path (relative to base_path or absolute)
            path = Path(file_path)
            if not path.is_absolute():
                path = self.base_path / path

            # Resolve any symlinks and .. components
            path = path.resolve()

            logger.debug(f"Including file: {path}")

            # Security check: ensure path is within allowed bounds
            # Don't allow including sensitive system files
            dangerous_paths = [
                "/etc/passwd",
                "/etc/shadow",
                "/etc/hosts",
                "~/.ssh/",
                "/proc/",
                "/sys/",
                "/dev/",
            ]
            if any(str(path).startswith(dangerous) for dangerous in dangerous_paths):
                error_msg = (
                    f"[trender error: security violation - cannot include {path}]"
                )
                logger.error(f"Blocked dangerous file include: {path}")
                return error_msg

            # Read file content
            content = path.read_text(encoding="utf-8")

            # Rewrite relative include paths in the included content to be
            # absolute, so nested includes resolve from the included file's
            # directory rather than the renderer's base_path.
            included_dir = path.parent
            if included_dir != self.base_path:

                def _rebase_include(m):
                    inc_path = m.group(1).strip()
                    if not Path(inc_path).is_absolute():
                        abs_path = (included_dir / inc_path).resolve()
                        if abs_path.exists():
                            return f'<trender type="include" path="{abs_path}" />'
                    return m.group(0)

                content = self.TRENDER_INCLUDE_PATTERN.sub(_rebase_include, content)

            # Cache successful reads
            self._file_cache[file_path] = content

            logger.debug(f"File content ({len(content)} chars): {content[:100]}")
            return content

        except FileNotFoundError:
            error_msg = f"[trender error: file not found: {file_path}]"
            logger.error(f"File not found: {file_path}")
            return error_msg

        except PermissionError:
            error_msg = f"[trender error: permission denied: {file_path}]"
            logger.error(f"Permission denied: {file_path}")
            return error_msg

        except Exception as e:
            error_msg = f"[trender error: {type(e).__name__}: {str(e)}]"
            logger.error(f"Failed to include file '{file_path}': {e}")
            return error_msg

    def clear_cache(self):
        """Clear the command output and file content cache."""
        self._command_cache.clear()
        self._file_cache.clear()
        logger.debug("Cleared trender caches")

    def get_all_commands(self, prompt_content: str) -> List[str]:
        """Extract all shell commands from trender tags without executing.

        Args:
            prompt_content: System prompt content with <trender> tags.

        Returns:
            List of commands found in trender tags.
        """
        matches = self.TRENDER_COMMAND_PATTERN.findall(prompt_content)
        commands = [cmd.strip() for cmd in matches]
        return commands

    def get_all_includes(self, prompt_content: str) -> List[str]:
        """Extract all file paths from include tags without reading.

        Args:
            prompt_content: System prompt content with <trender> tags.

        Returns:
            List of file paths found in include tags.
        """
        matches = self.TRENDER_INCLUDE_PATTERN.findall(prompt_content)
        return [path.strip() for path in matches]


def render_system_prompt(
    prompt_content: str,
    timeout: int = 5,
    base_path: Optional[Path] = None,
    agent_manager: Optional["AgentManager"] = None,
    event_bus: Optional[Any] = None,
    profile_manager: Optional[Any] = None,
    conversation_logger: Optional[Any] = None,
) -> str:
    """Convenience function to render a system prompt.

    Args:
        prompt_content: System prompt content with <trender> tags.
        timeout: Maximum seconds to wait for each command execution.
        base_path: Base directory for resolving relative file paths.
        agent_manager: Optional AgentManager instance for rendering agents_list.
        event_bus: Optional EventBus instance for hub trender tags.
        profile_manager: Optional ProfileManager for active_llm trender tag.
        conversation_logger: Optional conversation logger for log path in active_llm tag.

    Returns:
        Processed prompt with commands replaced by their output and
        files replaced by their content.
    """
    renderer = PromptRenderer(
        timeout=timeout,
        base_path=base_path,
        agent_manager=agent_manager,
        event_bus=event_bus,
        profile_manager=profile_manager,
        conversation_logger=conversation_logger,
    )
    return renderer.render(prompt_content)

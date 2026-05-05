"""Save conversation plugin for exporting chat transcripts.

Provides /save command to export conversations to file or clipboard
in various formats (transcript, markdown, jsonl).
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_events.models import CommandCategory, CommandDefinition, CommandMode
from kollabor_tui.visual_effects import AgnosterSegment

logger = logging.getLogger(__name__)


class SaveConversationPlugin:
    """Plugin for saving conversations to file or clipboard."""

    def __init__(
        self,
        name: str = "save_conversation",
        event_bus=None,
        renderer=None,
        config=None,
    ) -> None:
        """Initialize the save conversation plugin.

        Args:
            name: Plugin name (default: "save_conversation")
            event_bus: Event bus instance
            renderer: Terminal renderer instance
            config: Configuration manager instance
        """
        self.name = name
        self.version = "1.0.0"
        self.description = "Save conversations to file or clipboard"
        self.enabled = True
        self.logger = logger

        # Store injected dependencies
        self.event_bus = event_bus
        self.renderer = renderer
        self.config_manager = config

        # References to be set during initialize()
        self.command_registry: Optional[Any] = None
        self.llm_service = None
        self.config = None

    async def initialize(self, event_bus, config, **kwargs) -> None:
        """Initialize the plugin and register commands.

        Args:
            event_bus: Application event bus.
            config: Configuration manager.
            **kwargs: Additional initialization parameters.
        """
        try:
            self.event_bus = event_bus
            self.config_manager = config
            self.renderer = kwargs.get("renderer", self.renderer)
            self.config = config
            self.command_registry = kwargs.get("command_registry")
            self.llm_service = kwargs.get("llm_service")

            if not self.command_registry:
                self.logger.warning(
                    "No command registry provided, /save not registered"
                )
                return

            if not self.llm_service:
                self.logger.warning("No LLM service provided, /save may not work")

            # Register the /save command
            self._register_commands()  # type: ignore[union-attr]

            self.logger.info("Save conversation plugin initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing save conversation plugin: {e}")
            raise

    def _get_status_content(self) -> List[str]:
        """Get save conversation status (agnoster style)."""
        try:
            seg = AgnosterSegment()
            seg.add_lime("Save", "dark")
            seg.add_cyan("/save", "dark")
            seg.add_neutral("file | clipboard | json | md", "mid")
            return [seg.render()]

        except Exception as e:
            self.logger.error(f"Error getting status content: {e}")
            seg = AgnosterSegment()
            seg.add_neutral("Save: Error", "dark")
            return [seg.render()]

    def _register_commands(self) -> None:
        """Register all plugin commands."""
        from kollabor_events.models import SubcommandInfo

        save_command = CommandDefinition(
            name="save",
            description="Save conversation to file or clipboard",
            handler=self._handle_save_command,
            plugin_name=self.name,
            aliases=["export", "transcript"],
            mode=CommandMode.INSTANT,
            category=CommandCategory.CONVERSATION,
            icon="[SAVE]",
            subcommands=[
                SubcommandInfo(
                    "transcript", "[clipboard|both|local]", "Plain text format"
                ),
                SubcommandInfo("markdown", "[clipboard|both|local]", "Markdown format"),
                SubcommandInfo("jsonl", "[clipboard|both|local]", "JSON lines format"),
                SubcommandInfo("clipboard", "", "Copy to clipboard (default format)"),
                SubcommandInfo("both", "", "Save to file and clipboard"),
                SubcommandInfo("local", "", "Save to current directory"),
            ],
        )

        if self.command_registry:
            self.command_registry.register_command(save_command)
            self.logger.info("Registered /save command")

    async def _handle_save_command(self, command) -> str:
        """Handle the /save command.

        Formatting is delegated to StateService.save_conversation so that
        both local and attach modes produce identical output and share
        a single source of truth for the conversation being saved.

        Args:
            command: SlashCommand object with parsed command data.

        Returns:
            Status message about the save operation.
        """
        try:
            # Parse arguments: /save [format] [destination]
            # Formats: transcript (default), markdown, jsonl
            # Destinations: file (default), clipboard, both

            args = command.args if hasattr(command, "args") else []

            # Get configuration
            if self.config is None:
                return "Error: Configuration not available"
            save_format = self.config.get(
                "plugins.save_conversation.default_format", "transcript"
            )
            save_to = self.config.get(
                "plugins.save_conversation.default_destination", "file"
            )
            auto_timestamp = self.config.get(
                "plugins.save_conversation.auto_timestamp", True
            )
            output_dir = self.config.get(
                "plugins.save_conversation.output_directory", "logs/transcripts"
            )

            # Parse command arguments
            # Smart detection: if first arg is a destination, use default format
            valid_formats = ["transcript", "markdown", "jsonl", "raw"]
            valid_destinations = ["file", "clipboard", "both", "local"]

            if len(args) >= 1:
                first_arg = args[0].lower()
                if first_arg in valid_destinations:
                    # First arg is destination, keep default format
                    save_to = first_arg
                elif first_arg in valid_formats:
                    save_format = first_arg
                    if len(args) >= 2:
                        save_to = args[1].lower()
                else:
                    formats = ", ".join(valid_formats)
                    destinations = ", ".join(valid_destinations)
                    return (
                        f"Error: Invalid argument '{first_arg}'. "
                        f"Use format ({formats}) or destination ({destinations})"
                    )

            # Validate destination
            if save_to not in valid_destinations:
                return f"Error: Invalid destination '{save_to}'. Use: {', '.join(valid_destinations)}"

            # Local saves to current working directory
            if save_to == "local":
                output_dir = "."
                save_to = "file"

            # === State-aware conversation save (phase 2 of daemon transparency) ===
            # Ask the StateService for the formatted content. In local mode
            # this runs in-process; in attach mode it calls the daemon via
            # RPC and returns the daemon's conversation. Either way, the
            # formatting and the source-of-truth for messages live behind
            # a single interface.
            state_service = None
            if self.event_bus and hasattr(self.event_bus, "get_service"):
                state_service = self.event_bus.get_service("state_service")

            formatted_content = None

            if state_service is not None:
                try:
                    formatted_content = await state_service.save_conversation(
                        save_format
                    )
                except ValueError as e:
                    return f"Error: {e}"
                except Exception as e:
                    self.logger.error(f"state service save failed: {e}")
                    formatted_content = None  # fall through to fallback

            # Fallback: format directly from llm_service.conversation_history
            # when state_service is unavailable (e.g. silent registration
            # failure in attach mode, or running before full init).
            if formatted_content is None:
                self.logger.warning(
                    "state_service unavailable or failed; using llm_service "
                    "conversation_history fallback for /save"
                )
                formatted_content = self._format_conversation_fallback(
                    save_format
                )
                if formatted_content is None:
                    return "No conversation to save"

            # Empty formatted output means empty conversation.
            if not formatted_content:
                return "No conversation to save"

            # Save to file
            saved_path = None
            if save_to in ["file", "both"]:
                saved_path = self._save_to_file(
                    formatted_content, output_dir, save_format, auto_timestamp
                )

            # Copy to clipboard
            if save_to in ["clipboard", "both"]:
                self._copy_to_clipboard(formatted_content)

            # Return status message
            if save_to == "both":
                return f"Conversation saved to {saved_path} and copied to clipboard"
            elif save_to == "clipboard":
                return "Conversation copied to clipboard"
            else:
                return f"Conversation saved to {saved_path}"

        except Exception as e:
            self.logger.error(f"Error handling /save command: {e}")
            return f"Error saving conversation: {str(e)}"

    def _save_to_file(
        self, content: str, output_dir: str, format_type: str, auto_timestamp: bool
    ) -> Path:
        """Save content to file.

        Args:
            content: Content to save.
            output_dir: Output directory path.
            format_type: Format type for file extension.
            auto_timestamp: Whether to add timestamp to filename.

        Returns:
            Path to saved file.
        """
        # Create output directory
        from kollabor_config.config_utils import get_config_directory

        # Handle paths
        if output_dir == ".":
            # Local: save to current working directory
            save_dir = Path.cwd()
        elif output_dir.startswith("/"):
            # Absolute path
            save_dir = Path(output_dir)
        else:
            # Relative path under config directory
            config_dir = get_config_directory()
            save_dir = config_dir / output_dir

        save_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if auto_timestamp:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"conversation_{timestamp}"
        else:
            filename = "conversation"

        # Add extension based on format
        if format_type == "raw":
            filename += ".json"
        elif format_type == "jsonl":
            filename += ".jsonl"
        elif format_type == "markdown":
            filename += ".md"
        else:
            filename += ".txt"

        filepath = save_dir / filename

        # Write to file
        filepath.write_text(content, encoding="utf-8")

        self.logger.info(f"Saved conversation to: {filepath}")
        return filepath

    def _copy_to_clipboard(self, content: str) -> bool:
        """Copy content to system clipboard.

        Args:
            content: Content to copy.

        Returns:
            True if successful, False otherwise.
        """
        try:
            # Try pbcopy (macOS)
            try:
                process = subprocess.Popen(
                    ["pbcopy"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(input=content.encode("utf-8"))
                self.logger.info("Copied to clipboard using pbcopy")
                return True
            except FileNotFoundError:
                pass

            # Try xclip (Linux)
            try:
                process = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(input=content.encode("utf-8"))
                self.logger.info("Copied to clipboard using xclip")
                return True
            except FileNotFoundError:
                pass

            # Try xsel (Linux alternative)
            try:
                process = subprocess.Popen(
                    ["xsel", "--clipboard", "--input"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(input=content.encode("utf-8"))
                self.logger.info("Copied to clipboard using xsel")
                return True
            except FileNotFoundError:
                pass

            # Try wl-copy (Wayland)
            try:
                process = subprocess.Popen(
                    ["wl-copy"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(input=content.encode("utf-8"))
                self.logger.info("Copied to clipboard using wl-copy")
                return True
            except FileNotFoundError:
                pass

            self.logger.warning(
                "No clipboard utility found (pbcopy, xclip, xsel, wl-copy)"
            )
            return False

        except Exception as e:
            self.logger.error(f"Error copying to clipboard: {e}")
            return False

    def _format_conversation_fallback(self, format_type: str) -> Optional[str]:
        """Format conversation directly from llm_service when state_service is unavailable.

        Reads conversation_history from llm_service and formats using the
        same logic as LocalStateService. Returns None if no conversation
        or no llm_service available.
        """
        if not self.llm_service:
            self.logger.warning(
                "no llm_service available for fallback formatting"
            )
            return None

        history = getattr(self.llm_service, "conversation_history", None)
        if not history:
            return None

        # Build simple message dicts from ConversationMessage objects
        messages = []
        for m in history:
            role = getattr(m, "role", "") or ""
            content = getattr(m, "content", "") or ""
            ts = getattr(m, "timestamp", None)
            timestamp = ""
            if ts is not None:
                try:
                    timestamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                except Exception:
                    timestamp = ""
            messages.append({
                "role": role,
                "content": content,
                "timestamp": timestamp,
            })

        if not messages:
            return None

        if format_type == "transcript":
            return self._format_fallback_transcript(messages)
        elif format_type == "markdown":
            return self._format_fallback_markdown(messages)
        elif format_type == "jsonl":
            return self._format_fallback_jsonl(messages)
        elif format_type == "raw":
            return self._format_fallback_raw(messages)
        else:
            return self._format_fallback_transcript(messages)

    def _format_fallback_transcript(self, messages: list) -> str:
        lines = []
        for msg in messages:
            role = msg["role"] or "unknown"
            content = msg["content"] or ""
            if role == "system":
                lines.append("--- system_prompt ---")
            elif role == "user":
                lines.append("\n--- user ---")
            elif role == "assistant":
                lines.append("\n--- llm ---")
            else:
                lines.append(f"\n--- {role} ---")
            lines.append(content)
        return "\n".join(lines)

    def _format_fallback_markdown(self, messages: list) -> str:
        lines = ["# Conversation Transcript", ""]
        if messages:
            lines.append(f"**Started:** {messages[0].get('timestamp', '')}")
            lines.append(f"**Ended:** {messages[-1].get('timestamp', '')}")
            lines.append(f"**Messages:** {len(messages)}")
            lines.append("")
            lines.append("---")
            lines.append("")
        for i, msg in enumerate(messages):
            role = msg["role"] or "unknown"
            content = msg["content"] or ""
            ts = msg.get("timestamp", "")
            if role == "system":
                lines.append("## System Prompt")
                lines.append("")
                lines.append(f"```\n{content}\n```")
            elif role == "user":
                lines.append(f"## User Message {i+1}")
                if ts:
                    lines.append(f"*{ts}*")
                lines.append("")
                lines.append(content)
            elif role == "assistant":
                lines.append(f"## Assistant Response {i+1}")
                if ts:
                    lines.append(f"*{ts}*")
                lines.append("")
                lines.append(content)
            else:
                lines.append(f"## {role.title()} {i+1}")
                lines.append("")
                lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    def _format_fallback_jsonl(self, messages: list) -> str:
        import json as _json
        lines = []
        for msg in messages:
            msg_dict = {
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": msg.get("timestamp") or datetime.now().isoformat(),
            }
            lines.append(_json.dumps(msg_dict))
        return "\n".join(lines)

    def _format_fallback_raw(self, messages: list) -> str:
        import json as _json
        api_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        model = "unknown"
        temperature = 0.7
        try:
            profile_manager = None
            if self.event_bus and hasattr(self.event_bus, "get_service"):
                profile_manager = self.event_bus.get_service("profile_manager")
            if profile_manager is None and hasattr(self, "config"):
                profile_manager = getattr(self.config, "profile_manager", None)
            if profile_manager:
                profile = profile_manager.get_active_profile()
                if profile:
                    if hasattr(profile, "get_model"):
                        model = profile.get_model() or "unknown"
                    if hasattr(profile, "get_temperature"):
                        temperature = float(profile.get_temperature())
        except Exception:
            pass
        payload = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "message_count": len(messages),
                "format": "raw_api_payload",
            },
        }
        return _json.dumps(payload, indent=2, ensure_ascii=False)


    async def shutdown(self) -> None:
        """Shutdown the plugin and cleanup resources."""
        try:
            self.logger.info("Save conversation plugin shutdown completed")
        except Exception as e:
            self.logger.error(f"Error shutting down save conversation plugin: {e}")

    async def register_hooks(self) -> None:
        """Register event hooks for the plugin."""
        pass

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration for save conversation plugin."""
        return {
            "plugins": {
                "save_conversation": {
                    "enabled": True,
                    "default_format": "transcript",
                    "default_destination": "file",
                    "auto_timestamp": True,
                    "output_directory": "logs/transcripts",
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Save Conversation",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.save_conversation.enabled",
                    "help": "Enable the save conversation plugin",
                },
                {
                    "type": "dropdown",
                    "label": "Default Format",
                    "config_path": "plugins.save_conversation.default_format",
                    "options": ["transcript", "markdown", "jsonl", "raw"],
                    "help": "Default export format for /save command",
                },
                {
                    "type": "dropdown",
                    "label": "Default Destination",
                    "config_path": "plugins.save_conversation.default_destination",
                    "options": ["file", "clipboard", "both", "local"],
                    "help": "Default save destination",
                },
                {
                    "type": "checkbox",
                    "label": "Auto Timestamp",
                    "config_path": "plugins.save_conversation.auto_timestamp",
                    "help": "Add timestamp to saved filenames",
                },
                {
                    "type": "text_input",
                    "label": "Output Directory",
                    "config_path": "plugins.save_conversation.output_directory",
                    "help": "Directory for saved transcripts (relative to config dir)",
                },
            ],
        }

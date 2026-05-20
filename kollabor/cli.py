"""CLI entry point for kollab command."""

import argparse
import asyncio
import difflib
import logging
import re
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .commands.registry import SlashCommandRegistry

# Fix encoding for Windows to support Unicode characters
if sys.platform == "win32":
    # Set UTF-8 mode for stdin/stdout/stderr
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure") and sys.stdin.isatty():
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    # Also set console output code page to UTF-8
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)  # UTF-8
        kernel32.SetConsoleCP(65001)  # UTF-8 for input too
    except Exception:
        pass  # Ignore if this fails

# Import from the same directory
from .application import TerminalLLMChat
from .logging import setup_bootstrap_logging
from .version import __version__

STOP_GRACE_SECONDS = 1.5
STOP_TERM_SECONDS = 1.0


def parse_timeout(timeout_str: str) -> int:
    """Parse timeout string into seconds.

    Args:
        timeout_str: Timeout string like "30s", "2min", "1h"

    Returns:
        Timeout in seconds

    Raises:
        ValueError: If timeout format is invalid
    """
    timeout_str = timeout_str.strip().lower()

    # Match pattern like "30s", "2min", "1h"
    match = re.match(
        r"^(\d+(?:\.\d+)?)(s|sec|second|seconds|m|min|minute|minutes|h|hour|hours)$",
        timeout_str,
    )
    if not match:
        raise ValueError(
            f"Invalid timeout format: {timeout_str}. Use format like '30s', '2min', or '1h'"
        )

    value = float(match.group(1))
    unit = match.group(2)

    # Convert to seconds
    if unit in ("s", "sec", "second", "seconds"):
        return int(value)
    elif unit in ("m", "min", "minute", "minutes"):
        return int(value * 60)
    elif unit in ("h", "hour", "hours"):
        return int(value * 3600)
    else:
        raise ValueError(f"Unknown time unit: {unit}")


def build_cli_help_text(command_registry: "SlashCommandRegistry") -> str:
    """Build help text showing CLI-invocable commands.

    Args:
        command_registry: Registry with all registered commands.

    Returns:
        Formatted help text for epilog.
    """
    # Get all non-hidden CLI commands
    cli_commands = []
    for cmd_def in command_registry.get_all_commands():
        if not cmd_def.cli_hidden:
            cli_commands.append(cmd_def)

    if not cli_commands:
        return ""

    # Build help text
    lines = ["\nslash commands (use as --command args...):"]

    # Limit to 50 commands to prevent OOM
    shown = cli_commands[:50]
    for cmd in shown:
        # Build flag list
        if cmd.aliases:
            flags = f"  --{cmd.name}, --{', --'.join(cmd.aliases[:2])}"
        else:
            flags = f"  --{cmd.name}"

        # Format help
        desc = (
            cmd.description[:60] + "..."
            if len(cmd.description) > 60
            else cmd.description
        )
        if len(flags) > 24:
            lines.append(flags)
            lines.append(f"{'':24}{desc}")
        else:
            lines.append(f"{flags:24}{desc}")

    if len(cli_commands) > 50:
        lines.append(f"\n  ... and {len(cli_commands) - 50} more commands")

    return "\n".join(lines)


def print_full_help(
    parser, command_registry: "SlashCommandRegistry", start_time: Optional[float] = None
) -> None:
    """Print full help including plugin commands.

    Args:
        parser: The argparse parser instance.
        command_registry: Registry with all registered commands.
        start_time: Optional start time for elapsed time display.
    """
    # Print standard argparse help
    parser.print_help()

    # Print plugin commands section
    cli_commands = []
    for cmd_def in command_registry.get_all_commands():
        if not cmd_def.cli_hidden:
            cli_commands.append(cmd_def)

    if cli_commands:
        print("\nPlugin Commands (invoke as kollab --<command>):")
        for cmd in sorted(cli_commands, key=lambda c: c.name):
            if cmd.aliases:
                aliases = f" ({', '.join(cmd.aliases[:2])})"
            else:
                aliases = ""
            name_part = f"--{cmd.name}{aliases}"
            desc = (
                cmd.description[:50] + "..."
                if len(cmd.description) > 50
                else cmd.description
            )
            print(f"  {name_part:36} {desc}")

    # Print render time
    if start_time is not None:
        import time

        elapsed = (time.perf_counter() - start_time) * 1000
        print(f"\n({elapsed:.0f}ms)")


def _unknown_command_error(cmd_name: str, available: list[str]) -> str:
    """Format an unknown CLI command error with a close-match hint."""
    known_cli_flags = {
        "hub",
        "org",
        "attach",
        "detached",
        "daemon",
        "agent",
        "profile",
        "project",
        "context",
        "update",
        "reset-config",
    }
    candidates = sorted(set(available) | known_cli_flags)
    available_flags = [f"--{c}" for c in candidates[:10]]
    message = f"Unknown command: --{cmd_name}\n"
    matches = difflib.get_close_matches(cmd_name, candidates, n=1, cutoff=0.6)
    if matches:
        message += f"Did you mean --{matches[0]}?\n"
    message += (
        f"Available commands: {', '.join(available_flags)}\n"
        f"Run 'kollab -h' to see all"
    )
    return message


def discover_plugin_args() -> tuple:
    """Discover plugins and collect their CLI arg registrations.

    Returns:
        Tuple of (plugin_classes, discovery) where:
        - plugin_classes: List of plugin class types that may register CLI args
        - discovery: PluginDiscovery object for reuse in app initialization
    """
    from pathlib import Path

    from kollabor_plugins.discovery import PluginDiscovery

    # Determine plugins directory (same logic as application.py)
    package_dir = Path(__file__).parent.parent
    plugins_dir = package_dir / "plugins"
    if not plugins_dir.exists():
        plugins_dir = Path.cwd() / "plugins"

    discovery = PluginDiscovery(plugins_dir)
    plugin_classes = discovery.discover_classes_only()

    return plugin_classes, discovery


def parse_arguments(
    plugin_classes: Optional[list] = None,
    argv: Optional[list[str]] = None,
    command_registry: Optional["SlashCommandRegistry"] = None,
):
    """Parse command-line arguments including plugin-registered args.

    Args:
        plugin_classes: Optional list of plugin classes for CLI arg registration.
        argv: Optional list of argument strings to parse (defaults to sys.argv).
        command_registry: Optional command registry for CLI slash command support.

    Returns:
        Parsed arguments namespace
    """
    # Check for -h/--help early - we'll handle it after plugins are initialized
    # so we can show plugin commands in help output
    args_to_check = argv if argv is not None else sys.argv[1:]

    # Pre-argparse intercept: `kollab --hub -h`, `--hub --help`, `--hub help`,
    # or a bare `--hub` with nothing after it should ALL print the hub help
    # and exit cleanly. Without this, argparse's nargs="+" on --hub errors
    # out ("expected at least one argument") because our early help-strip
    # removes -h before argparse sees it.
    if "--hub" in args_to_check:
        hub_idx = args_to_check.index("--hub")
        next_token = (
            args_to_check[hub_idx + 1] if hub_idx + 1 < len(args_to_check) else None
        )
        hub_help_tokens = {"-h", "--help", "help"}
        if next_token is None or next_token in hub_help_tokens:
            # Rewrite argv so argparse sees `--hub help` -- the hub handler
            # will recognize the "help" subcommand and print usage.
            if next_token is None:
                args_to_check = (
                    args_to_check[: hub_idx + 1]
                    + ["help"]
                    + args_to_check[hub_idx + 1 :]
                )
            elif next_token in ("-h", "--help"):
                args_to_check = (
                    args_to_check[: hub_idx + 1]
                    + ["help"]
                    + args_to_check[hub_idx + 2 :]
                )
            argv = args_to_check

    help_requested = "-h" in args_to_check or "--help" in args_to_check

    # Remove help flags so argparse doesn't handle them immediately
    if help_requested:
        args_to_check = [a for a in args_to_check if a not in ("-h", "--help")]
        argv = args_to_check

    # Build CLI help epilog if registry available
    cli_help = ""
    if command_registry:
        cli_help = build_cli_help_text(command_registry)

    parser = argparse.ArgumentParser(
        description="Kollab - Terminal-based LLM chat interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,  # We handle -h ourselves to show plugin commands
        epilog=f"""
Examples:
  kollab                                    # Start interactive mode
  kollab "what is 1+1?"                     # Interactive mode with initial message
  kollab -p "what is 1+1?"                  # Pipe mode: process and exit
  kollab --timeout 30s -p "complex query"   # Pipe mode with custom timeout
  kollab --timeout 5min -p "long task"      # Pipe mode with custom timeout
  echo "hello" | kollab -p                  # Pipe mode from stdin
  cat file.txt | kollab -p --timeout 1h     # Process file with 1 hour timeout
  cat code.py | kollab "find issues" -p     # Combine stdin + query (stdin as context)
  git diff | kollab "write commit msg" -p   # Pipe git diff with instruction
  kollab --system-prompt my-prompt.md       # Use custom system prompt
  kollab --agent lint-editor               # Use specific agent
  kollab -a lint-editor                    # Short form for agent
  kollab --profile claude                  # Use specific LLM profile
  kollab -a myagent -s coding -s review    # Agent with multiple skills
  kollab --agent myagent --skill coding    # Agent with skill (long form)
  kollab --agent coder --as lapis          # Run coder bundle under hub identity 'lapis'
  kollab --agent coder --as lapis --detached  # Same, detached (backgrounded agent)
  kollab -d                                # Short form for --detached
  kollab --attach lapis                     # Attach to agent 'lapis' and see its output
  kollab --reset-config                    # Reset configs to defaults with updated profiles
  kollab --update                          # Update this source checkout from Git
  kollab --sub list                         # Execute /sub list and exit
  kollab --sub list --stay                  # Execute /sub list, then interactive mode

Telegram bridge setup (run inside interactive mode):
  /hub bridge setup                         # Guided setup: token, chat ID, test message
  /hub bridge enable                        # Start receiving Telegram messages
  /hub bridge disable                       # Stop the bridge
  /hub notify channel telegram              # Set notification channel to Telegram
  /hub notify enable                        # Enable idle/status notifications
  Environment variables (alternative to interactive setup):
    KOLLAB_HUB_BRIDGE_TOKEN=<bot-token>   # From @BotFather on Telegram
    KOLLAB_HUB_BRIDGE_CHAT_ID=<chat-id>   # From @userinfobot on Telegram
{cli_help}
        """,
    )

    # Manual help argument (we handle it after plugins are initialized)
    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        dest="show_help",
        help="Show this help message and exit",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version number and exit",
    )

    parser.add_argument(
        "query",
        nargs="?",
        help=(
            "Message to send (interactive mode) or instruction (pipe mode). "
            "In pipe mode, combines with stdin content."
        ),
    )

    parser.add_argument(
        "-p",
        "--pipe",
        action="store_true",
        help="Pipe mode: process input and exit (requires -p flag or stdin redirect)",
    )

    parser.add_argument(
        "--timeout",
        type=str,
        default="2min",
        help="Timeout for pipe mode processing (e.g., 30s, 2min, 1h). Default: 2min",
    )

    parser.add_argument(
        "--system-prompt",
        type=str,
        default=None,
        metavar="FILE",
        help="Use a custom system prompt file (e.g., --system-prompt my-prompt.md)",
    )

    parser.add_argument(
        "-a",
        "--agent",
        type=str,
        default=None,
        metavar="AGENT",
        help="Use a specific agent (e.g., --agent lint-editor)",
    )

    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        metavar="PROFILE",
        help="Use a specific LLM profile (e.g., --profile claude, --profile openai)",
    )

    parser.add_argument(
        "--as",
        dest="as_identity",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            "Hub designation for this agent (e.g., --as lapis). "
            "Pairs with --agent to run a bundle under a gem identity: "
            "'kollab --agent coder --as lapis'. Without --as, the hub "
            "picks a designation automatically."
        ),
    )

    parser.add_argument(
        "--project",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Override project root for hub siloing (defaults to git root "
            "or cwd). Path is encoded the same way as conversation storage."
        ),
    )

    parser.add_argument(
        "--attach",
        type=str,
        default=None,
        metavar="IDENTITY",
        help="Attach to a running agent and stream its output (read-only)",
    )

    parser.add_argument(
        "--context",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            "Conversation context to attach to (created if it doesn't exist). "
            "Only meaningful with --attach."
        ),
    )

    parser.add_argument(
        "--hub",
        nargs="*",
        default=None,
        metavar="CMD",
        help=(
            "Hub CLI: status, agents, stop <name|all>, capture <name> [lines], "
            "msg <name> <text>, broadcast <text>, user [name], on/off, "
            "org <name> [mission]. Pass '--hub help' for the full list. "
            "Telegram bridge and notifications are configured interactively "
            "via /hub bridge setup and /hub notify."
        ),
    )

    parser.add_argument(
        "--org",
        type=str,
        default=None,
        metavar="ORG",
        help="Launch an organization (e.g., --org engineering, --org startup)",
    )

    parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help="Save auto-created profile to global config (use with --profile for env-var profiles)",
    )

    parser.add_argument(
        "--local",
        action="store_true",
        default=False,
        help="With --save, save profile to local project config instead of global",
    )

    parser.add_argument(
        "--default",
        dest="make_default_profile",
        action="store_true",
        default=False,
        help=(
            "Set --profile as default for next startups "
            "(use --local to set project default instead of global)"
        ),
    )

    parser.add_argument(
        "--simple",
        action="store_true",
        default=False,
        help="Use simple text output (no fancy boxes or colors)",
    )

    parser.add_argument(
        "-d",
        "--detached",
        action="store_true",
        default=False,
        help="Run as a detached agent (interactive mode via piped stdin, implies --simple)",
    )

    parser.add_argument(
        "--daemon",
        action="store_true",
        default=False,
        help="Run as daemon + attach client (Ctrl+Z to detach, agent survives)",
    )

    parser.add_argument(
        "--no-daemon",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,  # Hidden, kept for compat
    )

    parser.add_argument(
        "-s",
        "--skill",
        type=str,
        action="append",
        default=None,
        metavar="SKILL",
        help="Load a skill for the active agent (can be used multiple times: -s skill1 -s skill2)",
    )

    parser.add_argument(
        "--font-dir",
        action="store_true",
        help="Print path to bundled Nerd Fonts directory and exit (for use with agg)",
    )

    parser.add_argument(
        "--reset-config",
        action="store_true",
        help="Reset global and local config.json to defaults with updated profiles",
    )

    parser.add_argument(
        "--update",
        action="store_true",
        help="Update this source checkout from Git and refresh the editable install",
    )

    parser.add_argument(
        "--login",
        type=str,
        choices=["openai"],
        default=None,
        metavar="PROVIDER",
        help="Login via OAuth (e.g., --login openai)",
    )

    # CLI slash command support
    parser.add_argument(
        "--stay",
        action="store_true",
        help="Stay in interactive mode after CLI command executes",
    )

    # Register plugin CLI arguments
    if plugin_classes:
        registered_args = set()  # Track registered argument names to detect conflicts
        for plugin_class in plugin_classes:
            if hasattr(plugin_class, "register_cli_args"):
                try:
                    # Track arguments before registration
                    before_count = len(parser._actions)
                    plugin_class.register_cli_args(parser)
                    # Check for conflicts by detecting new argument names
                    for action in parser._actions[before_count:]:
                        for arg_str in action.option_strings:
                            if arg_str in registered_args:
                                logging.warning(
                                    f"Plugin {plugin_class.__name__} argument conflict: "
                                    f"{arg_str} already registered by another plugin"
                                )
                            registered_args.add(arg_str)
                except Exception as e:
                    logging.warning(
                        f"Plugin {plugin_class.__name__} arg registration failed: {e}"
                    )

    # Parse known args, capture unknown as potential CLI commands
    args, unknown = parser.parse_known_args(argv)

    # Validate profile persistence flags
    if getattr(args, "make_default_profile", False) and not getattr(args, "profile", None):
        parser.error("--default requires --profile")

    # --project override: propagate to env BEFORE any hub code boots
    # (project_scope.resolve_project_root reads KOLLAB_PROJECT_ROOT).
    if getattr(args, "project", None):
        import os as _os

        _os.environ["KOLLAB_PROJECT_ROOT"] = str(args.project)

    # Store help request flag and parser for deferred help generation
    args._help_pending = help_requested or getattr(args, "show_help", False)
    args._parser = parser

    # Initialize CLI command storage
    args.cli_command = None
    args._unknown_args = []

    # Process unknown args as potential CLI commands
    if unknown:
        if unknown[0].startswith("--") and len(unknown[0]) > 2:
            cmd_name = unknown[0][2:]  # Strip '--'

            # Validate command name (prevent injection)
            if not cmd_name.replace("-", "").replace("_", "").isalnum():
                parser.error(f"Invalid command name: {cmd_name}")

            # IMPORTANT: Handle positional args that got split between query and unknown
            # due to argparse's greedy positional matching.
            #
            # Case 1: `kollab --permission trust`
            #   -> unknown=['--permission'], query='trust'
            #   -> CLI arg is 'trust', no message
            #
            # Case 2: `kollab "hello" --permission trust`
            #   -> unknown=['--permission', 'trust'], query='hello'
            #   -> CLI arg is 'trust', message is 'hello'
            #
            # Case 3: `kollab --permission trust --stay "hello"`
            #   -> unknown=['--permission', 'hello'], query='trust'
            #   -> CLI arg is 'trust', message is 'hello'
            #   -> Need to detect this by checking argv order!

            cmd_flag = unknown[0]
            non_flag_unknown = [a for a in unknown[1:] if not a.startswith("-")]

            if args.query and len(unknown) == 1:
                # Case 1: Just CLI flag, query is the CLI arg
                unknown.append(args.query)
                args.query = None
            elif args.query and non_flag_unknown:
                # Cases 2 or 3: Both query and extra positionals exist
                # Check original argv order to determine which is which
                argv_to_check = argv if argv is not None else sys.argv[1:]
                try:
                    cmd_pos = argv_to_check.index(cmd_flag)
                    query_pos = argv_to_check.index(args.query)

                    if query_pos > cmd_pos:
                        # Case 3: Query appears AFTER CLI flag in argv
                        # Collect contiguous positional args after cmd_flag from argv
                        # (these are all command args, not user messages)
                        cli_args = []
                        for i in range(cmd_pos + 1, len(argv_to_check)):
                            if argv_to_check[i].startswith("-"):
                                break
                            cli_args.append(argv_to_check[i])

                        unknown = [cmd_flag] + cli_args

                        # Any non-flag unknowns NOT in the contiguous block
                        # are user messages (e.g. after --stay)
                        cli_arg_set = set(cli_args)
                        leftover = [a for a in non_flag_unknown if a not in cli_arg_set]
                        args.query = leftover[0] if leftover else None
                    # else: Case 2 - query is BEFORE CLI flag, it's the user's message
                    # No changes needed, unknown already has the CLI arg
                except ValueError:
                    # Couldn't find positions, leave as-is
                    pass

            if command_registry:
                # Full processing with registry
                cmd_def = command_registry.get_command(cmd_name)

                if cmd_def:
                    if cmd_def.cli_hidden:
                        parser.error(
                            f"Command /{cmd_name} is not available from command line.\n"
                            f"Start kollabor in interactive mode and use /{cmd_name} instead."
                        )

                    # Sanitize arguments
                    sanitized_args = [shlex.quote(arg) for arg in unknown[1:]]

                    args.cli_command = {
                        "name": cmd_def.name,  # Use canonical name
                        "args": sanitized_args,
                        "raw": f"/{cmd_def.name} {' '.join(sanitized_args)}".strip(),
                        "requires_interactive": cmd_def.requires_interactive,
                        "command_def": cmd_def,
                    }
                else:
                    # Unknown command - show available commands
                    available = [
                        c.name
                        for c in command_registry.get_all_commands()
                        if not c.cli_hidden
                    ]
                    parser.error(_unknown_command_error(cmd_name, available))
            else:
                # No registry yet - store for later processing in application.py
                args._unknown_args = unknown
        else:
            # Unknown args that don't look like CLI commands
            parser.error(f"Unrecognized arguments: {' '.join(unknown)}")

    return args


def process_cli_command(
    args: argparse.Namespace, command_registry: "SlashCommandRegistry"
) -> Optional[dict]:
    """Process CLI command with command registry.

    Called from application.py once command registry is available.

    Args:
        args: Parsed arguments with potential _unknown_args.
        command_registry: Initialized command registry.

    Returns:
        CLI command dict if valid command found, None otherwise.

    Raises:
        ValueError: If command is invalid or not CLI-invocable.
    """
    # Check for stored unknown args
    unknown = getattr(args, "_unknown_args", [])
    if not unknown:
        return args.cli_command  # type: ignore[no-any-return, return-value]

    if not unknown[0].startswith("--") or len(unknown[0]) <= 2:
        raise ValueError(f"Unrecognized arguments: {' '.join(unknown)}")

    cmd_name = unknown[0][2:]  # Strip '--'

    # Validate command name
    if not cmd_name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid command name: {cmd_name}")

    # NOTE: Positional arg fixing already happened in parse_arguments()
    # when _unknown_args was stored. Don't re-apply the fix here.

    # Look up in registry
    cmd_def = command_registry.get_command(cmd_name)

    if not cmd_def:
        available = [
            c.name for c in command_registry.get_all_commands() if not c.cli_hidden
        ]
        raise ValueError(_unknown_command_error(cmd_name, available))

    if cmd_def.cli_hidden:
        raise ValueError(
            f"Command /{cmd_name} is not available from command line.\n"
            f"Start kollabor in interactive mode and use /{cmd_name} instead."
        )

    # Sanitize arguments
    sanitized_args = [shlex.quote(arg) for arg in unknown[1:]]

    return {
        "name": cmd_def.name,  # Use canonical name
        "args": sanitized_args,
        "raw": f"/{cmd_def.name} {' '.join(sanitized_args)}".strip(),
        "requires_interactive": cmd_def.requires_interactive,
        "command_def": cmd_def,
    }


def handle_early_plugin_args(args, plugin_classes: list) -> bool:
    """Let plugins handle args that should exit early.

    Args:
        args: Parsed command-line arguments.
        plugin_classes: List of plugin classes.

    Returns:
        True if a plugin requested early exit, False otherwise.
    """
    should_exit = False

    for plugin_class in plugin_classes:
        if hasattr(plugin_class, "handle_early_args"):
            try:
                result = plugin_class.handle_early_args(args)
                # Handle both old-style (bool) and new-style (tuple) returns
                if isinstance(result, tuple):
                    plugin_should_exit, output_message = result
                    if plugin_should_exit and output_message:
                        print(output_message)
                    should_exit = should_exit or plugin_should_exit
                elif result:  # Old-style bool return
                    should_exit = True
            except Exception as e:
                logging.error(
                    f"Plugin {plugin_class.__name__} early arg handler failed: {e}"
                )

    return should_exit


async def async_main() -> None:
    """Main async entry point for the application with proper error handling."""
    import time

    _start_time = time.perf_counter()

    # Check for help flag - use null logging to avoid file creation
    help_mode = "-h" in sys.argv or "--help" in sys.argv

    if help_mode:
        # Use null logging for help mode - no files created
        logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()])
    else:
        # Setup bootstrap logging before application starts
        setup_bootstrap_logging()

    logger = logging.getLogger(__name__)

    # Early plugin discovery for CLI args
    plugin_classes, discovery = discover_plugin_args()

    # Parse all args (core + plugin)
    args = parse_arguments(plugin_classes)
    args._start_time = _start_time  # For timing display in help

    # Handle early-exit args from plugins (like --capture)
    if handle_early_plugin_args(args, plugin_classes):
        return  # Plugin handled it and requested exit

    # Handle --reset-config: reset configs and exit
    if args.reset_config:
        from kollabor_config.config_utils import (
            get_global_config_path,
            get_local_config_path,
            initialize_config,
        )

        global_config = get_global_config_path()
        local_config = get_local_config_path()

        print("Resetting configuration files...")
        initialize_config(force=True)
        print("Configuration reset complete!")
        print(f"  - Global config: {global_config}")
        print(f"  - Local config:  {local_config}")
        return

    if args.update:
        from .updates.git_update import run_source_update

        result = run_source_update()
        print(result.message)
        if not result.success:
            sys.exit(1)
        return

    # Handle --font-dir: print font directory and exit
    if args.font_dir:
        try:
            from fonts import get_font_dir  # type: ignore[import-not-found]

            print(get_font_dir())
        except ImportError:
            # Fallback for development mode
            font_dir = Path(__file__).parent.parent / "fonts"
            if font_dir.exists():
                print(font_dir)
            else:
                print("Error: fonts directory not found", file=sys.stderr)
                sys.exit(1)
        return

    # Handle --login: run OAuth flow and exit (or continue to interactive)
    if args.login:
        if not sys.stdin.isatty():
            print("Error: --login requires an interactive terminal", file=sys.stderr)
            sys.exit(1)

        await _handle_cli_login(args.login)
        return

    # Handle --hub.
    # Bare `--hub` (or help) must launch full interactive TUI with hub plugin.
    # The pipe_mode path has proven extremely fragile. Force the interactive
    # path immediately and let the hub plugin print the roster banner.
    if args.hub is not None:
        sub = args.hub[0].lower() if args.hub else ""
        if sub in ("help", "-h", "--help"):
            _print_hub_help()
            return
        if sub == "":
            print("hub: launching interactive session with mesh enabled...")
            args.hub = None
            args.pipe = False
            # force hub plugin on for this session
            if hasattr(args, "_cli_args") and args._cli_args is not None:
                args._cli_args.hub = True
            # continue to full TUI below
        else:
            await _handle_cli_hub(args.hub)
            return

    # Handle --attach: boot full TUI app in proxy mode
    # (connects to remote agent's socket instead of local LLM)
    if args.attach:
        attach_identity = args.attach

    # Check if we have a CLI command or --help pending
    # These should bypass pipe mode detection.
    # --hub is explicitly treated as non-pipe (forces interactive TUI).
    has_cli_command = (
        getattr(args, "cli_command", None) is not None
        or getattr(args, "_unknown_args", [])
        or getattr(args, "_help_pending", False)
        or getattr(args, "hub", None) is not None
    )

    # --hub always forces interactive mode (zero-config multi-terminal workflow)
    # even if the parser turned it into hub=["help"]. This prevents the
    # "No input received from pipe" error on bare `kollab --hub`.
    if getattr(args, "hub", None) is not None:
        has_cli_command = True
        args.pipe = False

    # --detached renders full TUI (to /dev/null) so attach clients get rich output.
    # stdout/stderr are already redirected to /dev/null in cli_main().

    # Determine if we're in pipe mode and what the input is.
    # Pipe mode ONLY when:
    # 1. Explicit -p/--pipe flag is set, OR
    # 2. stdin is not a tty (redirected/piped input) AND no CLI command.
    # Attach/detached sessions are excluded here because they still need the
    # full interactive app lifecycle even when stdin is not a normal TTY.
    # EXCEPTION: --detached forces interactive mode even with piped stdin
    is_detached = getattr(args, "detached", False)
    is_attach = getattr(args, "attach", None) is not None
    pipe_mode_active = args.pipe or (
        not sys.stdin.isatty()
        and not has_cli_command
        and not is_detached
        and not is_attach
    )

    piped_input = None
    initial_message = None

    if pipe_mode_active:
        # Pipe mode: read from query arg and/or stdin, process and exit
        stdin_content = None
        query_text = None

        # Check for piped input from stdin
        if not sys.stdin.isatty():
            stdin_content = sys.stdin.read().strip()

        # Check for query argument
        if args.query:
            query_text = args.query.strip()

        # Combine both if present (stdin as context, query as instruction)
        if stdin_content and query_text:
            piped_input = f"{stdin_content}\n\n{query_text}"
        elif stdin_content:
            piped_input = stdin_content
        elif query_text:
            piped_input = query_text
        else:
            print("Error: No input received from pipe", file=sys.stderr)
            sys.exit(1)
    else:
        # Interactive mode: query arg becomes initial message
        if args.query:
            initial_message = args.query.strip()

    app = None
    try:
        logger.info("Creating application instance...")

        # Create plugin registry once to avoid double discovery
        # Reuse the discovery object from CLI arg parsing
        from kollabor_plugins import PluginRegistry

        plugin_registry = PluginRegistry(discovery.plugins_dir)
        plugin_registry.discovery = discovery  # Reuse discovery from CLI args

        # Resolve attach identity (set earlier if --attach was used)
        _attach_to = locals().get("attach_identity", None)

        app = TerminalLLMChat(
            args=args,
            system_prompt_file=args.system_prompt,
            agent_name=args.agent,
            profile_name=args.profile,
            save_profile=args.save,
            save_local=args.local,
            make_default_profile=args.make_default_profile,
            skill_names=args.skill,
            plugin_registry=plugin_registry,
            attach_to=_attach_to,
            context_name=getattr(args, "context", None),
        )
        logger.info("Starting application...")

        if pipe_mode_active and piped_input:
            # Parse timeout for pipe mode
            try:
                timeout_seconds = parse_timeout(args.timeout)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

            # Pipe mode: send input and exit after response
            await app.start_pipe_mode(piped_input, timeout=timeout_seconds)
        else:
            # Interactive mode (with optional initial message)
            await app.start(initial_message=initial_message)
    except KeyboardInterrupt:
        # print("\n\nApplication interrupted by user")
        logger.info("Application interrupted by user")
    except Exception as e:
        print(f"\n\nApplication failed to start: {e}")
        logger.error(f"Application startup failed: {type(e).__name__}: {e}")
        # Print helpful error message for common issues
        if "permission" in str(e).lower():
            print(
                "\nTip: Check file permissions and try running with appropriate privileges"
            )
        elif "already in use" in str(e).lower():
            print(
                "\nTip: Another instance may be running. Try closing other applications."
            )
        elif "not found" in str(e).lower():
            print("\nTip: Check that all required dependencies are installed.")
        raise  # Re-raise for full traceback in debug mode
    finally:
        # Ensure cleanup happens even if startup fails (skip if in pipe mode and already cleaned up)
        pipe_mode = getattr(app, "pipe_mode", False) if app else False
        if app and not app._startup_complete and not pipe_mode:
            logger.info("Performing emergency cleanup after startup failure...")
            try:
                await app.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Emergency cleanup failed: {cleanup_error}")
                # print("Warning: Some resources may not have been cleaned up properly")


def _apply_hub_project_scope_from_config() -> None:
    """Read plugins.hub.project_scoped from config and set the env var.

    CLI short-circuit paths (--hub, --attach) skip plugin boot, so they
    never see the flag the hub plugin would translate from config. Call
    this before any plugins.hub.* import that reads hub paths. Env var
    still takes precedence over config when explicitly set.
    """
    import os as _os

    if _os.environ.get("KOLLAB_HUB_PROJECT_SCOPED"):
        return
    try:
        import json as _json

        from kollabor_config.config_utils import get_existing_global_config_path

        cfg_path = get_existing_global_config_path()
        if not cfg_path.exists():
            _os.environ["KOLLAB_HUB_PROJECT_SCOPED"] = "1"
            return
        cfg = _json.loads(cfg_path.read_text())
        scoped = cfg.get("plugins", {}).get("hub", {}).get("project_scoped", True)
        _os.environ["KOLLAB_HUB_PROJECT_SCOPED"] = "1" if scoped else "0"
    except Exception:
        pass


def _print_hub_help() -> None:
    """Print hub CLI subcommand reference.

    Shared between `kollab --hub`, `kollab --hub help`, `kollab --hub -h`,
    and the unknown-subcommand fallback. Layout matches the rest of the
    CLI help (lowercase, aligned columns, dense).
    """
    print("usage: kollab --hub <subcommand> [args...]\n")
    print("subcommands:")
    print("  status                     show online agents and their state")
    print("  agents | list              alias for status")
    print("  stop <name|all>            send shutdown signal to an agent")
    print("  kill <name|all>            alias for stop")
    print("  capture <name> [lines]     dump last N lines of agent output")
    print("  msg <name> <text>          send a direct message to one agent")
    print("  broadcast <text>           send a message to all online agents")
    print("  user [name]                show or set the hub user display name")
    print("  on                         enable hub plugin (next session)")
    print("  off                        disable hub plugin (next session)")
    print("  org <name> [mission]       launch an organization")
    print("  help                       show this help")
    print()
    print("examples:")
    print("  kollab --hub status")
    print("  kollab --hub stop koordinator")
    print("  kollab --hub stop all")
    print("  kollab --hub msg lapis 'hey, got a minute?'")
    print("  kollab --hub broadcast 'rolling to lunch, bbiab'")
    print("  kollab --hub capture koordinator 200")
    print("  kollab --hub org engineering 'ship the billing flow'")
    print()
    print("interactive equivalents (run these inside a kollab session):")
    print("  /hub status        /hub msg <name> <text>    /hub broadcast <text>")
    print("  /hub bridge setup  /hub notify channel <...>  /hub feed")


async def _handle_cli_hub(hub_args: list) -> None:
    """Handle --hub CLI commands without starting the TUI.

    Reads presence files and talks to agent sockets directly.

    Commands:
        status                  show online agents
        agents / list           same as status
        stop <identity|all>     send shutdown signal (alias: kill)
        capture <name> [n]      get last N lines of agent output
        msg <name> <text>       send hub message to agent
        broadcast <text>        send message to all agents
        user [name]             show or set hub user name
        on / off                enable or disable hub
        org <name> [mission]    launch an organization
    """
    import json
    import os
    from pathlib import Path
    from typing import List, Optional

    _apply_hub_project_scope_from_config()

    from plugins.hub.messenger import AgentMessenger
    from plugins.hub.presence import get_presence_dir

    subcmd = hub_args[0].lower() if hub_args else "status"
    rest = hub_args[1:] if len(hub_args) > 1 else []

    # bare `kollab --hub` or explicit help should launch full interactive TUI
    # with hub plugin active (zero-config multi-terminal workflow). only
    # real subcommands go through the CLI handler.
    if subcmd in ("help", "-h", "--help") or not hub_args:
        return  # fall through to normal interactive path

    presence_dir = get_presence_dir()

    def _get_live_agents() -> List[dict]:
        import socket as _socket
        import time as _time

        agents = []
        for f in presence_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                pid = data.get("pid", 0)
                # Pid must exist
                try:
                    os.kill(pid, 0)
                except (OSError, ProcessLookupError):
                    f.unlink(missing_ok=True)
                    continue
                # Pid alive but heartbeat stale = recycled pid, clean up
                heartbeat_age = _time.time() - (data.get("last_heartbeat") or 0)
                if heartbeat_age > 30:
                    sock = data.get("socket_path", "")
                    alive = False
                    if sock:
                        try:
                            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                            s.settimeout(1.0)
                            s.connect(sock)
                            s.close()
                            alive = True
                        except Exception:
                            pass
                    if not alive:
                        f.unlink(missing_ok=True)
                        continue
                agents.append(data)
            except Exception:
                continue
        return agents

    def _find_agent(identity: str) -> Optional[dict]:
        for a in _get_live_agents():
            if a.get("identity") == identity:
                return a
        return None

    def _pid_alive(pid: int) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    async def _wait_for_pid_exit(pid: int, timeout: float = 5.0) -> bool:
        import time as _time

        if not pid:
            return True
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if not _pid_alive(pid):
                return True
            await asyncio.sleep(0.1)
        return not _pid_alive(pid)

    def _cleanup_presence_for_pid(pid: int) -> None:
        if not pid:
            return
        for f in presence_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    d = json.load(fh)
                if d.get("pid") == pid:
                    socket_path = d.get("socket_path", "")
                    f.unlink(missing_ok=True)
                    if socket_path:
                        try:
                            Path(socket_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    break
            except Exception:
                pass

    async def _stop_agent_record(agent: dict, reason: str) -> tuple[bool, str]:
        desig = agent.get("identity", "?")
        sock = agent.get("socket_path", "")
        pid = int(agent.get("pid") or 0)

        shutdown_acked = False
        if sock:
            shutdown_acked = await AgentMessenger.signal_shutdown(sock, reason=reason)

        if shutdown_acked:
            if await _wait_for_pid_exit(pid, timeout=STOP_GRACE_SECONDS):
                _cleanup_presence_for_pid(pid)
                return True, f"stopped {desig}"

            try:
                os.kill(pid, 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                _cleanup_presence_for_pid(pid)
                return True, f"stopped {desig}"

            if await _wait_for_pid_exit(pid, timeout=STOP_TERM_SECONDS):
                _cleanup_presence_for_pid(pid)
                return True, f"stopped {desig} (SIGTERM after graceful timeout)"

            return False, f"failed to stop {desig} (pid still alive)"

        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                _cleanup_presence_for_pid(pid)
                return True, f"stopped {desig}"

            if await _wait_for_pid_exit(pid, timeout=STOP_TERM_SECONDS):
                _cleanup_presence_for_pid(pid)
                return True, f"stopped {desig} (SIGTERM fallback)"

        return False, f"failed to stop {desig}"

    # --- config helpers for on/off/user ---
    from kollabor_config.config_utils import get_existing_global_config_path

    config_path = get_existing_global_config_path()

    def _read_config() -> dict:
        if config_path.exists():
            try:
                with open(config_path) as fh:
                    return dict(json.load(fh))
            except Exception:
                return {}
        return {}

    def _write_config(cfg: dict) -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as fh:
            json.dump(cfg, fh, indent=2)

    def _set_nested(d: dict, dotpath: str, value) -> None:
        keys = dotpath.split(".")
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    def _get_nested(d: dict, dotpath: str, default=None):
        keys = dotpath.split(".")
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, default)
            else:
                return default
        return d

    if subcmd in ("status", "agents", "list"):
        agents = _get_live_agents()
        if not agents:
            print("no agents online")
            return
        print(f"{len(agents)} agent(s) online:\n")
        for a in sorted(agents, key=lambda x: x.get("identity", "")):
            desig = a.get("identity", "?")
            state = a.get("state", "?")
            pid = a.get("pid", "?")
            coord = " *" if a.get("is_coordinator") else ""
            task = a.get("current_task", "")
            task_str = f"  task: {task[:60]}" if task else ""
            print(f"  {desig}{coord}  pid={pid}  {state}{task_str}")

    elif subcmd in ("stop", "kill"):
        if not rest:
            print("usage: kollab --hub stop <identity|all>")
            sys.exit(1)
        target = rest[0]

        if target == "all":
            agents = _get_live_agents()
            if not agents:
                print("no agents online")
                return
            stopped = 0
            results = await asyncio.gather(
                *[
                    _stop_agent_record(a, reason="stopped via CLI (all)")
                    for a in agents
                ]
            )
            for ok, message in results:
                print(f"  {message}")
                if ok:
                    stopped += 1
            print(f"\n{stopped}/{len(agents)} agent(s) stopped")
        else:
            agent = _find_agent(target)
            if not agent:
                agents = _get_live_agents()
                available = [a.get("identity", "?") for a in agents]
                print(f"agent '{target}' not found")
                if available:
                    print(f"online: {', '.join(sorted(available))}")
                sys.exit(1)
            success, message = await _stop_agent_record(agent, reason="stopped via CLI")
            if success:
                print(message)
            else:
                print(message)
                sys.exit(1)

    elif subcmd == "capture":
        if not rest:
            print("usage: kollab --hub capture <identity> [lines]")
            sys.exit(1)
        target = rest[0]
        num_lines = int(rest[1]) if len(rest) > 1 else 50
        agent = _find_agent(target)
        if not agent:
            agents = _get_live_agents()
            available = [a.get("identity", "?") for a in agents]
            print(f"agent '{target}' not found")
            if available:
                print(f"online: {', '.join(sorted(available))}")
            sys.exit(1)
        socket_path = agent.get("socket_path", "")
        if not socket_path:
            print(f"agent '{target}' has no socket")
            sys.exit(1)
        lines = await AgentMessenger.request_output(socket_path, lines=num_lines)
        if lines:
            for line in lines:
                print(line)
        else:
            print("(no output)")

    elif subcmd == "msg":
        if len(rest) < 2:
            print("usage: kollab --hub msg <identity> <message>")
            sys.exit(1)
        target = rest[0]
        content = " ".join(rest[1:])
        agent = _find_agent(target)
        if not agent:
            print(f"agent '{target}' not found")
            sys.exit(1)
        socket_path = agent.get("socket_path", "")
        from plugins.hub.models import HubMessage, MessageScope

        # Send to ALL agents (open channel) so everyone sees the operator message
        all_agents = _get_live_agents()
        sent_count = 0
        for a in all_agents:
            a_socket = a.get("socket_path", "")
            if not a_socket:
                continue
            msg = HubMessage(
                action="message",
                from_agent="cli",
                from_identity=os.environ.get("USER", "user"),
                to=target,
                content=content,
                scope=MessageScope.BROADCAST.value,
            )
            ok = await AgentMessenger.send_to_agent(a_socket, msg)
            if ok:
                sent_count += 1
        if sent_count > 0:
            print(
                f"sent to {target} (broadcast to {sent_count} agent{'s' if sent_count != 1 else ''})"
            )
            # Show conversation file location for the target agent
            from kollabor_config.config_utils import get_conversations_dir

            conv_dir = str(get_conversations_dir())
            if os.path.isdir(conv_dir):
                # Find the most recent JSONL file
                import glob

                files = sorted(
                    glob.glob(os.path.join(conv_dir, "*.jsonl")),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if files:
                    print(f"conversations: {conv_dir}")
        else:
            print(f"failed to send to {target}")
            sys.exit(1)

    elif subcmd == "broadcast":
        if not rest:
            print("usage: kollab --hub broadcast <message>")
            sys.exit(1)
        content = " ".join(rest)
        agents = _get_live_agents()
        if not agents:
            print("no agents online")
            return
        from plugins.hub.models import HubMessage, MessageScope

        sent_count = 0
        for a in agents:
            a_socket = a.get("socket_path", "")
            if not a_socket:
                continue
            msg = HubMessage(
                action="message",
                from_agent="cli",
                from_identity=os.environ.get("USER", "user"),
                to="all",
                content=content,
                scope=MessageScope.BROADCAST.value,
            )
            ok = await AgentMessenger.send_to_agent(a_socket, msg)
            if ok:
                sent_count += 1
        print(f"broadcast to {sent_count} agent{'s' if sent_count != 1 else ''}")

    elif subcmd == "user":
        cfg = _read_config()
        if not rest:
            current = _get_nested(cfg, "plugins.hub.user_name")
            if not current:
                current = os.environ.get("USER", "user")
            print(f"hub user: {current}")
        else:
            new_name = " ".join(rest)
            _set_nested(cfg, "plugins.hub.user_name", new_name)
            _write_config(cfg)
            print(f"hub user set to: {new_name}")

    elif subcmd == "on":
        cfg = _read_config()
        _set_nested(cfg, "plugins.hub.enabled", True)
        _write_config(cfg)
        print("hub enabled")

    elif subcmd == "off":
        cfg = _read_config()
        _set_nested(cfg, "plugins.hub.enabled", False)
        _write_config(cfg)
        print("hub disabled")

    elif subcmd == "org":
        if not rest:
            print("usage: kollab --hub org <name> [mission]")
            sys.exit(1)
        org_name = rest[0]
        mission = " ".join(rest[1:]) if len(rest) > 1 else ""

        from plugins.hub.org_launcher import OrgLauncher, load_organization

        org_def = load_organization(org_name)
        if not org_def:
            print(f"organization '{org_name}' not found")
            sys.exit(1)

        launcher = OrgLauncher()
        count, identities = launcher.launch_org(org_name, mission=mission)
        if count == 0:
            print(f"no agents defined in org '{org_name}'")
            sys.exit(1)
        print(f"launched {count} agent(s): {', '.join(identities)}")

    elif subcmd in ("help", "-h", "--help"):
        _print_hub_help()
        return  # let async_main continue to full interactive TUI with hub enabled

    else:
        print(f"unknown hub command: {subcmd}\n")
        _print_hub_help()
        sys.exit(1)


async def _handle_cli_login(provider: str) -> None:
    """Handle --login flag: run OAuth flow from CLI.

    Args:
        provider: Provider to authenticate with (e.g. "openai").
    """
    if provider != "openai":
        print(f"Error: unsupported login provider: {provider}", file=sys.stderr)
        sys.exit(1)

    try:
        from kollabor_ai.oauth import OAuthTokenStorage, OpenAIOAuthClient
        from kollabor_ai.oauth.openai_oauth import DEVICE_VERIFY_URL

        client = OpenAIOAuthClient()
        storage = OAuthTokenStorage()

        print("\n  prerequisite: enable device code auth in ChatGPT")
        print("    1. go to chatgpt.com")
        print("    2. settings > security (or data controls)")
        print("    3. enable 'device code authorization' / Codex toggle")
        print()

        try:
            # Step 1: Request device code
            device = await client._request_device_code()

            print(f"  open: {DEVICE_VERIFY_URL}")
            print(f"  code: {device.user_code}\n")
            print("  waiting for browser authorization...")

            # Open browser
            import webbrowser

            try:
                webbrowser.open(DEVICE_VERIFY_URL)
            except Exception:
                print("  (could not open browser, use the URL above)")

            # Step 3: Poll for authorization_code
            auth_resp = await client._poll_for_auth_code(device)

            # Step 4: Exchange via PKCE for real tokens
            tokens = await client._exchange_code(
                auth_resp.authorization_code,
                auth_resp.code_verifier,
            )
        finally:
            await client.close()

        # Store tokens
        await storage.store_tokens("openai", tokens)

        # Query available models and pick the best one
        from kollabor_ai.oauth.openai_oauth import (
            pick_best_model,
            query_codex_models,
        )

        print("\n  querying available models...")
        available_models = await query_codex_models(
            tokens.access_token, tokens.account_id
        )
        model = pick_best_model(available_models)

        import time

        remaining = tokens.expires_at - time.time()
        if remaining > 86400:
            exp_str = f"{remaining / 86400:.1f}d"
        else:
            exp_str = f"{remaining / 3600:.1f}h"

        print("\n  logged in via OpenAI OAuth")
        print(f"  model:   {model}")
        if available_models:
            print(f"  available: {', '.join(available_models)}")
        print(f"  expires: {exp_str}")
        print("  run 'kollab' to start chatting\n")

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


def _kill_owned_daemon() -> None:
    """Kill the daemon process we forked, if we own it.

    Called on ctrl+c exit. Sends SIGTERM for graceful shutdown.
    Skipped if KOLLAB_DAEMON_PID was cleared (ctrl+z detach).
    """
    import os
    import signal

    pid_str = os.environ.pop("KOLLAB_DAEMON_PID", "")
    if not pid_str:
        return  # ctrl+z cleared it, daemon should survive

    try:
        pid = int(pid_str)
        os.kill(pid, signal.SIGTERM)
    except (ValueError, OSError, ProcessLookupError):
        pass  # already dead


def _hub_is_enabled() -> bool:
    """Quick check if hub plugin is enabled in config.

    Daemon-first mode requires hub (for the socket server).
    If hub is disabled, fall back to single-process mode.
    """
    import json

    from kollabor_config.config_utils import get_existing_global_config_path

    config_path = get_existing_global_config_path()
    if not config_path.exists():
        return True  # Default is enabled

    try:
        with open(config_path) as f:
            config = json.load(f)
        return bool(config.get("plugins", {}).get("hub", {}).get("enabled", True))
    except Exception:
        return True  # Default is enabled


def _should_use_daemon() -> bool:
    """Determine if this invocation should auto-fork a daemon.

    Daemon mode only for normal interactive sessions. Skip for:
    - explicit flags (--detached, --attach, --no-daemon, --hub)
    - pipe mode (stdin not a tty, or -p flag)
    - info flags (-h, --help, --version, --reset-config, --update, --font-dir, --login)
    """
    args = sys.argv[1:]
    skip_flags = {
        "--detached",
        "-d",
        "--attach",
        "--no-daemon",
        "--hub",
        "--org",
        "-h",
        "--help",
        "--version",
        "--reset-config",
        "--update",
        "--font-dir",
        "--login",
        "-p",
        "--pipe",
    }
    daemon_launch_flags = {
        "--agent",
        "-a",
        "--as",
        "--profile",
        "--project",
        "--context",
        "--system-prompt",
        "--skill",
        "-s",
        "--timeout",
    }
    daemon_launch_switches = {
        "--daemon",
        "--simple",
        "--save",
        "--local",
        "--default",
    }
    for arg in args:
        if arg in skip_flags:
            return False
        # --attach=value or --hub=value style
        for flag in ("--attach=", "--hub=", "--org="):
            if arg.startswith(flag):
                return False
        if arg.startswith("--"):
            flag_name = arg.split("=", 1)[0]
            if (
                flag_name not in daemon_launch_flags
                and flag_name not in daemon_launch_switches
            ):
                return False

    # Skip if stdin is piped (non-interactive)
    if not sys.stdin.isatty():
        return False

    return True


def cli_main() -> None:
    """Synchronous entry point for pip-installed CLI command."""
    # --detached: fork into a fully detached daemon process.
    # Shell backgrounding (&) doesn't work because zsh suspends
    # processes that touch the TTY (SIGTTOU). Instead, we fork,
    # create a new session (setsid), redirect all fds, and let
    # the parent exit immediately. The child runs headless.
    if "--detached" in sys.argv or "-d" in sys.argv:
        import os

        pid = os.fork()
        if pid > 0:
            # Parent: print child PID and exit cleanly
            sys.stdout.write(f"[detached] pid {pid}\n")
            sys.stdout.flush()
            sys.exit(0)

        # Child: new session, detach from terminal
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)  # stdin
        os.dup2(devnull, 1)  # stdout
        os.dup2(devnull, 2)  # stderr
        os.close(devnull)

    # Daemon mode (default): auto-fork daemon + connect as attach client.
    # The daemon runs headless (like --detached). The parent becomes
    # an attach client. Ctrl+Z detaches; daemon keeps running.
    # Use --no-daemon for single-process mode.
    elif _should_use_daemon():
        import os

        from kollabor.daemon import fork_daemon

        try:
            daemon_pid, socket_path = fork_daemon(sys.argv)
        except RuntimeError as e:
            print(f"daemon startup failed: {e}", file=sys.stderr)
            print("falling back to single-process mode", file=sys.stderr)
            # Fall through to normal async_main()
        else:
            # Parent: re-enter the CLI as a lightweight attach client.
            # Client only needs --attach <identity>. All other args
            # (--agent, --profile, query text) already went to the daemon.
            identity = os.path.basename(socket_path).replace(".sock", "")
            sys.argv = [sys.argv[0], "--attach", identity]

            # Store daemon PID so cleanup knows to kill it on ctrl+c
            os.environ["KOLLAB_DAEMON_PID"] = str(daemon_pid)

            try:
                asyncio.run(async_main())
            except KeyboardInterrupt:
                pass
            finally:
                # Owner process: kill the daemon on exit (ctrl+c).
                # Ctrl+Z sets _attach_detaching which clears the env var
                # before we get here, so daemon survives detach.
                _kill_owned_daemon()
            return

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Can't print if detached (stdout is /dev/null), but try anyway
        try:
            print(f"\n\nFatal error: {e}")
        except Exception:
            pass
        sys.exit(1)

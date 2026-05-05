"""Shell command utilities for AI context injection.

This module provides utilities for detecting shell aliases and
providing them to the AI so it can use correct command syntax.
"""

import logging
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Commands with significantly different syntax when aliased
# Maps standard command -> common replacements that have different flags
SYNTAX_CHANGING_ALIASES = {
    "find": ["fd", "fdfind"],  # fd uses different flag syntax
    "grep": ["rg", "ripgrep", "ag"],  # ripgrep/ag use different flags
    "cat": ["bat", "batcat"],  # bat has different options
    "ls": ["exa", "eza", "lsd"],  # modern ls replacements
    "diff": ["delta", "difft"],  # diff tools
    "du": ["dust", "dua"],  # disk usage tools
    "top": ["htop", "btop", "gtop"],  # process viewers
    "ps": ["procs"],  # process listing
    "sed": ["sd"],  # stream editor
    "man": ["tldr", "tealdeer"],  # manual pages
    "ping": ["gping"],  # ping with graph
    "dig": ["dog", "doggo"],  # DNS lookup
    "curl": ["httpie", "http", "xh"],  # HTTP clients
}

# Reverse mapping for quick lookup
REPLACEMENT_TO_STANDARD = {}
for standard, replacements in SYNTAX_CHANGING_ALIASES.items():
    for replacement in replacements:
        REPLACEMENT_TO_STANDARD[replacement] = standard


def detect_shell_aliases(timeout: int = 5) -> Dict[str, str]:
    """Detect user's shell aliases.

    Runs the user's shell in interactive mode to source rc files
    and extract alias definitions.

    Args:
        timeout: Max seconds to wait for shell

    Returns:
        Dict mapping alias name -> target command
    """
    aliases = {}

    # Get user's shell
    user_shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = os.path.basename(user_shell)

    try:
        # Run shell in interactive mode to source rc files, then print aliases
        if shell_name in ("zsh", "bash"):
            # -i for interactive (sources rc), -c for command
            cmd = [user_shell, "-i", "-c", "alias"]
        else:
            # Fallback for other shells
            cmd = [user_shell, "-c", "alias"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PS1": ""},  # Suppress prompt
        )

        if result.returncode == 0:
            aliases = _parse_alias_output(result.stdout, shell_name)
            logger.info(f"Detected {len(aliases)} shell aliases")
        else:
            logger.warning(f"Failed to get aliases: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.warning(f"Alias detection timed out after {timeout}s")
    except Exception as e:
        logger.error(f"Error detecting aliases: {e}")

    return aliases


def _parse_alias_output(output: str, shell_name: str) -> Dict[str, str]:
    """Parse alias command output.

    Handles different formats:
    - bash: alias name='value'
    - zsh:  name='value' or name=value

    Args:
        output: Raw output from alias command
        shell_name: Shell type for format hints

    Returns:
        Dict mapping alias name -> target (first word of value)
    """
    aliases = {}

    for line in output.strip().split("\n"):
        if not line.strip():
            continue

        # Remove 'alias ' prefix if present (bash format)
        if line.startswith("alias "):
            line = line[6:]

        # Parse name=value or name='value'
        match = re.match(r"^(\w+)=['\"]?([^'\"]+)", line)
        if match:
            name = match.group(1)
            value = match.group(2).strip()

            # Extract the actual command (first word)
            target = value.split()[0] if value else ""

            if target:
                aliases[name] = target
                logger.debug(f"Found alias: {name} -> {target}")

    return aliases


def get_syntax_changing_aliases(
    aliases: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, str, str]]:
    """Get aliases that change command syntax.

    These are aliases where the user has replaced a standard command
    with a modern alternative that has different flags/options.

    Args:
        aliases: Pre-detected aliases, or None to detect now

    Returns:
        List of (standard_cmd, alias_name, replacement_cmd) tuples
    """
    if aliases is None:
        aliases = detect_shell_aliases()

    results = []

    for alias_name, target in aliases.items():
        # Check if this alias replaces a standard command with a different tool
        if alias_name in SYNTAX_CHANGING_ALIASES:
            # The alias IS a standard command name
            if target in SYNTAX_CHANGING_ALIASES[alias_name]:
                results.append((alias_name, alias_name, target))
        elif target in REPLACEMENT_TO_STANDARD:
            # The target is a known replacement tool
            standard = REPLACEMENT_TO_STANDARD[target]
            if alias_name == standard:
                results.append((standard, alias_name, target))

    return results


def format_aliases_for_prompt(aliases: Optional[Dict[str, str]] = None) -> str:
    """Format detected aliases as context for AI prompt.

    Focuses on syntax-changing aliases that the AI needs to know about.

    Args:
        aliases: Pre-detected aliases, or None to detect now

    Returns:
        Formatted string for injection into system prompt
    """
    if aliases is None:
        aliases = detect_shell_aliases()

    if not aliases:
        return ""

    # Get syntax-changing aliases
    syntax_aliases = get_syntax_changing_aliases(aliases)

    if not syntax_aliases:
        return ""

    lines = ["## Shell Aliases (IMPORTANT)", ""]
    lines.append("The user has these command aliases that change syntax:")
    lines.append("")

    for standard, alias_name, replacement in syntax_aliases:
        lines.append(f"- `{alias_name}` -> `{replacement}`")

        # Add syntax hints for common replacements
        if replacement in ("fd", "fdfind"):
            lines.append("  - Use `fd` syntax: `fd PATTERN` not `find -name PATTERN`")
            lines.append(
                "  - fd flags: `--type f` (not `-type f`), `--max-depth N` (not `-maxdepth N`)"
            )
            lines.append("  - fd is recursive by default, no need for `-r`")
        elif replacement in ("rg", "ripgrep"):
            lines.append("  - Use `rg` syntax: `rg PATTERN` not `grep PATTERN`")
            lines.append("  - rg is recursive by default")
        elif replacement in ("bat", "batcat"):
            lines.append("  - `bat` has syntax highlighting, paging by default")
        elif replacement in ("exa", "eza", "lsd"):
            lines.append(f"  - `{replacement}` has different flags than `ls`")

    lines.append("")
    lines.append("When running shell commands, use the replacement tool's syntax.")
    lines.append("")

    return "\n".join(lines)


# Cache for detected aliases (detect once per session)
_cached_aliases: Optional[Dict[str, str]] = None


def get_cached_aliases() -> Dict[str, str]:
    """Get cached aliases, detecting if not already done."""
    global _cached_aliases
    if _cached_aliases is None:
        _cached_aliases = detect_shell_aliases()
    return _cached_aliases


def clear_alias_cache():
    """Clear the cached aliases."""
    global _cached_aliases
    _cached_aliases = None

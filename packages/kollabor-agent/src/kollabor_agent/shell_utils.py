"""Shell command utilities for AI context injection.

This module provides utilities for detecting shell aliases and
providing them to the AI so it can use correct command syntax.
"""

import logging
import ntpath
import os
import platform
import re
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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


def detect_shell_aliases(timeout: int = 1) -> Dict[str, str]:
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


POWERSHELL_PREFIX = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "

_RULES = {
    "git-bash": (
        "Git Bash on Windows: write bash. Quote Windows paths and use forward slashes "
        "(C:/Users/...). Native Windows switches need a doubled slash in Git Bash "
        "(cmd //c dir, taskkill //F)."
    ),
    "pwsh": (
        "PowerShell 7 (pwsh): write PowerShell, not bash. && and || stop on a failure. "
        "Get-ChildItem for ls -la, Select-String for grep, Remove-Item -Recurse -Force "
        "for rm -rf, 2>$null for 2>/dev/null, curl.exe for curl."
    ),
    "powershell": (
        "Windows PowerShell 5.1: write PowerShell, not bash. There is no && or ||, and ; "
        "runs the next command even after a failure: write cmd1; if ($?) { cmd2 }. "
        "Get-ChildItem for ls -la, Select-String for grep, Remove-Item -Recurse -Force "
        "for rm -rf, 2>$null for 2>/dev/null, curl.exe for curl."
    ),
}
_LABELS = {
    "git-bash": "Git Bash",
    "pwsh": "PowerShell 7",
    "powershell": "Windows PowerShell 5.1",
}
_KINDS = {"bash.exe": "git-bash", "pwsh.exe": "pwsh", "powershell.exe": "powershell"}


class AgentShellError(RuntimeError):
    """No usable shell for agent commands (a bad KOLLAB_SHELL names itself and its value)."""


@dataclass(frozen=True)
class AgentShell:
    """The shell that runs agent commands; kind is posix, git-bash, pwsh or powershell."""

    path: str
    kind: str

    @property
    def label(self) -> str:
        return _LABELS.get(self.kind) or os.path.basename(self.path) or self.path

    @property
    def rule(self) -> str:
        return _RULES.get(self.kind, "")

    def argv(self, command: str, interactive: bool = False) -> list[str]:
        if self.kind in ("pwsh", "powershell"):
            return [
                self.path,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                POWERSHELL_PREFIX + command,
            ]
        return (
            [self.path, "-i", "-c", command]
            if interactive
            else [self.path, "-c", command]
        )


def windows_path_entries(path_value: str, cwd: str) -> list[str]:
    """PATH entries a Windows shell may search: absolute only, never cwd or inside it."""
    cwd_norm = ntpath.normcase(ntpath.normpath(cwd))
    cwd_prefix = cwd_norm.rstrip("\\") + "\\"
    kept: list[str] = []
    for part in path_value.split(";"):
        entry = part.strip().strip('"')
        if not entry or not ntpath.isabs(entry):
            continue
        entry_norm = ntpath.normcase(ntpath.normpath(entry))
        if entry_norm == cwd_norm or entry_norm.startswith(cwd_prefix):
            continue
        kept.append(ntpath.normpath(entry))
    return kept


_cached: AgentShell | None = None


def _is_file(path: str) -> bool:
    try:
        return not stat.S_ISDIR(os.stat(path).st_mode)
    except OSError:
        return False


def agent_shell(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: str | None = None,
    system: str | None = None,
    is_file: Callable[[str], bool] | None = None,
) -> AgentShell:
    """The shell for agent commands: $SHELL on POSIX; Git Bash, else PowerShell, on Windows."""
    global _cached
    real = environ is None and cwd is None and system is None and is_file is None
    environ = os.environ if environ is None else environ
    system = system or platform.system()
    is_file = is_file or _is_file
    if system != "Windows":
        return AgentShell(environ.get("SHELL") or "/bin/sh", "posix")
    if real and _cached is not None and is_file(_cached.path):
        return _cached
    cwd = os.getcwd() if cwd is None else cwd
    root = environ.get("SystemRoot") or r"C:\Windows"
    resolved = _resolve_windows(environ, cwd, root, is_file)
    if real:
        _cached = resolved
    return resolved


def _resolve_windows(
    environ: Mapping[str, str],
    cwd: str,
    root: str,
    is_file: Callable[[str], bool],
) -> AgentShell:
    """Resolve a Windows shell: KOLLAB_SHELL, then Git Bash, PowerShell 7, PowerShell 5.1."""
    value = environ.get("KOLLAB_SHELL", "").strip()
    if value:
        name = ntpath.basename(value).lower()
        if (
            ntpath.isabs(value)
            and name in _KINDS
            and ntpath.normcase(value)
            != ntpath.normcase(ntpath.join(root, "System32", "bash.exe"))
            and is_file(value)
        ):
            return AgentShell(value, _KINDS[name])
        raise AgentShellError(
            f"KOLLAB_SHELL={value!r} cannot run agent commands: it must be the absolute "
            "path of an existing bash.exe, pwsh.exe or powershell.exe, and not WSL's "
            "System32 bash.exe."
        )
    bash_candidates: list[str] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = environ.get(var)
        if base and ntpath.isabs(base):
            bash_candidates.append(ntpath.join(base, "Git", "bin", "bash.exe"))
    local_appdata = environ.get("LOCALAPPDATA")
    if local_appdata and ntpath.isabs(local_appdata):
        bash_candidates.append(
            ntpath.join(local_appdata, "Programs", "Git", "bin", "bash.exe")
        )
    scoop = environ.get("SCOOP")
    if scoop and ntpath.isabs(scoop):
        bash_candidates.append(
            ntpath.join(scoop, "apps", "git", "current", "bin", "bash.exe")
        )
    profile = environ.get("USERPROFILE")
    if profile and ntpath.isabs(profile):
        bash_candidates.append(
            ntpath.join(profile, "scoop", "apps", "git", "current", "bin", "bash.exe")
        )
    for path in bash_candidates:
        if is_file(path):
            return AgentShell(path, "git-bash")
    entries = windows_path_entries(environ.get("PATH", ""), cwd)
    for entry in entries:
        if not is_file(ntpath.join(entry, "git.exe")):
            continue
        candidate = ntpath.normpath(ntpath.join(entry, "..", "bin", "bash.exe"))
        if is_file(candidate):
            return AgentShell(candidate, "git-bash")
    program_files = environ.get("ProgramFiles")
    if program_files and ntpath.isabs(program_files):
        candidate = ntpath.join(program_files, "PowerShell", "7", "pwsh.exe")
        if is_file(candidate):
            return AgentShell(candidate, "pwsh")
    for entry in entries:
        candidate = ntpath.join(entry, "pwsh.exe")
        if is_file(candidate):
            return AgentShell(candidate, "pwsh")
    candidate = ntpath.join(
        root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    if is_file(candidate):
        return AgentShell(candidate, "powershell")
    raise AgentShellError(
        "No shell for agent commands: install Git for Windows, or set KOLLAB_SHELL to "
        "the absolute path of bash.exe, pwsh.exe or powershell.exe."
    )

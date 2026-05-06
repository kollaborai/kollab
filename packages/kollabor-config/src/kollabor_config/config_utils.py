"""Configuration utilities for Kollab."""

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Platform check
IS_WINDOWS = sys.platform == "win32"

# Canonical runtime/config roots for the public Kollab app.
APP_CONFIG_DIR_NAME = ".kollab"


def get_config_directory() -> Path:
    """Get the global Kollab runtime/config directory."""
    return Path.home() / APP_CONFIG_DIR_NAME


def get_config_directory_candidates() -> list[Path]:
    """Return global config roots in read precedence order."""
    return [get_config_directory()]


def get_local_config_directory() -> Path:
    """Get the project-local Kollab runtime/config directory."""
    return Path.cwd() / APP_CONFIG_DIR_NAME


def get_local_config_directory_candidates() -> list[Path]:
    """Return project-local config roots in read precedence order."""
    return [get_local_config_directory()]


def get_existing_config_directory() -> Path:
    """Return the global config root."""
    return get_config_directory()


def get_global_config_path() -> Path:
    """Return the preferred global config.json path."""
    return get_config_directory() / "config.json"


def get_global_config_path_candidates() -> list[Path]:
    """Return global config.json candidates in read precedence order."""
    return [directory / "config.json" for directory in get_config_directory_candidates()]


def get_existing_global_config_path() -> Path:
    """Return the global config.json path."""
    return get_global_config_path()


def get_local_config_path() -> Path:
    """Return the preferred project-local config.json path."""
    return get_local_config_directory() / "config.json"


def get_local_config_path_candidates() -> list[Path]:
    """Return project-local config.json candidates in read precedence order."""
    return [
        directory / "config.json" for directory in get_local_config_directory_candidates()
    ]


def get_existing_local_config_path() -> Path:
    """Return the project-local config.json path."""
    return get_local_config_path()


def resolve_global_path(*parts: str) -> Path:
    """Resolve a global runtime path under ~/.kollab."""
    return get_config_directory().joinpath(*parts)


def resolve_local_path(*parts: str) -> Path:
    """Resolve a project-local runtime path under .kollab."""
    return get_local_config_directory().joinpath(*parts)


def get_project_data_dir_candidates(project_path: Optional[Path] = None) -> list[Path]:
    """Return project data directories in read precedence order."""
    if project_path is None:
        project_path = Path.cwd()

    encoded = encode_project_path(project_path)
    return [
        directory / "projects" / encoded
        for directory in get_config_directory_candidates()
    ]


# ============================================================================
# Project Data Path Utilities
# ============================================================================


def encode_project_path(project_path: Path) -> str:
    """Encode a project path to a safe directory name.

    Replaces path separators (/ and \\) with underscores and strips
    leading underscores for use as a directory name.

    Args:
        project_path: Path to encode

    Returns:
        Encoded string safe for use as directory name

    Examples:
        >>> encode_project_path(Path("/home/user/dev/hello_world"))
        'home_user_dev_hello_world'
        >>> encode_project_path(Path("C:\\\\Users\\\\dev\\\\project"))
        'C_Users_dev_project'
    """
    path_str = str(project_path.resolve())
    # Replace path separators with underscores
    encoded = path_str.replace("/", "_").replace("\\", "_")
    # Remove leading underscore if present (from root /)
    while encoded.startswith("_"):
        encoded = encoded[1:]
    return encoded


def decode_project_path(encoded: str) -> Path:
    """Decode an encoded project path back to a Path.

    Reverses the encoding done by encode_project_path().

    Args:
        encoded: Encoded project path string

    Returns:
        Original Path object

    Examples:
        >>> decode_project_path("home_user_dev_hello_world")
        Path('/home/user/dev/hello_world')
        >>> decode_project_path("C_Users_dev_project")
        Path('C:\\\\Users\\\\dev\\\\project')
    """
    # Detect Windows paths (start with drive letter + underscore)
    if len(encoded) > 1 and encoded[1] == "_" and encoded[0].isalpha():
        # Windows: C_Users_... -> C:\Users\...
        path_str = encoded[0] + ":\\" + encoded[2:].replace("_", "\\")
    else:
        # Unix: home_user_... -> /home/user/...
        path_str = "/" + encoded.replace("_", "/")
    return Path(path_str)


def get_project_data_dir(project_path: Optional[Path] = None) -> Path:
    """Get the centralized project data directory.

    Returns ~/.kollab/projects/<encoded-path>/ for the current
    or specified project.

    Args:
        project_path: Path to project directory. If None, uses Path.cwd()

    Returns:
        Path to project-specific data directory
    """
    return get_project_data_dir_candidates(project_path)[0]


def get_conversations_dir(project_path: Optional[Path] = None) -> Path:
    """Get the conversations directory for a project.

    Args:
        project_path: Path to project directory. If None, uses Path.cwd()

    Returns:
        Path to project's conversations directory
    """
    return get_project_data_dir(project_path) / "conversations"


def get_logs_dir(project_path: Optional[Path] = None) -> Path:
    """Get the logs directory for a project.

    Args:
        project_path: Path to project directory. If None, uses Path.cwd()

    Returns:
        Path to project's logs directory
    """
    return get_project_data_dir(project_path) / "logs"


def get_local_agents_dir() -> Path | None:
    """Get the local agents directory if it exists.

    Checks for .kollab/agents/ in the current working directory.

    Returns:
        Path to local agents directory if it exists, None otherwise
    """
    for local_agents_dir in (
        directory / "agents" for directory in get_local_config_directory_candidates()
    ):
        if local_agents_dir.exists():
            return local_agents_dir
    return None


def get_local_agents_path() -> Path:
    """Get the local agents directory path (for creation purposes).

    Unlike get_local_agents_dir(), this returns the path even if it doesn't exist.
    Use this when you need to create the local agents directory.

    Returns:
        Path to .kollab/agents/ in the current working directory
    """
    return get_local_config_directory() / "agents"


def get_global_agents_dir() -> Path:
    """Get the global agents directory.

    Returns:
        Path to the global agents directory.
    """
    return resolve_global_path("agents")


# ── Skill library directories ──────────────────────────────────────


def get_bundled_skills_dir() -> Path | None:
    """Get the bundled skills directory shipped with the package.

    Checks package install path first, then cwd/bundles/skills/ for dev mode.

    Returns:
        Path to bundled skills directory if it exists, None otherwise
    """
    # Installed package: relative to this file's package root
    package_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    bundled = package_root / "bundles" / "skills"
    if bundled.exists():
        return bundled
    # Development mode: cwd
    fallback = Path.cwd() / "bundles" / "skills"
    return fallback if fallback.exists() else None


def get_global_skills_dir() -> Path:
    """Get the global skills directory.

    Returns:
        Path to the global skills directory.
    """
    return resolve_global_path("skills")


def get_local_skills_dir() -> Path | None:
    """Get the local skills directory if it exists.

    Checks for .kollab/skills/ in the current working directory.

    Returns:
        Path to local skills directory if it exists, None otherwise
    """
    for local_skills_dir in (
        directory / "skills" for directory in get_local_config_directory_candidates()
    ):
        if local_skills_dir.exists():
            return local_skills_dir
    return None


def get_local_skills_path() -> Path:
    """Get the local skills directory path (for creation purposes).

    Unlike get_local_skills_dir(), this returns the path even if it doesn't exist.

    Returns:
        Path to .kollab/skills/ in the current working directory
    """
    return get_local_config_directory() / "skills"


# CLI override for system prompt file (set via --system-prompt argument)
_cli_system_prompt_file: str | None = None


def set_cli_system_prompt_file(file_path: str | None) -> None:
    """Set the CLI override for system prompt file.

    Args:
        file_path: Path to the system prompt file, or None to clear
    """
    global _cli_system_prompt_file
    _cli_system_prompt_file = file_path
    if file_path:
        logger.info(f"CLI system prompt override set: {file_path}")


def _resolve_system_prompt_path(filename: str) -> Path | None:
    """Resolve a system prompt filename to a full path.

    Searches in order:
    1. As-is (if absolute path or exists in cwd)
    2. Local .kollab/agents/default/
    3. Global ~/.kollab/agents/default/

    Args:
        filename: The filename or path provided by the user

    Returns:
        Resolved Path if found, None otherwise
    """
    # Expand ~ in path
    expanded = Path(filename).expanduser()

    # 1. Check as-is (absolute path or relative from cwd)
    if expanded.exists():
        return expanded

    # If it's an absolute path that doesn't exist, don't search further
    if expanded.is_absolute():
        return None

    # Get just the filename for searching in directories
    name = expanded.name

    # Also try with .md extension if not present
    names_to_try = [name]
    if not name.endswith(".md"):
        names_to_try.append(f"{name}.md")

    for base_dir in (
        directory / "agents" / "default"
        for directory in (
            get_local_config_directory_candidates() + get_config_directory_candidates()
        )
    ):
        for n in names_to_try:
            candidate = base_dir / n
            if candidate.exists():
                return candidate

    return None


def ensure_config_directory() -> Path:
    """Get and ensure the configuration directory exists.

    Creates both the global config directory and the project-specific
    data directory under ~/.kollab/projects/<encoded>/.

    Returns:
        Path to the global configuration directory
    """
    config_dir = get_config_directory()
    config_dir.mkdir(exist_ok=True)

    # Also ensure project data directory exists
    project_data_dir = get_project_data_dir()
    project_data_dir.mkdir(parents=True, exist_ok=True)

    return config_dir


def get_system_prompt_path() -> Path:
    """Get the system prompt file path, preferring env var over local/global.

    Resolution order:
    1. KOLLAB_SYSTEM_PROMPT_FILE environment variable (custom file path)
    2. Local .kollab/agents/default/system_prompt.md (project-specific)
    3. Global ~/.kollab/agents/default/system_prompt.md (global default)

    Returns:
        Path to the system prompt file
    """
    # Check for environment variable override
    env_prompt_file = os.environ.get("KOLLAB_SYSTEM_PROMPT_FILE")
    if env_prompt_file:
        env_path = Path(env_prompt_file).expanduser()
        if env_path.exists():
            logger.debug(
                f"Using system prompt from KOLLAB_SYSTEM_PROMPT_FILE: {env_path}"
            )
            return env_path
        else:
            logger.warning(
                f"KOLLAB_SYSTEM_PROMPT_FILE points to non-existent file: {env_path}"
            )

    local_config_dirs = get_local_config_directory_candidates()
    global_config_dirs = get_config_directory_candidates()

    # On Windows, prefer default_win.md if it exists (in agent directory)
    if IS_WINDOWS:
        for config_dir in local_config_dirs + global_config_dirs:
            win_prompt = config_dir / "agents" / "default" / "system_prompt_win.md"
            if win_prompt.exists():
                logger.debug(f"Using Windows-specific system prompt: {win_prompt}")
                return win_prompt

    # If local exists, use it (override)
    for config_dir in local_config_dirs:
        local_agent_prompt = config_dir / "agents" / "default" / "system_prompt.md"
        if local_agent_prompt.exists():
            return local_agent_prompt

    for config_dir in global_config_dirs:
        global_agent_prompt = config_dir / "agents" / "default" / "system_prompt.md"
        if global_agent_prompt.exists():
            return global_agent_prompt

    return get_config_directory() / "agents" / "default" / "system_prompt.md"


def get_system_prompt_content() -> str:
    """Get the system prompt content, checking CLI args, env vars, and files.

    Resolution order:
    1. CLI --system-prompt argument (highest priority)
    2. KOLLAB_SYSTEM_PROMPT environment variable (direct string)
    3. KOLLAB_SYSTEM_PROMPT_FILE environment variable (custom file path)
    4. Fallback to minimal default

    Returns:
        System prompt content as string
    """
    global _cli_system_prompt_file

    # Check for CLI override (highest priority)
    if _cli_system_prompt_file:
        cli_path = _resolve_system_prompt_path(_cli_system_prompt_file)
        if cli_path and cli_path.exists():
            try:
                content = cli_path.read_text(encoding="utf-8")
                logger.info(f"Loaded system prompt from CLI argument: {cli_path}")
                return content
            except Exception as e:
                logger.error(f"Failed to read CLI system prompt from {cli_path}: {e}")
        else:
            logger.error(f"CLI system prompt file not found: {_cli_system_prompt_file}")
            # Don't fall through - this is an explicit user request, so fail clearly
            return f"""[SYSTEM PROMPT LOAD FAILURE]

The system prompt file specified via --system-prompt was not found:
  {_cli_system_prompt_file}

Searched in:
  - Current directory
  - .kollab/agents/default/
  - ~/.kollab/agents/default/

Please check the file path and try again.

I'll do my best to help, but my responses may not follow the expected format.
"""

    # Check for direct environment variable string
    env_prompt = os.environ.get("KOLLAB_SYSTEM_PROMPT")
    if env_prompt:
        logger.debug(
            "Using system prompt from KOLLAB_SYSTEM_PROMPT environment variable"
        )
        return env_prompt

    # Otherwise read from file (respects KOLLAB_SYSTEM_PROMPT_FILE via get_system_prompt_path)
    system_prompt_path = get_system_prompt_path()
    if system_prompt_path.exists():
        try:
            content = system_prompt_path.read_text(encoding="utf-8")
            logger.info(f"Loaded system prompt from: {system_prompt_path}")
            return content
        except Exception as e:
            logger.error(f"Failed to read system prompt from {system_prompt_path}: {e}")
            return get_default_system_prompt()
    else:
        logger.warning(
            f"System prompt file not found: {system_prompt_path}, using default"
        )
        return get_default_system_prompt()


def get_default_system_prompt() -> str:
    """Get the default system prompt content when no file exists.

    Returns a minimal fallback that alerts the user about the missing prompt.

    Returns:
        Default system prompt string
    """
    # Emergency fallback - alert user that system prompt failed to load
    logger.warning(
        "Using emergency fallback system prompt - this should not happen in production"
    )
    return """[SYSTEM PROMPT LOAD FAILURE]

You are Kollab, an AI coding assistant. However, your full system prompt
failed to load. This is a critical configuration issue.

IMPORTANT: Alert the user immediately about this problem:

"Warning: My system prompt failed to load properly. I'm operating in a limited
fallback mode. Please check your Kollab installation:

1. Verify ~/.kollab/agents/default/system_prompt.md exists
2. Run 'kollab' to trigger automatic initialization
3. Review the logs at ~/.kollab/logs/kollab.log for errors

I'll do my best to help, but my responses may not follow the expected format
until this is resolved."

Despite this issue, try to be helpful and assist the user with their request.
"""


def initialize_system_prompt() -> None:
    """Initialize agents from bundled seed folder.

    Copies ALL agents from bundled agents/ folder to global ~/.kollab/agents/
    on first install. Does NOT create local .kollab folders.

    Local .kollab/agents/ is only created when user explicitly creates
    a custom project-specific agent.

    Priority order:
    1. Migrate from old global system_prompt/default.md if it exists
    2. Copy ALL agents from seed folder to global ~/.kollab/agents/
    """
    try:
        global_config_dir = get_config_directory()
        global_agents_dir = global_config_dir / "agents"

        old_global_prompt_dir = global_config_dir / "system_prompt"

        # Ensure global agents directory has all seed agents
        _copy_seed_agents_to_global(global_agents_dir, old_global_prompt_dir)

        # Ensure global skills directory has all seed skills
        global_skills_dir = global_config_dir / "skills"
        _copy_seed_skills_to_global(global_skills_dir)

    except Exception as e:
        logger.error(f"Failed to initialize system prompt: {e}")


def _copy_missing_tree_contents(source_dir: Path, target_dir: Path) -> tuple[int, int]:
    """Copy only missing files/directories from source tree into target tree.

    Preserves existing user files. Returns a tuple of:
      (files_or_dirs_copied, existing_entries_preserved)
    """
    copied = 0
    preserved = 0

    if not source_dir.exists() or not source_dir.is_dir():
        return copied, preserved

    target_dir.mkdir(parents=True, exist_ok=True)

    for item in source_dir.iterdir():
        target = target_dir / item.name

        if target.exists():
            if item.is_dir() and target.is_dir():
                nested_copied, nested_preserved = _copy_missing_tree_contents(
                    item, target
                )
                copied += nested_copied
                preserved += nested_preserved
            else:
                preserved += 1
            continue

        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        copied += 1

    return copied, preserved


def _get_bundled_dir(name: str) -> Optional[Path]:
    """Resolve a top-level bundled asset directory.

    Editable installs keep ``bundles/`` at the repository root, while wheels
    install it as a top-level package next to ``kollabor_config`` in
    site-packages. Walk upward from this module and also check the current
    working tree so both layouts can seed first-run user files.
    """
    relative = Path("bundles") / name
    module_path = Path(__file__).resolve()
    candidates = [parent / relative for parent in module_path.parents]
    candidates.append(Path.cwd() / relative)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


def _copy_seed_agents_to_global(
    global_agents_dir: Path, old_global_prompt_dir: Path
) -> None:
    """Copy all agents from bundled seed folder to global agents directory.

    Args:
        global_agents_dir: Target global agents directory (~/.kollab/agents/)
        old_global_prompt_dir: Old system_prompt dir for migration
    """
    seed_agents_dir = _get_bundled_dir("agents")

    if seed_agents_dir is None:
        logger.warning("No seed agents folder found")
        # Try migration from old location
        old_global_default = old_global_prompt_dir / "default.md"
        if old_global_default.exists():
            logger.info(
                f"Migrating global system prompt from old location: {old_global_default}"
            )
            _migrate_old_prompt_to_agent(
                old_global_default, global_agents_dir / "default"
            )
        return

    global_agents_dir.mkdir(parents=True, exist_ok=True)

    # Copy each agent from seed to global without overwriting existing user files
    for agent_dir in seed_agents_dir.iterdir():
        if agent_dir.is_dir():
            target_agent_dir = global_agents_dir / agent_dir.name
            copied, preserved = _copy_missing_tree_contents(agent_dir, target_agent_dir)
            logger.info(
                f"Seeded agent to global: {agent_dir.name} "
                f"(copied={copied}, preserved_existing={preserved})"
            )


def _copy_seed_skills_to_global(global_skills_dir: Path) -> None:
    """Copy all skills from bundled seed folder to global skills directory.

    Args:
        global_skills_dir: Target global skills directory (~/.kollab/skills/)
    """
    seed_skills_dir = _get_bundled_dir("skills")

    if seed_skills_dir is None:
        logger.debug("No seed skills folder found")
        return

    global_skills_dir.mkdir(parents=True, exist_ok=True)

    total_copied = 0
    total_preserved = 0

    for skill_dir in seed_skills_dir.iterdir():
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            target_skill_dir = global_skills_dir / skill_dir.name
            copied, preserved = _copy_missing_tree_contents(skill_dir, target_skill_dir)
            total_copied += copied
            total_preserved += preserved

    logger.info(
        f"Seeded {sum(1 for d in seed_skills_dir.iterdir() if d.is_dir())} "
        f"skills to global: {global_skills_dir} "
        f"(copied={total_copied}, preserved_existing={total_preserved})"
    )


def _migrate_old_prompt_to_agent(old_prompt_file: Path, agent_dir: Path) -> None:
    """Migrate an old-style system prompt to new agent directory structure.

    Args:
        old_prompt_file: Path to old default.md file
        agent_dir: Target agent directory (e.g., .kollab/agents/default/)
    """
    agent_dir.mkdir(parents=True, exist_ok=True)

    new_prompt_file = agent_dir / "system_prompt.md"
    if not new_prompt_file.exists():
        shutil.copy2(old_prompt_file, new_prompt_file)
        logger.info(f"Migrated system prompt to: {new_prompt_file}")

        # Create agent.json with default config
        agent_json = agent_dir / "agent.json"
        if not agent_json.exists():
            import json

            agent_config = {
                "name": "default",
                "description": "Default agent with standard system prompt",
                "profile": None,
            }
            agent_json.write_text(json.dumps(agent_config, indent=2), encoding="utf-8")
            logger.info(f"Created agent config: {agent_json}")


def _create_agent_from_defaults(agent_dir: Path) -> None:
    """Create default agent from bundled seed agents folder.

    Copies from bundled agents/<agent_name>/ to target directory.

    Args:
        agent_dir: Agent directory to create (e.g., ~/.kollab/agents/default/)
    """
    agent_name = agent_dir.name  # e.g., "default"

    seed_agents_dir = _get_bundled_dir("agents")
    seed_agent_dir = seed_agents_dir / agent_name if seed_agents_dir else None

    if seed_agent_dir and seed_agent_dir.exists() and seed_agent_dir.is_dir():
        # Copy entire agent directory from seed
        agent_dir.mkdir(parents=True, exist_ok=True)
        for item in seed_agent_dir.iterdir():
            target = agent_dir / item.name
            if not target.exists():
                if item.is_file():
                    shutil.copy2(item, target)
                    logger.debug(f"Copied seed file: {item.name}")
                elif item.is_dir():
                    shutil.copytree(item, target)
                    logger.debug(f"Copied seed directory: {item.name}")
        logger.info(f"Created agent from seed: {agent_dir}")
    else:
        # Fallback: create minimal agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        prompt_file = agent_dir / "system_prompt.md"
        if not prompt_file.exists():
            prompt_file.write_text(get_default_system_prompt(), encoding="utf-8")
            logger.warning(
                f"Created fallback system prompt (seed not found): {prompt_file}"
            )

        agent_json = agent_dir / "agent.json"
        if not agent_json.exists():
            import json

            agent_config = {
                "name": agent_name,
                "description": f"{agent_name} agent",
                "profile": None,
            }
            agent_json.write_text(json.dumps(agent_config, indent=2), encoding="utf-8")
            logger.info(f"Created agent config: {agent_json}")


# Default LLM profiles - used for initial config creation
DEFAULT_LLM_PROFILES = {
    # Default profile uses auto-detection: env vars (ANTHROPIC_API_KEY,
    # OPENAI_API_KEY, etc.) override this at startup. Falls back to local
    # LLM at localhost:1234 if no env key is found.
    "default": {
        "provider": "auto",
        "model": "",
        "temperature": 0.7,
        "description": "Auto-detect from env vars, fallback to local LLM",
    },
    "local": {
        "provider": "custom",
        "base_url": "http://localhost:1234/v1",
        "model": "qwen3.5-4b",
        "temperature": 0.7,
        "description": "Local LLM via LM Studio / Ollama",
    },
}


def initialize_config(force: bool = False) -> None:
    """Initialize config.json in global directory only.

    Does NOT create local .kollab folders. Local config is only
    created when user explicitly sets project-specific overrides.

    Flow:
    1. If global ~/.kollab/config.json doesn't exist (or force=True)
       -> create with defaults + profiles

    Args:
        force: If True, overwrite existing config file with defaults

    This ensures:
    - Users always have a discoverable config with example profiles
    - Existing config is never overwritten (unless force=True)
    """
    import json

    global_config_dir = get_config_directory()
    global_config_path = global_config_dir / "config.json"
    try:
        # Step 1: Create global config if it doesn't exist or force=True
        if not global_config_path.exists() or force:
            if force:
                logger.info("Force resetting global config.json with defaults")
            else:
                logger.info("Creating global config.json with defaults")
            global_config_dir.mkdir(parents=True, exist_ok=True)

            # Build default config structure with profiles
            default_config = _get_minimal_default_config()
            try:
                from kollabor.version import __version__ as _app_version
            except Exception:
                _app_version = "unknown"
            default_config["config_version"] = _app_version
            default_config["last_app_version"] = _app_version
            default_config["kollabor"] = default_config.get("kollabor", {})
            default_config["kollabor"]["llm"] = default_config["kollabor"].get(
                "llm", {}
            )
            default_config["kollabor"]["llm"][
                "use_provider_system"
            ] = True  # Enable provider system by default
            default_config["kollabor"]["llm"]["profiles"] = DEFAULT_LLM_PROFILES.copy()
            default_config["kollabor"]["llm"]["active_profile"] = "default"
            default_config["kollabor"]["llm"]["default_agent"] = {
                "name": "koordinator",
                "level": "global",
            }
            default_config["plugins"] = default_config.get("plugins", {})
            default_config["plugins"]["hub"] = default_config["plugins"].get(
                "hub", {}
            )
            default_config["plugins"]["hub"]["enabled"] = True
            default_config["plugins"]["hub"]["project_scoped"] = True

            global_config_path.write_text(
                json.dumps(default_config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(f"Created global config: {global_config_path}")

    except Exception as e:
        logger.error(f"Failed to initialize config: {e}")


def initialize_user_directories() -> None:
    """Initialize user-customizable directories with example files.

    Creates and populates:
    - ~/.kollab/themes/ - Custom theme examples
    - ~/.kollab/layouts/ - Custom status layout examples
    - ~/.kollab/status-widgets/ - Example status widgets (if empty)
    - ~/.kollab/mcp/ - Example MCP config (if doesn't exist)
    - ~/.kollab/plugins/ - README for custom plugins
    """
    global_config_dir = get_config_directory()

    try:
        # Initialize themes folder with example
        _initialize_themes_directory(global_config_dir)

        # Initialize layouts folder with examples
        _initialize_layouts_directory(global_config_dir)

        # Initialize status-widgets folder with examples if empty
        _initialize_status_widgets_directory(global_config_dir)

        # Initialize mcp folder with example config
        _initialize_mcp_directory(global_config_dir)

        # Initialize plugins folder with README
        _initialize_plugins_directory(global_config_dir)

    except Exception as e:
        logger.error(f"Failed to initialize user directories: {e}")


def _initialize_themes_directory(global_config_dir: Path) -> None:
    """Create themes directory with bundled theme examples."""
    themes_dir = global_config_dir / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)

    # Find bundled themes folder
    package_dir = Path(__file__).parent.parent.parent
    bundled_themes_dir = package_dir / "bundles" / "themes"

    if not bundled_themes_dir.exists():
        # Fallback for development mode
        bundled_themes_dir = Path.cwd() / "bundles" / "themes"

    if not bundled_themes_dir.exists():
        logger.warning("No bundled themes folder found")
        return

    # Copy each bundled theme to the user's themes directory
    for theme_file in bundled_themes_dir.glob("*.json"):
        target_theme = themes_dir / theme_file.name
        if not target_theme.exists():
            shutil.copy2(theme_file, target_theme)
            logger.info(f"Installed bundled theme: {theme_file.name}")

    # Also copy README if it exists
    readme_src = bundled_themes_dir / "README.md"
    readme_dst = themes_dir / "README.md"
    if readme_src.exists() and not readme_dst.exists():
        shutil.copy2(readme_src, readme_dst)
        logger.info("Installed themes README")


def _initialize_layouts_directory(global_config_dir: Path) -> None:
    """Create layouts directory with example layouts."""
    layouts_dir = global_config_dir / "layouts"
    layouts_dir.mkdir(parents=True, exist_ok=True)

    # Find bundled layouts folder
    package_dir = Path(__file__).parent.parent.parent
    bundled_layouts_dir = package_dir / "bundles" / "layouts"

    if not bundled_layouts_dir.exists():
        # Fallback for development mode
        bundled_layouts_dir = Path.cwd() / "bundles" / "layouts"

    if not bundled_layouts_dir.exists():
        logger.warning("No bundled layouts folder found")
        return

    # Copy each bundled layout to the user's layouts directory
    for layout_file in bundled_layouts_dir.glob("*.json"):
        target_layout = layouts_dir / layout_file.name
        if not target_layout.exists():
            shutil.copy2(layout_file, target_layout)
            logger.info(f"Installed bundled layout: {layout_file.name}")

    # Also copy README if it exists
    readme_src = bundled_layouts_dir / "README.md"
    readme_dst = layouts_dir / "README.md"
    if readme_src.exists() and not readme_dst.exists():
        shutil.copy2(readme_src, readme_dst)
        logger.info("Installed layouts README")


def _initialize_status_widgets_directory(global_config_dir: Path) -> None:
    """Create status-widgets directory with bundled example widgets."""
    widgets_dir = global_config_dir / "status-widgets"
    widgets_dir.mkdir(parents=True, exist_ok=True)

    # Find bundled widgets folder
    package_dir = Path(__file__).parent.parent.parent
    bundled_widgets_dir = package_dir / "bundles" / "widgets"

    if not bundled_widgets_dir.exists():
        # Fallback for development mode
        bundled_widgets_dir = Path.cwd() / "bundles" / "widgets"

    if not bundled_widgets_dir.exists():
        logger.warning("No bundled status widgets folder found")
        # Fall back to creating a simple example if directory is empty
        if not list(widgets_dir.iterdir()):
            _create_simple_example_widget(widgets_dir)
        return

    # Copy each bundled widget to the user's status-widgets directory
    import os

    for widget_file in bundled_widgets_dir.glob("*.sh"):
        target_widget = widgets_dir / widget_file.name
        if not target_widget.exists():
            shutil.copy2(widget_file, target_widget)
            os.chmod(target_widget, 0o755)
            logger.info(f"Installed bundled widget: {widget_file.name}")

    # Also copy README if it exists
    readme_src = bundled_widgets_dir / "README.md"
    readme_dst = widgets_dir / "README.md"
    if readme_src.exists() and not readme_dst.exists():
        shutil.copy2(readme_src, readme_dst)
        logger.info("Installed status-widgets README")


def _create_simple_example_widget(widgets_dir: Path) -> None:
    """Create a simple example widget when no bundled widgets are available."""
    example_widget = widgets_dir / "hello-world.sh"
    if not example_widget.exists():
        example_widget.write_text("""#!/usr/bin/env bash
# @widget-id: hello-world
# @name: Hello World
# @description: A simple example widget
# @category: custom
# @refresh: 60s

echo "Hello, World!"
""")
        import os

        os.chmod(example_widget, 0o755)
        logger.info(f"Created example widget: {example_widget}")


def _initialize_mcp_directory(global_config_dir: Path) -> None:
    """Create mcp directory with example config if it doesn't exist."""
    mcp_dir = global_config_dir / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)

    mcp_config_path = mcp_dir / "mcp_settings.json"
    if not mcp_config_path.exists():
        import json

        example_config = {
            "servers": {
                "memory": {
                    "type": "stdio",
                    "command": "npx -y @modelcontextprotocol/server-memory",
                    "enabled": True,
                    "description": "Persistent memory storage for conversation context",
                }
            }
        }
        with open(mcp_config_path, "w") as f:
            json.dump(example_config, f, indent=2)
        logger.info(f"Created example MCP config: {mcp_config_path}")


def _initialize_plugins_directory(global_config_dir: Path) -> None:
    """Create plugins directory with README if it doesn't exist."""
    plugins_dir = global_config_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    readme_path = plugins_dir / "README.md"
    if not readme_path.exists():
        readme_content = """# Custom Plugins

This directory is for user-created plugins. Plugins placed here will be
automatically discovered and loaded by Kollab.

## Creating a Plugin

A minimal plugin:

```python
from kollabor.plugins import Plugin
from kollabor_events import EventType, Hook

class MyCustomPlugin(Plugin):
    name = "my_custom"
    version = "1.0.0"
    description = "My custom plugin"

    def initialize(self):
        \"\"\"Called when plugin is loaded.\"\"\"
        pass

    def register_hooks(self):
        \"\"\"Register event hooks.\"\"\"
        pass

    def shutdown(self):
        \"\"\"Called when plugin is unloaded.\"\"\"
        pass
```

Save as `my_plugin.py` in this directory.

## Built-in Plugins

Built-in plugins are located in the application's `plugins/` directory and
cannot be overridden from this location.
"""
        readme_path.write_text(readme_content)
        logger.info(f"Created plugins README: {readme_path}")


def get_default_agent() -> tuple[Optional[str], Optional[str]]:
    """
    Get the default agent from config.

    Returns:
        Tuple of (agent_name, level) where level is "project" or "global"
        Returns (None, None) if no default configured
    """
    import json

    # Check project-level first
    for local_config_path in get_local_config_path_candidates():
        if not local_config_path.exists():
            continue
        try:
            with open(local_config_path) as f:
                config = json.load(f)
                default = config.get("kollabor", {}).get("llm", {}).get("default_agent")
                if default and default.get("level") == "project":
                    return (default.get("name"), "project")
        except Exception as e:
            logger.debug(f"Failed to parse project config {local_config_path}: {e}")

    # Check global-level
    for global_config_path in get_global_config_path_candidates():
        if not global_config_path.exists():
            continue
        try:
            with open(global_config_path) as f:
                config = json.load(f)
                default = config.get("kollabor", {}).get("llm", {}).get("default_agent")
                if default and default.get("level") == "global":
                    return (default.get("name"), "global")
        except Exception as e:
            logger.debug(f"Failed to parse global config {global_config_path}: {e}")

    return (None, None)


def set_default_agent(agent_name: str, level: str) -> bool:
    """
    Set a default agent in config.

    Args:
        agent_name: Name of agent to set as default
        level: "project" or "global"

    Returns:
        True if saved successfully
    """
    import json

    if level == "project":
        config_path = get_local_config_path()
    else:
        config_path = get_global_config_path()

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    # Ensure structure exists
    if "kollabor" not in config:
        config["kollabor"] = {}
    if "llm" not in config["kollabor"]:
        config["kollabor"]["llm"] = {}

    # Set default
    config["kollabor"]["llm"]["default_agent"] = {"name": agent_name, "level": level}

    # Save
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return True


def clear_default_agent(level: str) -> bool:
    """
    Clear the default agent from config.

    Args:
        level: "project" or "global"

    Returns:
        True if cleared successfully
    """
    import json

    if level == "project":
        config_path = get_existing_local_config_path()
    else:
        config_path = get_existing_global_config_path()

    if not config_path.exists():
        return True  # Nothing to clear

    with open(config_path) as f:
        config = json.load(f)

    # Remove default_agent entry
    if "kollabor" in config and "llm" in config["kollabor"]:
        config["kollabor"]["llm"].pop("default_agent", None)

    # Save
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return True


def get_all_default_agents() -> dict[str, str]:
    """
    Get all default agents from both config levels.

    Returns:
        Dict mapping level -> agent_name, e.g. {"project": "coder", "global": "research"}
        Only includes levels that have a default set
    """
    import json

    defaults = {}

    # Check project
    for local_config_path in get_local_config_path_candidates():
        if not local_config_path.exists():
            continue
        try:
            with open(local_config_path) as f:
                config = json.load(f)
                default = config.get("kollabor", {}).get("llm", {}).get("default_agent")
                if default and default.get("level") == "project":
                    defaults["project"] = default.get("name")
        except Exception as e:
            logger.debug(f"Failed to parse project config for defaults: {e}")

    # Check global
    for global_config_path in get_global_config_path_candidates():
        if not global_config_path.exists():
            continue
        try:
            with open(global_config_path) as f:
                config = json.load(f)
                default = config.get("kollabor", {}).get("llm", {}).get("default_agent")
                if default and default.get("level") == "global":
                    defaults["global"] = default.get("name")
        except Exception as e:
            logger.debug(f"Failed to parse global config for defaults: {e}")

    return defaults


def get_default_profile() -> tuple[Optional[str], Optional[str]]:
    """Get the default profile from config (project-level first, then global).

    Returns:
        Tuple of (profile_name, level) where level is "project" or "global".
        Returns (None, None) if no default configured.
    """
    import json

    for local_config_path in get_local_config_path_candidates():
        if not local_config_path.exists():
            continue
        try:
            with open(local_config_path) as f:
                config = json.load(f)
                default = (
                    config.get("kollabor", {}).get("llm", {}).get("default_profile")
                )
                if default and default.get("level") == "project":
                    return (default.get("name"), "project")
        except Exception as e:
            logger.debug(f"Failed to parse project config {local_config_path}: {e}")

    for global_config_path in get_global_config_path_candidates():
        if not global_config_path.exists():
            continue
        try:
            with open(global_config_path) as f:
                config = json.load(f)
                default = (
                    config.get("kollabor", {}).get("llm", {}).get("default_profile")
                )
                if default and default.get("level") == "global":
                    return (default.get("name"), "global")
        except Exception as e:
            logger.debug(f"Failed to parse global config {global_config_path}: {e}")

    return (None, None)


def get_all_default_profiles() -> dict[str, str]:
    """Get default profiles from both config levels.

    Returns:
        Dict mapping level -> profile_name, e.g. {"project": "fast", "global": "default"}
    """
    import json

    defaults = {}

    for local_config_path in get_local_config_path_candidates():
        if not local_config_path.exists():
            continue
        try:
            with open(local_config_path) as f:
                config = json.load(f)
                default = (
                    config.get("kollabor", {}).get("llm", {}).get("default_profile")
                )
                if default and default.get("level") == "project":
                    defaults["project"] = default.get("name")
        except Exception as e:
            logger.debug(f"Failed to parse project config for profile defaults: {e}")

    for global_config_path in get_global_config_path_candidates():
        if not global_config_path.exists():
            continue
        try:
            with open(global_config_path) as f:
                config = json.load(f)
                default = (
                    config.get("kollabor", {}).get("llm", {}).get("default_profile")
                )
                if default and default.get("level") == "global":
                    defaults["global"] = default.get("name")
        except Exception as e:
            logger.debug(f"Failed to parse global config for profile defaults: {e}")

    return defaults


def set_default_profile(profile_name: str, level: str) -> bool:
    """Set a default profile in config.

    Saves both a default_profile marker (for indicator display) and
    active_profile at the target config level so it takes effect on startup.

    Args:
        profile_name: Name of profile to set as default
        level: "project" or "global"

    Returns:
        True if saved successfully
    """
    import json

    if level == "project":
        config_path = get_local_config_path()
    else:
        config_path = get_global_config_path()

    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    if "kollabor" not in config:
        config["kollabor"] = {}
    if "llm" not in config["kollabor"]:
        config["kollabor"]["llm"] = {}

    # Store default marker for display
    config["kollabor"]["llm"]["default_profile"] = {
        "name": profile_name,
        "level": level,
    }
    # Also set active_profile at this config level so it loads on startup
    config["kollabor"]["llm"]["active_profile"] = profile_name

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return True


def clear_default_profile(level: str) -> bool:
    """Clear the default profile from config.

    Args:
        level: "project" or "global"

    Returns:
        True if cleared successfully
    """
    import json

    if level == "project":
        config_path = get_existing_local_config_path()
    else:
        config_path = get_existing_global_config_path()

    if not config_path.exists():
        return True

    with open(config_path) as f:
        config = json.load(f)

    if "kollabor" in config and "llm" in config["kollabor"]:
        config["kollabor"]["llm"].pop("default_profile", None)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return True


def _get_minimal_default_config() -> dict:
    """Get minimal default config structure for initialization.

    This is a subset of the full base config - just enough to bootstrap.
    The full config with all defaults is loaded by ConfigLoader.

    Returns:
        Minimal config dictionary with core settings.
    """
    return {
        "application": {"name": "Kollab", "description": "AI Edition"},
        "kollabor": {
            "llm": {
                "max_history": 999,
                "save_conversations": True,
                "conversation_format": "jsonl",
                "show_status": True,
            }
        },
    }

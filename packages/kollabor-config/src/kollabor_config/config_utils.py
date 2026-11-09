"""Configuration utilities for Kollab."""

import errno
import hashlib
import io
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

from .atomic_write import write_text_atomic

logger = logging.getLogger(__name__)

# Platform check
IS_WINDOWS = sys.platform == "win32"
_SEED_DIR_FD_SUPPORTED = all(
    function in os.supports_dir_fd for function in (os.open, os.mkdir, os.link, os.unlink)
)

# Canonical runtime/config roots for the public Kollab app.
APP_CONFIG_DIR_NAME = ".kollab"


def get_config_directory() -> Path:
    """Get the global Kollab runtime/config directory."""
    return Path.home() / APP_CONFIG_DIR_NAME


def get_config_directory_candidates() -> list[Path]:
    """Return global config roots in read precedence order."""
    return [get_config_directory()]


def get_local_config_directory(project_path: Optional[Path] = None) -> Path:
    """Get the project-local Kollab runtime/config directory.

    Of ``project_path``, or of the current working directory when none is given.
    """
    return (project_path or Path.cwd()) / APP_CONFIG_DIR_NAME


def get_local_config_directory_candidates(
    project_path: Optional[Path] = None,
) -> list[Path]:
    """Return project-local config roots in read precedence order."""
    return [get_local_config_directory(project_path)]


def get_existing_config_directory() -> Path:
    """Return the global config root."""
    return get_config_directory()


def get_global_config_path() -> Path:
    """Return the preferred global config.json path."""
    return get_config_directory() / "config.json"


def get_global_config_path_candidates() -> list[Path]:
    """Return global config.json candidates in read precedence order."""
    return [
        directory / "config.json" for directory in get_config_directory_candidates()
    ]


def get_existing_global_config_path() -> Path:
    """Return the global config.json path."""
    return get_global_config_path()


def get_local_config_path() -> Path:
    """Return the preferred project-local config.json path."""
    return get_local_config_directory() / "config.json"


def get_local_config_path_candidates(project_path: Optional[Path] = None) -> list[Path]:
    """Return project-local config.json candidates in read precedence order.

    ``project_path`` reads another folder's own layers (the web UI lists the
    chosen workspace); None is this process's working directory.
    """
    return [
        directory / "config.json"
        for directory in get_local_config_directory_candidates(project_path)
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


class ProjectStateLocationError(RuntimeError):
    """Project state cannot safely be identified or claimed."""


def _legacy_encode_project_path(project_path: Path) -> str:
    """The exact pre-#439 encoder, including its Windows drive-relative prefix."""
    encoded = str(project_path.resolve()).replace("/", "_").replace("\\", "_")
    return encoded.lstrip("_")


_PROJECT_OWNER_FILE = ".kollab-project-owner.json"
_PROJECT_OWNER_WAIT_SECONDS = 2.0
_PROJECT_OWNER_POLL_SECONDS = 0.05


def _project_owner(project: Path, identity: str | None) -> dict:
    return {"version": 1, "project": os.path.normcase(str(project.resolve())), "identity": identity or ""}


def _read_project_owner(directory: Path) -> dict | None:
    """Read a private ownership claim; absence is distinct from an invalid claim."""
    from . import private_fs

    marker = directory / _PROJECT_OWNER_FILE
    try:
        fd = private_fs.open_nofollow(marker, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectStateLocationError(f"Cannot read project ownership marker: {marker}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not private_fs.owned_private(marker, info, mask=0o022):
            raise ProjectStateLocationError(f"Unsafe project ownership marker: {marker}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(16385)
        value = json.loads(raw.decode("utf-8"))
        if (
            len(raw) > 16384 or not isinstance(value, dict) or set(value) != {"version", "project", "identity"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["project"], str) or not isinstance(value["identity"], str)
        ):
            raise ValueError("invalid ownership marker")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProjectStateLocationError(f"Invalid project ownership marker: {marker}") from exc
    finally:
        os.close(fd)


def _write_project_owner(directory: Path, owner: dict) -> None:
    """Publish a complete claim without replacing a competing claim."""
    fd, temporary = tempfile.mkstemp(prefix=".project-owner-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(owner, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, directory / _PROJECT_OWNER_FILE)
    finally:
        os.unlink(temporary)


def _windows_project_state_dir(
    project_path: Path,
    root: Path,
    *,
    selection_prefix: str = "KOLLAB_PROJECT_DATA",
    identity: str | None = None,
    create_new: bool = True,
) -> Path:
    """Reuse an existing Windows project directory without moving or merging it."""
    project = project_path.resolve()
    current = root / "projects" / encode_project_path(project)
    legacy_raw = root / "projects" / _legacy_encode_project_path(project)
    selection = os.environ.get(f"{selection_prefix}_DIR", "").strip()
    selected_project = os.environ.get(f"{selection_prefix}_PROJECT", "").strip()
    selected_identity = os.environ.get(f"{selection_prefix}_IDENTITY", "").strip() if identity else None
    if selection or selected_project or selected_identity:
        if not selection or not selected_project or (identity and not selected_identity):
            raise ProjectStateLocationError(f"Incomplete {selection_prefix} selection")
        applies = os.path.normcase(str(Path(selected_project).resolve())) == os.path.normcase(str(project))
        if identity:
            applies = applies and selected_identity == identity
        if not applies:
            selection = ""
    if selection:
        selected = Path(selection).expanduser()
        if not selected.is_absolute() or not selected.is_dir():
            raise ProjectStateLocationError(f"{selection_prefix}_DIR must name an existing absolute directory")
        recorded = _read_project_owner(selected)
        if recorded is not None and recorded != _project_owner(project, identity):
            raise ProjectStateLocationError(f"Selected project state belongs to another project: {selected}")
        return selected.resolve()

    legacy_paths = [legacy_raw]
    cross_drive = not legacy_raw.is_absolute()
    if cross_drive:
        # Previous cross-drive joins were relative to the old process's D: CWD.
        # Check the current resolution, the project CWD, and the drive root;
        # other historical drive CWDs cannot be reconstructed without an index.
        tail = str(legacy_raw)[len(legacy_raw.drive) :]
        legacy_paths = [legacy_raw.resolve(), project / tail, Path(project.anchor) / tail]
    existing = []
    for candidate in [current, *legacy_paths]:
        try:
            info = candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            raise ProjectStateLocationError(f"Cannot inspect project state location: {candidate}") from exc
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if not stat.S_ISDIR(info.st_mode) or reparse:
            raise ProjectStateLocationError(f"Project state location is not a regular directory: {candidate}")
        resolved = candidate.resolve()
        if all(os.path.normcase(str(resolved)) != os.path.normcase(str(p)) for p in existing):
            existing.append(resolved)
    if len(existing) > 1:
        raise ProjectStateLocationError(
            f"Multiple project state locations exist; select one with {selection_prefix}_PROJECT "
            f"and {selection_prefix}_DIR: "
            + ", ".join(str(path) for path in existing)
        )
    if existing:
        recorded = _read_project_owner(existing[0])
        if (
            recorded is None and identity is None
            and os.path.normcase(str(existing[0])) == os.path.normcase(str(current.resolve()))
        ):
            # A second process may observe the new directory between mkdir and
            # the atomic marker publication. Never claim it on its behalf.
            deadline = time.monotonic() + _PROJECT_OWNER_WAIT_SECONDS
            while recorded is None and time.monotonic() < deadline:
                time.sleep(min(_PROJECT_OWNER_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
                recorded = _read_project_owner(existing[0])
        if recorded == _project_owner(project, identity):
            # A legacy candidate might have appeared while we waited for the
            # claim. Do not start using the new location if that happened.
            for candidate in legacy_paths:
                if os.path.normcase(str(candidate.resolve())) == os.path.normcase(str(existing[0])):
                    continue
                try:
                    candidate.lstat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except OSError as exc:
                    raise ProjectStateLocationError(f"Cannot inspect project state location: {candidate}") from exc
                raise ProjectStateLocationError(f"Multiple project state locations exist: {existing[0]}, {candidate}")
            return existing[0]
        if recorded is not None:
            raise ProjectStateLocationError(f"Project state belongs to another project: {existing[0]}")
        identity_help = f", {selection_prefix}_IDENTITY" if identity else ""
        raise ProjectStateLocationError(
            f"Cannot establish ownership of existing project state: {existing[0]}. Inspect it, then select it "
            f"with {selection_prefix}_PROJECT{identity_help} and {selection_prefix}_DIR"
        )
    if identity or not create_new:
        # Read-side probes must not claim a project or create a nested vault.
        return current
    try:
        current.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ProjectStateLocationError(f"Could not create project state parent: {current.parent}") from exc
    try:
        current.mkdir(mode=0o700)
    except FileExistsError:
        # A competing startup created it after our scan. Read its claim; an
        # incomplete or absent claim is never adopted as this project.
        return _windows_project_state_dir(
            project, root, selection_prefix=selection_prefix, identity=identity, create_new=create_new
        )
    except OSError as exc:
        raise ProjectStateLocationError(f"Could not create project state: {current}") from exc
    try:
        _write_project_owner(current, _project_owner(project, identity))
    except OSError as exc:
        raise ProjectStateLocationError(f"Could not claim new project state: {current}") from exc
    # An older process may have created a legacy candidate while we claimed.
    for candidate in legacy_paths:
        if candidate != current:
            try:
                candidate.lstat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                raise ProjectStateLocationError(f"Cannot inspect project state location: {candidate}") from exc
            raise ProjectStateLocationError(f"A legacy project state appeared during startup: {candidate}")
    return current.resolve()


def get_project_data_dir_candidates(
    project_path: Optional[Path] = None, *, create_new: bool = True
) -> list[Path]:
    """Return project data directories in read precedence order.

    Windows creates and claims a project's state directory the first time it is looked up;
    ``create_new=False`` only reads, for a folder nobody has started in.
    """
    if project_path is None:
        project_path = Path.cwd()

    if IS_WINDOWS:
        return [
            _windows_project_state_dir(project_path, directory, create_new=create_new)
            for directory in get_config_directory_candidates()
        ]
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
    path = project_path.resolve()
    path_str = str(path)
    if path.drive.endswith(":"):
        # Windows drive prefix "C:" reads as a drive-relative prefix once the
        # name is joined under projects/, so drop the colon: "C:\Users\x" -> "C\Users\x".
        path_str = path.drive[:-1] + path_str[len(path.drive):]
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


def get_local_agents_dir(project_path: Optional[Path] = None) -> Path | None:
    """Get the local agents directory if it exists.

    Checks for .kollab/agents/ in ``project_path``, or in the current working
    directory when none is given.

    Returns:
        Path to local agents directory if it exists, None otherwise
    """
    for local_agents_dir in (
        directory / "agents"
        for directory in get_local_config_directory_candidates(project_path)
    ):
        if local_agents_dir.exists():
            return local_agents_dir
    return None


def get_local_agents_path(project_path: Optional[Path] = None) -> Path:
    """Get the local agents directory path (for creation purposes).

    Unlike get_local_agents_dir(), this returns the path even if it doesn't exist.
    Use this when you need to create the local agents directory.

    Returns:
        Path to .kollab/agents/ in ``project_path``, or in the current working
        directory when none is given
    """
    return get_local_config_directory(project_path) / "agents"


def get_global_agents_dir() -> Path:
    """Get the global agents directory.

    Returns:
        Path to the global agents directory.
    """
    return resolve_global_path("agents")


def get_bundled_agents_dir() -> Path | None:
    """Get the bundled agents (``bundles/agents/<name>/``), if present."""
    return _get_bundled_dir("agents")


def get_agent_tier_dirs(project_path: Optional[Path] = None) -> list[Path]:
    """The agents directories that exist, highest priority first: local, global, bundled.

    For a file that lives beside the agents (``system/hub-*.md``, ``_base/sections``):
    the first tier that has it wins, and a computer nothing has seeded still
    reaches the bundled copy.
    """
    tiers = (
        get_local_agents_dir(project_path),
        get_global_agents_dir(),
        get_bundled_agents_dir(),
    )
    return [tier for tier in tiers if tier is not None and tier.is_dir()]


# ── Skill library directories ──────────────────────────────────────


def get_bundled_skills_dir() -> Path | None:
    """Get the bundled skills directory shipped with the package.

    Checks package install paths and cwd/bundles/skills/ for dev mode.

    Returns:
        Path to bundled skills directory if it exists, None otherwise
    """
    return _get_bundled_dir("skills")


def get_bundled_rosters_dir() -> Path | None:
    """Get the bundled persona rosters (``bundles/rosters/*.json``), if present."""
    return _get_bundled_dir("rosters")


def get_bundled_teams_dir() -> Path | None:
    """Get the bundled teams (``bundles/teams/*.json``), if present."""
    return _get_bundled_dir("teams")


def get_global_skills_dir() -> Path:
    """Get the global skills directory.

    Returns:
        Path to the global skills directory.
    """
    return resolve_global_path("skills")


def get_local_skills_dir(project_path: Optional[Path] = None) -> Path | None:
    """Get the local skills directory if it exists.

    Checks for .kollab/skills/ in ``project_path``, or in the current working
    directory when none is given.

    Returns:
        Path to local skills directory if it exists, None otherwise
    """
    for local_skills_dir in (
        directory / "skills"
        for directory in get_local_config_directory_candidates(project_path)
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
    4. Bundled bundles/agents/default/system_prompt.md, so a computer nothing
       has seeded yet (the engine on a new machine) still has the real prompt

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

    # agents/default in every tier, highest priority first: local, global, bundled
    default_dirs = [tier / "default" for tier in get_agent_tier_dirs()]

    # On Windows, prefer default_win.md if it exists (in agent directory)
    if IS_WINDOWS:
        for default_dir in default_dirs:
            win_prompt = default_dir / "system_prompt_win.md"
            if win_prompt.exists():
                logger.debug(f"Using Windows-specific system prompt: {win_prompt}")
                return win_prompt

    for default_dir in default_dirs:
        agent_prompt = default_dir / "system_prompt.md"
        if agent_prompt.exists():
            return agent_prompt

    return get_config_directory() / "agents" / "default" / "system_prompt.md"


def get_system_prompt_content() -> str:
    """Get the system prompt content, checking CLI args, env vars, and files.

    Resolution order:
    1. CLI --system-prompt argument (highest priority)
    2. KOLLAB_SYSTEM_PROMPT environment variable (direct string)
    3. KOLLAB_SYSTEM_PROMPT_FILE environment variable (custom file path)
    4. agents/default/system_prompt.md: local, global, then bundled
       (get_system_prompt_path)
    5. Fallback to minimal default

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
    3. Retire unmodified seed copies of bundled skills (moves, never deletes)
    """
    try:
        global_config_dir = get_config_directory()
        global_agents_dir = global_config_dir / "agents"

        old_global_prompt_dir = global_config_dir / "system_prompt"

        # Ensure global agents directory has all seed agents
        try:
            _copy_seed_agents_to_global(global_agents_dir, old_global_prompt_dir)
        except Exception as e:
            logger.error("Failed to seed bundled agents: %s", e)

        # Skills load from bundles/skills; stale global copies would shadow them
        try:
            _retire_seeded_skills(global_config_dir / "skills")
        except Exception as e:
            logger.error("Failed to retire seeded skills: %s", e)

    except Exception as e:
        logger.error(f"Failed to initialize system prompt: {e}")


def _is_link_or_reparse(path: Path) -> bool:
    """Treat links, junctions, and other Windows reparse entries as user-owned."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


@contextmanager
def _seed_directory(path: Path, *, create: bool):
    """Pin a destination directory without traversing links or reparse points."""
    if os.name == "nt":
        with _windows_seed_directory(path, create=create):
            yield None
        return

    if not _SEED_DIR_FD_SUPPORTED or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("safe directory-relative agent seeding is unavailable")
    absolute = Path(os.path.abspath(path))
    # The OS-supplied home may itself sit beneath a system link (for example
    # macOS /var -> /private/var). Treat the resolved home as the trusted
    # anchor, while still refusing links below it such as ~/.kollab/agents.
    home = Path(os.path.abspath(Path.home()))
    try:
        relative_to_home = absolute.relative_to(home)
    except ValueError:
        pass
    else:
        absolute = home.resolve() / relative_to_home
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _windows_seed_directory(path: Path, *, create: bool):
    """Pin each directory against rename while refusing Windows reparse points."""
    import ctypes
    from ctypes import wintypes

    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_dir = kernel.CreateFileW
    open_dir.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    open_dir.restype = wintypes.HANDLE
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    list_directory = 0x1
    read_attributes = 0x80
    share_read_write = 0x1 | 0x2
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    attribute_tag_info = 9
    directory_attribute = 0x10
    reparse_attribute = 0x400
    absolute = Path(os.path.abspath(path))
    parents = list(reversed([absolute, *absolute.parents]))
    handles = []
    try:
        for directory in parents[1:]:
            if _is_link_or_reparse(directory):
                raise OSError(errno.ELOOP, "refusing linked seed directory", str(directory))
            if create and not directory.exists():
                try:
                    directory.mkdir()
                except FileExistsError:
                    pass
            # FILE_SHARE_DELETE is deliberately omitted, preventing another
            # process from renaming a checked ancestor before publication.
            handle = open_dir(
                str(directory), list_directory | read_attributes, share_read_write, None,
                open_existing, backup_semantics | open_reparse_point, None,
            )
            if handle == invalid:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            info = AttributeTagInfo()
            if not get_info(handle, attribute_tag_info, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & reparse_attribute or not info.attributes & directory_attribute:
                raise OSError(errno.ELOOP, "refusing linked seed directory", str(directory))
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def _copy_missing_tree_contents(source_dir: Path, target_dir: Path) -> tuple[int, int]:
    """Copy missing seed entries, keeping each destination directory pinned."""
    copied = 0
    preserved = 0
    if not source_dir.is_dir():
        return copied, preserved
    if _is_link_or_reparse(target_dir):
        return copied, preserved + 1

    try:
        with _seed_directory(target_dir, create=True) as directory_fd:
            for item in source_dir.iterdir():
                target = target_dir / item.name
                if _is_link_or_reparse(target):
                    preserved += 1
                    continue
                if item.is_dir():
                    try:
                        if directory_fd is None:
                            target.mkdir()
                        else:
                            os.mkdir(item.name, dir_fd=directory_fd)
                        copied += 1
                    except FileExistsError:
                        pass
                    if _is_link_or_reparse(target) or not target.is_dir():
                        preserved += 1
                        continue
                    nested_copied, nested_preserved = _copy_missing_tree_contents(item, target)
                    copied += nested_copied
                    preserved += nested_preserved
                elif _copy_file_if_missing(item, target, directory_fd=directory_fd):
                    copied += 1
                else:
                    preserved += 1
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            logger.warning("Preserving linked or replaced agent seed directory: %s", target_dir)
            return copied, preserved + 1
        raise
    return copied, preserved


def _copy_file_if_missing(
    source: Path, target: Path, *, directory_fd: Optional[int] = None
) -> bool:
    """Copy a complete seed without replacing a concurrently created target."""
    if _is_link_or_reparse(target):
        return False
    with source.open("rb") as source_file:
        info = os.fstat(source_file.fileno())
        return _publish_seed_file(
            source_file,
            target,
            mode=stat.S_IMODE(info.st_mode),
            timestamps=(info.st_atime_ns, info.st_mtime_ns),
            directory_fd=directory_fd,
        )


def _write_text_if_missing(target: Path, content: str) -> bool:
    """Create generated defaults only if no entry already occupies the path."""
    return _publish_seed_file(io.BytesIO(content.encode("utf-8")), target)


def _publish_seed_file(
    source: BinaryIO,
    target: Path,
    *,
    mode: int = 0o600,
    timestamps: Optional[tuple[int, int]] = None,
    directory_fd: Optional[int] = None,
) -> bool:
    """Stage complete bytes in a pinned directory and publish without replacement."""
    if directory_fd is not None:
        return _publish_in_seed_directory(source, target, mode, timestamps, directory_fd)
    with _seed_directory(target.parent, create=False) as pinned:
        return _publish_in_seed_directory(source, target, mode, timestamps, pinned)


def _publish_in_seed_directory(
    source: BinaryIO,
    target: Path,
    mode: int,
    timestamps: Optional[tuple[int, int]],
    directory_fd: Optional[int],
) -> bool:
    if directory_fd is None:
        if _is_link_or_reparse(target) or target.exists():
            return False
        fd, staged_name = tempfile.mkstemp(prefix=f".{target.name}.seed-", dir=target.parent)
    else:
        try:
            os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return False
        staged_name = f".{target.name}.seed-{os.urandom(16).hex()}"
        fd = os.open(
            staged_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )

    try:
        with os.fdopen(fd, "wb") as staged_file:
            shutil.copyfileobj(source, staged_file)
            staged_file.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(staged_file.fileno(), mode)
            if timestamps and os.utime in os.supports_fd:
                os.utime(staged_file.fileno(), ns=timestamps)
            os.fsync(staged_file.fileno())
        try:
            if directory_fd is None:
                os.link(staged_name, target)
            else:
                os.link(
                    staged_name,
                    target.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
        except FileExistsError:
            return False
        except (OSError, NotImplementedError, AttributeError):
            if IS_WINDOWS:
                try:
                    if directory_fd is None:
                        os.rename(staged_name, target)
                    else:
                        os.rename(
                            staged_name,
                            target.name,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                        )
                except FileExistsError:
                    return False
                except (OSError, NotImplementedError, AttributeError) as rename_error:
                    raise OSError(
                        f"Cannot safely publish agent seed {target}: both hard-link "
                        "and no-overwrite rename failed. Existing files were preserved. "
                        "Check filesystem permissions or use the bundled agent where "
                        "no local or global override exists."
                    ) from rename_error
            else:
                try:
                    if directory_fd is None:
                        raise OSError(
                            errno.ENOTSUP,
                            "pinned directory descriptors are required for exclusive rename",
                        )
                    _rename_seed_noreplace(
                        staged_name,
                        target.name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                    )
                except FileExistsError:
                    return False
                except (OSError, NotImplementedError, AttributeError) as rename_error:
                    raise OSError(
                        f"Cannot safely publish agent seed {target}: both hard-link "
                        "and POSIX no-replace rename failed. Existing files were preserved. "
                        "Use a filesystem that supports one of these safe operations, "
                        "or use the bundled agent where no local or global override exists."
                    ) from rename_error
    finally:
        if directory_fd is None:
            Path(staged_name).unlink(missing_ok=True)
        else:
            try:
                os.unlink(staged_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    return True


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
    if _is_link_or_reparse(global_agents_dir) or _is_link_or_reparse(
        global_agents_dir.parent
    ):
        logger.info("Preserving linked global agents directory or parent: %s", global_agents_dir)
        return

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

    # Copy each agent from seed to global without overwriting existing user files
    for agent_dir in seed_agents_dir.iterdir():
        if agent_dir.is_dir():
            target_agent_dir = global_agents_dir / agent_dir.name
            try:
                copied, preserved = _copy_missing_tree_contents(agent_dir, target_agent_dir)
            except OSError:
                if (target_agent_dir / "system_prompt.md").is_file():
                    logger.error(
                        "Global agent %s still shadows the bundle unless a project-local "
                        "agent takes precedence; its missing files may leave it incomplete. "
                        "Review %s before activation.",
                        agent_dir.name,
                        target_agent_dir,
                    )
                raise
            logger.info(
                f"Seeded agent to global: {agent_dir.name} "
                f"(copied={copied}, preserved_existing={preserved})"
            )


SEED_BACKUP_DIR_NAME = ".seed-backup"
SKILL_SEED_HASHES_NAME = "skill-seed-hashes.json"


def _load_skill_seed_hashes() -> dict[str, dict[str, set[str]]] | None:
    """Return {skill name: {file path: sha256s ever bundled}}, None if unreadable."""
    data_dir = _get_bundled_dir("data")
    if data_dir is None:
        return None
    try:
        raw = json.loads(
            (data_dir / SKILL_SEED_HASHES_NAME).read_text(encoding="utf-8")
        )
        return {
            name: {rel: set(hashes) for rel, hashes in files.items()}
            for name, files in raw.items()
        }
    except (OSError, ValueError, AttributeError) as e:
        logger.warning(f"Skill seed hashes unreadable, keeping global skills: {e}")
        return None


def _is_seed_copy(folder: Path, known: dict[str, set[str]]) -> bool:
    """True when every file in folder is a version this app bundled at that path.

    The old seeding copied only missing files, so one copy can mix versions:
    each file is checked on its own. Finder and Python caches don't count; a
    link, an unknown file or an unreadable one means the user changed it.
    """
    try:
        skill_md = folder / "SKILL.md"
        if _is_link_or_reparse(skill_md) or hashlib.sha256(
            skill_md.read_bytes()
        ).hexdigest() not in known.get("SKILL.md", ()):
            return False  # the usual edit, checked first
        for path in folder.rglob("*"):
            rel = path.relative_to(folder)
            if rel.name == ".DS_Store" or "__pycache__" in rel.parts:
                continue
            if _is_link_or_reparse(path):
                return False
            if path.is_file() and (
                hashlib.sha256(path.read_bytes()).hexdigest()
                not in known.get(rel.as_posix(), ())
            ):
                return False
    except (OSError, TypeError):
        return False
    return True


def _is_seed_copy_at(parent_fd: int, name: str, known: dict[str, set[str]]) -> bool:
    """Fingerprint a moved skill through its pinned POSIX backup directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError:
        return False
    seen_skill = False

    def check(directory_fd: int, prefix: str) -> bool:
        nonlocal seen_skill
        for entry in os.listdir(directory_fd):
            if entry in (".DS_Store", "__pycache__"):
                continue
            relative = f"{prefix}/{entry}" if prefix else entry
            info = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry, flags, dir_fd=directory_fd)
                try:
                    if not check(child, relative):
                        return False
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                descriptor = os.open(
                    entry,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=directory_fd,
                )
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        return False
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest not in known.get(relative, ()):
                    return False
                if relative == "SKILL.md":
                    seen_skill = True
            else:
                return False
        return True

    try:
        return check(root_fd, "") and seen_skill
    except (OSError, TypeError):
        return False
    finally:
        os.close(root_fd)


def _restore_changed_skill(
    folder: Path,
    backup_path: Path,
    *,
    skills_fd: Optional[int],
    backup_fd: Optional[int],
) -> bool:
    """Restore a changed move only if the original name is still unoccupied."""
    try:
        if skills_fd is None or backup_fd is None:
            _rename_seed_noreplace(backup_path, folder)
        else:
            _rename_seed_noreplace(
                backup_path.name,
                folder.name,
                src_dir_fd=backup_fd,
                dst_dir_fd=skills_fd,
            )
    except OSError:
        logger.error(
            "Skill retirement conflict: %s changed during the move and could not "
            "be restored safely. Do not replace an entry at %s; the moved copy "
            "is at %s (the backup directory may have been moved). Review both paths.",
            folder.name,
            folder,
            backup_path,
        )
        return False
    logger.warning("Skill %s changed during retirement and was restored to %s", folder.name, folder)
    return True


def _rename_seed_noreplace(
    source: str | Path,
    target: str | Path,
    *,
    src_dir_fd: Optional[int] = None,
    dst_dir_fd: Optional[int] = None,
) -> None:
    """Atomically rename without replacing an entry, or fail closed."""
    if os.name == "nt":
        os.rename(source, target)  # Windows raises if target exists.
        return
    if src_dir_fd is None or dst_dir_fd is None:
        raise OSError(errno.ENOTSUP, "pinned directory descriptors are required")

    # Python has no no-replace directory rename on POSIX. Use only the native
    # exclusive variant; never fall back to os.rename, which can overwrite.
    import ctypes

    if sys.platform == "darwin":
        symbol, flag = "renameatx_np", 0x4  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        symbol, flag = "renameat2", 0x1  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, symbol, None)
    if function is None:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(src_dir_fd, os.fsencode(source), dst_dir_fd, os.fsencode(target), flag)
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _classify_global_skills(global_skills_dir: Path) -> tuple[list[Path], int]:
    """Split global skill folders into unmodified seed copies and edited ones.

    Only folders named like a skill this app ever bundled count, including
    skills since removed from the bundle. Symlinks are the user's own links,
    so they are left alone.

    Returns (seed folders to retire, folders kept as edited).
    """
    seed_hashes = _load_skill_seed_hashes()
    if (
        seed_hashes is None
        or _is_link_or_reparse(global_skills_dir)
        or _is_link_or_reparse(global_skills_dir.parent)
        or not global_skills_dir.is_dir()
    ):
        return [], 0

    seeds: list[Path] = []
    edited = 0
    for folder in sorted(global_skills_dir.iterdir()):
        known = seed_hashes.get(folder.name)
        if known is None or _is_link_or_reparse(folder) or not (folder / "SKILL.md").is_file():
            continue
        if _is_seed_copy(folder, known):
            seeds.append(folder)
        else:
            edited += 1
    return seeds, edited


def _unused_backup_path(backup_dir: Path, name: str) -> Path:
    """Return backup_dir/name, or a timestamped sibling when that name is taken."""
    target = backup_dir / name
    if _is_link_or_reparse(target) or target.exists():
        target = backup_dir / f"{name}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    return target


def _retire_seeded_skills(global_skills_dir: Path) -> None:
    """Move unmodified seed copies of bundled skills into .seed-backup/.

    Earlier builds copied every bundled skill into ~/.kollab/skills/. The skill
    library ranks global above bundled, so those frozen copies hid every later
    bundled update. Retired folders move aside; nothing is deleted.
    """
    seeds, edited = _classify_global_skills(global_skills_dir)
    moved = 0
    skipped = 0
    if seeds:
        backup_dir = global_skills_dir / SEED_BACKUP_DIR_NAME
        if _is_link_or_reparse(backup_dir):
            logger.warning("Preserving linked skill backup directory: %s", backup_dir)
            return
        known = _load_skill_seed_hashes()
        if known is None:
            return
        with _seed_directory(global_skills_dir, create=False) as skills_fd:
            with _seed_directory(backup_dir, create=True) as backup_fd:
                for folder in seeds:
                    versions = known.get(folder.name)
                    if versions is None or _is_link_or_reparse(folder) or not _is_seed_copy(folder, versions):
                        skipped += 1
                        continue
                    if skills_fd is None or backup_fd is None:
                        # The pinned Windows directories cannot be replaced; its
                        # rename fails rather than overwriting an existing path.
                        backup_path = _unused_backup_path(backup_dir, folder.name)
                        while True:
                            try:
                                _rename_seed_noreplace(folder, backup_path)
                                break
                            except FileExistsError:
                                suffix = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{os.urandom(4).hex()}"
                                backup_path = backup_dir / f"{folder.name}-{suffix}"
                            except OSError as exc:
                                logger.warning("Could not safely retire %s: %s", folder, exc)
                                backup_path = None
                                break
                        if backup_path is None:
                            skipped += 1
                            continue
                        try:
                            with _seed_directory(backup_path, create=False):
                                unchanged = _is_seed_copy(backup_path, versions)
                        except OSError:
                            unchanged = False
                    else:
                        candidate = folder.name
                        while True:
                            try:
                                _rename_seed_noreplace(
                                    folder.name, candidate,
                                    src_dir_fd=skills_fd, dst_dir_fd=backup_fd,
                                )
                                break
                            except FileExistsError:
                                candidate = f"{folder.name}-{datetime.now():%Y%m%d-%H%M%S-%f}-{os.urandom(4).hex()}"
                            except OSError as exc:
                                logger.warning("Could not safely retire %s: %s", folder, exc)
                                candidate = ""
                                break
                        if not candidate:
                            skipped += 1
                            continue
                        backup_path = backup_dir / candidate
                        unchanged = _is_seed_copy_at(backup_fd, candidate, versions)
                    if not unchanged:
                        _restore_changed_skill(
                            folder, backup_path, skills_fd=skills_fd, backup_fd=backup_fd
                        )
                        skipped += 1
                        continue
                    moved += 1
    if moved or edited or skipped:
        logger.info(
            "Retired seed skills: moved=%s, kept_as_edited=%s, skipped=%s",
            moved, edited, skipped,
        )


def _migrate_old_prompt_to_agent(old_prompt_file: Path, agent_dir: Path) -> None:
    """Migrate an old-style system prompt to new agent directory structure.

    Args:
        old_prompt_file: Path to old default.md file
        agent_dir: Target agent directory (e.g., .kollab/agents/default/)
    """
    if any(_is_link_or_reparse(path) for path in (agent_dir, agent_dir.parent, agent_dir.parent.parent)):
        return

    with _seed_directory(agent_dir, create=True):
        pass

    new_prompt_file = agent_dir / "system_prompt.md"
    if _copy_file_if_missing(old_prompt_file, new_prompt_file):
        logger.info(f"Migrated system prompt to: {new_prompt_file}")

        # Create agent.json with default config
        agent_json = agent_dir / "agent.json"
        agent_config = {
            "name": "default",
            "description": "Default agent with standard system prompt",
            "profile": None,
        }
        if _write_text_if_missing(agent_json, json.dumps(agent_config, indent=2)):
            logger.info(f"Created agent config: {agent_json}")


def _create_agent_from_defaults(agent_dir: Path) -> None:
    """Create default agent from bundled seed agents folder.

    Copies from bundled agents/<agent_name>/ to target directory.

    Args:
        agent_dir: Agent directory to create (e.g., ~/.kollab/agents/default/)
    """
    if any(_is_link_or_reparse(path) for path in (agent_dir, agent_dir.parent, agent_dir.parent.parent)):
        return

    agent_name = agent_dir.name  # e.g., "default"

    seed_agents_dir = _get_bundled_dir("agents")
    seed_agent_dir = seed_agents_dir / agent_name if seed_agents_dir else None

    if seed_agent_dir and seed_agent_dir.exists() and seed_agent_dir.is_dir():
        # Copy entire agent directory from seed
        _copy_missing_tree_contents(seed_agent_dir, agent_dir)
        logger.info(f"Created agent from seed: {agent_dir}")
    else:
        # Fallback: create minimal agent
        with _seed_directory(agent_dir, create=True):
            pass

        prompt_file = agent_dir / "system_prompt.md"
        if _write_text_if_missing(prompt_file, get_default_system_prompt()):
            logger.warning(
                f"Created fallback system prompt (seed not found): {prompt_file}"
            )

        agent_json = agent_dir / "agent.json"
        agent_config = {
            "name": agent_name,
            "description": f"{agent_name} agent",
            "profile": None,
        }
        if _write_text_if_missing(agent_json, json.dumps(agent_config, indent=2)):
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
                "name": "default",
                "level": "global",
            }
            default_config["plugins"] = default_config.get("plugins", {})
            default_config["plugins"]["hub"] = default_config["plugins"].get("hub", {})
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

    bundled_themes_dir = _get_bundled_dir("themes")
    if bundled_themes_dir is None:
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

    bundled_layouts_dir = _get_bundled_dir("layouts")
    if bundled_layouts_dir is None:
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

    bundled_widgets_dir = _get_bundled_dir("widgets")
    if bundled_widgets_dir is None:
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

    # Provisioned defaults are a private overlay. Project and explicit user
    # defaults retain precedence; an installed default fills only the gap.
    try:
        from .provisioned_state import ProvisionedStateFile

        state = ProvisionedStateFile().read()
        for enrollment_id in sorted(state["installs"]):
            name = state["installs"][enrollment_id]["settings"]["default_agent"]
            if name:
                return name, "global"
    except Exception as e:
        logger.debug("Could not read private provisioned agent default: %s", e)

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
    write_text_atomic(config_path, json.dumps(config, indent=2, ensure_ascii=False))

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
    write_text_atomic(config_path, json.dumps(config, indent=2, ensure_ascii=False))

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

    write_text_atomic(config_path, json.dumps(config, indent=2, ensure_ascii=False))

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

    write_text_atomic(config_path, json.dumps(config, indent=2, ensure_ascii=False))

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
                "max_retries": 5,
                "save_conversations": True,
                "conversation_format": "jsonl",
                "show_status": True,
            }
        },
    }

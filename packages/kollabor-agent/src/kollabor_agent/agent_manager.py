"""
Agent and Skill Manager.

Manages agents defined in .kollab/agents/ directories and skills
from the centralized skill library (bundles/skills/, ~/.kollab/skills/,
.kollab/skills/).

Skills follow the open Agent Skills standard (agentskills.io):
- Each skill is a directory containing SKILL.md with YAML frontmatter
- Skills are assigned to agents via the "skills" field in agent.json
- Progressive disclosure: metadata at startup, full content on activation

Directory structure:
    bundles/skills/              # Bundled skills (shipped with app)
        debugging/
            SKILL.md
            scripts/             # Optional executable scripts
            references/          # Optional documentation
            assets/              # Optional templates/resources
    ~/.kollab/skills/      # Global user skills
    .kollab/skills/        # Local project skills (highest priority)

    .kollab/agents/
        default/
            system_prompt.md
            agent.json           # "skills": ["debugging", "tdd"] or ["*"]
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml

if TYPE_CHECKING:
    pass  # For forward reference to AgentManager

from kollabor_config.config_utils import (
    get_config_directory,
    get_global_agents_dir,
    get_local_agents_dir,
    get_local_agents_path,
)

from .runtime import AgentRuntime

logger = logging.getLogger(__name__)

# Cache settings
CACHE_VERSION = 3  # Strict Agent Skills SKILL.md validation (agentskills.io)
CACHE_TTL_SECONDS = 86400  # 24 hours
AGENT_CACHE_FILE = get_config_directory() / "agent_metadata.cache"


_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate_skill_name(name: str) -> bool:
    """Validate a skill name per the Agent Skills standard.

    Rules: lowercase + hyphens only, max 64 chars, no consecutive/leading/trailing hyphens.
    """
    return bool(name and len(name) <= 64 and _SKILL_NAME_RE.match(name))


def _parse_skill_yaml_frontmatter(
    raw: str, *, path_for_log: Optional[Path] = None
) -> Optional[tuple[Dict[str, Any], str]]:
    """Parse SKILL.md: YAML frontmatter between --- markers and Markdown body.

    Returns None if frontmatter is missing or invalid (per agentskills.io).
    """
    if not raw.startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        if path_for_log is not None:
            logger.error(f"Invalid YAML frontmatter in {path_for_log}: {e}")
        return None
    if frontmatter is None or not isinstance(frontmatter, dict):
        return None
    body = parts[2].lstrip("\n")
    return frontmatter, body


def skill_markdown_body(raw: str) -> str:
    """Return SKILL.md instructions (body below frontmatter); raw if none."""
    parsed = _parse_skill_yaml_frontmatter(raw)
    if parsed is not None:
        return parsed[1]
    return raw


@dataclass
class Skill:
    """A skill following the open Agent Skills standard (agentskills.io).

    Skills are directories containing a SKILL.md file with YAML frontmatter
    and markdown instructions. They live in the centralized skill library
    and are assigned to agents via agent.json.

    Attributes:
        name: Skill identifier (must match directory name, lowercase+hyphens)
        description: What the skill does and when to use it (max 1024 chars)
        content: Full SKILL.md body below frontmatter (loaded on demand)
        file_path: Path to the SKILL.md file
        skill_dir: Directory containing SKILL.md and optional subdirs
        license: License name or reference
        compatibility: Environment requirements
        metadata: Arbitrary key-value pairs
        allowed_tools: Pre-approved tools (experimental)
        source: Origin tier ("bundled", "global", "local")
    """

    name: str
    description: str = ""
    content: str = ""
    file_path: Path = field(default_factory=Path)
    skill_dir: Optional[Path] = None
    license: str = ""
    compatibility: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    allowed_tools: List[str] = field(default_factory=list)
    source: str = "bundled"

    @classmethod
    def from_file(cls, file_path: Path, source: str = "bundled") -> Optional["Skill"]:
        """Load skill from SKILL.md with YAML frontmatter.

        Enforces the Agent Skills directory contract (agentskills.io): filename
        SKILL.md, required frontmatter fields, ``name`` matches parent directory.

        Args:
            file_path: Path to SKILL.md inside the skill directory
            source: Origin tier ("bundled", "global", "local")

        Returns:
            Skill instance or None when the file does not meet the standard
        """
        if file_path.name != "SKILL.md":
            logger.error(f"Skill file must be named SKILL.md, got {file_path}")
            return None

        skill_dir_name = file_path.parent.name

        try:
            raw = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read skill file {file_path}: {e}")
            return None

        parsed = _parse_skill_yaml_frontmatter(raw, path_for_log=file_path)
        if parsed is None:
            logger.error(f"Skill {file_path} requires valid YAML frontmatter (--- ... ---)")
            return None

        frontmatter, body = parsed

        name_fm = frontmatter.get("name")
        if not isinstance(name_fm, str) or not name_fm.strip():
            logger.error(f"Skill {file_path} requires non-empty string 'name' in frontmatter")
            return None
        name = name_fm.strip()
        if name != skill_dir_name:
            logger.error(
                f"Skill directory name '{skill_dir_name}' must match "
                f"frontmatter name '{name}' ({file_path})"
            )
            return None
        if not validate_skill_name(name):
            logger.warning(f"Invalid skill name '{name}' in {file_path}")
            return None

        raw_desc = frontmatter.get("description")
        if not isinstance(raw_desc, str) or not raw_desc.strip():
            logger.error(
                f"Skill {file_path} requires non-empty string 'description' in frontmatter "
                "(agentskills.io)"
            )
            return None
        description = raw_desc.strip()
        if len(description) > 1024:
            logger.error(f"Skill 'description' exceeds 1024 chars in {file_path}")
            return None

        comp_raw = frontmatter.get("compatibility")
        compatibility = ""
        if comp_raw is not None:
            if not isinstance(comp_raw, str):
                logger.error(f"Skill 'compatibility' must be a string in {file_path}")
                return None
            stripped = comp_raw.strip()
            if stripped:
                if len(stripped) > 500:
                    logger.error(
                        f"Skill 'compatibility' exceeds 500 chars in {file_path}"
                    )
                    return None
                compatibility = stripped

        lic = frontmatter.get("license")
        if lic is not None and not isinstance(lic, str):
            logger.error(f"Skill 'license' must be a string in {file_path}")
            return None
        license_str = lic if isinstance(lic, str) else ""

        md_raw = frontmatter.get("metadata")
        metadata: Dict[str, Any] = {}
        if md_raw is not None:
            if not isinstance(md_raw, dict):
                logger.error(f"Skill 'metadata' must be a mapping in {file_path}")
                return None
            for k, v in md_raw.items():
                if not isinstance(k, str):
                    logger.error(f"Skill metadata keys must be strings in {file_path}")
                    return None
                if not isinstance(v, str):
                    logger.error(
                        f"Skill metadata values must be strings (key {k!r}) in {file_path}"
                    )
                    return None
            metadata = dict(md_raw)

        at_raw = frontmatter.get("allowed-tools")
        if at_raw is None:
            allowed_tools: List[str] = []
        elif isinstance(at_raw, str):
            allowed_tools = at_raw.split() if at_raw.strip() else []
        else:
            logger.error(
                f"Skill 'allowed-tools' must be a space-separated string in {file_path}"
            )
            return None

        return cls(
            name=name,
            description=description,
            content=body,
            file_path=file_path,
            skill_dir=file_path.parent,
            license=license_str,
            compatibility=compatibility,
            metadata=metadata,
            allowed_tools=allowed_tools,
            source=source,
        )

    @classmethod
    def from_directory(
        cls, skill_dir: Path, source: str = "bundled"
    ) -> Optional["Skill"]:
        """Load skill from a directory containing SKILL.md.

        Args:
            skill_dir: Directory containing SKILL.md
            source: Origin tier ("bundled", "global", "local")

        Returns:
            Skill instance or None if SKILL.md not found or invalid
        """
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return None
        return cls.from_file(skill_file, source=source)

    def to_dict(self) -> Dict[str, Any]:
        """Convert skill to dictionary representation."""
        return {
            "name": self.name,
            "description": self.description,
            "file_path": str(self.file_path),
            "skill_dir": str(self.skill_dir) if self.skill_dir else "",
            "source": self.source,
            "license": self.license,
            "compatibility": self.compatibility,
        }


class SkillLibrary:
    """Centralized skill discovery and management across three tiers.

    Discovers skills from:
      1. bundled:  bundles/skills/       (shipped with app, lowest priority)
      2. global:   ~/.kollab/skills/  (user-installed)
      3. local:    .kollab/skills/    (project-specific, highest priority)

    Local overrides global overrides bundled when names collide.
    """

    def __init__(self) -> None:
        from kollabor_config.config_utils import (
            get_bundled_skills_dir,
            get_global_skills_dir,
            get_local_skills_dir,
        )

        self.bundled_dir = get_bundled_skills_dir()
        self.global_dir = get_global_skills_dir()
        self.local_dir = get_local_skills_dir()
        self._skills: Dict[str, Skill] = {}
        self._skill_dirs: Dict[str, tuple[Path, str]] = {}  # name -> (dir, source)
        self._loaded = False
        self._discover()

    def _discover(self) -> None:
        """Scan all tiers for skill directories containing SKILL.md."""
        self._skill_dirs.clear()
        skip = {"__pycache__", ".git", ".svn", "node_modules"}

        # Scan in priority order: bundled (lowest) -> global -> local (highest)
        tiers: list[tuple[Path | None, str]] = [
            (self.bundled_dir, "bundled"),
            (self.global_dir, "global"),
            (self.local_dir, "local"),
        ]

        for tier_dir, source in tiers:
            if not tier_dir or not tier_dir.exists():
                continue
            for entry in tier_dir.iterdir():
                if (
                    entry.is_dir()
                    and entry.name not in skip
                    and not entry.name.startswith(".")
                    and (entry / "SKILL.md").exists()
                ):
                    # Higher priority tiers overwrite lower ones
                    self._skill_dirs[entry.name] = (entry, source)

        logger.info(f"Discovered {len(self._skill_dirs)} skills in library")

    def _ensure_loaded(self) -> None:
        """Lazy-load skill metadata from SKILL.md frontmatter."""
        if self._loaded:
            return
        for name, (skill_dir, source) in self._skill_dirs.items():
            if name not in self._skills:
                skill = Skill.from_directory(skill_dir, source=source)
                if skill:
                    self._skills[name] = skill
                else:
                    logger.warning(f"Failed to load skill from {skill_dir}")
        self._loaded = True

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name, loading content on demand."""
        self._ensure_loaded()
        skill = self._skills.get(name)
        if skill and not skill.content:
            try:
                raw = skill.file_path.read_text(encoding="utf-8")
                skill.content = skill_markdown_body(raw)
            except Exception as e:
                logger.error(f"Failed to load skill content for {name}: {e}")
        return skill

    def get_skills_for_agent(self, spec: List[str]) -> Dict[str, Skill]:
        """Resolve skills for an agent based on its skills spec.

        Args:
            spec: List of skill names. ["*"] means all skills.

        Returns:
            Dict of skill name -> Skill for the agent's assigned skills.
        """
        self._ensure_loaded()
        if spec == ["*"]:
            return dict(self._skills)
        result: Dict[str, Skill] = {}
        for name in spec:
            if name in self._skills:
                result[name] = self._skills[name]
            else:
                logger.warning(f"Skill '{name}' not found in library")
        return result

    def list_all(self) -> List[Skill]:
        """List all available skills across all tiers."""
        self._ensure_loaded()
        return list(self._skills.values())

    def list_names(self) -> List[str]:
        """List all discovered skill names (without loading full metadata)."""
        return list(self._skill_dirs.keys())


def _get_cache_key(agent_dir: Path) -> str:
    """Generate cache key for an agent directory.

    Based on directory path + modification times of all files.
    """
    if not agent_dir.exists():
        return ""

    # Collect all file paths and modification times
    files_data = []
    for file_path in sorted(agent_dir.rglob("*")):
        if file_path.is_file() and not file_path.name.startswith("."):
            mtime = file_path.stat().st_mtime
            files_data.append(f"{file_path}:{mtime}")

    # Hash to create unique key
    hash_source = "|".join(files_data).encode("utf-8")
    return hashlib.md5(hash_source).hexdigest()


def _load_cached_agents(cache_file: Path) -> Dict[str, Dict]:
    """Load cached agent metadata from disk.

    Returns:
        Dict mapping agent names to cached metadata
    """
    if not cache_file.exists():
        return {}

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))

        # Check cache version and TTL
        if data.get("version") != CACHE_VERSION:
            logger.debug("Agent cache version mismatch, ignoring")
            return {}

        cache_age = time.time() - data.get("timestamp", 0)
        if cache_age > CACHE_TTL_SECONDS:
            logger.debug(f"Agent cache expired (age={cache_age}s), ignoring")
            return {}

        logger.debug(f"Loaded agent cache with {len(data.get('agents', {}))} agents")
        return data.get("agents", {})  # type: ignore[no-any-return]

    except Exception as e:
        logger.warning(f"Failed to load agent cache: {e}")
        return {}


def _save_cached_agents(cache_file: Path, agents: List[AgentRuntime]) -> None:
    """Save agent metadata to cache.

    Args:
        cache_file: Path to cache file
        agents: List of Agent instances to cache
    """
    try:
        cache_dir = cache_file.parent
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)

        # Build cache data
        agents_data = {}
        for agent in agents:
            agents_data[agent.name] = {
                "cache_key": _get_cache_key(agent.directory),
                "name": agent.name,
                "directory": str(agent.directory),
                "description": agent.description,
                "profile": agent.profile,
                "skills": {
                    name: skill.to_dict() for name, skill in agent.skills.items()
                },
                "default_skills": agent.default_skills,
                "source": agent.source,
                "overrides_global": agent.overrides_global,
                "identity": agent.identity,
                "capabilities": agent.capabilities,
                "vault_enabled": agent.vault_enabled,
                "tools": (
                    list(agent._agent_ref.tools)
                    if agent._agent_ref and hasattr(agent._agent_ref, "tools")
                    else []
                ),
            }

        cache_data = {
            "version": CACHE_VERSION,
            "timestamp": int(time.time()),
            "agents": agents_data,
        }

        cache_file.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
        logger.debug(f"Saved agent cache with {len(agents_data)} agents")

    except Exception as e:
        logger.warning(f"Failed to save agent cache: {e}")


@dataclass
class Agent:
    """
    An agent configuration with system prompt and available skills.

    Agents are loaded from directories containing:
    - system_prompt.md (required)
    - agent.json (optional config including ``skills`` referencing the skill library)
    - sections/ directory (optional prompt fragments included via <trender>)

    Skill modules use the Agent Skills directory contract (agentskills.io):
    each skill is ``<skill-name>/SKILL.md`` with YAML frontmatter, discovered
    from ``bundles/skills/``, ``~/.kollab/skills/``, and ``.kollab/skills/``.
    Agents list skill names in agent.json ``skills``.

    Attributes:
        name: Agent identifier (directory name)
        directory: Path to agent directory
        system_prompt: Base system prompt content
        skills: Available skills (name -> Skill)
        active_skills: Currently loaded skill names
        profile: Optional preferred LLM profile
        description: Human-readable description
        default_skills: Skills to auto-load when agent is activated
        source: 'local' or 'global' - where the agent was loaded from
        overrides_global: True if local agent overrides a global agent with same name
        identity: Hub identity (defaults to agent name when empty)
        capabilities: Capability tags for hub discovery
        vault_enabled: Whether persistent memory is enabled for this agent
    """

    name: str
    directory: Path
    system_prompt: str
    skills: Dict[str, Skill] = field(default_factory=dict)
    active_skills: List[str] = field(default_factory=list)
    profile: Optional[str] = None
    description: str = ""
    default_skills: List[str] = field(default_factory=list)
    source: str = "global"
    overrides_global: bool = False
    identity: str = ""  # Hub identity, defaults to agent name if empty
    capabilities: List[str] = field(default_factory=list)  # Capability tags
    vault_enabled: bool = True  # Enable persistent memory via hub vault
    tools: List[str] = field(default_factory=list)
    """Tool names from agent.json 'tools' field. Used for bundle scoping."""

    @classmethod
    def from_directory(
        cls,
        agent_dir: Path,
        source: str = "global",
        overrides_global: bool = False,
        skill_library: Optional["SkillLibrary"] = None,
    ) -> Optional["Agent"]:
        """
        Load agent from a directory.

        Skills are resolved from the centralized SkillLibrary based on the
        "skills" field in agent.json. If no "skills" field exists, no skills
        are assigned (agents must explicitly declare their skills).

        Args:
            agent_dir: Path to agent directory
            source: 'local' or 'global' - where the agent was loaded from
            overrides_global: True if local agent overrides a global agent
            skill_library: Centralized skill library for resolving skill assignments

        Returns:
            Agent instance or None if invalid
        """
        if not agent_dir.is_dir():
            return None

        # Load system prompt (required)
        system_prompt_file = agent_dir / "system_prompt.md"
        if not system_prompt_file.exists():
            logger.warning(f"Agent {agent_dir.name} missing system_prompt.md")
            return None

        try:
            system_prompt = system_prompt_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read system prompt for {agent_dir.name}: {e}")
            return None

        # Load optional config
        profile = None
        description = ""
        default_skills: List[str] = []
        skill_spec: List[str] = []
        identity = ""
        capabilities: List[str] = []
        vault_enabled = True
        tools: List[str] = []
        config_file = agent_dir / "agent.json"
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
                profile = config.get("profile")
                description = config.get("description", "")
                default_skills = config.get("default_skills", [])
                skill_spec = config.get("skills", [])
                identity = config.get("identity", "")
                capabilities = config.get("capabilities", [])
                vault_enabled = config.get("vault_enabled", True)
                tools = config.get("tools", [])
            except Exception as e:
                logger.warning(f"Failed to load agent config for {agent_dir.name}: {e}")

        # Resolve skills from the centralized library
        skills: Dict[str, Skill] = {}
        if skill_library and skill_spec:
            skills = skill_library.get_skills_for_agent(skill_spec)
            logger.debug(
                f"Agent {agent_dir.name}: resolved {len(skills)} skills from library"
            )

        return cls(
            name=agent_dir.name,
            directory=agent_dir,
            system_prompt=system_prompt,
            skills=skills,
            profile=profile,
            description=description,
            default_skills=default_skills,
            source=source,
            overrides_global=overrides_global,
            identity=identity,
            capabilities=capabilities,
            vault_enabled=vault_enabled,
            tools=tools,
        )

    def get_full_system_prompt(
        self,
        agent_manager: Optional["AgentManager"] = None,
        event_bus=None,
    ) -> str:
        """
        Get system prompt with active skills appended.

        Skills are added under "## Skill: {name}" headers.
        System docs from agents/system/ directories are auto-appended
        before the agent's own prompt content.
        The system prompt is rendered through PromptRenderer to process
        any <trender> tags (both commands and file includes).

        Args:
            agent_manager: Optional AgentManager for rendering <trender type="agents_list" /> tags.
            event_bus: Optional EventBus for rendering hub trender tags.

        Returns:
            Combined system prompt string
        """
        from kollabor_ai import PromptRenderer

        # Lazy load system_prompt if loaded from cache with empty content
        if not self.system_prompt and self.directory:
            system_prompt_file = self.directory / "system_prompt.md"
            if system_prompt_file.exists():
                try:
                    self.system_prompt = system_prompt_file.read_text(encoding="utf-8")
                    logger.debug(
                        f"Loaded system_prompt on demand for {self.name} ({len(self.system_prompt)} chars)"
                    )
                except Exception as e:
                    logger.error(f"Failed to load system_prompt for {self.name}: {e}")

        # Render the base system prompt (processes <trender> tags)
        # Use agent directory as base_path for resolving relative includes
        renderer = PromptRenderer(
            base_path=self.directory,
            agent_manager=agent_manager,
            event_bus=event_bus,
        )
        rendered_prompt = renderer.render(self.system_prompt)

        # Auto-inject system docs from agents/system/ directories
        # Global system dir first, then local (local can override by filename)
        system_docs = self._load_system_docs(renderer)

        parts = []
        if system_docs:
            parts.append(system_docs)
        parts.append(rendered_prompt)

        # Level 2: Active skills (full content loaded into context)
        for skill_name in self.active_skills:
            if skill_name in self.skills:
                skill = self.skills[skill_name]
                # Load content on demand if needed
                if not skill.content and skill.file_path:
                    try:
                        raw = skill.file_path.read_text(encoding="utf-8")
                        skill.content = skill_markdown_body(raw)
                    except Exception as e:
                        logger.error(
                            f"Failed to load skill content for {skill_name}: {e}"
                        )
                # Render skill content (may contain trender tags)
                rendered_skill = renderer.render(skill.content)
                parts.append(f"\n\n## Skill: {skill_name}\n\n{rendered_skill}")

        # Level 1: Skills catalog (name + description only for inactive skills)
        inactive_skills = [
            (name, skill)
            for name, skill in self.skills.items()
            if name not in self.active_skills
        ]
        if inactive_skills:
            catalog_lines = [
                "\n\navailable skills (use /skills load <name> to activate):"
            ]
            for name, skill in sorted(inactive_skills, key=lambda x: x[0]):
                desc = skill.description or "no description"
                catalog_lines.append(f"  - {name}: {desc}")
            parts.append("\n".join(catalog_lines))

        return "\n".join(parts)

    def _load_system_docs(self, renderer) -> str:
        """Load and render all .md files from agents/system/ directories.

        Scans global (~/.kollab/agents/system/) and local
        (.kollab/agents/system/) directories. Files are sorted
        alphabetically. Local files override global ones with the same name.

        Args:
            renderer: PromptRenderer instance for processing trender tags.

        Returns:
            Rendered system docs concatenated, or empty string if none found.
        """
        from kollabor_config.config_utils import (
            get_global_agents_dir,
            get_local_agents_dir,
        )

        # Collect files: global first, local overrides by filename
        files_by_name: Dict[str, Path] = {}

        global_system = get_global_agents_dir() / "system"
        if global_system.is_dir():
            for md_file in sorted(global_system.glob("*.md")):
                files_by_name[md_file.name] = md_file

        local_agents = get_local_agents_dir()
        if local_agents:
            local_system = local_agents / "system"
            if local_system.is_dir():
                for md_file in sorted(local_system.glob("*.md")):
                    files_by_name[md_file.name] = md_file

        if not files_by_name:
            return ""

        parts = []
        for name in sorted(files_by_name.keys()):
            path = files_by_name[name]
            try:
                content = path.read_text(encoding="utf-8")
                rendered = renderer.render(content)
                parts.append(rendered)
                logger.debug(f"Loaded system doc: {path}")
            except Exception as e:
                logger.error(f"Failed to load system doc {path}: {e}")

        return "\n\n".join(parts)

    def load_skill(self, skill_name: str) -> bool:
        """
        Load a skill into active context.

        Args:
            skill_name: Name of skill to load

        Returns:
            True if loaded, False if not found
        """
        if skill_name not in self.skills:
            logger.error(f"Skill not found: {skill_name}")
            return False

        if skill_name not in self.active_skills:
            self.active_skills.append(skill_name)
            logger.info(f"Loaded skill: {skill_name}")
        return True

    def unload_skill(self, skill_name: str) -> bool:
        """
        Unload a skill from active context.

        Args:
            skill_name: Name of skill to unload

        Returns:
            True if unloaded, False if not loaded
        """
        if skill_name in self.active_skills:
            self.active_skills.remove(skill_name)
            logger.info(f"Unloaded skill: {skill_name}")
            return True
        return False

    def list_skills(self) -> List[Skill]:
        """Get list of available skills."""
        return list(self.skills.values())

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a specific skill by name.

        Loads content from disk on demand if skill was loaded from cache
        with empty content.
        """
        skill = self.skills.get(name)
        if skill and not skill.content:
            # Load content on demand from file
            try:
                raw = skill.file_path.read_text(encoding="utf-8")
                skill.content = skill_markdown_body(raw)
                logger.debug(
                    f"Loaded skill content on demand: {name} ({len(skill.content)} chars)"
                )
            except Exception as e:
                logger.error(f"Failed to load skill content for {name}: {e}")
        return skill

    def to_dict(self) -> Dict[str, Any]:
        """Convert agent to dictionary representation."""
        return {
            "name": self.name,
            "directory": str(self.directory),
            "description": self.description,
            "profile": self.profile,
            "skills": [s.to_dict() for s in self.skills.values()],
            "active_skills": self.active_skills,
            "source": self.source,
            "overrides_global": self.overrides_global,
            "identity": self.identity,
            "capabilities": self.capabilities,
            "vault_enabled": self.vault_enabled,
            "tools": self.tools,
        }


# Phase 3: AgentRuntime is the unified identity. Agent kept for backward compat.


class AgentManager:
    """
    Manages agent discovery, loading, and skill management.

    Searches for agents in:
    1. Local: .kollab/agents/ (project-specific, higher priority)
    2. Global: ~/.kollab/agents/ (user defaults)

    Local agents override global agents with the same name.
    """

    def __init__(self, config=None, event_bus=None):
        """
        Initialize agent manager with lazy loading.

        Args:
            config: Configuration object (optional)
            event_bus: Optional EventBus for hub trender tag rendering
        """
        self.config = config
        self.event_bus = event_bus
        self._agents: Dict[str, AgentRuntime] = {}
        self._active_agent_name: Optional[str] = None
        self._on_agent_changed = None  # Callback: fn(agent_or_none) called on activate/clear

        # Agent directories (in discovery order, lowest to highest priority)
        # 1. Global: ~/.kollab/agents/ (user defaults)
        # 2. Local: .kollab/agents/ (project-specific, where agents are created)
        self.global_agents_dir = get_global_agents_dir()
        self.local_agents_dir = get_local_agents_dir()

        # Centralized skill library
        self.skill_library = SkillLibrary()

        # Lazy loading: only discover agent names on init
        # Full agent data loaded on first access
        self._agent_names: List[str] = []  # Just names, not full agent objects
        self._agents_loaded = False
        self._discover_agent_names()

    def _discover_agent_names(self) -> None:
        """Discover agent names only (lazy loading - full data loaded on first access).

        This avoids reading 70+ markdown files during startup.
        """
        # Skip these directory names during discovery
        skip_dirs = {
            "__pycache__",
            ".git",
            ".svn",
            "node_modules",
            "_shared",
            "_base",
            "system",
        }

        # Discover names from global first (lowest priority)
        if self.global_agents_dir and self.global_agents_dir.exists():
            for agent_dir in self.global_agents_dir.iterdir():
                if (
                    agent_dir.is_dir()
                    and agent_dir.name not in skip_dirs
                    and not agent_dir.name.startswith(".")
                ):
                    if agent_dir.name not in self._agent_names:
                        self._agent_names.append(agent_dir.name)
                        logger.debug(f"Discovered global agent name: {agent_dir.name}")

        # Discover names from local (higher priority)
        if self.local_agents_dir and self.local_agents_dir.exists():
            for agent_dir in self.local_agents_dir.iterdir():
                if (
                    agent_dir.is_dir()
                    and agent_dir.name not in skip_dirs
                    and not agent_dir.name.startswith(".")
                ):
                    if agent_dir.name not in self._agent_names:
                        self._agent_names.append(agent_dir.name)
                        logger.debug(f"Discovered local agent name: {agent_dir.name}")

        logger.info(
            f"Discovered {len(self._agent_names)} agent names (lazy load enabled)"
        )

    def _ensure_agents_loaded(self) -> None:
        """Load full agent data if not already loaded.

        This is called lazily when agent is first accessed.
        """
        if self._agents_loaded:
            return

        logger.debug("Lazy loading full agent data...")
        self._load_agents_from_names()
        self._agents_loaded = True

    def _load_agents_from_names(self) -> None:
        """Load full agent data from discovered names (with caching).

        This reads all agent files (markdown, json) on demand.
        Uses disk cache if available and valid.
        """
        # Try loading from cache first
        cached_agents = _load_cached_agents(AGENT_CACHE_FILE)

        loaded_from_cache = []
        needs_load = []

        # Check cache validity for each agent
        for agent_name in self._agent_names:
            # Try local first, then global
            agent_dir = None
            source = "unknown"
            overrides_global = False

            if self.local_agents_dir and (self.local_agents_dir / agent_name).exists():
                agent_dir = self.local_agents_dir / agent_name
                source = "local"
                if (
                    self.global_agents_dir
                    and (self.global_agents_dir / agent_name).exists()
                ):
                    overrides_global = True
            elif (
                self.global_agents_dir
                and (self.global_agents_dir / agent_name).exists()
            ):
                agent_dir = self.global_agents_dir / agent_name
                source = "global"

            if not agent_dir:
                continue

            # Check cache
            if agent_name in cached_agents:
                cache_key = _get_cache_key(agent_dir)
                if cached_agents[agent_name].get("cache_key") == cache_key:
                    # Cache hit - create minimal agent from cache
                    cached = cached_agents[agent_name]
                    agent = Agent(
                        name=cached["name"],
                        directory=Path(cached["directory"]),
                        system_prompt="",  # Will be loaded on demand
                        skills={},  # Skills loaded from cache
                        active_skills=[],
                        profile=cached.get("profile"),
                        description=cached.get("description", ""),
                        default_skills=cached.get("default_skills", []),
                        source=cached.get("source", "global"),
                        overrides_global=cached.get("overrides_global", False),
                        identity=cached.get("identity", ""),
                        capabilities=cached.get("capabilities", []),
                        vault_enabled=cached.get("vault_enabled", True),
                        tools=cached.get("tools", []),
                    )

                    # Reconstruct skills from cache (without full content)
                    for skill_name, skill_data in cached.get("skills", {}).items():
                        agent.skills[skill_name] = Skill(
                            name=skill_data["name"],
                            content="",  # Will be loaded on demand
                            file_path=Path(skill_data["file_path"]),
                            description=skill_data.get("description", ""),
                        )

                    self._agents[agent_name] = AgentRuntime.from_agent(agent)
                    loaded_from_cache.append(agent_name)
                    logger.debug(f"Loaded {source} agent from cache: {agent_name}")
                else:
                    needs_load.append((agent_name, agent_dir, source, overrides_global))
            else:
                needs_load.append((agent_name, agent_dir, source, overrides_global))

        # Load agents not in cache
        skip_dirs = {
            "__pycache__",
            ".git",
            ".svn",
            "node_modules",
            "_shared",
            "_base",
            "system",
        }
        for agent_name, agent_dir, source, overrides_global in needs_load:
            if (
                agent_dir.is_dir()
                and agent_dir.name not in skip_dirs
                and not agent_dir.name.startswith(".")
            ):
                loaded = Agent.from_directory(
                    agent_dir,
                    source=source,
                    overrides_global=overrides_global,
                    skill_library=self.skill_library,
                )
                if loaded and loaded.name not in self._agents:
                    self._agents[loaded.name] = AgentRuntime.from_agent(loaded)
                    logger.debug(f"Loaded {source} agent from disk: {loaded.name}")

        # Update cache if we loaded anything new
        if needs_load:
            _save_cached_agents(AGENT_CACHE_FILE, list(self._agents.values()))

        cache_stats = (
            f"{len(loaded_from_cache)} from cache, {len(needs_load)} from disk"
        )
        logger.info(f"Loaded {len(self._agents)} agents ({cache_stats})")

    def get_agent(self, name: str) -> Optional[AgentRuntime]:
        """
        Get agent by name (triggers lazy load if needed).

        Args:
            name: Agent name

        Returns:
            Agent instance or None if not found
        """
        # Trigger lazy load on first access
        self._ensure_agents_loaded()
        return self._agents.get(name)

    def get_active_agent(self) -> Optional[AgentRuntime]:
        """
        Get the currently active agent (triggers lazy load if needed).

        Returns:
            Active Agent or "kollabor" agent or None
        """
        # Trigger lazy load on first access
        self._ensure_agents_loaded()

        if self._active_agent_name:
            agent = self._agents.get(self._active_agent_name)
            if agent:
                return agent

        # Fall back to "kollabor" agent
        return self._agents.get("kollabor")

    def set_active_agent(self, name: str, load_defaults: bool = True) -> bool:
        """
        Set the active agent (triggers lazy load if needed).

        Args:
            name: Agent name to activate
            load_defaults: If True, auto-load the agent's default skills

        Returns:
            True if successful, False if agent not found
        """
        # Trigger lazy load to ensure agent is available
        self._ensure_agents_loaded()

        if name not in self._agents:
            logger.error(f"Agent not found: {name}")
            return False

        old_agent = self._active_agent_name
        self._active_agent_name = name

        # Auto-load default skills if configured
        agent = self._agents[name]
        if load_defaults and agent.default_skills:
            for skill_name in agent.default_skills:
                if skill_name in agent.skills and skill_name not in agent.active_skills:
                    agent.load_skill(skill_name)
                    logger.debug(f"Auto-loaded default skill: {skill_name}")

        logger.info(f"Activated agent: {old_agent} -> {name}")

        # Notify callback (e.g. to sync bundle scope)
        if self._on_agent_changed:
            try:
                self._on_agent_changed(agent)
            except Exception as e:
                logger.warning(f"on_agent_changed callback error: {e}")

        return True

    def clear_active_agent(self) -> None:
        """Clear the active agent (use default or no agent)."""
        self._active_agent_name = None
        logger.info("Cleared active agent")

        # Notify callback (e.g. to clear bundle scope)
        if self._on_agent_changed:
            try:
                self._on_agent_changed(None)
            except Exception as e:
                logger.warning(f"on_agent_changed callback error: {e}")

    def list_agents(self) -> List[AgentRuntime]:
        """
        List all available agents (triggers lazy load if needed).

        Returns:
            List of Agent instances
        """
        self._ensure_agents_loaded()
        return list(self._agents.values())

    def get_agent_names(self) -> List[str]:
        """
        Get list of agent names (no load needed - names cached on init).

        Returns:
            List of agent name strings
        """
        # Return cached names without triggering full load
        return self._agent_names.copy()

    def has_agent(self, name: str) -> bool:
        """Check if an agent exists (no load needed - names cached on init)."""
        return name in self._agent_names

    def list_skills(self, agent_name: Optional[str] = None) -> List[Skill]:
        """
        List skills for an agent.

        Args:
            agent_name: Agent name (default: active agent)

        Returns:
            List of Skill instances
        """
        agent = self._agents.get(agent_name) if agent_name else self.get_active_agent()
        if not agent:
            return []
        return agent.list_skills()

    def load_skill(self, skill_name: str, agent_name: Optional[str] = None) -> bool:
        """
        Load a skill into an agent's active context.

        Args:
            skill_name: Name of skill to load
            agent_name: Agent name (default: active agent)

        Returns:
            True if loaded, False otherwise
        """
        agent = self._agents.get(agent_name) if agent_name else self.get_active_agent()
        if not agent:
            logger.error("No agent available to load skill")
            return False

        return agent.load_skill(skill_name)

    def unload_skill(self, skill_name: str, agent_name: Optional[str] = None) -> bool:
        """
        Unload a skill from an agent's active context.

        Args:
            skill_name: Name of skill to unload
            agent_name: Agent name (default: active agent)

        Returns:
            True if unloaded, False otherwise
        """
        agent = self._agents.get(agent_name) if agent_name else self.get_active_agent()
        if not agent:
            return False

        return agent.unload_skill(skill_name)

    def toggle_default_skill(
        self, skill_name: str, agent_name: Optional[str] = None, scope: str = "project"
    ) -> tuple[bool, bool]:
        """
        Toggle a skill as default (auto-loaded when agent is activated).

        Args:
            skill_name: Name of skill to toggle
            agent_name: Agent name (default: active agent)
            scope: "project" for .kollab or "global" for ~/.kollab

        Returns:
            Tuple of (success, is_now_default)
        """
        agent = self._agents.get(agent_name) if agent_name else self.get_active_agent()
        if not agent:
            return (False, False)

        # Check if skill exists
        if skill_name not in agent.skills:
            logger.error(f"Skill not found: {skill_name}")
            return (False, False)

        # Determine target directory based on scope
        if scope == "global":
            target_dir = self.global_agents_dir / agent.name
        else:
            # Use get_local_agents_path() for creation (creates dir if needed)
            target_dir = get_local_agents_path() / agent.name

        # Ensure directory exists
        target_dir.mkdir(parents=True, exist_ok=True)

        # Load existing config from target scope
        config_file = target_dir / "agent.json"
        current_defaults = []
        if config_file.exists():
            try:
                config_data = json.loads(config_file.read_text(encoding="utf-8"))
                current_defaults = config_data.get("default_skills", [])
            except Exception as e:
                logger.error(f"Failed to read {scope} agent.json: {e}")

        # Toggle default status
        if skill_name in current_defaults:
            current_defaults.remove(skill_name)
            is_default = False
            logger.info(f"Removed skill from {scope} defaults: {skill_name}")
        else:
            current_defaults.append(skill_name)
            is_default = True
            logger.info(f"Added skill to {scope} defaults: {skill_name}")

        # Save to target scope
        self._save_agent_config_to_path(target_dir, current_defaults, agent)

        # Reload agent to reflect changes
        self._reload_agent(agent.name)

        return (True, is_default)

    def _save_agent_config(self, agent: Agent) -> bool:
        """
        Save agent configuration to agent.json.

        Args:
            agent: Agent to save config for

        Returns:
            True if saved, False otherwise
        """
        try:
            config_file = agent.directory / "agent.json"

            # Build config dict
            agent_json: Dict[str, Any] = {}
            if agent.description:
                agent_json["description"] = agent.description
            if agent.profile:
                agent_json["profile"] = agent.profile
            if agent.default_skills:
                agent_json["default_skills"] = agent.default_skills

            if agent_json:
                config_file.write_text(
                    json.dumps(agent_json, indent=4, ensure_ascii=False),
                    encoding="utf-8",
                )
            elif config_file.exists():
                # Remove agent.json if empty
                config_file.unlink()

            return True
        except Exception as e:
            logger.error(f"Failed to save agent config for {agent.name}: {e}")
            return False

    def _save_agent_config_to_path(
        self, target_dir: Path, default_skills: List[str], agent: AgentRuntime
    ) -> bool:
        """
        Save agent configuration to a specific directory.

        Args:
            target_dir: Directory to save to
            default_skills: List of default skill names
            agent: Agent instance for reference data

        Returns:
            True if saved, False otherwise
        """
        try:
            config_file = target_dir / "agent.json"

            # Load existing config to preserve other fields
            agent_json: Dict[str, Any] = {}
            if config_file.exists():
                try:
                    agent_json = json.loads(config_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Update default_skills
            if default_skills:
                agent_json["default_skills"] = default_skills
            elif "default_skills" in agent_json:
                del agent_json["default_skills"]

            if agent_json:
                config_file.write_text(
                    json.dumps(agent_json, indent=4, ensure_ascii=False),
                    encoding="utf-8",
                )
            elif config_file.exists():
                config_file.unlink()

            return True
        except Exception as e:
            logger.error(f"Failed to save agent config to {target_dir}: {e}")
            return False

    def _reload_agent(self, agent_name: str) -> None:
        """
        Reload an agent from disk to pick up configuration changes.

        Args:
            agent_name: Name of agent to reload
        """
        # Store active skills before reload
        active_skills = []
        if agent_name in self._agents:
            active_skills = self._agents[agent_name].active_skills.copy()

        # Reload from disk (local overrides global)
        local_path = (
            self.local_agents_dir / agent_name if self.local_agents_dir else None
        )
        global_path = (
            self.global_agents_dir / agent_name if self.global_agents_dir else None
        )

        if local_path and local_path.exists():
            # Check if this overrides a global agent
            overrides = global_path and global_path.exists()
            agent = Agent.from_directory(
                local_path,
                source="local",
                overrides_global=bool(overrides),
                skill_library=self.skill_library,
            )
            if agent:
                self._agents[agent_name] = AgentRuntime.from_agent(agent)
        elif global_path and global_path.exists():
            agent = Agent.from_directory(
                global_path,
                source="global",
                overrides_global=False,
                skill_library=self.skill_library,
            )
            if agent:
                self._agents[agent_name] = AgentRuntime.from_agent(agent)

        # Restore active skills
        if agent_name in self._agents and active_skills:
            for skill_name in active_skills:
                if skill_name in self._agents[agent_name].skills:
                    self._agents[agent_name].load_skill(skill_name)

    def get_system_prompt(self) -> Optional[str]:
        """
        Get the full system prompt for the active agent.

        Includes base system prompt and active skills.

        Returns:
            System prompt string or None if no agent
        """
        agent = self.get_active_agent()
        if agent:
            # Pass self for agents_list and event_bus for hub trender tags
            return agent.get_full_system_prompt(
                agent_manager=self, event_bus=self.event_bus
            )
        return None

    def get_preferred_profile(self) -> Optional[str]:
        """
        Get the preferred LLM profile for the active agent.

        Returns:
            Profile name or None
        """
        agent = self.get_active_agent()
        if agent:
            return agent.profile
        return None

    @property
    def active_agent_name(self) -> Optional[str]:
        """Get the name of the active agent."""
        return self._active_agent_name

    def is_active(self, name: str) -> bool:
        """Check if an agent is the active one."""
        return name == self._active_agent_name

    def get_agent_summary(self, name: Optional[str] = None) -> str:
        """
        Get a human-readable summary of an agent.

        Args:
            name: Agent name (default: active agent)

        Returns:
            Formatted summary string
        """
        agent = self._agents.get(name) if name else self.get_active_agent()
        if not agent:
            return f"Agent '{name}' not found" if name else "No active agent"

        lines = [
            f"Agent: {agent.name}",
            f"  Directory: {agent.directory}",
        ]
        if agent.description:
            lines.append(f"  Description: {agent.description}")
        if agent.profile:
            lines.append(f"  Preferred Profile: {agent.profile}")

        skills = agent.list_skills()
        if skills:
            lines.append(f"  Skills ({len(skills)}):")
            for skill in skills:
                active = "*" if skill.name in agent.active_skills else " "
                desc = f" - {skill.description[:40]}..." if skill.description else ""
                lines.append(f"    [{active}] {skill.name}{desc}")
        else:
            lines.append("  Skills: none")

        return "\n".join(lines)

    def refresh(self) -> None:
        """Re-discover agents from directories, preserving active skills."""
        # Preserve active skills state before refresh
        active_skills_backup: Dict[str, List[str]] = {}
        for name, agent in self._agents.items():
            if agent.active_skills:
                active_skills_backup[name] = list(agent.active_skills)

        # Clear and re-discover
        self._agents.clear()
        self._agent_names.clear()
        self._agents_loaded = False
        self._discover_agent_names()
        self._load_agents_from_names()
        self._agents_loaded = True

        # Restore active skills after refresh
        for name, skills in active_skills_backup.items():
            if name in self._agents:
                self._agents[name].active_skills = skills

    def create_agent(
        self,
        name: str,
        description: str = "",
        profile: Optional[str] = None,
        system_prompt: str = "",
        default_skills: Optional[List[str]] = None,
    ) -> Optional[AgentRuntime]:
        """
        Create a new agent with directory structure.

        Creates .kollab/agents/<name>/ directory with:
        - system_prompt.md
        - agent.json (if profile, description, or default_skills specified)

        Args:
            name: Agent name (becomes directory name)
            description: Agent description
            profile: Preferred LLM profile name
            system_prompt: Base system prompt content
            default_skills: List of skill names to auto-load when agent is activated

        Returns:
            Created Agent or None on failure
        """
        import json

        # Check if agent already exists
        if name in self._agents:
            logger.warning(f"Agent already exists: {name}")
            return None

        # Create in .kollab/agents/ directory (creates local dir if needed)
        local_path = get_local_agents_path()
        agent_dir = local_path / name

        if agent_dir.exists():
            logger.warning(f"Agent directory already exists: {agent_dir}")
            return None

        try:
            # Create directory structure
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Create system_prompt.md
            default_prompt = (
                system_prompt or f"""# {name.replace('-', ' ').title()} Agent

You are a specialized assistant.

## Your Mission

{description or 'Help users with their tasks.'}

## Approach

1. Analyze the user's request
2. Provide clear, actionable guidance
3. Follow best practices
"""
            )
            prompt_file = agent_dir / "system_prompt.md"
            prompt_file.write_text(default_prompt, encoding="utf-8")

            # Create agent.json if profile, description, or default_skills specified
            if profile or description or default_skills:
                agent_json: Dict[str, Any] = {
                    "description": description or f"Agent: {name}",
                }
                if profile and profile != "(none)":
                    agent_json["profile"] = profile
                if default_skills:
                    agent_json["default_skills"] = default_skills

                json_file = agent_dir / "agent.json"
                json_file.write_text(
                    json.dumps(agent_json, indent=4, ensure_ascii=False),
                    encoding="utf-8",
                )

            # Update local_agents_dir since we just created the local directory
            self.local_agents_dir = get_local_agents_dir()

            # Load the newly created agent
            # Check if it overrides a global agent
            overrides = (
                self.global_agents_dir is not None
                and (self.global_agents_dir / name).exists()
            )
            agent = Agent.from_directory(
                agent_dir,
                source="local",
                overrides_global=overrides,
                skill_library=self.skill_library,
            )
            if agent:
                runtime = AgentRuntime.from_agent(agent)
                self._agents[name] = runtime
                logger.info(f"Created agent: {name} at {agent_dir}")
                return runtime

            return None

        except Exception as e:
            logger.error(f"Failed to create agent {name}: {e}")
            # Clean up on failure
            if agent_dir.exists():
                import shutil

                shutil.rmtree(agent_dir, ignore_errors=True)
            return None

    def delete_agent(self, name: str) -> bool:
        """
        Delete an agent by removing its directory.

        Cannot delete the active agent or protected agents like "kollabor".

        Args:
            name: Agent name to delete

        Returns:
            True if deleted, False if cannot delete
        """
        import shutil

        # Protected agents that cannot be deleted
        protected_agents = {"kollabor"}

        # Check if agent exists
        if name not in self._agents:
            logger.warning(f"Agent not found: {name}")
            return False

        # Check if protected
        if name in protected_agents:
            logger.warning(f"Cannot delete protected agent: {name}")
            return False

        # Check if active
        if self.is_active(name):
            logger.warning(f"Cannot delete active agent: {name}")
            return False

        agent = self._agents[name]
        agent_dir = agent.directory

        # Only delete from local directory (never delete global agents)
        if not agent_dir.is_relative_to(self.local_agents_dir):
            logger.warning(f"Cannot delete agent from global directory: {name}")
            return False

        try:
            # Remove the directory
            shutil.rmtree(agent_dir)
            # Remove from internal dict
            del self._agents[name]
            logger.info(f"Deleted agent: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete agent {name}: {e}")
            return False

    def load_default_agent(self, cli_agent_name: Optional[str] = None) -> Optional[str]:
        """
        Load the appropriate default agent based on priority.

        Priority:
        1. CLI agent name (highest, one-time override)
        2. Project default (.kollab/config.json)
        3. Global default (~/.kollab/config.json)
        4. Fallback to "koordinator" agent

        Args:
            cli_agent_name: Agent name from CLI --agent argument

        Returns:
            Name of agent that was activated, or None if failed
        """
        from kollabor_config.config_utils import get_default_agent

        # Priority 1: CLI argument (one-time override)
        if cli_agent_name:
            if self.set_active_agent(cli_agent_name):
                logger.info(f"Loaded agent from CLI argument: {cli_agent_name}")
                return cli_agent_name
            else:
                logger.warning(
                    f"CLI agent '{cli_agent_name}' not found, trying defaults"
                )

        # Priority 2: Project default
        project_agent, level = get_default_agent()
        if level == "project" and project_agent:
            if self.set_active_agent(project_agent):
                logger.info(f"Loaded project default agent: {project_agent}")
                return project_agent
            else:
                logger.warning(
                    f"Project default agent '{project_agent}' not found, trying next level"
                )

        # Priority 3: Global default
        global_agent, level = get_default_agent()
        if level == "global" and global_agent:
            if self.set_active_agent(global_agent):
                logger.info(f"Loaded global default agent: {global_agent}")
                return global_agent
            else:
                logger.warning(
                    f"Global default agent '{global_agent}' not found, trying fallback"
                )

        # Priority 4: Fallback to "default" agent. The koordinator
        # orchestrator bundle is no longer the universal default — the hub
        # promotes the elected coordinator to it at startup instead.
        if self.set_active_agent("default", load_defaults=True):
            logger.info("Loaded fallback default agent")
            return "default"

        logger.error("Failed to load any agent")
        return None

    def update_agent(
        self,
        original_name: str,
        new_name: str,
        description: str = "",
        profile: Optional[str] = None,
        system_prompt: str = "",
        default_skills: Optional[List[str]] = None,
    ) -> bool:
        """
        Update an existing agent's configuration.

        Can rename the agent (rename directory), update description,
        profile, system prompt, and default skills. Only works for agents in the
        local directory (.kollab/agents/).

        Args:
            original_name: Current name of the agent to update.
            new_name: New name for the agent (can be same as original).
            description: New description.
            profile: New preferred LLM profile name.
            system_prompt: New system prompt content.
            default_skills: List of skill names to auto-load when agent is activated.

        Returns:
            True if updated successfully, False otherwise.
        """
        import shutil

        # Check if agent exists
        if original_name not in self._agents:
            logger.warning(f"Agent not found for update: {original_name}")
            return False

        agent = self._agents[original_name]
        agent_dir = agent.directory

        # Only update local agents (not global)
        local_path = get_local_agents_path()
        if not self.local_agents_dir or not agent_dir.is_relative_to(
            self.local_agents_dir
        ):
            logger.warning(f"Cannot edit agent from global directory: {original_name}")
            return False

        try:
            # If renaming, we need to move the directory
            if new_name != original_name:
                # Check if new name already exists
                if new_name in self._agents:
                    logger.warning(f"Agent already exists with new name: {new_name}")
                    return False

                new_agent_dir = local_path / new_name

                # Check if target directory already exists
                if new_agent_dir.exists():
                    logger.warning(f"Target directory already exists: {new_agent_dir}")
                    return False

                # Rename directory
                shutil.move(str(agent_dir), str(new_agent_dir))
                agent_dir = new_agent_dir
                logger.info(f"Renamed agent directory: {original_name} -> {new_name}")

            # Update system_prompt.md
            prompt_file = agent_dir / "system_prompt.md"
            if system_prompt:
                prompt_file.write_text(system_prompt, encoding="utf-8")
                logger.info(f"Updated system prompt for agent: {new_name}")

            # Update or create agent.json for description, profile, and default_skills
            agent_json: Dict[str, Any] = {}
            if description or profile or default_skills:
                agent_json["description"] = description or f"Agent: {new_name}"
                if profile:
                    agent_json["profile"] = profile
                if default_skills:
                    agent_json["default_skills"] = default_skills

            if agent_json:
                json_file = agent_dir / "agent.json"
                json_file.write_text(
                    json.dumps(agent_json, indent=4, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info(f"Updated agent.json for agent: {new_name}")
            elif (agent_dir / "agent.json").exists():
                # Remove agent.json if no description or profile
                (agent_dir / "agent.json").unlink()

            # If renamed, remove old entry from dict
            if new_name != original_name:
                del self._agents[original_name]

            # Reload the agent from directory
            # Check if it overrides a global agent
            overrides = (
                self.global_agents_dir is not None
                and (self.global_agents_dir / new_name).exists()
            )
            updated_agent = Agent.from_directory(
                agent_dir,
                source="local",
                overrides_global=overrides,
                skill_library=self.skill_library,
            )
            if updated_agent:
                self._agents[new_name] = AgentRuntime.from_agent(updated_agent)

                # If this was the active agent, update the active name
                if self._active_agent_name == original_name:
                    self._active_agent_name = new_name

                logger.info(f"Updated agent: {new_name}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to update agent {original_name}: {e}")
            # If rename failed, try to revert
            if new_name != original_name:
                original_dir = local_path / original_name
                new_dir = local_path / new_name
                if not original_dir.exists() and new_dir.exists():
                    try:
                        shutil.move(str(new_dir), str(original_dir))
                    except Exception:
                        pass
            return False

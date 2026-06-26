"""Data models for the hub system."""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.config_utils import (
    get_config_directory_candidates,
    get_local_config_directory_candidates,
)

logger = logging.getLogger(__name__)


class AgentState(Enum):
    BOOTING = "booting"
    CONNECTING = "connecting"
    REGISTERED = "registered"
    IDLE = "idle"
    WORKING = "working"
    DISCONNECTING = "disconnecting"
    DEAD = "dead"


class MessageScope(Enum):
    DIRECT = "direct"
    PROJECT = "project"
    TEAM = "team"
    BROADCAST = "broadcast"


# Pool-based identity system with color castes.
# Each identity has a functional caste, personality, unique color,
# and optional agent_type/skills bindings.
@dataclass
class PoolIdentity:
    """A pool identity with associated color, personality, and optional bindings.

    The ``agent_type`` field lets a pool define a default agent bundle for
    this identity.  When koordinator spawns a "research" agent and lapis is
    assigned, lapis already knows it should load the "research" bundle
    because the pool says so.  If ``agent_type`` is empty the spawner's
    requested type is used (current behaviour).

    The ``skills`` field lets the pool attach extra skills per identity.
    A marketing pool could give ``strategist`` the skills
    ``["market-research", "competitor-analysis"]`` on top of whatever
    the base bundle provides.
    """

    name: str
    color_rgb: tuple  # (r, g, b) for TagBox rendering
    role_aliases: list  # functional names that map to this identity
    personality: str  # injected into roster context
    caste: (
        str  # communication, engineering, defense, intelligence, creative, leadership
    )
    agent_type: str = ""  # optional: default agent bundle for this identity
    skills: list = field(default_factory=list)  # optional: extra skills


# Backward-compat alias — external code importing GemIdentity still works.
GemIdentity = PoolIdentity


def _validate_pool_entry(entry: dict, index: int) -> Optional[str]:
    """Validate a single pool entry and return the name, or None on error."""
    name = entry.get("name")
    if not name or not isinstance(name, str):
        logger.warning(f"pool entry {index}: missing or invalid 'name', skipping")
        return None
    # Name format: lowercase alphanumeric + hyphens, must start with a letter
    import re

    if not re.match(r"^[a-z][a-z0-9-]*$", name):
        logger.warning(
            f"pool entry {index}: name '{name}' must match [a-z][a-z0-9-]*, skipping"
        )
        return None
    color = entry.get("color_rgb")
    if color is not None:
        if not isinstance(color, list) or len(color) != 3:
            logger.warning(f"pool entry {index} '{name}': color_rgb must be [r,g,b], skipping")
            return None
        if not all(isinstance(c, int) and 0 <= c <= 255 for c in color):
            logger.warning(
                f"pool entry {index} '{name}': color_rgb values must be 0-255, skipping"
            )
            return None
    return name


def _parse_pool_data(data: dict) -> List[PoolIdentity]:
    """Parse pool JSON data into PoolIdentity objects.

    Supports both ``"identities"`` (new schema) and ``"gems"`` (legacy
    schema) as the array key.
    """
    entries = data.get("identities") or data.get("gems") or []
    if not isinstance(entries, list):
        logger.warning("pool data: expected 'identities' or 'gems' array, got %s", type(entries).__name__)
        return []

    seen_names: set = set()
    identities: List[PoolIdentity] = []

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning(f"pool entry {i}: expected dict, got {type(entry).__name__}")
            continue

        name = _validate_pool_entry(entry, i)
        if name is None:
            continue
        if name in seen_names:
            logger.warning(f"pool entry {i}: duplicate name '{name}', skipping")
            continue
        seen_names.add(name)

        color_rgb = tuple(entry.get("color_rgb", (128, 128, 128)))
        identities.append(
            PoolIdentity(
                name=name,
                color_rgb=color_rgb,
                role_aliases=entry.get("role_aliases", []),
                personality=entry.get("personality", ""),
                caste=entry.get("caste", "general"),
                agent_type=entry.get("agent_type", ""),
                skills=entry.get("skills", []),
            )
        )

    return identities


def _load_from_file(path: Path, source: str) -> Optional[List[PoolIdentity]]:
    """Try loading identities from a JSON file. Returns None on any failure."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        identities = _parse_pool_data(data)
        if not identities:
            logger.warning(
                f"pool file ({source}: {path}) contained no valid identities, "
                f"trying next source"
            )
            return None
        logger.info(f"Loaded {len(identities)} identities from {source}: {path}")
        return identities
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning(f"failed to parse pool file ({source}: {path}): {e}")
        return None


def load_pool_identities(pool_file: str = "") -> List[PoolIdentity]:
    """Load identity pool from JSON config with fallback chain.

    Resolution order (highest to lowest priority):
    1. Project-local:   {cwd}/.kollab/hub/{pool_file}
    2. User global:     ~/.kollab/hub/{pool_file}
    3. User legacy:     ~/.kollab/hub/organizations/gems.json
    4. Bundled pool:    plugins/hub/organizations/{pool_file}
    5. Bundled legacy:  plugins/hub/organizations/gems.json
    6. Hardcoded fallback

    Args:
        pool_file: filename to load (default: "pool.json").
                   Searched in the same resolution order.
    """
    _pool_file = pool_file or "pool.json"
    hub_dir = Path(__file__).parent  # plugins/hub/

    # 1. Project-local pool
    for directory in get_local_config_directory_candidates():
        result = _load_from_file(directory / "hub" / _pool_file, "project-local")
        if result is not None:
            return result

    # 2. User global pool
    for directory in get_config_directory_candidates():
        result = _load_from_file(directory / "hub" / _pool_file, "user-global")
        if result is not None:
            return result

    # 3. User legacy (organizations/gems.json)
    for directory in get_config_directory_candidates():
        result = _load_from_file(
            directory / "hub" / "organizations" / "gems.json",
            "user-legacy",
        )
        if result is not None:
            return result

    # 4. Bundled pool file
    result = _load_from_file(hub_dir / "organizations" / _pool_file, "bundled")
    if result is not None:
        return result

    # 5. Bundled legacy (organizations/gems.json)
    result = _load_from_file(hub_dir / "organizations" / "gems.json", "bundled-legacy")
    if result is not None:
        return result

    # 6. Hardcoded fallback
    logger.debug("no pool file found, using hardcoded defaults")
    return _hardcoded_gem_identities()


# Keep legacy function name as alias for backward compat
_load_gem_identities = load_pool_identities


def _hardcoded_gem_identities() -> List[PoolIdentity]:
    """Fallback hardcoded identity list (identical to gems.json contents)."""
    return [
        # communication (blue spectrum)
        PoolIdentity(
            "lapis",
            (30, 90, 180),
            ["herald", "nexus", "messenger"],
            "calm mediator, bridges gaps",
            "communication",
        ),
        PoolIdentity(
            "sapphire",
            (15, 82, 186),
            ["oracle", "sage", "forecaster"],
            "serene foresight, sees outcomes",
            "communication",
        ),
        PoolIdentity(
            "aquamarine",
            (100, 200, 235),
            ["scout", "pathfinder", "recon"],
            "sharp and precise, gets intel fast",
            "communication",
        ),
        PoolIdentity(
            "zircon",
            (70, 130, 200),
            ["cipher", "analyst", "decoder"],
            "methodical investigator",
            "communication",
        ),
        # engineering (earth tones)
        PoolIdentity(
            "bismuth",
            (200, 100, 150),
            ["forger", "artisan", "builder"],
            "passionate builder, loves crafting tools",
            "engineering",
        ),
        PoolIdentity(
            "peridot",
            (120, 190, 33),
            ["tinker", "engineer", "technician"],
            "perfectionist, obsessed with optimization",
            "engineering",
        ),
        PoolIdentity(
            "jasper",
            (210, 120, 50),
            ["vanguard", "enforcer", "heavy"],
            "brute-force problem solver, relentless",
            "engineering",
        ),
        PoolIdentity(
            "nephrite",
            (80, 160, 80),
            ["ranger", "navigator", "pilot"],
            "reliable workhorse, just gets it done",
            "engineering",
        ),
        # defense (warm tones)
        PoolIdentity(
            "ruby",
            (200, 30, 50),
            ["sentinel", "guard", "watcher"],
            "fiery and impulsive, acts first",
            "defense",
        ),
        PoolIdentity(
            "garnet",
            (140, 20, 60),
            ["warden", "aegis", "protector"],
            "composed fusion of strength and wisdom",
            "defense",
        ),
        PoolIdentity(
            "topaz",
            (240, 200, 50),
            ["shield", "escort", "defender"],
            "silent and loyal, unbreakable composure",
            "defense",
        ),
        PoolIdentity(
            "hessonite",
            (200, 140, 60),
            ["captain", "commander", "tactician"],
            "strategic military mind",
            "defense",
        ),
        # intelligence (cool tones)
        PoolIdentity(
            "pearl",
            (230, 220, 240),
            ["analyst", "assistant", "curator"],
            "meticulous organizer, remembers everything",
            "intelligence",
        ),
        PoolIdentity(
            "moonstone",
            (200, 210, 230),
            ["observer", "monitor", "archivist"],
            "quiet watcher, notices what others miss",
            "intelligence",
        ),
        PoolIdentity(
            "opal",
            (180, 200, 255),
            ["catalyst", "synthesizer", "connector"],
            "brilliant but scattered, flashes of genius",
            "intelligence",
        ),
        PoolIdentity(
            "padparadscha",
            (240, 170, 140),
            ["logger", "historian", "chronicler"],
            "reports what just happened with clarity",
            "intelligence",
        ),
        # creative (purple/pink spectrum)
        PoolIdentity(
            "amethyst",
            (140, 80, 200),
            ["drift", "rebel", "wildcard"],
            "laid-back creative, breaks rules productively",
            "creative",
        ),
        PoolIdentity(
            "quartz",
            (240, 150, 170),
            ["architect", "visionary", "designer"],
            "empathetic leader-creator, bigger picture",
            "creative",
        ),
        PoolIdentity(
            "spinel",
            (230, 50, 120),
            ["flux", "tester", "chaos"],
            "chaotic energy, stress-tests everything",
            "creative",
        ),
        PoolIdentity(
            "citrine",
            (240, 230, 100),
            ["ember", "spark", "prototyper"],
            "rapid prototyper, throws ideas at the wall",
            "creative",
        ),
        # leadership (bright/bold)
        PoolIdentity(
            "diamond",
            (245, 245, 250),
            ["director", "supreme", "overseer"],
            "absolute authority, sees the whole system",
            "leadership",
        ),
        PoolIdentity(
            "aureate",
            (255, 230, 50),
            ["commander", "lead", "coordinator"],
            "efficient strategist, demands results",
            "leadership",
        ),
        PoolIdentity(
            "cobalt",
            (70, 100, 200),
            ["mentor", "counselor", "advisor"],
            "empathetic authority",
            "leadership",
        ),
        PoolIdentity(
            "coral",
            (255, 130, 170),
            ["founder", "champion", "pioneer"],
            "compassionate disruptor",
            "leadership",
        ),
    ]


GEM_IDENTITIES = load_pool_identities()

# Module-level convenience aliases for backward compat.
# New code should prefer POOL_* names; old code that imports
# GEM_IDENTITIES / DESIGNATION_POOL / GEM_BY_NAME / ROLE_TO_GEM
# continues to work without changes.

POOL_IDENTITIES = GEM_IDENTITIES

# Flat pool for identity assignment (coordinator iterates this)
DESIGNATION_POOL = [g.name for g in GEM_IDENTITIES]
POOL_NAMES = DESIGNATION_POOL  # preferred name

# Lookup tables
GEM_BY_NAME = {g.name: g for g in GEM_IDENTITIES}
POOL_BY_NAME = GEM_BY_NAME  # preferred name
ROLE_TO_GEM = {}
for _g in GEM_IDENTITIES:
    for _alias in _g.role_aliases:
        ROLE_TO_GEM[_alias] = _g.name


# Identity whose agent bundle is the koordinator orchestrator.
COORDINATOR_IDENTITY = "koordinator"


def desired_bundle_for_identity(identity: str, default_bundle: str = "default") -> str:
    """Return the agent bundle a given hub identity should run.

    The koordinator orchestrator bundle (its system prompt plus the
    hub-spawn/hub-stop/hub-queue tools) belongs ONLY to the elected
    coordinator, i.e. the agent whose identity is ``koordinator``. Every
    pool gem runs its declared ``agent_type`` (``coder`` by default in
    ``pool.json``); anything unrecognised falls back to ``default_bundle``.

    This is the single source of truth used by the hub to reconcile a plain
    ``kollab`` launch's loaded bundle to its assigned mesh role, so it is a
    pure function with no side effects to keep it unit-testable.

    Args:
        identity: The hub identity the agent was assigned (e.g. "lapis").
        default_bundle: Fallback bundle for non-gem, non-coordinator names.

    Returns:
        The agent bundle name this identity should run.
    """
    if identity == COORDINATOR_IDENTITY:
        return COORDINATOR_IDENTITY
    pool = POOL_BY_NAME.get(identity)
    if pool and pool.agent_type:
        return pool.agent_type
    return default_bundle


@dataclass
class HubMessage:
    """A message between agents."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: str = "message"
    action: str = "message"
    from_agent: str = ""
    from_identity: str = ""
    to: str = ""
    content: str = ""
    scope: str = MessageScope.DIRECT.value
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    force: bool = False
    """If True, this message breaks through the recipient's
    cooldown even if the sender is not the coordinator."""
    thread_id: str = ""
    """Conversation thread identifier. First message in a thread sets this
    to its own id; all replies carry the same thread_id. Empty = unthreaded."""
    reply_to: str = ""
    """ID of the specific message being replied to within the thread."""

    def __post_init__(self):
        # If no thread_id set, this message starts a new thread
        if not self.thread_id:
            self.thread_id = self.id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "action": self.action,
            "from_agent": self.from_agent,
            "from_identity": self.from_identity,
            "to": self.to,
            "content": self.content,
            "scope": self.scope,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "force": self.force,
            "thread_id": self.thread_id,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HubMessage":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkSlot:
    """A pending work item in the queue."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    task: str = ""
    project: str = ""
    priority: int = 5
    queued_at: float = field(default_factory=time.time)
    queued_by: str = ""
    assigned_to: Optional[str] = None
    status: str = "pending"
    context: str = ""
    required_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "project": self.project,
            "priority": self.priority,
            "queued_at": self.queued_at,
            "queued_by": self.queued_by,
            "assigned_to": self.assigned_to,
            "status": self.status,
            "context": self.context,
            "required_capabilities": self.required_capabilities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkSlot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

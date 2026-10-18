"""Persona rosters and teams: the JSON libraries behind agent names.

A *roster* is a themed set of personas (the gems, a marketing crew, ...). A *team*
wires personas into a reporting chain. Both are plain JSON that resolve like agents
and skills: bundled (``bundles/rosters``, ``bundles/teams``), then user
(``~/.kollab/rosters``, ``~/.kollab/teams``), then project (``.kollab/rosters``,
``.kollab/teams``); a later file replaces an earlier one of the same name.

Contract, locations and examples: ``docs/specs/rosters-and-teams.md``.
"""

import json
import logging
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kollabor_config.config_utils import (
    APP_CONFIG_DIR_NAME,
    get_bundled_rosters_dir,
    get_bundled_teams_dir,
    get_config_directory,
)

logger = logging.getLogger(__name__)

DEFAULT_ROSTER = "gems"
ROSTER_CONFIG_PATH = "plugins.hub.roster"
AVATAR_KINDS = ("gem", "image", "emoji", "initials")

_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
# A persona with no color of its own gets one of these, stable per name.
_PALETTE = (
    (120, 200, 255),
    (255, 180, 100),
    (150, 255, 150),
    (255, 140, 180),
    (200, 170, 255),
    (100, 240, 220),
)


class LibraryError(ValueError):
    """A roster or team file (or one entry in it) breaks the contract."""


@dataclass
class Persona:
    """One launchable identity. ``name`` is the hub designation (``--as <name>``)."""

    name: str
    roster: str = ""  # "" for a persona defined inline in a team
    display_name: str = ""
    avatar: Dict[str, str] = field(default_factory=lambda: {"kind": "initials"})
    color: Tuple[int, int, int] = (128, 128, 128)
    agent: str = ""  # agent bundle it runs; "" = the default bundle
    role: str = ""
    personality: str = ""
    skills: List[str] = field(default_factory=list)
    profile: str = ""  # LLM profile it prefers
    prompt: str = ""  # appended to the bundle's prompt when it runs
    caste: str = ""  # gems: communication, engineering, ...
    role_aliases: List[str] = field(default_factory=list)


@dataclass
class Roster:
    name: str
    display_name: str
    description: str
    avatar: Dict[str, str]  # default for personas that set none
    personas: List[Persona]
    source: str  # bundled | user | project
    path: Path


@dataclass
class Member:
    """A persona's seat on a team. role/agent/profile fall back to the persona's."""

    persona: Persona
    role: str = ""
    agent: str = ""
    profile: str = ""
    prompt: str = ""  # team-specific instructions
    reports_to: Optional[str] = None  # another member's persona name


@dataclass
class Team:
    name: str
    description: str
    roster: str  # default roster for bare persona references
    lead: str  # persona name of the member that starts first
    members: List[Member]
    source: str
    path: Path


@dataclass
class Library:
    rosters: Dict[str, Roster] = field(default_factory=dict)
    teams: Dict[str, Team] = field(default_factory=dict)
    inline: Dict[str, Persona] = field(default_factory=dict)  # personas defined in teams
    problems: List[Dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- parsing


def _text(entry: Dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key, "")
    if not isinstance(value, str):
        raise LibraryError(f"{where}: '{key}' must be a string")
    return value.strip()


def _text_list(entry: Dict[str, Any], key: str, where: str) -> List[str]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LibraryError(f"{where}: '{key}' must be a list of strings")
    return list(value)


def _slug(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise LibraryError(f"{where}: name must match [a-z][a-z0-9-]*, got {value!r}")
    return value


def _color(value: Any, where: str, name: str) -> Tuple[int, int, int]:
    if value is None:
        return _PALETTE[zlib.crc32(name.encode()) % len(_PALETTE)]
    ok = isinstance(value, list) and len(value) == 3
    if not ok or not all(type(c) is int and 0 <= c <= 255 for c in value):
        raise LibraryError(f"{where}: color must be [r, g, b] with values 0-255")
    return (value[0], value[1], value[2])


def _avatar(value: Any, where: str, default: Dict[str, str]) -> Dict[str, str]:
    if value is None:
        return dict(default)
    kind = value.get("kind") if isinstance(value, dict) else None
    if kind not in AVATAR_KINDS:
        raise LibraryError(f"{where}: avatar kind must be one of {', '.join(AVATAR_KINDS)}")
    if kind == "emoji":
        glyph = value.get("value")
        if not isinstance(glyph, str) or not glyph.strip():
            raise LibraryError(f"{where}: an emoji avatar needs a 'value'")
        return {"kind": "emoji", "value": glyph}
    if kind == "image":
        src = value.get("src")
        if not isinstance(src, str) or not src.strip():
            raise LibraryError(f"{where}: an image avatar needs a 'src'")
        parts = Path(src).parts
        if not src.startswith("https://") and (Path(src).is_absolute() or ".." in parts):
            raise LibraryError(
                f"{where}: avatar src must be an https URL or a path inside the roster's folder"
            )
        return {"kind": "image", "src": src}
    return {"kind": kind}


def _persona(entry: Any, roster: str, default_avatar: Dict[str, str], where: str) -> Persona:
    if not isinstance(entry, dict):
        raise LibraryError(f"{where}: expected an object")
    name = _slug(entry.get("name"), where)
    where = f"{where} '{name}'"
    return Persona(
        name=name,
        roster=roster,
        display_name=_text(entry, "display_name", where) or name.replace("-", " ").title(),
        avatar=_avatar(entry.get("avatar"), where, default_avatar),
        color=_color(entry.get("color"), where, name),
        agent=_text(entry, "agent", where),
        role=_text(entry, "role", where),
        personality=_text(entry, "personality", where),
        skills=_text_list(entry, "skills", where),
        profile=_text(entry, "profile", where),
        prompt=_text(entry, "prompt", where),
        caste=_text(entry, "caste", where),
        role_aliases=_text_list(entry, "role_aliases", where),
    )


def parse_roster(
    data: Any, path: Path, source: str, problems: Optional[List[Dict[str, str]]] = None
) -> Roster:
    """Parse one roster file. A bad persona is skipped (and reported); a bad roster raises."""
    if not isinstance(data, dict):
        raise LibraryError("expected a JSON object")
    name = _slug(data.get("name", path.stem), "roster")
    default_avatar = _avatar(data.get("avatar"), f"roster '{name}'", {"kind": "initials"})
    entries = data.get("personas")
    if not isinstance(entries, list):
        raise LibraryError(f"roster '{name}': 'personas' must be a list")
    personas: List[Persona] = []
    seen: set = set()
    for index, entry in enumerate(entries):
        try:
            persona = _persona(entry, name, default_avatar, f"persona {index}")
            if persona.name in seen:
                raise LibraryError(f"persona {index}: duplicate name '{persona.name}'")
        except LibraryError as exc:
            if problems is not None:
                problems.append({"file": str(path), "message": f"roster '{name}': {exc}"})
            continue
        seen.add(persona.name)
        personas.append(persona)
    if not personas:
        raise LibraryError(f"roster '{name}' has no valid personas")
    return Roster(
        name=name,
        display_name=_text(data, "display_name", f"roster '{name}'") or name.replace("-", " ").title(),
        description=_text(data, "description", f"roster '{name}'"),
        avatar=default_avatar,
        personas=personas,
        source=source,
        path=path,
    )


def _lookup(rosters: Dict[str, Roster], ref: str, prefer: Iterable[str] = ()) -> Optional[Persona]:
    """``roster/name`` names one roster; a bare name tries ``prefer`` first, then load order."""
    roster_name, _, name = ref.rpartition("/")
    order = [roster_name] if roster_name else [*prefer, *rosters]
    for candidate in order:
        roster = rosters.get(candidate)
        found = next((p for p in roster.personas if p.name == name), None) if roster else None
        if found:
            return found
    return None


def parse_team(
    data: Any,
    path: Path,
    source: str,
    rosters: Dict[str, Roster],
    prefer: str = DEFAULT_ROSTER,
) -> Team:
    """Parse one team file; any broken reference raises (a half team is worse than none)."""
    if not isinstance(data, dict):
        raise LibraryError("expected a JSON object")
    name = _slug(data.get("name", path.stem), "team")
    where = f"team '{name}'"
    roster = _text(data, "roster", where)
    if roster and roster not in rosters:
        raise LibraryError(f"{where}: roster '{roster}' not found")
    entries = data.get("members")
    if not isinstance(entries, list) or not entries:
        raise LibraryError(f"{where}: 'members' must be a non-empty list")
    members: List[Member] = []
    for index, entry in enumerate(entries):
        seat = f"{where} member {index}"
        if not isinstance(entry, dict):
            raise LibraryError(f"{seat}: expected an object")
        ref = entry.get("persona")
        if isinstance(ref, dict):
            persona = _persona(ref, "", {"kind": "initials"}, seat)
        elif isinstance(ref, str) and ref.strip():
            persona = _lookup(rosters, ref.strip(), [roster or prefer])
            if persona is None:
                raise LibraryError(f"{seat}: persona '{ref}' not found in any roster")
        else:
            raise LibraryError(f"{seat}: 'persona' must be a name or an inline persona")
        reports_to = entry.get("reports_to")
        if reports_to is not None and not isinstance(reports_to, str):
            raise LibraryError(f"{seat}: 'reports_to' must be a persona name or null")
        members.append(
            Member(
                persona=persona,
                role=_text(entry, "role", seat) or persona.role,
                agent=_text(entry, "agent", seat) or persona.agent,
                profile=_text(entry, "profile", seat) or persona.profile,
                prompt=_text(entry, "prompt", seat),
                reports_to=reports_to or None,
            )
        )
    names = [m.persona.name for m in members]
    for dup in {n for n in names if names.count(n) > 1}:
        raise LibraryError(f"{where}: '{dup}' is on the team twice")
    lead = _text(data, "lead", where)
    if lead not in names:
        raise LibraryError(f"{where}: lead '{lead}' is not a member")
    by_name = {m.persona.name: m for m in members}
    for member in members:
        boss, hops = member.reports_to, 0
        while boss is not None:
            if boss not in by_name:
                raise LibraryError(f"{where}: {member.persona.name} reports to '{boss}', who is not a member")
            hops += 1
            if hops > len(members):
                raise LibraryError(f"{where}: {member.persona.name} is in a reporting loop")
            boss = by_name[boss].reports_to
    return Team(
        name=name,
        description=_text(data, "description", where),
        roster=roster,
        lead=lead,
        members=members,
        source=source,
        path=path,
    )


# ---------------------------------------------------------------- loading

_SOURCES = ("bundled", "user", "project")


def project_dir(folder: Optional[Any] = None) -> Path:
    """The ``.kollab`` of ``folder`` (default: the current directory)."""
    return Path(folder or Path.cwd()) / APP_CONFIG_DIR_NAME


def _library_dirs(kind: str, folder: Optional[Any]) -> List[Tuple[str, Path]]:
    bundled = get_bundled_rosters_dir() if kind == "rosters" else get_bundled_teams_dir()
    found = [("bundled", bundled)] if bundled else []
    found.append(("user", get_config_directory() / kind))
    found.append(("project", project_dir(folder) / kind))
    return found


def _files(kind: str, folder: Optional[Any]) -> List[Tuple[str, Path]]:
    return [(src, p) for src, d in _library_dirs(kind, folder) for p in sorted(d.glob("*.json"))]


def _stamp(files: Iterable[Tuple[str, Path]]) -> Tuple[Any, ...]:
    stamp = []
    for _, path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        stamp.append((str(path), st.st_mtime_ns, st.st_size))
    return tuple(stamp)


_library_cache: Dict[Tuple[Any, ...], Library] = {}


def load_library(folder: Optional[Any] = None) -> Library:
    """Every roster and team visible from ``folder``. Cached until a file changes."""
    roster_files, team_files = _files("rosters", folder), _files("teams", folder)
    key = (_stamp(roster_files), _stamp(team_files), get_config_directory())
    cached = _library_cache.get(key)
    if cached is not None:
        return cached

    lib = Library()
    for source, path in roster_files:
        try:
            roster = parse_roster(json.loads(path.read_text(encoding="utf-8")), path, source, lib.problems)
        except (OSError, ValueError) as exc:  # LibraryError is a ValueError
            lib.problems.append({"file": str(path), "message": str(exc)})
            continue
        lib.rosters[roster.name] = roster  # a later source replaces an earlier one
    prefer = folder_roster_name(folder)
    for source, path in team_files:
        try:
            team = parse_team(json.loads(path.read_text(encoding="utf-8")), path, source, lib.rosters, prefer)
        except (OSError, ValueError) as exc:
            lib.problems.append({"file": str(path), "message": str(exc)})
            continue
        lib.teams[team.name] = team
    for team in lib.teams.values():
        for member in team.members:
            if not member.persona.roster:
                lib.inline.setdefault(member.persona.name, member.persona)
    for problem in lib.problems:
        logger.warning("roster/team library: %(file)s: %(message)s", problem)

    if len(_library_cache) >= 16:
        _library_cache.clear()
    _library_cache[key] = lib
    return lib


_json_cache: Dict[str, Tuple[int, Any]] = {}


def _read_json(path: Path) -> Any:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _json_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    _json_cache[str(path)] = (mtime, data)
    return data


def folder_roster_name(folder: Optional[Any] = None) -> str:
    """The roster a folder draws agent names from: ``plugins.hub.roster``, project over global."""
    for path in (project_dir(folder) / "config.json", get_config_directory() / "config.json"):
        data = _read_json(path)
        value = data
        for key in ROSTER_CONFIG_PATH.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_ROSTER


# ---------------------------------------------------------------- lookups


def split_ref(ref: str) -> Tuple[str, str]:
    """``"roster/name"`` -> (roster, name); a bare name -> ("", name)."""
    roster, _, name = (ref or "").strip().rpartition("/")
    return roster, name


def find_persona(ref: str, folder: Optional[Any] = None) -> Optional[Persona]:
    """The persona a name refers to: ``name`` or ``roster/name``, in any roster.

    A bare name prefers the folder's roster. ``lapis-2`` (what a roster hands out
    once every name is live) finds ``lapis``. Personas defined inline in a team
    are found last.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    lib = load_library(folder)
    prefer = [folder_roster_name(folder)]
    persona = _lookup(lib.rosters, ref, prefer)
    if persona is None:
        base, _, number = ref.rpartition("-")
        if base and number.isdigit():
            persona = _lookup(lib.rosters, base, prefer)
    if persona is None:
        persona = lib.inline.get(split_ref(ref)[1])
    return persona


def roster_personas(folder: Optional[Any] = None) -> List[Persona]:
    """The personas of the folder's roster, in file order (the designation pool)."""
    lib = load_library(folder)
    wanted = folder_roster_name(folder)
    roster = lib.rosters.get(wanted)
    if roster is None:
        logger.warning("roster '%s' not found, using '%s'", wanted, DEFAULT_ROSTER)
        roster = lib.rosters.get(DEFAULT_ROSTER)
    return list(roster.personas) if roster else []


def roster_names(folder: Optional[Any] = None) -> List[str]:
    return [p.name for p in roster_personas(folder)]


def persona_color(name: str, folder: Optional[Any] = None) -> Tuple[int, int, int]:
    """A name's color: its persona's, else one picked from the palette (stable per name)."""
    persona = find_persona(name, folder)
    return persona.color if persona else _PALETTE[zlib.crc32(name.encode()) % len(_PALETTE)]


# ---------------------------------------------------------------- prompts and launch order


def persona_context(persona: Persona) -> List[str]:
    """Lines the hub adds to the agent's context. A persona with no role or prompt adds none."""
    if not (persona.prompt or persona.role):
        return []
    head = f"your persona: {persona.display_name or persona.name}"
    if persona.role:
        head += f" ({persona.role})"
    return [head, *([persona.prompt] if persona.prompt else [])]


def team_launch_order(team: Team) -> List[Member]:
    """Lead first, then each member after the one they report to (listing order within a level)."""
    by_name = {m.persona.name: m for m in team.members}

    def depth(member: Member) -> int:
        hops = 0
        while member.persona.name != team.lead and member.reports_to in by_name:
            member = by_name[member.reports_to]
            hops += 1
        return hops

    lead_first = sorted(
        enumerate(team.members),
        key=lambda item: (item[1].persona.name != team.lead, depth(item[1]), item[0]),
    )
    return [member for _, member in lead_first]


def _label(member: Member) -> str:
    return f"{member.persona.name} ({member.role})" if member.role else member.persona.name


def team_member_prompt(team: Team, member: Member) -> str:
    """What a launcher adds for one seat: its team instructions plus the reporting chain."""
    me = member.persona.name
    lines = [member.prompt, ""] if member.prompt else []
    lines.append(f"--- team: {team.name} ---")
    lines.append(f"your identity: {me}")
    lines.append(f"your role: {member.role or 'team member'}")
    if member.reports_to:
        lines.append(f"you report to: {member.reports_to}")
    directs = [m for m in team.members if m.reports_to == me]
    if directs:
        lines.append("your direct reports: " + ", ".join(_label(m) for m in directs))
    others = [m for m in team.members if m.persona.name != me]
    if others:
        lines.append(f"your team ({team.name}): " + ", ".join(_label(m) for m in others))
    lines.append("")
    lines.append('communicate with other agents using: <hub_msg to="identity">your message</hub_msg>')
    lines.append("when you finish a task, report to your manager. when you need help, ask a teammate.")
    lines.append("--- end team ---")
    return "\n".join(lines)

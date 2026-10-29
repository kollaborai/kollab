"""Prompt libraries and role compositions for the System Prompts page.

A library is a folder of shared prompt sections under
~/.kollab/prompt-libraries/<library>/<key>.md. The "defaults" library is the
shipped _base section set: a file in its folder replaces that _base section
for every role. Any other library is the user's own, sections a role can add
(say, shorter versions for small local models).

A role (an agent bundle, or "default") picks from them in
~/.kollab/prompt-roles/<role>.json:

    {"variants": {
        "all":   {"exclude": ["notifications"], "add": ["local-lite/tool-workflow"]},
        "local": {"providers": ["ollama"], "exclude": ["code-review"], "add": []}}}

"exclude" drops _base sections by key, "add" appends library sections after
them in the order listed. A variant that lists the active profile's provider
wins over "all"; with no file, a role gets every _base section as before.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .prompt_overrides import (
    PromptOverrideError,
    PromptOverrideStore,
    is_valid_bundle,
    is_valid_section_key,
)

logger = logging.getLogger(__name__)

DEFAULTS_LIBRARY = "defaults"
ALL_VARIANT = "all"

# Never excluded: the per-turn context and the base wrapper itself.
UNEXCLUDABLE_KEYS = frozenset({"01-session-context", "base_prompt"})

PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_ENV_LIBRARIES_DIR = "KOLLAB_PROMPT_LIBRARIES_DIR"
_ENV_ROLES_DIR = "KOLLAB_PROMPT_ROLES_DIR"


class CompositionError(ValueError):
    """Invalid library, role or composition data."""


def get_libraries_dir() -> Path:
    env_dir = os.environ.get(_ENV_LIBRARIES_DIR)
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".kollab" / "prompt-libraries"


def get_roles_dir() -> Path:
    env_dir = os.environ.get(_ENV_ROLES_DIR)
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".kollab" / "prompt-roles"


def library_store() -> PromptOverrideStore:
    """Library sections share the override store's layout: <root>/<library>/<key>.md."""
    return PromptOverrideStore(root=get_libraries_dir())


def is_base_section(path: Any) -> bool:
    """True for a section file inside an agents _base directory."""
    try:
        return "_base" in Path(path).parts
    except TypeError:
        return False


def list_user_libraries() -> List[str]:
    """User library names (every library folder except defaults), sorted."""
    root = get_libraries_dir()
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir()
        and entry.name != DEFAULTS_LIBRARY
        and is_valid_bundle(entry.name)
    )


def create_library(name: str) -> Path:
    if not is_valid_bundle(name) or name == DEFAULTS_LIBRARY:
        raise CompositionError(f"invalid library name: {name!r}")
    path = get_libraries_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean_variant(name: str, raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise CompositionError(f"variant {name!r} must be an object")
    exclude = raw.get("exclude", [])
    add = raw.get("add", [])
    providers = raw.get("providers", [])
    if not isinstance(exclude, list) or not all(
        is_valid_section_key(k) for k in exclude
    ):
        raise CompositionError(f"variant {name!r}: exclude must be section keys")
    if any(k in UNEXCLUDABLE_KEYS for k in exclude):
        raise CompositionError(f"variant {name!r}: that section is always sent")
    if not isinstance(add, list):
        raise CompositionError(f"variant {name!r}: add must be a list")
    for ref in add:
        lib, _, key = str(ref).partition("/")
        if (
            not is_valid_bundle(lib)
            or lib == DEFAULTS_LIBRARY
            or not is_valid_section_key(key)
        ):
            raise CompositionError(f"variant {name!r}: bad library section {ref!r}")
    if not isinstance(providers, list) or not all(
        isinstance(p, str) and PROVIDER_RE.match(p) for p in providers
    ):
        raise CompositionError(f"variant {name!r}: providers must be provider names")
    cleaned: Dict[str, Any] = {
        "exclude": list(dict.fromkeys(exclude)),
        "add": list(dict.fromkeys(add)),
    }
    if name != ALL_VARIANT:
        cleaned["providers"] = list(dict.fromkeys(providers))
    return cleaned


def clean_composition(data: Any) -> Dict[str, Any]:
    """Validated copy of a composition; raises CompositionError."""
    if not isinstance(data, dict) or not isinstance(data.get("variants"), dict):
        raise CompositionError('composition must be {"variants": {...}}')
    variants: Dict[str, Any] = {}
    for name, raw in data["variants"].items():
        if not is_valid_bundle(name):
            raise CompositionError(f"invalid variant name: {name!r}")
        variants[name] = _clean_variant(name, raw)
    variants.setdefault(ALL_VARIANT, {"exclude": [], "add": []})
    return {"variants": variants}


def _role_path(role: str) -> Path:
    if not is_valid_bundle(role):
        raise CompositionError(f"invalid role name: {role!r}")
    return get_roles_dir() / f"{role}.json"


def load_composition(role: str) -> Dict[str, Any]:
    """The role's composition; every _base section when it has none."""
    empty = {"variants": {ALL_VARIANT: {"exclude": [], "add": []}}}
    try:
        path = _role_path(role)
    except CompositionError:
        return empty
    try:
        return clean_composition(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return empty
    except (OSError, ValueError) as exc:
        logger.warning(f"Ignoring unreadable prompt composition {path}: {exc}")
        return empty


def save_composition(role: str, data: Any) -> Dict[str, Any]:
    cleaned = clean_composition(data)
    path = _role_path(role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Saved prompt composition for {role}")
    return cleaned


def active_variant_name(composition: Dict[str, Any], provider: Optional[str]) -> str:
    if provider:
        for name, variant in composition["variants"].items():
            if name != ALL_VARIANT and provider in variant.get("providers", []):
                return name
    return ALL_VARIANT


def active_variant(
    role: str, provider: Optional[str], variant: Optional[str] = None
) -> Dict[str, Any]:
    """The variant a render uses: the one named, else the provider's, else "all"."""
    composition = load_composition(role)
    if variant in composition["variants"]:
        return composition["variants"][variant]
    return composition["variants"][active_variant_name(composition, provider)]


def library_section(ref: str) -> Optional[str]:
    """Text of a "library/key" section, or None when it is missing."""
    lib, _, key = ref.partition("/")
    try:
        return library_store().get_override(lib, key)
    except PromptOverrideError:
        return None


def added_sections_text(
    role: str, provider: Optional[str], variant: Optional[str] = None
) -> str:
    """The role's added library sections, joined in order; missing ones skipped."""
    parts = []
    for ref in active_variant(role, provider, variant).get("add", []):
        text = library_section(ref)
        if text is None:
            logger.warning(f"Prompt role {role} adds missing library section {ref}")
            continue
        parts.append(text.strip())
    return "\n\n".join(part for part in parts if part)

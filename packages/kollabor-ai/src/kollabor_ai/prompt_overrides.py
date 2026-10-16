"""User-space prompt overrides: ~/.kollab/prompt-overrides/<bundle>/<key>.md.

The System Prompts page edits individual prompt sections without touching
the shipped bundles or installed agent directories. Each override is one
markdown file per section key; absent file means "use the bundled
content". Overrides never write into bundles/ — the store owns exactly
one directory tree and sanitizes every key it accepts.

The renderer consults the store when it resolves <trender include> tags
(see PromptRenderer._include_file), so an override changes what agents
actually receive, not just what the page shows.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default bundle name when an override is not tied to a specific agent.
DEFAULT_BUNDLE = "default"

# Section keys are file stems (or stable slugs): letters, digits, dots,
# dashes, underscores. No separators, no traversal.
SECTION_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Bundle names follow agent directory naming.
BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Max override size (1 MiB) — prompts are large but not unbounded.
MAX_OVERRIDE_BYTES = 1_048_576

_ENV_OVERRIDE_DIR = "KOLLAB_PROMPT_OVERRIDES_DIR"


def get_overrides_dir() -> Path:
    """Root directory for user prompt overrides.

    Tests point KOLLAB_PROMPT_OVERRIDES_DIR at a temp dir; production
    resolves to ~/.kollab/prompt-overrides.
    """
    env_dir = os.environ.get(_ENV_OVERRIDE_DIR)
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".kollab" / "prompt-overrides"


def is_valid_section_key(key: Any) -> bool:
    return (
        isinstance(key, str)
        and bool(key)
        and bool(SECTION_KEY_RE.match(key))
        and ".." not in key
    )


def is_valid_bundle(bundle: Any) -> bool:
    return (
        isinstance(bundle, str)
        and bool(bundle)
        and bool(BUNDLE_RE.match(bundle))
        and ".." not in bundle
    )


class PromptOverrideError(ValueError):
    """Invalid bundle/section key or content for a prompt override."""


class PromptOverrideStore:
    """Load/save/delete user prompt overrides for prompt section keys.

    Layout: <root>/<bundle>/<section-key>.md
    All writes stay inside <root>; keys are validated before use.
    """

    def __init__(self, root: Optional[Path] = None):
        self._root = Path(root) if root else None

    @property
    def root(self) -> Path:
        if self._root is not None:
            return self._root
        return get_overrides_dir()

    def _bundle_dir(self, bundle: str) -> Path:
        if not is_valid_bundle(bundle):
            raise PromptOverrideError(f"invalid bundle name: {bundle!r}")
        return self.root / bundle

    def _section_path(self, bundle: str, section_key: str) -> Path:
        if not is_valid_section_key(section_key):
            raise PromptOverrideError(f"invalid section key: {section_key!r}")
        return self._bundle_dir(bundle) / f"{section_key}.md"

    def get_override(self, bundle: str, section_key: str) -> Optional[str]:
        """Return the override content for a section, or None."""
        try:
            path = self._section_path(bundle, section_key)
        except PromptOverrideError:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(f"Could not read prompt override {path}: {exc}")
            return None

    def has_override(self, bundle: str, section_key: str) -> bool:
        try:
            return self._section_path(bundle, section_key).exists()
        except PromptOverrideError:
            return False

    def save_override(self, bundle: str, section_key: str, content: str) -> Path:
        """Write an override file. Returns the written path.

        Raises PromptOverrideError for invalid keys or oversized content.
        """
        if content is None:
            raise PromptOverrideError("override content is required")
        if len(content.encode("utf-8")) > MAX_OVERRIDE_BYTES:
            raise PromptOverrideError("override content exceeds 1 MiB limit")
        path = self._section_path(bundle, section_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Saved prompt override {bundle}/{section_key}")
        return path

    def delete_override(self, bundle: str, section_key: str) -> bool:
        """Delete an override. Returns True if it existed."""
        try:
            path = self._section_path(bundle, section_key)
        except PromptOverrideError:
            return False
        try:
            path.unlink()
            logger.info(f"Deleted prompt override {bundle}/{section_key}")
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning(f"Could not delete prompt override {path}: {exc}")
            return False

    def list_overrides(self, bundle: Optional[str] = None) -> List[Dict[str, str]]:
        """List overrides as {bundle, section_key, path}; optionally one bundle.

        Malformed directories/files are skipped, never followed outside root.
        """
        results: List[Dict[str, str]] = []
        root = self.root
        if not root.exists():
            return results
        bundles = (
            [bundle]
            if bundle
            else sorted(
                (entry.name for entry in root.iterdir() if entry.is_dir()),
            )
        )
        for bundle_name in bundles:
            bundle_dir = root / bundle_name
            if not bundle_dir.is_dir():
                continue
            for entry in sorted(bundle_dir.iterdir()):
                if entry.is_file() and entry.suffix == ".md":
                    results.append(
                        {
                            "bundle": bundle_name,
                            "section_key": entry.stem,
                            "path": str(entry),
                        }
                    )
        return results

    def overrides_for_bundle(self, bundle: str) -> Dict[str, str]:
        """All override contents for a bundle as {section_key: content}."""
        return {
            item["section_key"]: self.get_override(bundle, item["section_key"]) or ""
            for item in self.list_overrides(bundle)
        }

    def section_key_for_path(self, path: Path) -> str:
        """Logical section key for a prompt file: the file stem.

        Stems are unique within a bundle's include chain in practice
        (00-header, 10-practices, hub-context, ...); the bundle namespace
        separates agents. Traversal-unsafe stems just never match overrides.
        """
        return Path(path).stem


_default_store: Optional[PromptOverrideStore] = None


def get_default_store() -> PromptOverrideStore:
    """Process-wide store instance (tests may monkeypatch get_overrides_dir)."""
    global _default_store
    if _default_store is None:
        _default_store = PromptOverrideStore()
    return _default_store


def set_default_store(store: Optional[PromptOverrideStore]) -> None:
    """Replace the process-wide store (used by tests and the engine routes)."""
    global _default_store
    _default_store = store

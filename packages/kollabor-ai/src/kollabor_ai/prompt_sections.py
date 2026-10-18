"""Prompt section inspection for the System Prompts page.

Given a prompt bundle (an agent name, or "default" for the file-based
system prompt), walks the real assembly chain — system_prompt.md plus
every <trender type="include"> it pulls in, in true assembly order —
and classifies each section:

- editable:   a section file on disk; a user override may replace it
- runtime:    content generated per session (hub blocks, volatile
              session context, aliases, active LLM); override rejected
- generated:  assembled by code rather than an include (skills, system
              docs); override rejected

Token counts use tiktoken's o200k_base when available, with a cheap
character-based fallback so the page never fails on a missing wheel.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .prompt_overrides import DEFAULT_BUNDLE, PromptOverrideStore

logger = logging.getLogger(__name__)

# Sections whose include content is generated per session — not editable.
RUNTIME_INCLUDE_SUFFIXES = ("01-session-context.md",)
RUNTIME_INCLUDE_SUFFIXES_STEMS = tuple(
    Path(name).stem for name in RUNTIME_INCLUDE_SUFFIXES
)

# Section files that only feed runtime trender tags (hub blocks etc.).
RUNTIME_PATTERNS = (
    re.compile(r"<trender\s+type=\"hub_"),
    re.compile(r"<trender\s+type=\"active_llm\""),
    re.compile(r"<trender\s+type=\"shell_aliases\""),
    re.compile(r"<trender\s+type=\"mcp_tools\""),
)

INCLUDE_RE = re.compile(
    r'<trender\s+type=["\']include["\']\s+path=["\']([^"\']+)["\']\s*/?>',
    re.DOTALL,
)

_tokens_encoder = None
_tokens_encoder_tried = False


def _get_encoder():
    """tiktoken o200k_base encoder, or None when the wheel is missing."""
    global _tokens_encoder, _tokens_encoder_tried
    if not _tokens_encoder_tried:
        _tokens_encoder_tried = True
        try:
            import tiktoken

            _tokens_encoder = tiktoken.get_encoding("o200k_base")
        except Exception as exc:  # pragma: no cover - depends on env
            logger.info(f"tiktoken unavailable, falling back to estimate: {exc}")
    return _tokens_encoder


def count_tokens(text: str) -> int:
    """o200k_base token count with a characters/4 fallback estimate."""
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:  # pragma: no cover - defensive
            pass
    return max(1, len(text) // 4)


@dataclass
class SectionInfo:
    """One section of a bundle's assembled system prompt."""

    key: str
    order: int
    kind: str  # editable | runtime | generated
    source_path: Optional[str]
    overridden: bool
    tokens: int
    chars: int
    title: str = ""
    tags: List[str] = field(default_factory=list)
    # Contract metadata (prompt-page-contract.md): stable=False sections are
    # stripped from the cached prefix and re-emitted per turn on the rail.
    stable: bool = True
    stage: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "order": self.order,
            "kind": self.kind,
            "source_path": self.source_path,
            "overridden": self.overridden,
            "tokens": self.tokens,
            "chars": self.chars,
            "title": self.title,
            "tags": self.tags,
            "stable": self.stable,
            "stage": self.stage,
        }


class SectionInfoError(ValueError):
    """Unknown bundle or invalid section key."""


def _section_title(content: str) -> str:
    """First markdown heading or first non-empty line, truncated."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        return stripped[:80]
    return ""


def _classify(content: str, source: Optional[Path]) -> str:
    if source is not None:
        try:
            rel = source.name
        except OSError:
            rel = ""
        if rel in RUNTIME_INCLUDE_SUFFIXES:
            return "runtime"
    if any(pattern.search(content) for pattern in RUNTIME_PATTERNS):
        return "runtime"
    if source is not None and source.exists():
        return "editable"
    return "generated"


class PromptSectionWalker:
    """Walks a bundle's assembly chain and reports section metadata."""

    def __init__(
        self,
        store: Optional[PromptOverrideStore] = None,
        agent_manager: Optional[Any] = None,
    ):
        self.store = store or PromptOverrideStore()
        self.agent_manager = agent_manager

    def base_prompt_info(self, bundle: str) -> tuple[str, Path]:
        """The bundle's raw system_prompt.md and its directory."""
        base_dir = None
        if bundle != DEFAULT_BUNDLE and self.agent_manager is not None:
            agent = self.agent_manager.get_agent(bundle)
            if agent is not None:
                if agent.system_prompt:
                    return agent.system_prompt, Path(agent.directory)
                # the manager's disk cache hands agents out without their prompt text
                base_dir = Path(agent.directory)
        # Bundled/local/global default prompt via AgentManager discovery,
        # falling back to the bundled default bundle directory.
        if base_dir is None:
            base_dir = self._default_prompt_dir(bundle)
        prompt_path = base_dir / "system_prompt.md"
        try:
            return prompt_path.read_text(encoding="utf-8"), base_dir
        except OSError as exc:
            raise SectionInfoError(f"cannot read prompt for bundle {bundle!r}: {exc}")

    def _default_prompt_dir(self, bundle: str) -> Path:
        """Directory of the system_prompt.md for bundle (default: discovery)."""
        if bundle == DEFAULT_BUNDLE:
            candidates = []
            try:
                from kollabor_config.config_utils import (
                    get_global_agents_dir,
                    get_local_agents_dir,
                )

                local = get_local_agents_dir()
                if local:
                    candidates.append(local / DEFAULT_BUNDLE)
                candidates.append(get_global_agents_dir() / DEFAULT_BUNDLE)
            except Exception:
                pass
            try:
                from kollabor_config.config_utils import get_bundled_agents_dir

                bundled = get_bundled_agents_dir()
                if bundled:
                    candidates.append(bundled / DEFAULT_BUNDLE)
            except Exception:
                pass
            for candidate in candidates:
                if (candidate / "system_prompt.md").exists():
                    return candidate
        elif self.agent_manager is None:
            try:
                from kollabor_config.config_utils import get_bundled_agents_dir

                bundled = get_bundled_agents_dir()
                if bundled and (bundled / bundle / "system_prompt.md").exists():
                    return bundled / bundle
            except Exception:
                pass
        raise SectionInfoError(f"unknown prompt bundle: {bundle!r}")

    def walk(
        self,
        bundle: str,
        max_depth: int = 8,
        include_base_wrapper: bool = True,
    ) -> List[SectionInfo]:
        """Sections of bundle's prompt in assembly order.

        Follows relative includes from their containing directory (matching
        PromptRenderer._include_file), de-duplicates by resolved path, and
        orders by position of the include in the assembled document.
        """
        base_prompt, base_dir = self.base_prompt_info(bundle)
        sections: List[SectionInfo] = []
        seen_paths: set = set()
        order = 0

        def add_section(content: str, source: Optional[Path]) -> None:
            nonlocal order
            key = source.stem if source is not None else f"inline-{order}"
            kind = _classify(content, source)
            stable = True
            stage = None
            if key in RUNTIME_INCLUDE_SUFFIXES_STEMS:
                stable = False
                stage = "session_context"
            elif kind == "runtime":
                stable = False
                stage = "hub_trenders"
            elif source is not None and source.parent.name == "system":
                stage = "system_docs"
            sections.append(
                SectionInfo(
                    key=key,
                    order=order,
                    kind=kind,
                    source_path=str(source) if source is not None else None,
                    overridden=self.store.has_override(bundle, key),
                    tokens=count_tokens(content),
                    chars=len(content),
                    title=_section_title(content),
                    tags=[],
                    stable=stable,
                    stage=stage,
                )
            )
            order += 1

        def walk_text(text: str, current_dir: Path, depth: int) -> None:
            if depth > max_depth:
                logger.warning(
                    f"Prompt section walk hit max depth ({max_depth}) for {bundle}"
                )
                return
            pos = 0
            for match in INCLUDE_RE.finditer(text):
                # Substantive inline prompt body between includes becomes an
                # anonymous section (top level only — a nested file's own
                # body is already reported as that file's section); bare
                # separators/newlines never become sections.
                inline = text[pos : match.start()].strip()
                if depth == 0 and len(inline) >= 20:
                    add_section(inline, None)
                rel_path = match.group(1).strip()
                path = Path(rel_path)
                resolved = path if path.is_absolute() else (current_dir / path)
                try:
                    resolved = resolved.resolve()
                except OSError:
                    resolved = current_dir / path
                if resolved in seen_paths:
                    pos = match.end()
                    continue
                seen_paths.add(resolved)
                try:
                    content = resolved.read_text(encoding="utf-8")
                except OSError as exc:
                    logger.warning(f"Section walk could not read {resolved}: {exc}")
                    content = ""
                add_section(content, resolved)
                walk_text(content, resolved.parent, depth + 1)
                pos = match.end()
            tail = text[pos:].strip()
            if depth == 0 and len(tail) >= 20:
                add_section(tail, None)

        if include_base_wrapper:
            walk_text(base_prompt, base_dir, 0)
        else:
            add_section(base_prompt, base_dir / "system_prompt.md")
        return sections

    def section_content(self, bundle: str, key: str) -> Dict[str, Any]:
        """Content of one section: override if present, else the source file."""
        override = self.store.get_override(bundle, key)
        if override is not None:
            return {"content": override, "source": "override"}
        for section in self.walk(bundle):
            if section.key == key and section.source_path is not None:
                try:
                    content = Path(section.source_path).read_text(encoding="utf-8")
                except OSError as exc:
                    raise SectionInfoError(f"cannot read section {key!r}: {exc}")
                return {"content": content, "source": "bundled"}
        raise SectionInfoError(f"unknown section key: {key!r}")

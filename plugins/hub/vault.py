"""Agent Memory Vaults - persistent identity across sessions.

Three-tier memory architecture:
  stream.jsonl      raw append-only log of all messages (ground truth)
  working_memory    rolling context for system prompt injection
  crystallized      distilled long-term knowledge (from dreaming)

When an agent is reborn with the same identity, they get their
vault hydrated into their system prompt. They remember everything.

Vault scoping (project-aware):
  stream.jsonl and working_memory are scoped per-project via get_hub_dir()
  so agents working in different repositories don't bleed context.
  Crystallized knowledge and meta are shared within the current project
  by default. Set KOLLAB_HUB_GLOBAL_VAULTS=1 to use the legacy
  cross-project ~/.kollab/hub/vaults/ location.

  Layout (project-scoped hub, e.g. ~/.kollab/projects/<enc>/hub/):
    vaults/{identity}/
      stream.jsonl             project-scoped event log
      working_memory.md        project-scoped rolling context

  Layout (project-scoped shared vault):
    vaults/_shared/{identity}/
      crystallized.md          global long-term wisdom
      meta.json                global metadata

  Migration handled automatically:
    - nested projects/<fp>/stream.jsonl -> flat stream.jsonl
    - project-scoped crystallized.md -> global crystallized.md
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_vaults_dir() -> Path:
    """Get the project-scoped vaults directory.

    Routes through get_hub_dir so project-scoped mode gets its own
    vault tree at projects/<encoded>/hub/vaults/ automatically.
    Used for stream.jsonl and working_memory (project-specific data).
    """
    from .presence import get_hub_dir

    d = get_hub_dir() / "vaults"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_global_vaults_dir() -> Path:
    """Get the shared vaults directory.

    Project-scoped hub mode keeps shared crystals inside the project hub
    tree by default so startup does not create ~/.kollab/hub. Set
    KOLLAB_HUB_GLOBAL_VAULTS=1 to use the legacy cross-project location.
    """
    from .project_scope import is_project_scoped

    global_vaults = os.environ.get("KOLLAB_HUB_GLOBAL_VAULTS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if is_project_scoped() and not global_vaults:
        d = get_vaults_dir() / "_shared"
    else:
        from kollabor_config.config_utils import get_config_directory

        d = get_config_directory() / "hub" / "vaults"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_active_stream(identity_dir: Path) -> Path:
    """Find the active stream.jsonl for a vault identity directory.

    After flatten, stream.jsonl lives directly under vaults/<identity>/.
    Falls back to old nested path for pre-migration vaults.

    Used by feed.py and other consumers that iterate vault directories.

    Args:
        identity_dir: Path to vaults/{identity}/ directory

    Returns:
        Path to stream.jsonl (may not exist if vault is empty)
    """
    flat_stream = identity_dir / "stream.jsonl"
    if flat_stream.exists():
        return flat_stream

    # Pre-flatten fallback: check nested project path
    fingerprint = _get_project_fingerprint()
    nested_stream = identity_dir / "projects" / fingerprint / "stream.jsonl"
    if nested_stream.exists():
        return nested_stream

    # Default to flat path (will be created there on first write)
    return flat_stream


def _get_project_fingerprint() -> str:
    """Get the current project fingerprint for vault scoping.

    Uses the same encoding scheme as kollabor_config.config_utils:
    pwd path with / replaced by _, leading underscores stripped.

    Returns:
        Encoded project path string, e.g. 'Users_example_dev_kollab'
    """
    try:
        from kollabor_config.config_utils import encode_project_path

        return encode_project_path(Path.cwd())
    except ImportError:
        # Fallback if config_utils not available (shouldn't happen in normal use)
        path_str = str(Path.cwd().resolve())
        encoded = path_str.replace("/", "_").replace("\\", "_")
        while encoded.startswith("_"):
            encoded = encoded[1:]
        return encoded


class AgentVault:
    """Persistent memory vault for an agent identity.

    Each identity gets a directory with three memory tiers.
    The vault persists across sessions - when an agent is reborn
    with the same identity, it gets its full history back.

    Stream and working memory are scoped per-project so agents
    don't bleed context between repositories. Crystallized knowledge
    and metadata remain shared within the project by default.
    """

    def __init__(self, identity: str, project_fingerprint: Optional[str] = None):
        self.identity = identity

        # Project-scoped vault dir (stream + working memory)
        self._vault_dir = get_vaults_dir() / identity
        self._vault_dir.mkdir(parents=True, exist_ok=True)

        # Shared vault dir (crystallized + meta)
        self._global_vault_dir = get_global_vaults_dir() / identity
        self._global_vault_dir.mkdir(parents=True, exist_ok=True)

        # Crystallized knowledge and meta are shared
        self._crystal_path = self._global_vault_dir / "crystallized.md"
        self._meta_path = self._global_vault_dir / "meta.json"

        # Stream and working memory live flat under project-scoped vault
        self._stream_path = self._vault_dir / "stream.jsonl"
        self._working_path = self._vault_dir / "working_memory.md"

        # Old nested paths (migration sources)
        fingerprint = project_fingerprint or _get_project_fingerprint()
        self._nested_stream = self._vault_dir / "projects" / fingerprint / "stream.jsonl"
        self._nested_working = self._vault_dir / "projects" / fingerprint / "working_memory.md"

        # Legacy global paths (pre-scoping, pre-flatten) — these lived in the
        # global vault dir before project-specific vault dirs existed.
        self._legacy_stream_path = self._global_vault_dir / "stream.jsonl"
        self._legacy_working_path = self._global_vault_dir / "working_memory.md"

        # Old project-scoped crystal/meta (migration sources)
        self._old_crystal_path = self._vault_dir / "crystallized.md"
        self._old_meta_path = self._vault_dir / "meta.json"

        # Run migrations
        self._migrate_flatten_if_needed()
        self._migrate_crystal_to_global_if_needed()

        self._write_lock = threading.Lock()

    @property
    def global_vault_dir(self) -> Path:
        """Global vault directory (for CrystalStore and cross-project data)."""
        return self._global_vault_dir


    def _migrate_flatten_if_needed(self) -> None:
        """Migrate from nested projects/<fp>/ to flat layout.

        If stream.jsonl exists at vaults/<id>/projects/<fp>/stream.jsonl
        but not at vaults/<id>/stream.jsonl, move it up and clean up
        the empty nested directory.
        """
        import shutil

        # Nested -> flat (project-scoped)
        if not self._stream_path.exists() and self._nested_stream.exists():
            try:
                shutil.move(str(self._nested_stream), str(self._stream_path))
                logger.info(f"Flattened stream for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to flatten stream: {e}")

        if not self._working_path.exists() and self._nested_working.exists():
            try:
                shutil.move(str(self._nested_working), str(self._working_path))
                logger.info(f"Flattened working memory for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to flatten working memory: {e}")

        # Legacy pre-scoping fallback (old global layout)
        if not self._stream_path.exists() and self._legacy_stream_path.exists():
            try:
                shutil.copy2(self._legacy_stream_path, self._stream_path)
                logger.debug(f"Seeded stream from legacy for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to seed from legacy stream: {e}")

        if not self._working_path.exists() and self._legacy_working_path.exists():
            try:
                shutil.copy2(self._legacy_working_path, self._working_path)
                logger.debug(f"Seeded working memory from legacy for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to seed from legacy working memory: {e}")

        # Clean up empty nested dirs
        try:
            nested_dir = self._nested_stream.parent
            if nested_dir.exists() and not any(nested_dir.iterdir()):
                nested_dir.rmdir()
                projects_dir = nested_dir.parent
                if projects_dir.exists() and not any(projects_dir.iterdir()):
                    projects_dir.rmdir()
        except Exception:
            pass

    def _migrate_crystal_to_global_if_needed(self) -> None:
        """Migrate crystallized.md and meta.json from project-scoped to global.

        If they exist at the project-scoped vault dir but not at the
        global dir, move them. Global always wins if both exist.
        """
        import shutil

        if not self._crystal_path.exists() and self._old_crystal_path.exists():
            try:
                shutil.move(str(self._old_crystal_path), str(self._crystal_path))
                logger.info(f"Migrated crystallized.md to global for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to migrate crystallized.md: {e}")

        if not self._meta_path.exists() and self._old_meta_path.exists():
            try:
                shutil.move(str(self._old_meta_path), str(self._meta_path))
                logger.info(f"Migrated meta.json to global for {self.identity}")
            except Exception as e:
                logger.warning(f"Failed to migrate meta.json: {e}")

    # === Stream Layer (raw ground truth) ===

    def append_stream(
        self,
        entry_type: str,
        content: str,
        from_agent: str = "",
        to_agent: str = "",
        metadata: Optional[Dict] = None,
    ) -> None:
        """Append an entry to the raw stream log."""
        entry = {
            "ts": time.time(),
            "type": entry_type,
            "content": content,
            "from": from_agent,
            "to": to_agent,
            "identity": self.identity,
        }
        if metadata:
            entry["metadata"] = metadata

        try:
            with self._write_lock:
                with open(self._stream_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Stream append error: {e}")

    def get_recent_stream(self, limit: int = 50) -> List[Dict]:
        """Get the most recent stream entries."""
        entries = []
        try:
            if not self._stream_path.exists():
                return []
            with open(self._stream_path) as f:
                for line in f:
                    try:
                        entries.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return entries[-limit:]

    def get_stream_since(self, since_ts: float) -> List[Dict]:
        """Get stream entries since a timestamp."""
        entries = []
        try:
            if not self._stream_path.exists():
                return []
            with open(self._stream_path) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("ts", 0) > since_ts:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return entries

    # === Working Memory Layer (rolling context) ===

    def save_working_memory(self, content: str) -> None:
        """Save the current working memory."""
        try:
            with self._write_lock:
                with open(self._working_path, "w") as f:
                    f.write(content)
        except Exception as e:
            logger.warning(f"Working memory save error: {e}")

    def get_working_memory(self) -> str:
        """Get the current working memory."""
        try:
            if self._working_path.exists():
                return self._working_path.read_text()
        except Exception:
            pass
        return ""

    def update_working_memory(
        self,
        recent_stream: List[Dict],
        current_task: str = "",
        peers: Optional[List[str]] = None,
    ) -> str:
        """Rebuild working memory from recent stream entries.

        Creates a concise summary of recent activity that fits
        in a system prompt injection (~2-4k tokens).
        """
        lines = []
        lines.append(f"# working memory for {self.identity}")
        lines.append(f"last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        if current_task:
            lines.append(f"current task: {current_task}")
            lines.append("")

        if peers:
            lines.append(f"known peers: {', '.join(peers)}")
            lines.append("")

        # Summarize recent messages
        sent = [e for e in recent_stream if e.get("type") == "sent"]
        received = [e for e in recent_stream if e.get("type") == "received"]
        user_msgs = [e for e in recent_stream if e.get("type") == "user_input"]

        if user_msgs:
            lines.append(f"recent user interactions: {len(user_msgs)}")
            # Last 3 user messages as context
            for msg in user_msgs[-3:]:
                content = msg.get("content", "")[:200]
                lines.append(f"  - {content}")
            lines.append("")

        if sent:
            lines.append(f"messages sent to other agents: {len(sent)}")
            for msg in sent[-5:]:
                to = msg.get("to", "?")
                content = msg.get("content", "")[:100]
                lines.append(f"  -> {to}: {content}")
            lines.append("")

        if received:
            lines.append(f"messages received from agents: {len(received)}")
            for msg in received[-5:]:
                frm = msg.get("from", "?")
                content = msg.get("content", "")[:100]
                lines.append(f"  <- {frm}: {content}")
            lines.append("")

        content = "\n".join(lines)
        self.save_working_memory(content)
        return content

    # === Crystallized Layer (long-term wisdom) ===

    def get_crystallized(self) -> str:
        """Get crystallized long-term knowledge."""
        try:
            if self._crystal_path.exists():
                return self._crystal_path.read_text()
        except Exception:
            pass
        return ""

    def save_crystallized(self, content: str) -> None:
        """Save crystallized knowledge."""
        try:
            with self._write_lock:
                with open(self._crystal_path, "w") as f:
                    f.write(content)
        except Exception as e:
            logger.error(f"Crystal save error: {e}")

    def append_crystallized(self, insight: str) -> None:
        """Append a new insight to crystallized knowledge."""
        try:
            with self._write_lock:
                with open(self._crystal_path, "a") as f:
                    f.write(f"\n- [{time.strftime('%Y-%m-%d')}] {insight}")
        except Exception as e:
            logger.warning(f"Crystal append error: {e}")

    # === Meta (vault metadata) ===

    def get_meta(self) -> Dict[str, Any]:
        """Get vault metadata."""
        try:
            if self._meta_path.exists():
                with open(self._meta_path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def save_meta(self, **kwargs) -> None:
        """Update vault metadata."""
        meta = self.get_meta()
        meta.update(kwargs)
        try:
            with self._write_lock:
                with open(self._meta_path, "w") as f:
                    json.dump(meta, f, indent=2)
        except Exception as e:
            logger.warning(f"Meta save error: {e}")

    def touch(self) -> None:
        """Mark vault as active (update last_active timestamp)."""
        self.save_meta(
            last_active=time.time(),
            last_active_human=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    # === Rebirth Context ===

    def get_rebirth_context(
        self,
        max_tokens: int = 4000,
        crystal_store: Optional[Any] = None,
        global_crystal_store: Optional[Any] = None,
    ) -> str:
        """Build the context injection for when an agent is reborn.

        This is what gets injected into the system prompt when
        an agent with this identity starts up again.

        Args:
            max_tokens: Budget for crystallized sections (split evenly
                between project and global tiers if both provided).
            crystal_store: Project-scoped CrystalStore (work done in
                this repo). Defaults to raw fallback if None.
            global_crystal_store: Global CrystalStore (cross-project
                personality, general skills). Shown after project tier.
        """
        meta = self.get_meta()
        last_active = meta.get("last_active_human", "never")
        session_count = meta.get("session_count", 0)

        lines: List[str] = []
        lines.append(f"--- vault: {self.identity} ---")
        lines.append(f"sessions: {session_count + 1} (previous: {session_count})")
        lines.append(f"last active: {last_active}")

        # Recent stream entries (verbatim -- what actually happened)
        recent = self.get_recent_stream(15)
        if recent:
            lines.append("")
            lines.append("last session activity (verbatim):")
            for entry in recent:
                etype = entry.get("type", "?")
                content = str(entry.get("content", ""))[:300]
                from_agent = entry.get("from", "")
                to_agent = entry.get("to", "")
                routing = ""
                if from_agent and to_agent:
                    routing = f" ({from_agent} -> {to_agent})"
                elif from_agent:
                    routing = f" (from {from_agent})"
                lines.append(f"  [{etype}]{routing} {content}")

        # Working memory (rolling summary)
        working = self.get_working_memory()
        if working:
            if len(working) > max_tokens * 2:
                working = working[: max_tokens * 2] + "\n...(truncated)"
            lines.append("")
            lines.append("working memory summary:")
            lines.append(working)

        # Dual-tier crystal injection.
        # Split the budget evenly when both tiers are present so neither
        # crowds out the other. Project tier first (most relevant to
        # current work), global tier second (cross-project identity).
        has_project = crystal_store is not None
        has_global = global_crystal_store is not None
        tier_budget = max_tokens // 2 if (has_project and has_global) else max_tokens

        if has_project:
            self._append_crystal_section(
                lines, crystal_store, tier_budget, label="project crystals"
            )
        else:
            # Legacy fallback: raw crystallized.md (written by old vault_write)
            self._append_raw_crystal(lines, tier_budget)

        if has_global:
            self._append_crystal_section(
                lines, global_crystal_store, tier_budget, label="global crystals"
            )

        lines.append("")
        lines.append("use this context silently. do not mention vault or rebirth.")
        lines.append("pick up where you left off naturally.")
        lines.append("--- end vault ---")

        context = "\n".join(lines)

        # Update session count
        self.save_meta(session_count=session_count + 1)

        return context

    def _append_crystal_section(
        self, lines: List[str], store: Any, budget: int, label: str
    ) -> None:
        """Append a labeled crystal store section to the rebirth lines."""
        try:
            injection = store.get_injection_context(budget=budget)
            if injection:
                lines.append("")
                # Replace the generic header with the tier-labeled one
                injection_lines = injection.split("\n")
                if injection_lines and injection_lines[0].startswith(
                    "crystallized memories"
                ):
                    count_part = injection_lines[0].split("(", 1)
                    suffix = f"({count_part[1]}" if len(count_part) > 1 else ""
                    injection_lines[0] = f"{label} {suffix}".rstrip()
                lines.extend(injection_lines)
                lines.append("use /hub vault read <id> for full entry details.")
        except Exception:
            logger.debug("crystal section injection failed for %s", label)

    def _append_raw_crystal(self, lines: List[str], max_tokens: int) -> None:
        """Append raw crystallized text (legacy fallback)."""
        crystal = self.get_crystallized()
        if crystal:
            if len(crystal) > max_tokens:
                crystal = crystal[-max_tokens:]
            lines.append("")
            lines.append("crystallized knowledge:")
            lines.append(crystal)

    # === Vault Info ===

    def exists(self) -> bool:
        """Check if this vault has any data.

        Checks flat, nested, and legacy paths.
        """
        return (
            self._stream_path.exists()
            or self._working_path.exists()
            or self._nested_stream.exists()
            or self._nested_working.exists()
            or self._crystal_path.exists()
        )

    def get_stream_count(self) -> int:
        """Get total number of stream entries."""
        try:
            if self._stream_path.exists():
                with open(self._stream_path) as f:
                    return sum(1 for _ in f)
        except Exception:
            pass
        return 0

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of this vault's contents."""
        meta = self.get_meta()
        return {
            "identity": self.identity,
            "exists": self.exists(),
            "stream_entries": self.get_stream_count(),
            "has_working_memory": (
                self._working_path.exists()
                or self._nested_working.exists()
            ),
            "has_crystallized": self._crystal_path.exists(),
            "last_active": meta.get("last_active_human", "never"),
            "session_count": meta.get("session_count", 0),
        }


def list_vaults() -> List[str]:
    """List all identity vaults that exist.

    Checks for flat stream.jsonl, nested project streams, or legacy
    top-level stream.jsonl.
    """
    vaults_dir = get_vaults_dir()
    result = []
    for d in vaults_dir.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        # Flat stream (new layout)
        if (d / "stream.jsonl").exists():
            result.append(d.name)
        # Nested project-scoped stream (pre-flatten)
        elif (d / "projects").exists() and any(
            p.is_dir() and (p / "stream.jsonl").exists()
            for p in (d / "projects").iterdir()
            if p.is_dir()
        ):
            result.append(d.name)
    return result


def archive_vaults(max_age_days: int = 7) -> Dict[str, List[str]]:
    """Archive vaults for identities not seen in max_age_days.

    Moves stale vaults to vaults/_archived/ to keep the main
    directory clean.  Preserves all data — archival is reversible.

    Returns dict with 'archived' and 'deleted' lists of identities.
    """
    vaults_dir = get_vaults_dir()
    archive_dir = vaults_dir / "_archived"
    archive_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    cutoff = now - (max_age_days * 86400)

    archived: List[str] = []
    deleted: List[str] = []

    for d in vaults_dir.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("_"):
            continue

        vault = AgentVault(d.name)

        # No stream anywhere = test garbage with no real data
        has_stream = vault._stream_path.exists()
        if not has_stream:
            # Check nested (pre-flatten) too
            has_stream = vault._nested_stream.exists()
            deleted.append(d.name)
            import shutil

            shutil.rmtree(d, ignore_errors=True)
            logger.info(f"Deleted garbage vault: {d.name}")
            continue

        meta = vault.get_meta()
        last_active = meta.get("last_active", 0)

        if last_active > 0 and last_active < cutoff:
            dest = archive_dir / d.name
            if dest.exists():
                import shutil

                shutil.rmtree(dest, ignore_errors=True)
            d.rename(dest)
            archived.append(d.name)
            logger.info(f"Archived stale vault: {d.name} -> _archived/{d.name}")

    result = {"archived": archived, "deleted": deleted}
    if archived or deleted:
        logger.info(f"Vault cleanup: archived {len(archived)}, deleted {len(deleted)}")
    return result


def get_vault_summaries() -> List[Dict]:
    """Get summaries of all vaults."""
    return [AgentVault(name).get_summary() for name in list_vaults()]

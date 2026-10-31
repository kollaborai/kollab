"""Structured crystallized memory store for hub agents.

Replaces the raw append-only crystallized.md with structured entries
that have IDs, dates, keywords, and summaries. Enables keyword-based
retrieval (nudge system) and deduplication during dreaming cycles.

Entry format in crystallized.md:
    ---
    id: crys-001
    date: 2026-04-09
    keywords: prompt_renderer, trender, duplicate, constant
    summary: Duplicate TRENDER_HUB_IDENTITY_PATTERN in prompt_renderer
    ---
    Full insight body text here spanning one or more lines.

Entries are separated by blank lines between the body and the next ---.
"""

import asyncio
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

from .text_utils import (
    extract_keywords,
    keyword_overlap,
    score_relevance,
    summary_tokens,
    token_overlap,
    tokenize,
)


def normalize_crystal_id(raw: str) -> str:
    """Normalize a crystal entry ID.

    - bare digits: zero-pad to 3 digits, prepend "crys-"
      "3" -> "crys-003", "110" -> "crys-110"
    - already has "crys-" prefix: pass through
    - other formats: pass through (caller checks existence)
    """
    raw = raw.strip()
    if raw.isdigit():
        return f"crys-{int(raw):03d}"
    return raw

logger = logging.getLogger(__name__)

# Dedup threshold: entries with keyword overlap above this merge.
# 0.55 catches semantic duplicates (same concept, different wording).
# Higher values (0.70+) miss many real duplicates in natural language.
DEDUP_THRESHOLD = 0.45

# Summary-level dedup threshold: entries whose summary lines have
# word overlap above this merge. Uses a size-penalized overlap
# coefficient from text_utils.summary_similarity.
# 0.40 catches semantic duplicates while avoiding false merges on
# short summaries that share common words like "insight" or "about".
SUMMARY_DEDUP_THRESHOLD = 0.40

# The GIL switch interval while the dedup pass runs in its worker thread. At
# CPython's default 5 ms, each syscall the agent's event loop makes waits for
# the pass to give the GIL back: a handler that takes 1 ms alone took 1.4 s,
# and the koordinator's command catalog RPC timed out at 10 s (#546).
_PASS_SWITCH_INTERVAL = 0.0002

# Minimum relevance score for nudge results
NUDGE_MIN_SCORE = 1.0

# Regex for parsing structured entries (header + body)
def _signature(entry: "CrystalEntry") -> tuple:
    return entry.id, entry.date, entry.summary, entry.body, tuple(entry.keywords)


def _jaccard(a: frozenset, b: frozenset) -> float:
    """text_utils.keyword_overlap on prebuilt keyword sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_ENTRY_RE = re.compile(
    r"---\n"
    r"id:\s*(\S+)\n"
    r"date:\s*(\S+)\n"
    r"keywords:\s*(.+?)\n"
    r"summary:\s*(.+?)\n"
    r"---\n"
    r"(.*?)(?=\n---\nid:|\Z)",
    re.DOTALL,
)


@dataclass
class CrystalEntry:
    """A single structured crystallized memory entry."""

    id: str
    date: str
    keywords: List[str]
    summary: str
    body: str

    def to_block(self) -> str:
        """Serialize to the on-disk block format."""
        kw_str = ", ".join(self.keywords)
        return (
            f"---\n"
            f"id: {self.id}\n"
            f"date: {self.date}\n"
            f"keywords: {kw_str}\n"
            f"summary: {self.summary}\n"
            f"---\n"
            f"{self.body}\n"
        )

    def summary_line(self) -> str:
        """One-line representation for injection."""
        return f"[{self.id}] {self.summary}"


class CrystalStore:
    """Structured store for crystallized agent memories.

    Thread-safe. Reads/writes to the same crystallized.md path
    used by the existing vault, but in structured format.
    """

    def __init__(self, vault_dir: Path):
        self._path = vault_dir / "crystallized.md"
        self._lock = threading.Lock()
        self._entries: List[CrystalEntry] = []
        self._next_id: int = 1
        self._loaded = False
        # Stemmed summary words, by summary text: stemming every pair again
        # made a pass over thousands of entries take minutes (#546).
        self._summary_words: dict = {}

    def _ensure_loaded(self) -> None:
        """Lazy-load entries from disk on first access."""
        if not self._loaded:
            self._entries = self._parse_file()
            if self._entries:
                # Set next ID from highest existing
                max_num = 0
                for e in self._entries:
                    try:
                        num = int(e.id.split("-")[1])
                        if num > max_num:
                            max_num = num
                    except (IndexError, ValueError):
                        pass
                self._next_id = max_num + 1
            self._loaded = True

    def _parse_file(self) -> List[CrystalEntry]:
        """Parse structured crystallized.md into entries."""
        if not self._path.exists():
            return []
        try:
            content = self._path.read_text()
        except OSError as e:
            logger.warning("crystal_store read error: %s", e)
            return []

        if not content.strip():
            return []

        # Check if file is in structured format
        if not content.lstrip().startswith("---"):
            return []

        entries: List[CrystalEntry] = []
        for match in _ENTRY_RE.finditer(content):
            entry_id = match.group(1)
            date = match.group(2)
            keywords_str = match.group(3)
            summary = match.group(4)
            body = match.group(5).strip()

            keywords = [
                k.strip() for k in keywords_str.split(",") if k.strip()
            ]

            entries.append(
                CrystalEntry(
                    id=entry_id,
                    date=date,
                    keywords=keywords,
                    summary=summary,
                    body=body,
                )
            )

        return entries

    def _save_to_disk(self) -> None:
        """Write all entries back to crystallized.md."""
        try:
            content = "\n".join(e.to_block() for e in self._entries)
            with self._lock:
                self._path.write_text(content)
        except OSError as e:
            logger.warning("crystal_store write error: %s", e)

    def _generate_id(self) -> str:
        """Generate next sequential entry ID."""
        entry_id = f"crys-{self._next_id:03d}"
        self._next_id += 1
        return entry_id

    def _extract_summary(self, text: str) -> str:
        """Extract a one-line summary from insight text.

        Strips leading list markers ("1. ", "- ", "* ") before extraction,
        then uses a bold header if present, otherwise the first sentence.
        Without the strip step, dreaming output like "1. **Users often..."
        would produce summary="1" because the first ". " splits at the
        list-marker dot.
        """
        cleaned = re.sub(r"^\s*(?:\d+\.|[-*])\s+", "", text).lstrip()

        # Check for **bold header** pattern at the start of the cleaned text
        bold_match = re.match(r"\*\*(.+?)\*\*", cleaned)
        if bold_match:
            summary = bold_match.group(1).strip()
            if len(summary) > 120:
                summary = summary[:117] + "..."
            return summary

        # Fall back to first sentence
        sentences = re.split(r"[.!?]\s", cleaned, maxsplit=1)
        summary = sentences[0].strip()
        if len(summary) > 120:
            summary = summary[:117] + "..."
        return summary

    def load(self) -> List[CrystalEntry]:
        """Load and return all entries."""
        self._ensure_loaded()
        return list(self._entries)

    def count(self) -> int:
        """Return number of entries."""
        self._ensure_loaded()
        return len(self._entries)

    def add_entry(
        self,
        text: str,
        manual_keywords: Optional[List[str]] = None,
        date: Optional[str] = None,
    ) -> CrystalEntry:
        """Add a new crystallized entry with auto keyword extraction.

        If the new entry has >70% keyword overlap with an existing
        entry, merges instead of appending (keeps newer date, unions
        keywords, keeps longer body).

        Returns the new or merged entry.
        """
        self._ensure_loaded()

        auto_keywords = extract_keywords(text)
        all_keywords = list(auto_keywords)
        if manual_keywords:
            for kw in manual_keywords:
                if kw.lower() not in {k.lower() for k in all_keywords}:
                    all_keywords.append(kw.lower())

        entry_date = date or time.strftime("%Y-%m-%d")
        summary = self._extract_summary(text)

        # Check for duplicates
        merge_target = self._find_merge_target(all_keywords, summary)
        if merge_target is not None:
            existing = self._entries[merge_target]
            # Merge: keep newer date, union keywords, keep longer body
            existing.date = entry_date
            existing_kw_set = {k.lower() for k in existing.keywords}
            for kw in all_keywords:
                if kw.lower() not in existing_kw_set:
                    existing.keywords.append(kw)
                    existing_kw_set.add(kw.lower())
            if len(text) > len(existing.body):
                existing.body = text
                existing.summary = summary
            self._save_to_disk()
            logger.debug(
                "crystal_store: merged into %s (keyword overlap > %.0f%%)",
                existing.id,
                DEDUP_THRESHOLD * 100,
            )
            return existing

        # New entry
        entry = CrystalEntry(
            id=self._generate_id(),
            date=entry_date,
            keywords=all_keywords,
            summary=summary,
            body=text,
        )
        self._entries.append(entry)
        self._save_to_disk()
        logger.debug("crystal_store: added %s (%d keywords)", entry.id, len(entry.keywords))
        return entry

    def _summary_similarity(self, summary_a: str, summary_b: str) -> float:
        """Compute word-level overlap ratio between two summaries.

        The score of text_utils.summary_similarity (stemmed words, stopwords
        removed), from cached words. This catches semantic duplicates that
        keyword Jaccard misses because summaries distill the same concept.
        """
        return token_overlap(self._words(summary_a), self._words(summary_b))

    def _words(self, summary: str) -> frozenset:
        words = self._summary_words.get(summary)
        if words is None:
            words = self._summary_words[summary] = summary_tokens(summary)
        return words

    def _find_merge_target(
        self, new_keywords: List[str], new_summary: str = ""
    ) -> Optional[int]:
        """Find an existing entry to merge with.

        Uses two-layer matching:
        1. Summary-level word overlap (catches semantic duplicates)
        2. Keyword Jaccard (catches content overlap)

        Either signal above its threshold triggers a merge.
        """
        best_idx = None
        best_score = 0.0
        for i, existing in enumerate(self._entries):
            # Layer 1: summary similarity (primary)
            if new_summary and existing.summary:
                sim = self._summary_similarity(new_summary, existing.summary)
                if sim > SUMMARY_DEDUP_THRESHOLD and sim > best_score:
                    best_score = sim
                    best_idx = i
                    continue

            # Layer 2: keyword overlap (secondary)
            overlap = keyword_overlap(new_keywords, existing.keywords)
            if overlap > DEDUP_THRESHOLD and overlap > best_score:
                best_score = overlap
                best_idx = i
        return best_idx

    def nudge(self, input_text: str, top_k: int = 5) -> List[CrystalEntry]:
        """Find crystal entries relevant to input text.

        Tokenizes input, extracts keywords, scores each entry,
        returns top-K above minimum threshold. This is the main
        retrieval method for the nudge system.
        """
        self._ensure_loaded()
        if not self._entries:
            return []

        query_keywords = extract_keywords(input_text)
        if not query_keywords:
            # Fall back to simple tokenization
            query_keywords = tokenize(input_text)
        if not query_keywords:
            return []

        scored: List[Tuple[float, CrystalEntry]] = []
        for entry in self._entries:
            score = score_relevance(query_keywords, entry.keywords)
            if score >= NUDGE_MIN_SCORE:
                scored.append((score, entry))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def find_by_keywords(
        self, keywords: List[str], top_k: int = 5
    ) -> List[CrystalEntry]:
        """Find entries matching given keywords."""
        self._ensure_loaded()
        if not self._entries or not keywords:
            return []

        scored: List[Tuple[float, CrystalEntry]] = []
        for entry in self._entries:
            score = score_relevance(keywords, entry.keywords)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def get_recent(self, n: int = 3) -> List[CrystalEntry]:
        """Get the N most recent entries by date."""
        self._ensure_loaded()
        if not self._entries:
            return []
        sorted_entries = sorted(self._entries, key=lambda e: e.date, reverse=True)
        return sorted_entries[:n]

    def get_by_id(self, entry_id: str) -> Optional[CrystalEntry]:
        """Look up an entry by its ID."""
        self._ensure_loaded()
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def get_all(self) -> List[CrystalEntry]:
        """Return all entries."""
        self._ensure_loaded()
        return list(self._entries)

    def reindex_keywords(self) -> int:
        """Re-extract keywords for all entries using current stemmer.

        Useful after stemmer improvements. Returns count of entries
        reindexed.
        """
        self._ensure_loaded()
        count = 0
        for entry in self._entries:
            new_keywords = extract_keywords(entry.body)
            entry.keywords = new_keywords
            count += 1
        if count > 0:
            self._save_to_disk()
            logger.info("crystal_store: reindexed %d entries", count)
        return count

    def deduplicate(self) -> int:
        """Merge entries with keyword or summary overlap above threshold.

        Uses two-layer matching: summary word overlap (primary) and
        keyword Jaccard (secondary). Either signal triggers a merge.

        Returns number of entries merged.
        """
        self._ensure_loaded()
        merged_count = self._merge_pass(self._entries)
        if merged_count > 0:
            self._save_to_disk()
            logger.info("crystal_store: deduplicated %d entries", merged_count)
        return merged_count

    async def deduplicate_off_loop(self) -> int:
        """deduplicate() in a worker thread, for the dreaming cycle.

        A pass compares every pair: 105 s for the koordinator's 2,608 entries
        when it ran on the agent's event loop, which then answered nothing and
        peers deleted its socket as dead (#546). The worker merges copies and
        records each merge; back on the loop the merges replay on the live
        entries. A merge touching an entry that changed meanwhile waits for the
        next pass, and so does every later merge into the same entry; a write
        during the pass never holds back the others.
        """
        self._ensure_loaded()
        snapshot = [_signature(e) for e in self._entries]
        before = {sig[0]: sig for sig in snapshot}
        copies = [replace(e, keywords=list(e.keywords)) for e in self._entries]
        merges: List[Tuple[str, str]] = []
        interval = sys.getswitchinterval()
        sys.setswitchinterval(min(interval, _PASS_SWITCH_INTERVAL))
        try:
            found = await asyncio.to_thread(self._merge_pass, copies, merges)
        finally:
            sys.setswitchinterval(interval)
        if not found:
            return 0
        waiting: set = set()
        if len(before) < len(snapshot):  # repeated ids: merges cannot be matched to entries
            if [_signature(e) for e in self._entries] != snapshot:
                return 0
            self._entries = copies
        else:
            live = {e.id: e for e in self._entries}
            done = []
            for keep_id, drop_id in merges:
                keep, drop = live.get(keep_id), live.get(drop_id)
                if (keep is None or drop is None or keep_id in waiting
                        or _signature(keep) != before[keep_id]
                        or _signature(drop) != before[drop_id]):
                    waiting.add(keep_id)
                    continue
                self._merge_into(keep, drop)
                before[keep_id] = _signature(keep)
                del live[drop_id]
                done.append(drop_id)
            self._entries = [e for e in self._entries if e.id in live]
            merges = done
        if waiting:
            logger.info("crystal_store: %d entries changed during dedup, merging them on the next pass",
                        len(waiting))
        if merges:
            self._save_to_disk()
            logger.info("crystal_store: deduplicated %d entries", len(merges))
        return len(merges)

    def _merge_pass(
        self, entries: List[CrystalEntry], merges: Optional[List[Tuple[str, str]]] = None
    ) -> int:
        """Merge duplicates in ``entries`` in place, each into the earliest match.

        The words and keyword sets are built once per entry, not once per pair.
        Each merge is appended to ``merges`` as ``(kept id, merged id)``.
        """
        # ponytail: every pair, about 11 s for 2,608 entries in the worker
        # thread; index entries by word if vaults grow to tens of thousands.
        words = [self._words(e.summary) if e.summary else frozenset() for e in entries]
        keyword_sets = [frozenset(e.keywords) for e in entries]
        merged_count = 0
        i = 0
        while i < len(entries):
            j = i + 1
            while j < len(entries):
                if token_overlap(words[i], words[j]) > SUMMARY_DEDUP_THRESHOLD or (
                    _jaccard(keyword_sets[i], keyword_sets[j]) > DEDUP_THRESHOLD
                ):
                    target, source = entries[i], entries.pop(j)
                    self._merge_into(target, source)
                    if merges is not None:
                        merges.append((target.id, source.id))
                    words.pop(j)
                    keyword_sets.pop(j)
                    words[i] = self._words(target.summary) if target.summary else frozenset()
                    keyword_sets[i] = frozenset(target.keywords)
                    merged_count += 1
                else:
                    j += 1
            i += 1
        return merged_count

    @staticmethod
    def _merge_into(target: CrystalEntry, source: CrystalEntry) -> None:
        # Keep newer date
        if source.date > target.date:
            target.date = source.date

        # Union keywords
        target_kw_set = {k.lower() for k in target.keywords}
        for kw in source.keywords:
            if kw.lower() not in target_kw_set:
                target.keywords.append(kw)
                target_kw_set.add(kw.lower())

        # Keep longer body
        if len(source.body) > len(target.body):
            target.body = source.body
            target.summary = source.summary

    def delete_entry(self, entry_id: str) -> Optional[CrystalEntry]:
        """Remove an entry by ID. Returns the removed entry or None."""
        self._ensure_loaded()
        entry_id = normalize_crystal_id(entry_id)
        for i, entry in enumerate(self._entries):
            if entry.id == entry_id:
                removed = self._entries.pop(i)
                self._save_to_disk()
                logger.debug("crystal_store: deleted %s", removed.id)
                return removed
        return None

    def update_entry(
        self,
        entry_id: str,
        body: str,
        summary: Optional[str] = None,
        keywords: Optional[List[str]] = None,
    ) -> Optional[CrystalEntry]:
        """Update an existing entry's fields.

        Args:
            entry_id: ID to update (normalized internally).
            body: New body text.
            summary: New summary, or None to keep existing.
            keywords: New keyword list, or None to re-extract from body.

        Returns:
            Updated entry, or None if entry_id not found.
        """
        self._ensure_loaded()
        entry_id = normalize_crystal_id(entry_id)
        entry = self.get_by_id(entry_id)
        if not entry:
            return None

        entry.body = body
        if summary is not None:
            entry.summary = summary
        if keywords is not None:
            entry.keywords = keywords
        else:
            # Re-extract keywords from new body
            entry.keywords = extract_keywords(body)

        self._save_to_disk()
        logger.debug("crystal_store: updated %s", entry.id)
        return entry

    def get_injection_context(self, budget: int = 4000) -> str:
        """Build injection context for system prompt.

        Always includes last 3 entries by date (recency bias).
        Fills remaining budget with keyword-rich entries.
        Shows summary lines only -- agents use crystal_read to see full
        entry.
        """
        self._ensure_loaded()
        if not self._entries:
            return ""

        lines: List[str] = []
        lines.append(f"crystallized memories ({len(self._entries)} entries):")
        used_ids = set()
        char_count = 0

        # Always include recent entries with full summaries
        recent = self.get_recent(3)
        if recent:
            lines.append("")
            lines.append("recent:")
            for entry in recent:
                line = f"  {entry.summary_line()}"
                char_count += len(line) + 1
                lines.append(line)
                used_ids.add(entry.id)

        # Fill remaining budget with other entries (by keyword density)
        remaining = [e for e in self._entries if e.id not in used_ids]
        # Sort by keyword count (proxy for information density)
        remaining.sort(key=lambda e: len(e.keywords), reverse=True)

        if remaining:
            lines.append("")
            lines.append("knowledge index:")
            for entry in remaining:
                line = f"  {entry.summary_line()}"
                if char_count + len(line) + 1 > budget:
                    more = len(remaining) - len(
                        [e for e in remaining if e.id in used_ids]
                    )
                    lines.append(
                        f"  ... and {more} more"
                        " (use crystal_read to see full entry)"
                    )
                    break
                char_count += len(line) + 1
                lines.append(line)
                used_ids.add(entry.id)

        return "\n".join(lines)

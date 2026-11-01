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
import bisect
import functools
import heapq
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

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

# Minimum room for tokenized summaries. Larger stores grow the cache with
# their live entries so sequential scans do not evict the whole working set.
SUMMARY_WORD_CACHE_MIN_SIZE = 8192

# Minimum relevance score for nudge results
NUDGE_MIN_SCORE = 1.0

def _jaccard(a: frozenset, b: frozenset) -> float:
    """text_utils.keyword_overlap on prebuilt keyword sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _locked(method):
    """Run a store write under its lock. A dedup pass checks for writes and
    commits under the same lock, so a write from another thread lands wholly
    before that commit or after it, never lost inside it (#546)."""

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return run


# Regex for parsing structured entries (header + body)
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
        self._lock = threading.RLock()  # every write and dedup's commit (_locked)
        self._entries: List[CrystalEntry] = []
        self._next_id: int = 1
        self._loaded = False
        # Counts writes, so a dedup pass that gave the loop back sees one (#546),
        # and the count at which a pass last found nothing to merge.
        self._revision = 0
        self._dedup_clean = -1
        # The ids of entries changed since the last dedup pass: only pairs with
        # one of them can match. None: unknown, so every entry (#546).
        self._changed: Optional[set] = None
        # Sliced dedup reads this cache outside the store lock while a writer
        # can resize it. The cache lock protects only its data and size limit.
        self._summary_words: OrderedDict[str, frozenset] = OrderedDict()
        self._summary_words_lock = threading.Lock()
        self._summary_words_limit = SUMMARY_WORD_CACHE_MIN_SIZE

    def _resize_summary_cache(self) -> None:
        """Resize in place, retaining the most recently used summaries."""
        size = max(SUMMARY_WORD_CACHE_MIN_SIZE, 1 << (max(1, 2 * len(self._entries)) - 1).bit_length())
        with self._summary_words_lock:
            self._summary_words_limit = size
            while len(self._summary_words) > size:
                self._summary_words.popitem(last=False)

    def _ensure_loaded(self) -> None:
        """Lazy-load entries from disk on first access, under the lock: a load
        that read the file before a write saved, then published, would erase
        that write (#546). Writers already hold the lock."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._entries = self._parse_file()
            self._resize_summary_cache()
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
        with self._lock:
            self._resize_summary_cache()
            self._revision += 1
            try:
                self._path.write_text("\n".join(e.to_block() for e in self._entries))
            except OSError as e:
                logger.warning("crystal_store write error: %s", e)

    def _mark(self, entry_id: str) -> None:
        """Note an entry a write changed, for the next dedup pass."""
        if self._changed is not None:
            self._changed.add(entry_id)

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

    @_locked
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
            self._mark(existing.id)
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
        self._mark(entry.id)
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
        with self._summary_words_lock:
            words = self._summary_words.get(summary)
            if words is not None:
                self._summary_words.move_to_end(summary)
                return words

        # Tokenization is pure and can be expensive. Do it outside the lock so
        # an overlapping sliced pass and writer can keep making progress.
        words = summary_tokens(summary)
        with self._summary_words_lock:
            cached = self._summary_words.get(summary)
            if cached is None:
                self._summary_words[summary] = words
                if len(self._summary_words) > self._summary_words_limit:
                    self._summary_words.popitem(last=False)
            else:
                words = cached
                self._summary_words.move_to_end(summary)
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

    @_locked
    def reindex_keywords(self) -> int:
        """Re-extract keywords for all entries using current stemmer.

        Useful after stemmer improvements. Returns count of entries
        reindexed.
        """
        self._ensure_loaded()
        count = 0
        self._changed = None  # every entry's keywords change
        for entry in self._entries:
            new_keywords = extract_keywords(entry.body)
            entry.keywords = new_keywords
            count += 1
        if count > 0:
            self._save_to_disk()
            logger.info("crystal_store: reindexed %d entries", count)
        return count

    @_locked
    def deduplicate(self) -> int:
        """Merge entries with keyword or summary overlap above threshold.

        Uses two-layer matching: summary word overlap (primary) and
        keyword Jaccard (secondary). Either signal triggers a merge.

        Returns number of entries merged.
        """
        self._ensure_loaded()
        targets: list = []
        merged_count = self._merge_pass(self._entries, targets)
        self._changed = {entry.id for entry in targets}
        if merged_count > 0:
            self._save_to_disk()
            logger.info("crystal_store: deduplicated %d entries", merged_count)
        return merged_count

    async def deduplicate_in_slices(self, slice_s: float = 0.005, attempts: int = 3) -> int:
        """deduplicate() for the dreaming cycle, on the event loop a few ms at a time.

        A pass compares every pair: 105 s for the koordinator's 2,608 entries
        run whole on the agent's event loop, which then answered nothing, and
        peers deleted its socket as dead (#546). This pass merges copies and
        gives the loop back every ``slice_s``. A write to the store meanwhile
        starts it over on the entries as they now are, so its merges are always
        those of one full ordered pass. After ``attempts`` interrupted passes it
        leaves the entries as they are until the next cycle. A store unchanged
        since a pass found nothing to merge skips it: it would find nothing again.
        """
        self._ensure_loaded()
        if self._dedup_clean == self._revision:
            return 0
        for _ in range(attempts):
            with self._lock:
                revision = self._revision
                copies = [replace(e, keywords=list(e.keywords)) for e in self._entries]
                ids = self._changed
                changed = None if ids is None else {n for n, e in enumerate(copies) if e.id in ids}
            merged, deadline, written, targets = 0, time.monotonic() + slice_s, False, []
            for merged in self._merge_steps(copies, changed, targets):
                if time.monotonic() < deadline:
                    continue
                await asyncio.sleep(0)
                if self._revision != revision:
                    written = True
                    break
                deadline = time.monotonic() + slice_s
            if written:
                continue
            with self._lock:  # a write from another thread waits, or already counted
                if self._revision != revision:
                    continue
                self._changed = {entry.id for entry in targets}
                if merged:
                    self._entries = copies
                    self._save_to_disk()
                    logger.info("crystal_store: deduplicated %d entries", merged)
                else:
                    self._dedup_clean = self._revision
            return merged
        logger.info("crystal_store: written to during %d dedup passes; the next cycle tries again", attempts)
        return 0

    def _merge_pass(self, entries: List[CrystalEntry], targets: Optional[list] = None) -> int:
        """Merge duplicates in ``entries`` in place, each into the earliest match."""
        merged = 0
        for merged in self._merge_steps(entries, targets=targets):
            pass
        return merged

    def _merge_steps(
        self, entries: List[CrystalEntry], changed: Optional[set] = None, targets: Optional[list] = None
    ) -> Iterator[int]:
        """_merge_pass, yielding its merge count every 64 entries it reads, every 512
        comparisons and once at the end. ``changed`` holds the positions of the
        entries changed since the last pass (None: all of them); ``targets`` gets
        each entry another merged into.

        Two entries that share no summary word and no keyword score 0 on both
        measures, and two unchanged since the last pass were compared there and
        did not match. So each entry is compared only with the later ones it
        shares a word or keyword with, and an unchanged one only with changed
        ones until it takes in a merge, in list order and with what it holds
        after each merge: the merges of comparing every pair, without the
        quadratic scan after every write (#546).
        """
        words, keyword_sets, postings, changed_postings = [], [], {}, {}
        for n, entry in enumerate(entries):
            words.append(self._words(entry.summary) if entry.summary else frozenset())
            keyword_sets.append(frozenset(entry.keywords))
            for token in words[n] | keyword_sets[n]:
                postings.setdefault(token, []).append(n)
                if changed is None or n in changed:
                    changed_postings.setdefault(token, []).append(n)
            if (n + 1) % 64 == 0:  # stemming a summary the cache has not seen is slow
                yield 0

        def after(index, tokens, position):
            for token in tokens:
                found = index.get(token, [])
                yield from found[bisect.bisect_right(found, position):]

        alive = [True] * len(entries)
        merged_count = compared = 0
        for i in range(len(entries)):
            if not alive[i]:
                continue
            fresh = changed is None or i in changed
            tokens = words[i] | keyword_sets[i]
            if not fresh:  # an unchanged entry can match only a changed one
                tokens = changed_postings.keys() & tokens
            seen = set(after(postings if fresh else changed_postings, tokens, i))
            queue = sorted(seen)  # a sorted list is a heap
            while queue:
                j = heapq.heappop(queue)
                if not alive[j]:
                    continue
                compared += 1
                if compared % 512 == 0:
                    yield merged_count
                if token_overlap(words[i], words[j]) > SUMMARY_DEDUP_THRESHOLD or (
                    _jaccard(keyword_sets[i], keyword_sets[j]) > DEDUP_THRESHOLD
                ):
                    held = words[i] | keyword_sets[i] if fresh else frozenset()
                    self._merge_into(entries[i], entries[j])
                    alive[j] = False
                    words[i] = self._words(entries[i].summary) if entries[i].summary else frozenset()
                    keyword_sets[i] = frozenset(entries[i].keywords)
                    merged_count += 1
                    if targets is not None:
                        targets.append(entries[i])
                    # Changed now: a later entry sharing what it gained can match it
                    # (all it holds, if it was unchanged); those before j were passed.
                    fresh = True
                    for k in after(postings, (words[i] | keyword_sets[i]) - held, j):
                        if k not in seen:
                            seen.add(k)
                            heapq.heappush(queue, k)
        entries[:] = [entry for entry, kept in zip(entries, alive) if kept]
        yield merged_count

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

    @_locked
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

    @_locked
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
        self._mark(entry.id)

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

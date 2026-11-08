"""Conservative sweep for orphaned session directories under ~/.kollab/projects.

Every kollab process keeps per-workspace state under a slug-encoded directory
(see ``encode_project_path``). Workspaces are deleted all the time (review
sandboxes, agent fan-out lanes, worktrees, old device installs) while their
state directories survive forever. This module retires them safely:

- The slug encoding is LOSSY: both path separators and literal underscores
  collapse to ``_``. A slug therefore never proves which source path created
  it, so provenance is established conservatively (exact roundtrip, recorded
  owner marker, temp-dir variant, bounded sibling variants). Anything whose
  provenance cannot be proven live is a quarantine candidate, never a direct
  deletion.
- Nonempty candidates are quarantined into ``_trash`` (reversible); they are
  purged only by an explicit ``--execute`` run and only after a retention
  window, and only while their recorded source path is still missing.
- Directories with a recently modified conversation/state file are never
  touched: a live engine somewhere on the machine may own them.
- Empty shells (registered but never written) carry no data and are removed
  on first sight, behind the same activity guard.
- Dry-run is the default everywhere; only ``--cleanup-projects --execute``
  mutates anything beyond boot-time quarantine.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config_utils import (
    _PROJECT_OWNER_FILE,
    decode_project_path,
    encode_project_path,
)

logger = logging.getLogger(__name__)

TRASH_DIR_NAME = "_trash"
TRASH_META_FILE = ".sweep-meta.json"

#: Top-level ``projects/`` entries owned by the app itself, not by slugs of
#: workspace paths. ``decode_project_path("hub")`` is ``/hub`` which does not
#: exist, so without this allowlist a naive sweep would quarantine live
#: engine state.
RESERVED_NAMES = frozenset({"default", "hub", "logs", "voice", TRASH_DIR_NAME})

#: Slug prefix for state of workspaces created under the system temp dir
#: (agent fan-out lanes, review sandboxes). Their source path may contain
#: literal underscores, so a second, literal-underscore variant is checked.
TMP_SLUG_PREFIX = "private_tmp_"

DEFAULT_RETENTION_DAYS = 14
DEFAULT_ACTIVE_WINDOW_HOURS = 24

#: Bounds for the prefix-pruned provenance search. Exceeding either means
#: the encoding is treated as ambiguous and reported for a human instead.
DFS_MAX_DEPTH = 32
DFS_MAX_NODES = 2000


@dataclass(frozen=True)
class SweepDecision:
    """Outcome for one top-level entry of the projects tree."""

    name: str
    path: Path
    category: str  # live | reserved | active | ambiguous | empty-shell | orphan | trash
    reason: str
    size_bytes: int = 0
    mtime_age_days: float | None = None


@dataclass
class SweepPlan:
    """Everything one sweep run intends to do, before any mutation."""

    decisions: list[SweepDecision] = field(default_factory=list)
    quarantines: list[SweepDecision] = field(default_factory=list)
    empty_shells: list[SweepDecision] = field(default_factory=list)
    purges: list[tuple[str, Path, float]] = field(default_factory=list)  # name, path, age_days

    @property
    def quarantine_bytes(self) -> int:
        return sum(d.size_bytes for d in self.quarantines)



def _path_size(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _newest_mtime(path: Path) -> float | None:
    newest: float | None = None
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if newest is None or stat.st_mtime > newest:
                        newest = stat.st_mtime
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
        except OSError:
            continue
    return newest


def _is_empty_dir(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                return False
    except OSError:
        return False
    return True


def _tmp_variant_exists(decoded: Path) -> bool:
    """True when the decoded temp-dir path exists under a real tmp root.

    ``/private/tmp/my_dir`` encodes exactly like ``/private/tmp/my/dir``,
    so both readings must be checked before a temp entry is called orphaned.
    """
    if not str(decoded).startswith("/private/tmp") and not str(decoded).startswith("/tmp"):
        return False
    slug = encode_project_path(decoded)
    if not slug.startswith(TMP_SLUG_PREFIX):
        return False
    rest = slug[len(TMP_SLUG_PREFIX):]
    seen: set[str] = set()
    for root in (Path("/private/tmp"), Path("/tmp"), Path(tempfile.gettempdir())):
        try:
            root_str = str(root)
            if root_str in seen:
                continue
            seen.add(root_str)
            candidate = root / rest
            if candidate.is_dir():
                return True
        except OSError:
            continue
    return False


def _encode_literal(path_str: str) -> str:
    """Encode a path string WITHOUT resolving symlinks.

    Mirrors :func:`encode_project_path`'s transform so a symlinked
    directory compares equal to the slug minted from its link path.
    """
    encoded = path_str.replace("/", "_").replace("\\", "_")
    while encoded.startswith("_"):
        encoded = encoded[1:]
    return encoded


def _dfs_slug_match(start: Path, slug: str) -> tuple[bool, bool]:
    """Prefix-pruned search for a real directory that encodes to ``slug``.

    Returns (found, budget_exhausted). Only directories whose encoded path
    is a prefix of the slug are visited, so the walk stays tiny even under
    underscore-heavy ancestors. Budgets bound pathological trees.
    """
    visited = 0
    found = False
    exhausted = False

    def visit(node: Path, depth: int) -> None:
        nonlocal visited, found, exhausted
        if found or exhausted:
            return
        visited += 1
        if visited > DFS_MAX_NODES or depth > DFS_MAX_DEPTH:
            exhausted = True
            return
        try:
            with os.scandir(node) as entries:
                children = [Path(e.path) for e in entries if e.is_dir(follow_symlinks=True)]
        except OSError:
            # An unreadable branch is a gap in the search, not proof of
            # absence: report inconclusive so the entry is treated as
            # ambiguous (report-only) instead of a false orphan.
            exhausted = True
            return
        for child in children:
            # A symlinked directory must compare under its link path:
            # encode_project_path resolves links and would yield the
            # target's encoding instead.
            try:
                encoded_candidates = {_encode_literal(str(child))}
                if not child.is_symlink():
                    encoded_candidates.add(encode_project_path(child))
            except OSError:
                # A child whose encoding cannot be computed is a gap in
                # the search, not proof of absence: report inconclusive
                # so the entry reads ambiguous (report-only).
                exhausted = True
                return
            if slug in encoded_candidates:
                found = True
                return
            if any(slug.startswith(enc + "_") for enc in encoded_candidates):
                visit(child, depth + 1)

    visit(start, 0)
    return found, exhausted


def _provenance_is_live(
    slug: str,
    decoded: Path,
    entry: Path,
) -> tuple[bool, str]:
    """Decide whether a projects/ entry is provably backed by a live workspace.

    Returns (live, evidence). ``live`` is True only when some concrete
    existing filesystem path maps back to exactly this slug.
    """
    # 1. Exact roundtrip: a directory at the decoded path is live. A
    #    symlinked workspace still counts (encode() follows the link, so
    #    equality with the slug cannot be required here); quarantine and
    #    retention keep the cost of a rare over-keep low.
    try:
        resolved = decoded.resolve()
    except OSError:
        resolved = decoded
    if decoded.is_dir() or resolved.is_dir():
        return True, f"decoded path exists: {resolved}"

    # 2. Recorded owner marker inside the entry itself (authoritative when
    #    present). Bound to this slug so a stray marker cannot vouch for a
    #    foreign directory.
    marker = entry / _PROJECT_OWNER_FILE
    if entry.is_dir() and marker.is_file():
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
            recorded_path = Path(str(recorded["project"]))
            if (
                isinstance(recorded, dict)
                and recorded_path.is_dir()
                and encode_project_path(recorded_path) == slug
            ):
                return True, f"owner marker points at: {recorded_path}"
        except (OSError, ValueError, KeyError, TypeError):
            pass

    # 3. Temp-dir literal variant.
    if _tmp_variant_exists(decoded):
        return True, "temp workspace variant exists"

    # 4. Search from the root: every separator in the decoded path could
    #    instead be a literal underscore, including above existing ancestors.
    start = Path(decoded.anchor)
    found, exhausted = _dfs_slug_match(start, slug)
    if found:
        return True, "live workspace found under ancestor search"
    if exhausted:
        return False, f"ambiguous encoding (search inconclusive under {start})"
    return False, "no live workspace maps to this slug"


def _classify_entry(
    entry: Path,
    *,
    now: float,
    active_window_seconds: float,
    own_slug: str,
) -> SweepDecision:
    name = entry.name
    stat = entry.stat(follow_symlinks=False)
    age_days = max(0.0, (now - stat.st_mtime) / 86400)

    if not entry.is_dir(follow_symlinks=False):
        return SweepDecision(name, entry, "reserved", "not a directory (left untouched)")
    if name in RESERVED_NAMES:
        return SweepDecision(name, entry, "reserved", "app-owned directory")
    if name == own_slug:
        return SweepDecision(name, entry, "reserved", "this process's own project state")

    size = _path_size(entry)
    newest = _newest_mtime(entry)
    if newest is not None and (now - newest) <= active_window_seconds:
        age = max(0.0, (now - newest) / 86400)
        return SweepDecision(
            name, entry, "active", f"state modified {age:.1f}d ago (live session guard)", size
        )

    # Provenance runs before the empty-shell verdict: an empty state
    # directory of a live workspace is still that workspace's own state.
    live, evidence = _provenance_is_live(name, decode_project_path(name), entry)
    if live:
        return SweepDecision(name, entry, "live", evidence, size)
    if evidence.startswith("ambiguous"):
        return SweepDecision(name, entry, "ambiguous", evidence, size, age_days)
    if _is_empty_dir(entry):
        if age_days * 86400 < active_window_seconds:
            # Created moments ago: an engine may be mid-startup; hands off
            # until it has had its chance to write.
            return SweepDecision(
                name, entry, "active", f"created {age_days * 24:.1f}h ago (creation guard)", size
            )
        return SweepDecision(name, entry, "empty-shell", "registered but never written", size, age_days)
    return SweepDecision(name, entry, "orphan", evidence, size, age_days)


def _trash_meta_is_symlink(trash_dir: Path) -> bool:
    """True when the metadata file is a symlink.

    Records read or written through the link would live outside the
    projects root: reads could import crafted purge orders and writes
    would publish trash records into an external file.
    """
    return (trash_dir / TRASH_META_FILE).is_symlink()


def _load_trash_meta(trash_dir: Path) -> dict:
    if _trash_meta_is_symlink(trash_dir):
        return {}
    try:
        value = json.loads(
            (trash_dir / TRASH_META_FILE).read_text(encoding="utf-8")
        )
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_trash_meta(trash_dir: Path, meta: dict) -> None:
    trash_dir.mkdir(parents=True, exist_ok=True)
    if _trash_meta_is_symlink(trash_dir):
        raise OSError(
            "trash metadata is a symlink; refusing to write records"
        )
    (trash_dir / TRASH_META_FILE).write_text(
        json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
    )


def _trash_escapes(projects_root: Path, trash_dir: Path) -> bool:
    """True when trash exists but sits outside ``projects_root``.

    A symlinked or escaped trash root would let ``os.rename`` or
    ``shutil.rmtree`` reach a directory outside the projects root; the
    sweep refuses to move into or purge from one. A trash root that does
    not exist yet is not an escape: creating it lands inside the root.
    """
    if trash_dir.is_symlink():
        return True
    if not trash_dir.exists():
        return False
    try:
        root_real = os.path.realpath(projects_root)
        trash_real = os.path.realpath(trash_dir)
    except OSError:
        return True
    return not trash_real.startswith(root_real + os.sep)


def _plan_trash_purges(
    projects_root: Path,
    trash_dir: Path,
    *,
    now: float,
    retention_seconds: float,
) -> list[tuple[str, Path, float]]:
    """Trash entries old enough to purge, minus the resurrect guard.

    An entry is purgeable only when its recorded quarantine age exceeds the
    retention window AND full provenance for the original slug finds no
    live workspace at ANY lossy reading of it (an inconclusive search
    retains the copy). Entries without recorded metadata are never purged
    automatically. A symlinked or escaped trash root plans nothing.
    """
    purges: list[tuple[str, Path, float]] = []
    if trash_dir.is_symlink() or not trash_dir.is_dir() or _trash_escapes(
        projects_root, trash_dir
    ):
        return purges
    if _trash_meta_is_symlink(trash_dir):
        # Untrusted records: freeze purge planning entirely.
        return purges
    meta = _load_trash_meta(trash_dir)
    for child in sorted(trash_dir.iterdir()):
        if child.is_symlink() or not child.is_dir() or child.name == TRASH_META_FILE:
            # A symlinked child cannot be rmtree'd safely; never plan it.
            continue
        record = meta.get(child.name)
        if not isinstance(record, dict):
            continue
        moved_at = record.get("moved_at")
        decoded = record.get("decoded")
        if not isinstance(moved_at, (int, float)) or not isinstance(decoded, str):
            continue
        age = now - float(moved_at)
        if age < retention_seconds:
            continue
        decoded_path = Path(decoded)
        if decoded_path.exists() or _tmp_variant_exists(decoded_path):
            # The workspace came back; hand its state back by keeping the copy.
            continue
        # The decoded hint names only one lossy reading of the slug. The
        # workspace may have been recreated under a different reading, so
        # rerun the full provenance search for the original slug before
        # destroying the copy; an inconclusive search retains it. Without
        # the original slug no check is possible: fail closed and retain.
        slug = record.get("original_name")
        if not isinstance(slug, str) or not slug:
            continue
        try:
            live, evidence = _provenance_is_live(slug, decoded_path, child)
        except OSError:
            live, evidence = False, "ambiguous encoding (unreadable provenance check)"
        if live or evidence.startswith("ambiguous"):
            continue
        purges.append((child.name, child, age / 86400))
    return purges


def build_sweep_plan(
    projects_root: Path,
    *,
    own_project: Path | None = None,
    active_window_hours: float = DEFAULT_ACTIVE_WINDOW_HOURS,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    now: float | None = None,
) -> SweepPlan:
    """Classify every top-level entry and assemble the intended mutations."""
    now = time.time() if now is None else now
    plan = SweepPlan()
    if not projects_root.is_dir():
        return plan

    own_slug = encode_project_path(own_project) if own_project else ""
    for entry in sorted(projects_root.iterdir(), key=lambda p: p.name):
        try:
            decision = _classify_entry(
                entry,
                now=now,
                active_window_seconds=active_window_hours * 3600,
                own_slug=own_slug,
            )
        except OSError as exc:
            plan.decisions.append(
                SweepDecision(entry.name, entry, "reserved", f"unreadable, skipped: {exc}")
            )
            continue
        plan.decisions.append(decision)
        if decision.category == "orphan":
            plan.quarantines.append(decision)
        elif decision.category == "empty-shell":
            plan.empty_shells.append(decision)

    plan.purges = _plan_trash_purges(
        projects_root,
        projects_root / TRASH_DIR_NAME,
        now=now,
        retention_seconds=retention_days * 86400,
    )
    return plan


def _move_into_trash(source: Path, trash_dir: Path, decoded_hint: str) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    if _trash_meta_is_symlink(trash_dir):
        # Quarantine without a trusted record is how copies get lost or
        # purged later; fail before the rename, leaving the entry in place.
        raise OSError(
            "trash metadata is a symlink; refusing to quarantine without "
            "a trusted record"
        )
    target = trash_dir / source.name
    counter = 2
    while target.exists():
        target = trash_dir / f"{source.name}-{counter}"
        counter += 1
    os.rename(source, target)
    meta = _load_trash_meta(trash_dir)
    meta[target.name] = {
        "moved_at": time.time(),
        "decoded": decoded_hint,
        "original_name": source.name,
    }
    _save_trash_meta(trash_dir, meta)
    return target


def apply_sweep_plan(
    projects_root: Path,
    plan: SweepPlan,
    *,
    mode: str,
) -> list[str]:
    """Execute a plan. ``mode`` is ``dry-run``, ``boot`` or ``execute``.

    - dry-run: no mutation, only the report.
    - boot: quarantine orphans and remove empty shells; never purge trash.
    - execute: everything boot does plus retention-window trash purges.
    """
    if mode not in {"dry-run", "boot", "execute"}:
        raise ValueError(f"unknown sweep mode: {mode}")
    report: list[str] = []
    trash_dir = projects_root / TRASH_DIR_NAME
    trash_escapes = _trash_escapes(projects_root, trash_dir)
    moved = 0
    removed_shells = 0
    purged = 0
    freed = 0

    for decision in plan.quarantines:
        if mode == "dry-run":
            report.append(
                f"would quarantine {decision.name} "
                f"({_human_size(decision.size_bytes)}) - {decision.reason}"
            )
            continue
        if trash_escapes:
            report.append(
                f"FAILED to quarantine {decision.name}: trash root is a "
                "symlink or outside the projects root; entry left in place"
            )
            continue
        try:
            _move_into_trash(decision.path, trash_dir, str(decode_project_path(decision.name)))
        except OSError as exc:
            report.append(f"FAILED to quarantine {decision.name}: {exc}")
            continue
        moved += 1
        freed += decision.size_bytes
        report.append(
            f"quarantined {decision.name} "
            f"({_human_size(decision.size_bytes)}) - {decision.reason}"
        )

    for decision in plan.empty_shells:
        if mode == "dry-run":
            report.append(
                f"would remove empty shell {decision.name} - {decision.reason}"
            )
            continue
        try:
            decision.path.rmdir()
        except OSError as exc:
            report.append(f"FAILED to remove empty shell {decision.name}: {exc}")
            continue
        removed_shells += 1
        report.append(f"removed empty shell {decision.name}")

    if mode in {"dry-run", "execute"}:
        for name, path, age_days in plan.purges:
            if mode == "dry-run":
                report.append(
                    f"would purge trash/{name} after {age_days:.0f}d "
                    f"({_human_size(_path_size(path))}, source still absent)"
                )
                continue
            if trash_escapes:
                report.append(
                    f"FAILED to purge trash/{name}: trash root is a symlink "
                    "or outside the projects root; entry left in place"
                )
                continue
            size = _path_size(path)
            try:
                shutil.rmtree(path)
            except OSError as exc:
                report.append(f"FAILED to purge trash/{name}: {exc}")
                continue
            purged += 1
            freed += size
            report.append(
                f"purged trash/{name} after {age_days:.0f}d "
                f"({_human_size(size)}, source still absent)"
            )

    quarantined_note = (
        f"{moved} quarantined ({_human_size(freed)})"
        if mode != "dry-run"
        else f"{len(plan.quarantines)} would be quarantined ({_human_size(plan.quarantine_bytes)})"
    )
    logger.info(
        "projects sweep (%s): %s, %d empty shells, %d trash purges",
        mode,
        quarantined_note,
        removed_shells if mode != "dry-run" else len(plan.empty_shells),
        purged if mode == "execute" else len(plan.purges),
    )
    return report


def run_projects_sweep(
    projects_root: Path | None = None,
    *,
    mode: str = "dry-run",
    own_project: Path | None = None,
    active_window_hours: float = DEFAULT_ACTIVE_WINDOW_HOURS,
    retention_days: float = DEFAULT_RETENTION_DAYS,
) -> tuple[SweepPlan, list[str]]:
    """One-shot sweep used by the CLI and the boot hook."""
    if projects_root is None:
        from .config_utils import get_config_directory_candidates

        projects_root = get_config_directory_candidates()[0] / "projects"
    plan = build_sweep_plan(
        projects_root,
        own_project=own_project,
        active_window_hours=active_window_hours,
        retention_days=retention_days,
    )
    report = apply_sweep_plan(projects_root, plan, mode=mode)
    return plan, report


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"

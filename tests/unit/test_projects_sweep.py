"""Issue #647: conservative sweep of orphaned ~/.kollab/projects entries.

The slug encoding collapses separators AND literal underscores, so provenance
is decided conservatively: exact roundtrip, owner markers, temp-dir variants,
bounded sibling variants. Anything else is quarantined, never deleted outside
an explicit ``--execute`` retention purge.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from kollabor_config import projects_sweep
from kollabor_config.config_utils import encode_project_path
from kollabor_config.projects_sweep import (
    RESERVED_NAMES,
    TMP_SLUG_PREFIX,
    TRASH_DIR_NAME,
    TRASH_META_FILE,
    build_sweep_plan,
    run_projects_sweep,
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake projects root plus a sibling workspace root for live sources."""
    root = tmp_path / "projects"
    root.mkdir()
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setattr(
        projects_sweep.tempfile, "gettempdir", lambda: str(workspaces)
    )
    return root, workspaces


def _entry(root, name):
    entry = root / name
    entry.mkdir()
    return entry


def _stateful(entry):
    """Give an entry real content so it is not an empty shell."""
    (entry / "config.json").write_text("{}")
    return entry


def _age(entry, days=30):
    old = time.time() - days * 86400
    os.utime(entry, (old, old))
    for path in entry.rglob("*"):
        os.utime(path, (old, old))


def _orphan(root, name, content="conversation"):
    entry = _entry(root, name)
    (entry / "conversations").mkdir()
    (entry / "conversations" / "session_1.jsonl").write_text(content)
    _age(entry)
    return entry


def _categories(root, **kwargs):
    plan = build_sweep_plan(root, **kwargs)
    return {d.name: d.category for d in plan.decisions}


def test_dry_run_default_never_mutates(tree):
    root, _ = tree
    orphan = _orphan(root, "private_tmp_claude-dead-lane")
    shell = _entry(root, "private_tmp_never-used")
    _age(shell)
    plan, report = run_projects_sweep(root, mode="dry-run", own_project=None)
    assert orphan.is_dir() and shell.is_dir()
    assert not (root / TRASH_DIR_NAME).exists()
    assert {d.name: d.category for d in plan.decisions}[
        "private_tmp_claude-dead-lane"
    ] == "orphan"
    assert any(line.startswith("would quarantine") for line in report)
    assert any(line.startswith("would remove empty shell") for line in report)


def test_execute_quarantines_orphan_and_records_meta(tree):
    root, _ = tree
    orphan = _orphan(root, "private_tmp_rv553")
    plan, report = run_projects_sweep(root, mode="execute", own_project=None)
    assert not orphan.exists()
    quarantined = root / TRASH_DIR_NAME / "private_tmp_rv553"
    assert quarantined.is_dir()
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    assert meta["private_tmp_rv553"]["decoded"].endswith("rv553")
    assert "moved_at" in meta["private_tmp_rv553"]
    assert any(line.startswith("quarantined private_tmp_rv553") for line in report)


def test_quarantine_collision_gets_suffix(tree):
    root, _ = tree
    _orphan(root, "private_tmp_lane")
    run_projects_sweep(root, mode="execute")
    second = _orphan(root, "private_tmp_lane")
    run_projects_sweep(root, mode="execute")
    assert not second.exists()
    assert (root / TRASH_DIR_NAME / "private_tmp_lane-2").is_dir()


def test_live_roundtrip_survives(tree):
    root, workspaces = tree
    workspace = workspaces / "old-review-sandbox"
    workspace.mkdir()
    entry = _stateful(_entry(root, encode_project_path(workspace)))
    plan, _ = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert entry.is_dir()
    assert (root / encode_project_path(workspace)).is_dir()
    assert not plan.quarantines


def test_owner_marker_vouches_for_entry(tree):
    root, workspaces = tree
    # Underscore-named workspace: the slug cannot be decoded back exactly,
    # so the recorded owner marker is what proves provenance.
    workspace = workspaces / "team_notes"
    workspace.mkdir()
    slug = encode_project_path(workspace)
    entry = _stateful(_entry(root, slug))
    (entry / projects_sweep._PROJECT_OWNER_FILE).write_text(
        json.dumps({"version": 1, "project": str(workspace), "identity": ""})
    )
    categories = _categories(root, active_window_hours=0)
    assert categories[slug] == "live"
    assert entry.is_dir()


def test_symlinked_workspace_decodes_live(tree):
    root, workspaces = tree
    real = root / "real-target"
    real.mkdir()
    link = workspaces / "livekit-demo"
    link.symlink_to(real)
    slug = encode_project_path(link)
    entry = _stateful(_entry(root, slug))
    categories = _categories(root, active_window_hours=0)
    assert categories[slug] == "live"
    assert entry.is_dir()


def test_dfs_follows_symlinked_sibling(tree):
    root, workspaces = tree
    base = workspaces / "team"
    base.mkdir()
    real = root / "elsewhere"
    real.mkdir()
    (base / "symlinked-workspace").symlink_to(real)
    slug = encode_project_path(base) + "_symlinked-workspace"
    entry = _stateful(_entry(root, slug))
    # Decode lands on team/symlinked/workspace (missing), so provenance
    # must reach the symlink through the pruned search.
    categories = _categories(root, active_window_hours=0)
    assert categories[slug] == "live"
    assert entry.is_dir()


def test_wrong_owner_marker_does_not_vouch(tree):
    root, workspaces = tree
    entry = _stateful(_entry(root, "Users_someone_else_project"))
    (entry / projects_sweep._PROJECT_OWNER_FILE).write_text(
        json.dumps({"version": 1, "project": "/gone/workspace", "identity": ""})
    )
    _age(entry)
    categories = _categories(root)
    assert categories["Users_someone_else_project"] == "orphan"


def test_temp_literal_underscore_variant_is_live(tree):
    root, workspaces = tree
    workspace = workspaces / "scratch_pad_two"
    workspace.mkdir()
    slug = TMP_SLUG_PREFIX + "scratch_pad_two"
    entry = _stateful(_entry(root, slug))
    categories = _categories(root, active_window_hours=0)
    assert categories[slug] == "live"
    assert entry.is_dir()


def test_sibling_literal_underscore_variant_is_live(tree):
    root, workspaces = tree
    base = workspaces / "carter"
    base.mkdir()
    (base / "marcoverse_notes").mkdir()
    slug = encode_project_path(base) + "_marcoverse_notes"
    entry = _stateful(_entry(root, slug))
    categories = _categories(root, active_window_hours=0)
    assert categories[slug] == "live"
    assert entry.is_dir()


def test_underscore_heavy_live_workspace_survives(tree):
    root, workspaces = tree
    # The pytest tmp root itself contains underscored hash components; the
    # pruned search must still find the real workspace below it.
    base = workspaces / "team_notes"
    base.mkdir()
    (base / "draft_one").mkdir()
    slug = encode_project_path(base) + "_draft_one"
    entry = _stateful(_entry(root, slug))
    plan, _ = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert entry.is_dir()
    assert {d.name: d.category for d in plan.decisions}[slug] == "live"


def test_undecodable_unknown_slug_is_quarantined(tree):
    root, _ = tree
    # Nothing anywhere maps to /a/b/c/d/e/f/g/h and the decoded path does
    # not exist: a genuine orphan, quarantined (never deleted outright).
    slug = "a_b_c_d_e_f_g_h"
    _orphan(root, slug)
    plan, _ = run_projects_sweep(root, mode="execute")
    assert {d.name: d.category for d in plan.decisions}[slug] == "orphan"
    assert (root / TRASH_DIR_NAME / slug).is_dir()


def test_open_conversation_is_never_touched(tree):
    root, _ = tree
    entry = _orphan(root, "private_tmp_live-lane")
    (entry / "conversations" / "session_now.jsonl").write_text("live")
    plan, _ = run_projects_sweep(root, mode="execute")
    assert entry.is_dir()
    assert {d.name: d.category for d in plan.decisions}[
        "private_tmp_live-lane"
    ] == "active"
    assert not plan.quarantines


def test_empty_shell_removed_on_first_sight_and_dry_run_keeps_it(tree):
    root, _ = tree
    shell = _entry(root, "private_tmp_registered-never-used")
    _age(shell)
    run_projects_sweep(root, mode="dry-run")
    assert shell.is_dir()
    run_projects_sweep(root, mode="boot")
    assert not shell.exists()


def test_fresh_entries_hit_the_activity_guard_first(tree):
    root, _ = tree
    shell = _entry(root, "private_tmp_just-created")
    categories = _categories(root)
    assert categories["private_tmp_just-created"] == "active"
    assert shell.is_dir()


def test_reserved_names_are_never_swept(tree):
    root, _ = tree
    for name in sorted(RESERVED_NAMES):
        entry = _entry(root, name)
        (entry / "state.json").write_text("engine owned")
    _age(root)
    plan, _ = run_projects_sweep(root, mode="execute")
    for name in sorted(RESERVED_NAMES):
        assert (root / name).is_dir()
    assert not plan.quarantines


def test_own_project_state_is_skipped(tree, tmp_path):
    root, _ = tree
    own = tmp_path / "this-workspace"
    own.mkdir()
    slug = encode_project_path(own)
    entry = _orphan(root, slug)
    plan, _ = run_projects_sweep(root, mode="execute", own_project=own)
    assert entry.is_dir()
    assert {d.name: d.category for d in plan.decisions}[slug] == "reserved"
    assert not plan.quarantines


def test_file_entries_are_untouched(tree):
    root, _ = tree
    (root / "stray-file").write_text("not a session dir")
    plan, _ = run_projects_sweep(root, mode="execute")
    assert (root / "stray-file").is_file()
    assert {d.name: d.category for d in plan.decisions}[
        "stray-file"
    ] == "reserved"


def test_trash_purge_requires_execute_and_retention(tree):
    root, _ = tree
    run_projects_sweep(root, mode="execute")  # ensure trash exists
    stale = _orphan(root, "private_tmp_old-lane")
    run_projects_sweep(root, mode="execute")
    entry = root / TRASH_DIR_NAME / "private_tmp_old-lane"
    assert entry.is_dir()
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    meta["private_tmp_old-lane"]["moved_at"] = time.time() - 20 * 86400
    (root / TRASH_DIR_NAME / TRASH_META_FILE).write_text(json.dumps(meta))
    assert stale.exists() is False

    # Boot must never purge.
    run_projects_sweep(root, mode="boot")
    assert entry.is_dir()

    # Execute purges past the retention window (default 14 days).
    run_projects_sweep(root, mode="execute")
    assert not entry.exists()


def test_purge_skips_before_retention_window(tree):
    root, _ = tree
    _orphan(root, "private_tmp_fresh-trash")
    run_projects_sweep(root, mode="execute")
    entry = root / TRASH_DIR_NAME / "private_tmp_fresh-trash"
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    meta["private_tmp_fresh-trash"]["moved_at"] = time.time() - 2 * 86400
    (root / TRASH_DIR_NAME / TRASH_META_FILE).write_text(json.dumps(meta))
    run_projects_sweep(root, mode="execute")
    assert entry.is_dir()


def test_purge_resurrect_guard_keeps_returned_workspace_state(tree):
    root, workspaces = tree
    _orphan(root, "private_tmp_returned-lane")
    run_projects_sweep(root, mode="execute")
    entry = root / TRASH_DIR_NAME / "private_tmp_returned-lane"
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    # The workspace came back while the copy sat in quarantine.
    returned = workspaces / "returned-lane"
    returned.mkdir()
    meta["private_tmp_returned-lane"]["moved_at"] = time.time() - 30 * 86400
    (root / TRASH_DIR_NAME / TRASH_META_FILE).write_text(json.dumps(meta))
    run_projects_sweep(root, mode="execute")
    assert entry.is_dir(), "resurrected workspaces must keep their state copy"


def test_trash_without_meta_is_never_purged(tree):
    root, _ = tree
    trash = root / TRASH_DIR_NAME
    trash.mkdir()
    legacy = trash / "private_tmp_unmeta"
    legacy.mkdir()
    (legacy / "conversations").mkdir()
    (legacy / "conversations" / "s.jsonl").write_text("data")
    _age(legacy)
    run_projects_sweep(root, mode="execute")
    assert legacy.is_dir()


def test_report_includes_sizes(tree):
    root, _ = tree
    entry = _orphan(root, "private_tmp_sized", content="x" * 2048)
    _plan, report = run_projects_sweep(root, mode="dry-run")
    sized = [line for line in report if "private_tmp_sized" in line]
    assert sized and "KB" in sized[0]


def test_summary_log_line_emitted(tree, caplog):
    root, _ = tree
    _orphan(root, "private_tmp_logged")
    with caplog.at_level("INFO", logger="kollabor_config.projects_sweep"):
        run_projects_sweep(root, mode="execute")
    messages = [r.getMessage() for r in caplog.records]
    assert any("projects sweep (execute)" in m for m in messages)
    assert any("1 quarantined" in m for m in messages)


def test_unknown_mode_rejected(tree):
    root, _ = tree
    with pytest.raises(ValueError):
        run_projects_sweep(root, mode="yolo")


def test_missing_projects_root_is_noop(tmp_path):
    plan, report = run_projects_sweep(tmp_path / "absent", mode="execute")
    assert plan.decisions == [] and report == []


# ---------------------------------------------------------------------------
# Garnet review round: scan failures, resurrect provenance, trash
# containment, dry-run purge preview.


def _sealed_scandir(monkeypatch, marker):
    """Make os.scandir raise PermissionError for exactly ``marker``."""
    import kollabor_config.projects_sweep as sweep_module

    real_scandir = sweep_module.os.scandir

    def sealed(path, *args, **kwargs):
        if str(path) == str(marker):
            raise PermissionError(13, "simulated denied scan")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(sweep_module.os, "scandir", sealed)


def _quarantine_and_age(root, slug, days=20):
    """Quarantine an orphan on boot, then age its trash record past retention."""
    _orphan(root, slug)
    run_projects_sweep(root, mode="boot")
    trash_entry = root / TRASH_DIR_NAME / slug
    assert trash_entry.is_dir()
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    meta[slug]["moved_at"] = time.time() - days * 86400
    (root / TRASH_DIR_NAME / TRASH_META_FILE).write_text(json.dumps(meta))
    return trash_entry


def test_unreadable_branch_is_ambiguous_never_orphan(tree, monkeypatch):
    """A PermissionError during the provenance walk is a search GAP.

    An unreadable branch is not evidence of absence: marking the entry
    orphan would quarantine a workspace the sweep never got to examine.
    Inconclusive must read ambiguous (report-only) instead.
    """
    root, workspaces = tree
    base = workspaces / "sealed"
    base.mkdir()
    slug = encode_project_path(base) + "_inner_notes"
    entry = _orphan(root, slug)
    _sealed_scandir(monkeypatch, base)
    plan, _ = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert {d.name: d.category for d in plan.decisions}[slug] == "ambiguous"
    assert entry.is_dir(), "an unreadable branch must never be quarantined"


def test_purge_rechecks_provenance_for_lossy_recreation(tree):
    """A same-slug workspace recreated at a different lossy path is live.

    The trash record's decoded hint names only ONE reading of the slug;
    the resurrect guard must rerun the full provenance search for the
    slug before purging, not just check the hint path.
    """
    root, workspaces = tree
    base = workspaces / "carter"
    slug = encode_project_path(base) + "_marcoverse_notes"
    trash_entry = _quarantine_and_age(root, slug)
    # The user recreated the workspace while the copy sat in quarantine,
    # using a literal-underscore name (a different lossy reading).
    recreated = base / "marcoverse_notes"
    recreated.mkdir(parents=True)
    run_projects_sweep(root, mode="execute")
    assert trash_entry.is_dir(), (
        "a recreated same-slug workspace must keep its quarantine copy"
    )


def test_purge_keeps_copy_when_provenance_is_inconclusive(tree, monkeypatch):
    """Inconclusive provenance at purge time retains the copy."""
    root, workspaces = tree
    base = workspaces / "sealed_purge"
    base.mkdir()
    slug = encode_project_path(base) + "_inner_notes"
    trash_entry = _quarantine_and_age(root, slug)
    _sealed_scandir(monkeypatch, base)
    plan, _ = run_projects_sweep(root, mode="execute")
    assert plan.purges == []
    assert trash_entry.is_dir(), "inconclusive provenance must retain the copy"


def test_symlinked_trash_root_refuses_quarantine(tree, tmp_path):
    """A symlinked trash root must not receive quarantine moves."""
    root, _ = tree
    external = tmp_path / "external-vault"
    external.mkdir()
    (root / TRASH_DIR_NAME).symlink_to(external)
    orphan = _orphan(root, "private_tmp_sealed-lane")
    plan, report = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert orphan.is_dir(), (
        "a symlinked trash root must not receive quarantine moves"
    )
    assert not (external / "private_tmp_sealed-lane").exists()
    assert any("FAILED to quarantine" in line for line in report)


def test_symlinked_trash_root_never_purges(tree):
    """Execute must not rmtree through a symlinked trash root."""
    root, _ = tree
    slug = "private_tmp_legacy-lane"
    _quarantine_and_age(root, slug)
    external = root.parent / "external-trash"
    trash_dir = root / TRASH_DIR_NAME
    external.mkdir()
    for child in trash_dir.iterdir():
        os.rename(child, external / child.name)
    trash_dir.rmdir()
    trash_dir.symlink_to(external)
    plan, report = run_projects_sweep(root, mode="execute")
    assert plan.purges == [], "a symlinked trash root must not plan purges"
    assert (external / slug).is_dir(), (
        "execute must not rmtree through a symlinked trash root"
    )


def test_dry_run_previews_pending_purges(tree):
    """Dry-run lists would-purge rows instead of pretending there is no plan."""
    root, _ = tree
    slug = "private_tmp_preview-lane"
    trash_entry = _quarantine_and_age(root, slug)
    plan, report = run_projects_sweep(root, mode="dry-run")
    assert plan.purges, "aged entry must be planned for purge"
    assert any(f"would purge trash/{slug}" in line for line in report)
    assert trash_entry.is_dir(), "dry-run must not purge"


# ---------------------------------------------------------------------------
# Garnet round 2: encode failures, symlinked metadata, fail-closed purge
# provenance.


def test_unencodable_branch_is_ambiguous_never_orphan(tree, monkeypatch):
    """A candidate whose encoding cannot be computed is a search gap.

    encode_project_path resolves symlinks and can raise. Swallowing the
    error read as absence and classified a live workspace orphan; the
    entry must read ambiguous (report-only) instead.
    """
    import kollabor_config.projects_sweep as sweep_module

    real_encode = sweep_module.encode_project_path

    def sealed_encode(path):
        if str(path) == str(live):
            raise PermissionError(13, "simulated denied resolve")
        return real_encode(path)

    monkeypatch.setattr(sweep_module, "encode_project_path", sealed_encode)
    root, workspaces = tree
    base = workspaces / "sealed"
    live = base / "inner-notes"
    live.mkdir(parents=True)
    slug = encode_project_path(live)
    entry = _orphan(root, slug)
    plan, _ = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert {d.name: d.category for d in plan.decisions}[slug] == "ambiguous"
    assert entry.is_dir(), "an unencodable candidate must never be quarantined"


def test_symlinked_trash_metadata_blocks_quarantine(tree, tmp_path):
    """A symlinked metadata file must not receive quarantine records.

    write_text through the link would insert the trash record into an
    external file the projects root does not own; the whole move must
    fail closed with the entry left in place.
    """
    root, _ = tree
    external = tmp_path / "external-meta.json"
    external.write_text("{}")
    trash_dir = root / TRASH_DIR_NAME
    trash_dir.mkdir()
    (trash_dir / TRASH_META_FILE).symlink_to(external)
    orphan = _orphan(root, "private_tmp_meta-lane")
    plan, report = run_projects_sweep(root, mode="execute", active_window_hours=0)
    assert orphan.is_dir(), "symlinked metadata must block the quarantine move"
    assert json.loads(external.read_text()) == {}, (
        "an external metadata file must not receive trash records"
    )
    assert any("FAILED to quarantine" in line for line in report)


def test_symlinked_trash_metadata_never_plans_purges(tree):
    """Purge planning must not trust records read through a symlink.

    A link to an external, attacker-crafted JSON would otherwise supply
    aged records that name trash entries for destruction; planning has
    to freeze instead of following the link."""
    root, _ = tree
    slug = "private_tmp_meta-lane"
    trash_entry = _quarantine_and_age(root, slug)
    crafted = {
        slug: {
            "moved_at": time.time() - 30 * 86400,
            "decoded": str(root / "nowhere"),
            "original_name": slug,
        }
    }
    external = root.parent / "external-meta.json"
    external.write_text(json.dumps(crafted))
    meta_path = root / TRASH_DIR_NAME / TRASH_META_FILE
    meta_path.unlink()
    meta_path.symlink_to(external)
    plan, _ = run_projects_sweep(root, mode="execute")
    assert plan.purges == [], "records through a symlink must not drive purges"
    assert trash_entry.is_dir()


def test_purge_requires_original_name_metadata(tree):
    """Without original_name the provenance rerun is impossible: no purge.

    A record that lost its required provenance key used to skip the full
    check and purge anyway - even with the same-slug workspace alive at
    another lossy reading. Fail closed: absent metadata retains the copy.
    """
    root, workspaces = tree
    base = workspaces / "carter"
    slug = encode_project_path(base) + "_marcoverse_notes"
    trash_entry = _quarantine_and_age(root, slug)
    meta = json.loads((root / TRASH_DIR_NAME / TRASH_META_FILE).read_text())
    meta[slug].pop("original_name")
    (root / TRASH_DIR_NAME / TRASH_META_FILE).write_text(json.dumps(meta))
    # Same slug recreated at a lossy-different reading while the copy sits.
    (base / "marcoverse_notes").mkdir(parents=True)
    plan, _ = run_projects_sweep(root, mode="execute")
    assert plan.purges == [], "records without original_name must not purge"
    assert trash_entry.is_dir()

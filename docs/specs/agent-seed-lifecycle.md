# Global Agent Seed Lifecycle

Issue: #1303
Status: Implemented
Owner: Kollab configuration

## Problem

An existing global agent file shadows the bundled file at the same path. Startup
currently copies only missing files, so bundle updates and removals do not reach
existing installations. A global agent has higher resolution priority than its
bundle, which leaves stale instructions active.

## Goal and non-goals

Keep global copies of unchanged bundled files current while preserving files
that users have edited or added. Retain replaced and retired seed files in
`~/.kollab/agents/.seed-backup/`.

This does not change agent tier priority, rewrite unknown global files, or
reload agents already running in another process.

## What people see

Startup updates files whose SHA-256 matches a recorded bundled version. It moves
those replaced or retired files to `.seed-backup` and copies the current bundle.
Files that differ from every recorded seed version remain active; startup logs
their paths when they shadow a bundled file.

## Contract

- Owner: `packages/kollabor-config/src/kollabor_config/config_utils.py`.
- Source: each directory under `bundles/agents/`; destination:
  `~/.kollab/agents/<agent>/`.
- `bundles/data/agent-seed-hashes.json` maps each seeded agent name and relative
  file path to the SHA-256 hashes of every supported bundled version, including
  the current file content and files removed from the bundle.
- Missing destination files are published using the existing no-overwrite
  seeding operation. Existing files are replaced or retired only when their
  content matches a recorded seed hash.
- Replacement and retirement move the old file into
  `~/.kollab/agents/.seed-backup/<agent>/<relative-path>`. A collision uses a
  unique sibling name; existing backup data is never overwritten.
- Symbolic links, reparse points, unrecognized content, unreadable hash data,
  and unsafe backup paths are preserved. Failed moves or writes leave existing
  data available or retain the backup and emit a path-bearing diagnostic.

## Scenarios

- If a global file matches the current bundle hash, startup leaves it in place.
- If it matches an older recorded hash, startup backs it up and publishes the
  current bundle file.
- If a matching seeded file is no longer bundled, startup backs it up without
  deleting its contents.
- If a file differs from all known hashes, startup preserves it and logs its
  path when a bundled file would otherwise be shadowed.
- If a bundled file is missing globally, startup adds it without replacing a
  concurrent file or link.
- If startup runs again after a successful update, it makes no additional
  backup and leaves the current file unchanged.
- If hash metadata or a backup path is unsafe, startup preserves existing
  global files and logs why automatic management was skipped.

## Failure modes

- Missing or malformed hash metadata: do not update or retire existing files;
  retain the existing safe copy-missing behavior.
- A linked global agent or ancestor: skip that destination and do not traverse
  or modify the link target.
- A linked file or directory under a global agent: preserve it and do not write
  through it.
- A linked, unreadable, or occupied backup path: preserve the global file and
  report the affected path.
- A file changes while being moved: restore it to the original path when that
  path is free; otherwise retain the backup and report both locations.
- Publishing the new file fails after the old file is backed up: restore the old
  file if safe; otherwise keep the backup and report the missing or conflicting
  destination.

## Decisions

- Fix owner (this change): manage files individually so one hand-edited section
  does not freeze every other file in the agent directory.
- Fix owner (this change): use historical per-file hashes rather than timestamps
  or directory-level hashes because files can be edited independently and
  bundles can remove one file without removing its agent.
- Fix owner (this change): move old seed files instead of deleting them so
  users can recover an older prompt or section.
- Fix owner (this change): unknown hashes are user-owned; preserving user data
  takes priority over automatically applying a bundle update.

## Test plan

- Red-first regression: `tests/unit/test_agent_seed_lifecycle.py` proves stale
  files remain on the unmodified baseline and become current after the fix.
- Cover seeded updates, retired files, hand edits, newly added files, backup
  collisions, malformed metadata, links, safe failure handling, manager
  resolution, and repeated startup.
- Sweep existing agent seed symlink tests and skill retirement tests to protect
  the shared filesystem helpers and the sibling skill lifecycle.

## Docs and changelog fragment

- Update `docs/features/agents.md` with managed seed behavior and backup paths.
- Add `changes/1303.fixed.md`.

## Open questions

None.

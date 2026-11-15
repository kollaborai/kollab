# Plugin spec step 3 handoff

## Current state

- Worktree: `~/dev/kollab-wt/plugins-step3`; branch `feat/plugins-step3`; starting HEAD `82d1b390096aa43e73b3ca40a90fab581c1529ef` (step 2 handoff).
- Brief: `~/backups/plugin-spec-work/build/step3-brief.md`. Step 2 draft PR #1068 was open per brief; do not open the stacked step 3 PR while it remains open.
- First item in progress: recovery-state sketches before frontend code. Canvas source is `~/backups/plugin-spec-work/canvas/project/`; existing state boards cover off, failed, stopped, and empty; a portable gallery also covers changed-on-disk and skipped.
- Red check: `node --test packages/kollabor-webui/frontend/tests/plugin-state-sketch.test.mjs` failed both cases with ENOENT for the not-yet-created `docs/handoff/plugins/step3-states.html`.

## Remaining

Finish and visually review the sketches; implement all item 1–6 requirements in the brief. Then perform the named tests, lint/build, unit suite and isolated smoke; record exact results. No PR, push, merge, or deployment has been done in this worktree.

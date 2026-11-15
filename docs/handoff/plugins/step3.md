# Plugin spec step 3 handoff

## Current state

- Worktree: `~/dev/kollab-wt/plugins-step3`; branch `feat/plugins-step3`; starting HEAD `82d1b390096aa43e73b3ca40a90fab581c1529ef` (step 2 handoff).
- Brief: `~/backups/plugin-spec-work/build/step3-brief.md`. Step 2 draft PR #1068 was open per brief; do not open the stacked step 3 PR while it remains open.
- Recovery-state sketch: `docs/handoff/plugins/step3-states.html` is a static, responsive light/dark gallery covering off, stopping, failed load/render, crash-stopped, empty, page-wide off, changed-on-disk and skipped. Canvas source is `~/backups/plugin-spec-work/canvas/project/`. Buttons are intentionally nonfunctional in this sketch.
- Red check: `node --test packages/kollabor-webui/frontend/tests/plugin-state-sketch.test.mjs` failed both cases with ENOENT before the gallery was added (test commit `a035e60c`). Afterward the same command passed 2/2. These check required copy and responsive markup only; no visual review or functional UI coverage is implied.
- No browser screenshot tool was discovered. Chrome is installed; screenshots and the required visual review remain outstanding.
- Loader core red check: `cd packages/kollabor-webui/frontend && ./node_modules/.bin/vitest run tests/plugin-loader.test.ts` failed because `src/plugins/loader` did not exist (test commit `28ea17fc`). After adding the lifecycle core it passes 4/4; `npm run typecheck` also passes. Vite reports existing native config-loader warnings.
- The loader is not connected to the app, registry, CSS, events or SDK. Its tests cover parallel activation, discarded late results, timeout/retry cache busting and isolated errors; they do not prove the full contract. Next strengthen late-import behavior before integration.

## Remaining

Finish and visually review the sketches; implement all item 1–6 requirements in the brief. Then perform the named tests, lint/build, unit suite and isolated smoke; record exact results. No PR, push, merge, or deployment has been done in this worktree.

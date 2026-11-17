# Glass K with electric mist in the Kollab logo

Issue: #1246
Status: Implemented
Owner: Web UI

## Problem
The sidebar and welcome screen show Kollab's K mark. The earlier treatment drew electric mist inside a flat SVG; it did not make the mark itself glass. Issue #1246 requires a refracting, extruded glass K with the mist visible through it.

## Goal and non-goals
Render the K as a 3D glass mark with the electric mist behind it, visible through the glass. Preserve a flat SVG mark when the 3D renderer cannot run. Load the Three.js scene lazily, honor reduced motion, and release GPU resources on unmount. Keep the existing K polygon artwork as the single source for both SVG and 3D geometry.

This change does not alter the logo silhouette or introduce a second implementation for the sidebar and welcome screen.

## What people see
- Sidebar header: a small glass K with the electric mist visible inside; it tilts toward pointer movement and briefly flares on hover.
- Welcome screen: the same glass K component and treatment.
- Before the glass draws, and whenever WebGL2 or renderer setup is unavailable, the flat K remains visible.
- With reduced motion enabled, the glass remains still and does not tilt or animate.

## Contract
- `KollabLogo` accepts optional `className` and `glass` props; `glass` defaults to `false`.
- `K_POLYS` defines the K points used to create both SVG paths and the extruded 3D shape.
- When `glass` is enabled, the component lazily imports `kollab-logo-glass`. A failed chunk load or a `startGlass` result of `null` leaves the flat SVG visible. The SVG is hidden only after the glass scene starts successfully.
- The renderer requires WebGL2. Missing WebGL2, software renderers (SwiftShader, llvmpipe, or Basic Render Driver), context creation errors, and Three.js setup errors all keep the flat SVG fallback.
- The mist renders to a texture behind the glass and is visible through the glass surface; it is not drawn directly over the mark.
- Reduced motion uses a still frame without animation. Pointer entry triggers the brief flare; pointer movement controls the tilt.
- Unmount cancels animation and disposes renderer resources, render targets, geometry, and materials, then releases the WebGL context.
- Owning files: `packages/kollabor-webui/frontend/src/components/icons/kollab-logo.tsx`, `packages/kollabor-webui/frontend/src/components/icons/kollab-logo-glass.ts`, `packages/kollabor-webui/frontend/src/components/shell/AppSidebar.tsx`, and `packages/kollabor-webui/frontend/src/App.tsx`.

## Scenarios
- If `glass` is omitted or false, then the component renders only the SVG mark.
- If `glass` is true, then the SVG remains visible until the glass scene starts successfully.
- If the lazy scene chunk fails to load, then the SVG remains visible.
- If WebGL2 is missing, context creation throws, a software renderer is detected, or Three.js setup fails, then the SVG remains visible without an uncaught setup error.
- If the scene starts successfully, then the mist is visible through the glass and the SVG is hidden.
- If reduced motion is requested, then the rendered mark stays still without the animation loop.
- If the pointer enters the mark, then the glass briefly flares; pointer movement controls its tilt.
- If the component unmounts after the scene starts, then its animation and GPU resources are released.
- If the same component is rendered in the sidebar or welcome screen, then both use the same geometry and fallback behavior.

## Failure modes
- Lazy chunk load failure: retain the flat SVG.
- Missing WebGL2 context: retain the flat SVG.
- Software GPU renderer: release the context and retain the flat SVG.
- Renderer/context setup failure: catch the failure and retain the flat SVG.
- Reduced-motion preference: skip animation and keep the glass still.
- Component unmount: stop the animation and dispose GPU resources and context.

## Decisions
- Keep one polygon list as the source for both the SVG and glass geometry, so the two silhouettes cannot drift. Decision recorded in PR #1247.
- Keep the flat SVG visible until successful glass startup and on every setup failure. Decision recorded in issue #1246 and implemented in PR #1247.
- Load the glass scene lazily; Three.js is already used elsewhere in the frontend. Decision recorded in PR #1247.
- Reuse the same logo component in both display locations. Decision recorded in issue #1246 and implemented in PR #1247.

## Test plan
- `packages/kollabor-webui/frontend/tests/kollab-logo.test.tsx`: verify SVG geometry comes from `K_POLYS`, the initial fallback remains visible, missing WebGL2 and setup failures retain the fallback, and software renderers release the context.
- The PR description reports the frontend Vitest suite (49 files, 443 tests), TypeScript checking, and a production build.
- The PR description reports browser observation of the sidebar glass K in dark and light themes, with no clipping or console errors. It also says the welcome-screen K was not visible in that stack because an agent gem replaced it; visual behavior there remains unverified in that run.
- Sweep both locations: the sidebar header and welcome screen. The source implements reduced motion as a still frame, but the focused logo test file does not exercise that branch. The unmount cleanup and reduced-motion behavior need direct verification.

## Docs and changelog fragment
- Feature specification: `docs/specs/webui-logo-mist.md`.
- User-visible change fragment: `changes/1246.changed.md`.

## Open questions
None.

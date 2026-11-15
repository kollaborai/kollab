# Electric mist inside the Kollab logo

Issue: #1125  
Status: Implemented  
Owner: Web UI

## Problem
The Kollab Web UI uses a static K mark in prominent branding positions. This feature adds a subtle animated treatment while keeping the existing mark available as a fallback.

## Goal and non-goals
Show slow-moving grey and white electric mist clipped to the K in the sidebar header and the Start a Session screen. Keep the plain mark visible when reduced motion is requested or WebGL cannot render the effect. This change does not add a WebGL dependency or change the logo artwork.

## What people see
- Sidebar header: the small K mark has slowly moving grey and white mist.
- Start a Session screen: the larger K mark has the same effect.
- With reduced motion enabled, WebGL unavailable, or shader linking unsuccessful, the plain K remains.

## Contract
- `KollabLogo` accepts optional `className` and `mist` props; `mist` defaults to `false`.
- With `mist` disabled, it renders the existing SVG mark. With `mist` enabled, the SVG stays beneath a decorative canvas; the canvas is masked to the K silhouette and is hidden from assistive technology.
- `startMist` skips animation for `prefers-reduced-motion: reduce`, unavailable WebGL, or an unsuccessful program link. On success it draws a full-canvas shader, updates canvas dimensions to its client dimensions, and returns cleanup that cancels the animation frame and releases the WebGL context.
- The Web UI sidebar and Start a Session screen opt into the effect. No new package dependency is introduced.
- Owning files: `packages/kollabor-webui/frontend/src/components/icons/kollab-logo.tsx`, `packages/kollabor-webui/frontend/src/components/shell/AppSidebar.tsx`, and `packages/kollabor-webui/frontend/src/App.tsx`.

## Scenarios
- If the logo is rendered without `mist`, then the SVG mark renders without a canvas.
- If `mist` is enabled and reduced motion is requested, then no WebGL animation starts and the SVG remains visible.
- If `mist` is enabled and WebGL is unavailable, then the SVG remains visible without an animation.
- If the shader program fails to link, then no draw loop starts and the SVG remains visible.
- If the shader links successfully, then the mist draws only within the K silhouette while the SVG remains below it.
- If the logo component unmounts after animation starts, then its animation frame is cancelled and the WebGL context is released.
- If the logo's rendered dimensions change, then the canvas dimensions are updated before drawing at those dimensions.

## Failure modes
- Reduced-motion preference: animation is skipped; the SVG fallback remains.
- Missing WebGL context: animation is skipped; the SVG fallback remains.
- Shader program link failure: drawing is skipped; the SVG fallback remains.
- Mist disabled: the SVG renders on its own and no canvas is created.
- Component cleanup: the outstanding animation frame is cancelled and the WebGL context is released.
- Canvas resize: drawing uses the current client width and height after updating the backing canvas size.

## Decisions
- Keep the existing SVG beneath the canvas so a rendering fallback is present in the same component. Decision recorded by the implementation in PR #1126.
- Use a CSS mask over a canvas for the effect, preserving the existing SVG silhouette. Decision recorded by the implementation in PR #1126.
- Skip animation when reduced motion is requested. Decision recorded by the implementation and its regression tests in PR #1126.
- Add no external WebGL dependency. Decision recorded by the implementation in PR #1126.

## Test plan
- `packages/kollabor-webui/frontend/tests/kollab-logo.test.tsx`: cover plain SVG rendering, masked canvas rendering, reduced motion, unavailable WebGL, shader link failure, drawing/canvas sizing, and cleanup.
- The PR description records `npx vitest run tests/kollab-logo.test.tsx` (6 passed), the full Vitest suite (41 files, 377 tests), TypeScript checking, and a production build.
- The PR description records visual review at desktop, 390 px, and 820 px in dark and light themes. Reduced-motion fallback was covered by unit test only; browser observation of that fallback remains unverified.
- Sweep both opt-in locations: sidebar header and Start a Session screen, including the 390 px layout where the sidebar collapses.

## Docs and changelog fragment
- Feature specification: `docs/specs/webui-logo-mist.md`.
- User-visible change fragment: `changes/1125.added.md`.

## Open questions
None.

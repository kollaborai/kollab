# Sessionless setup panels

Issue: #1114, #1115  
Status: Implemented  
Owner: Kollab engine and Web UI

## Problem

On first run, the Web UI has no session. The setup guide opens Provider and Network settings, but the settings host was mounted only when a session or halt folder existed. Even if mounted, the engine exposed no setup panel routes without a session. Users therefore advanced through setup without seeing the requested settings.

## Goal and non-goals

Allow the first-run guide to open the Provider setup wizard and Network connect panel without an active session. Keep session-backed settings and halt-folder settings on their existing routes and behavior. Sessionless Settings also includes the Plugins tab; it reads the engine's plugin inventory without opening a session panel. This does not add a Hub service to the engine or enable arbitrary sessionless panels.

## What people see

- Provider Open opens Settings on the setup wizard, including the available provider controls and the ChatGPT sign-in note.
- Network Open opens Settings on the Network tab and shows the existing unavailable notice when no Hub is configured.
- Sessionless Settings includes the Plugins tab for the engine's plugin inventory and settings.
- The guide remains at its Network step after opening Provider settings.
- When saved provider state skips the Provider step, opening the resolved Network step advances to the Agent step.

## Contract

- The engine serves `GET /setup/panels/{name}` and `POST /setup/panels/{name}/actions/{action}` without requiring a session.
- Setup panel descriptions use the host `ProfileManager` with no LLM service. Unknown panel names return 404; a failed action returns 200 with `{ok: false, message}` per the shared panel contract.
- The setup context exposes only the setup wizard and connect picker. The connect panel uses its unavailable state when the engine has no Hub.
- With an empty session ID, setup panel requests use the `/setup/panels/*` route family. The Settings host mounts for a live session, halt waiter, or sessionless setup; a first-run tab strip contains Setup, Network and Plugins, while a halt-folder strip contains Network and Plugins. Network selects the connect panel; Plugins reads the engine plugin inventory.
- Owners: `packages/kollabor-engine/src/kollabor_engine/routes/panels.py`, `packages/kollabor-engine/src/kollabor_engine/server.py`, and the Web UI `App.tsx`, `api.ts`, `PanelHost.tsx`, and `panel-model.ts`.

## Scenarios

- If there is no session and the guide opens Provider, then the setup wizard is fetched and displayed.
- If there is no session and the guide opens Network, then the connect panel is fetched and displayed on the Network tab.
- If a live session opens settings, then the existing session-backed panel routes remain in use.
- If a halt waiter opens settings, then the existing halt settings behavior remains available.
- If a setup panel name is unknown, then the engine responds with 404.
- If a setup action fails, then the engine responds with 200 and `{ok: false, message}`, which the PanelHost displays as an action outcome.
- If the engine has no Hub, then the Network panel displays its unavailable notice.
- If the setup guide is open at 390px, then its bottom edge fully covers the staged setup action row beneath it.

## Failure modes

- No session ID: route setup panel requests through the sessionless setup endpoints.
- Unknown setup panel: return 404; do not construct an unrelated panel.
- Failed panel action: return 200 with `{ok: false, message}` and an empty `errors` mapping - an outcome, not a rejected request (see the contract note in Decisions).
- Missing Hub: describe the connect panel's unavailable notice rather than failing the settings host.
- Provider settings opened from the guide: keep the guide's current step at Network.
- Session-backed settings: preserve the existing session route selection.

## Decisions

- The engine builds setup context from the host configuration and `ProfileManager`, without an LLM service, matching the existing first-run profile creation path.
- Sessionless panel routes expose only the panels required by the first-run guide. The Plugins tab uses the plugin API directly and does not make other panels sessionless.
- The connect panel retains the established unavailable presentation when Hub support is absent.
- Contract note (status codes): the shared panel contract (`kollabor/panels/base.py`) reserves HTTP 400 for field-validation failures - a non-empty `errors` mapping the routes show per field. A failed action without field errors is an outcome: HTTP 200 with `{ok: false, message}`, which the PanelHost displays as the action's result. Returning 400 for every failure was considered and declined: it would break the shared contract and the UI's action-outcome message display, an unacceptable change for a patch release.

## Test plan

- `tests/unit/test_sessionless_setup_panels.py`: describe contract, wizard finish action, connect degradation, unknown panel, and failed action behavior.
- `packages/kollabor-webui/frontend/tests/panel-sessionless-setup.test.ts`: sessionless API routing, panel resolution, and tab selection.
- Recheck existing panel and halt tests, TypeScript checking, frontend unit tests, production build, and browser proofs at desktop, 820px, and 390px widths.
- Sibling surfaces: session-backed and halt-folder panel hosting, plus the setup wizard and connect panel.

## Docs and changelog fragment

- This spec records the implemented first-run setup contract.
- The feature PR's changelog fragment is `changes/1114.fixed.md`.

## Open questions

None.

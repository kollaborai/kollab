# Issue #263 browser verification

Captured on branch `issue-263-agent-list-order` at `270a0b0a` with headless
Google Chrome and the worktree-local frontend build.

The API was intentionally mocked. Chrome blocked requests to the live engine
(`127.0.0.1:7433`, including WebSocket) and API proxy (`127.0.0.1:8080`); the
mock supplied repeated local sessions for the same identity/workspace, an
offline overlap, a second workspace, and a remote network/device/workspace. It
recorded the order POST. No live engine or real agent was contacted.

Responsive checks used these viewports. Document/body width matched the viewport
at each size; workspace groups and remote device/workspace rows were visible.

390 x 844 phone viewport:

![390 x 844 sidebar with mocked API](sidebar-390.png)

820 x 1180 tablet viewport:

![820 x 1180 sidebar with mocked API](sidebar-820.png)

1280 x 900 desktop viewport:

![1280 x 900 sidebar with mocked API](sidebar-1280.png)

The second live Lapis session row exposes Move Up and Move Down independently
from the other same-identity rows; Move Down sent unique session keys plus the
persona/offline fallback anchor to the mock API:

![1280 x 900 duplicate local session order menu with mocked API](sidebar-order-menu-1280.png)

This is responsive UI and menu evidence only, not an end-to-end engine/browser
run. Engine persistence and stale-client behavior are covered separately by
`packages/kollabor-engine/tests/test_sidebar_order.py`, including a fresh OS
process reading the saved order through `GET /sessions`.

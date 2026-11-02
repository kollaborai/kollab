# Web UI hub cron jobs view

Issue: #612
Status: backend + tests landed; UI mockup awaiting approval (no UI code before approval)
Last updated: 2026-11-01

## Goal

Show scheduled hub cron jobs in the Web UI. Today the only view is
`/hub cron list` inside the agent's terminal. The Web UI gets a read-only
view of every schedule across the live agents on this machine.

## Architecture

Jobs stay owned by the agent process that scheduled them
(`HubPlugin._hub_cron_jobs`). Nothing about scheduling moves; the view is
served by two read-only hops:

1. Agent socket op (local operator only): the agent's unix socket answers
   `{"action": "cron_jobs"}` with
   `{"type": "cron_jobs", "jobs": [...], "error": ""}`. Remote hub peers
   are rejected: the action is absent from the remote allowlist, the same
   gate as `get_status`/`health_status`. Plugins: `plugins/hub/plugin.py`
   (`_cron_jobs_payload`), `plugins/hub/messenger.py`.
2. Engine aggregation: `GET /hub/cron-jobs` queries every live agent's
   socket and tags each row with the agent's identity. An agent that does
   not answer is reported in `agents_unavailable` and never blocks the
   others. Package: `packages/kollabor-engine/src/kollabor_engine/routes/hub.py`.

Row fields (epoch seconds; the renderer formats):

- `agent` (added by the engine), `id`, `target`, `message`
- `interval_seconds`, `interval` (human string), `next_fire`, `next_in_seconds`
- `recurring`, `mode` (`recurring` | `one-shot`), `created_at`, `last_error`

Task-ledger reminders are not cron jobs and never appear here (out of
scope per issue).

## UI decisions (recommendations, awaiting mockup approval)

- Placement: a Settings tab panel ("Hub cron jobs"), not the sidebar. The
  sidebar lists sessions and agents; a cross-agent schedule view is
  settings-shaped, like other read-only infrastructure views.
- Scope: all live agents on this machine, aggregated, with the agent name
  on every row. Per-agent filtering is a filter control on the same data.
- Mutations: read-only in this issue. Delete stays in the owning agent's
  terminal (`/hub cron delete`). Cross-agent destructive actions through
  the Web UI deserve their own issue and approval gate.

## Mockup

`webui-hub-cron-jobs-mockup.html` in this directory: standalone, no build
step, open it in a browser. Desktop shows the table; 390px stacks rows
into cards. Sample data is clearly marked as mock.

## Acceptance criteria (from the issue)

- Mockup approved before any UI code ships.
- Unit test for the socket op and the engine aggregation (landed:
  `tests/unit/test_hub_cron_jobs_view.py`,
  `tests/unit/test_engine_hub_routes_cron.py`).
- After UI lands (later commit): live proof at 390px and desktop widths,
  and a deleted job disappears from the view.

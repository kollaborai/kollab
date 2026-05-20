# Hub Cockpit PRD

## Objective

Expand hub status into a real read-only cockpit for multi-agent ownership and
delivery truth.

## User Impact

When a report is delivered, queued, rejected, or quarantined, the UI should say
which and why.

## Scope

- Build on `plugins/hub/plugin.py`, existing delivery trace, and pending reply
  data.
- Keep routing behavior unchanged.
- Add visibility for roster, coordinator, expected replies, stale endpoints,
  current assignments, delivery decisions, quarantine/reject counts, and trace
  path.
- Do not suppress join/idle visibility. The room should see agents arrive.

## Acceptance Criteria

- `/hub status` or the existing hub status surface includes cockpit sections.
- It works when the hub is disconnected or partially degraded.
- It reports pending replies, roster/coordinator, and recent delivery decisions.
- It exposes quarantine/reject state without dumping huge logs.
- Tests cover pending replies, delivery trace summary, quarantine/reject, and
  active/passive wake classification if touched.

## Validation

- Run hub unit tests and hub RPC integration tests.
- Run stabilization gate if hub status/render paths change.

## Deliverable

Open a PR from `codex/hub-cockpit` to `main` with a capture of `/hub status`.

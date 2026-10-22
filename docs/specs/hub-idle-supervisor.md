# Hub idle supervisor

## problem

Hub agents only act when a message or cron fires. A finished worker
reports to a coordinator that itself ends its turn, so open work could
sit for hours until an operator nudged someone (issue #211). Worse, a
coordinator that receives wakes but cannot complete a turn (provider
outage, context overflow, paused compaction) looks busy from the outside:
wakes keep "succeeding" as deliveries while nothing happens, and nobody
is told (issue #283, incidents of 2026-10-16 and 2026-10-20).

## goal

Open hub work never idles past the wake cooldown without a coordinator
wake, and a coordinator that stops completing turns is detected and
reported instead of silently re-woken forever.

## design

The supervisor runs on a poll loop inside the coordinator only
(`plugins/hub/supervisor.py`, wired in `plugins/hub/plugin.py`).
`evaluate` is a pure function of (ledger, roster, now, config, state) ->
decisions with one documented side effect: stalled cards past their
nudge grace are requeued on the ledger itself.

Decisions:

- `nudge_worker` — first stall of an active card whose worker is online;
  the nudge re-invokes the worker. A nudge that cannot be delivered is
  recorded as an alert (the card is requeued on a later pass).
- `requeue_stalled` — card still stalled after its nudge grace, or its
  worker offline: back to the open queue, unassigned. Blocked at
  `supervisor_max_reassignments` requeues; it is reported instead.
- `wake_coordinator` — open or stalled work exists and the coordinator
  is idle, throttled by `supervisor_wake_cooldown`. Delivered through
  the inbound pipeline the same way self-targeted cron does.
- `alert_coordinator_stalled` — wake efficacy failed: at least
  `supervisor_wake_inefficacy` wakes delivered since the coordinator's
  last **completed** turn, while the stall threshold has also passed.
  A failed turn does not count as life; its error text is kept and
  quoted in the alert. The wake still goes out alongside the alert — it
  is also the recovery path once the blockage clears (for example after
  compaction finishes). Alerts are throttled by
  `supervisor_alert_cooldown`.

Turn evidence comes from the display tap: `turn_complete` stamps
`last_turn_end` and resets the wake counter; `error` records the error
text. The loop stamps `last_turn_end` at boot so a fresh start needs
real stall time before alerting.

Surfaces (issue #283): the alert is logged at ERROR, appended to a
bounded alert list shown by `/hub status` (the supervisor section lists
open/stalled counts, wakes without a completed turn, and recent alerts —
shown while healthy too, not only on alert), published to attached web
clients as a `supervisor_alert` semantic event, and sent through the
configured notifier channel when one exists.

## configuration

`plugins.hub.*`: `supervisor_enabled` (true), `supervisor_poll_interval`
(10s), `supervisor_stall_threshold` (900s), `supervisor_wake_cooldown`
(120s), `supervisor_max_reassignments` (3), `supervisor_wake_inefficacy`
(3), `supervisor_alert_cooldown` (900s).

## tests

- `tests/unit/test_hub_supervisor_wake_path.py` — wake delivery, nudge
  routing, wake-efficacy alerting, alert cooldown, error quoting,
  undelivered-nudge alert, status section.
- `tests/unit/test_hub_idle_acceptance.py` — open work never idles past
  the wake cooldown.
- `tests/tmux/specs/hub_supervisor_status.json` — `/hub status` shows
  the supervisor section on a freshly booted coordinator.

## future considerations

- Goal awareness: `/goal` state lives in the engine per conversation and
  is not visible to the hub plugin; a goal-aware wake would cross that
  boundary. Deferred (tracked in issue #283).
- A dedicated Web UI banner for supervisor alerts: the semantic event is
  published; a frontend surface would render it.

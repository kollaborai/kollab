# Hub wake hooks

Opt-in rules that wake hub agents when something they wait on happens
(issue #727). Code: `plugins/hub/wake_hooks.py` (decisions) and
`HubPlugin.dispatch_wake_event` (delivery). Every hook is **off by default**.

| Hook | Trigger | Default action |
| --- | --- | --- |
| `review_ready` | A review verdict is posted on the current head | wake the PR's coder and the koordinator |
| `dependency_clear` | A blocking issue or PR is closed or merged | wake the agents whose lanes depend on it |
| `idle_nudge` | An agent idle past `thresholds.minutes` (15) while its lane has open work | nudge that agent |
| `auto_rereview` | A new commit lands on a PR with open findings | ask the configured reviewers to re-run the review on the new head |
| `undelivered_messages` | A hub message undelivered past `thresholds.minutes` (5) | wake the koordinator and the owner |
| `koordinator_heartbeat` | Heartbeat missed past `thresholds.minutes` (10) | log it and send the supervisor alert |

## Config

```json
{
  "plugins": {
    "hub": {
      "hooks": {
        "review_ready": {
          "enabled": true,
          "targets": ["@coder", "@koordinator"],
          "cooldown": 60,
          "max_per_hour": 20
        },
        "idle_nudge": { "enabled": true, "thresholds": { "minutes": 20 } },
        "auto_rereview": { "enabled": true, "targets": ["reviewer-agent"] }
      }
    }
  }
}
```

`targets` takes agent names or the tokens `@coder`, `@koordinator`, `@owner`,
`@dependents` and `@agent`, filled in from the event. `auto_rereview` has no
default target: name the reviewers or it does nothing. `koordinator_heartbeat`
with no targets still logs and sends the supervisor alert.

## Rules

- **Dedupe.** One action set per (hook, PR, head SHA, ref, agent). A new head is a new event.
- **Rate limits.** `cooldown` seconds between wakes of the same target; `max_per_hour` per hook.
- **No self-wakes.** A target never hears about an event its own action (`actor`) caused.
- **No chaining.** Wakes carry `metadata.wake_hook`; an event built from one must set
  `WakeEvent.from_hook`, and the engine drops it.

## Status

The decision rules, config and delivery are in place. **Nothing feeds events in yet**: the
GitHub review/merge/commit sources, the idle and undelivered-message detectors and the
heartbeat check must call `HubPlugin.dispatch_wake_event`. The idle-supervisor
(`plugins/hub/supervisor.py`) still runs its own stalled-card nudges and coordinator alerts
unchanged. Registering a hub job or changing required checks needs separate owner sign-off.

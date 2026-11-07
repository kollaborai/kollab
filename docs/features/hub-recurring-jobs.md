# Hub recurring jobs

Opt-in messages the hub coordinator sends to agents on a schedule (#728). Every
job ships **off**. Nothing here names a repository, a check or an agent: the
prompt, schedule and target of each job come from config.

| Job | What its default prompt asks the target to do |
| --- | --- |
| `koordinator_refresh` | Refresh its session and flag stalled lanes |
| `idle_sweep` | Wake idle agents whose lane still has open work |
| `re_review` | Ask for a fresh review of PRs with open findings and new commits |
| `kpi_rebuild` | Rebuild the KPI dashboard and artifact pages |
| `hub_heartbeat` | Check for undelivered messages and offline agents |

## Config

```json
{
  "plugins": {
    "hub": {
      "hub_jobs": {
        "hub_heartbeat": {
          "enabled": true,
          "schedule": "5m",
          "target": "koordinator",
          "prompt": "optional; replaces the default prompt",
          "max_messages_per_hour": 6
        }
      }
    }
  }
}
```

`target` is an agent name, `agent@device`, `all`, or a list of those.
`schedule` is `30m`, `1h`, `2h30m`; the minimum is 1 minute.
The `/config` modal has a toggle per job.

## Guardrails

- **Job rules.** Every message ends with "never merge, approve, close, push or
  restart"; a custom `prompt` cannot remove it.
- **Idempotent, with last-run state.** The last run and recent sends are kept in
  `hub_jobs_state.json` in the hub directory, so a restart neither fires a job
  early nor resets its cap. A newly enabled job first sends one schedule later.
- **No overlap.** A job whose previous run is still sending is skipped.
- **Rate limit.** `max_messages_per_hour` caps one job's sends over a rolling
  hour; extra targets in a run are skipped and logged.
- **Coordinator only.** Only the coordinator runs the jobs, so peers never
  duplicate them. Sends go out as `hub-cron` through the same path as
  `hub_cron_add`.

A failed send is logged and not retried before the next schedule.

Code: `plugins/hub/recurring_jobs.py`, `HubPlugin._run_hub_jobs`.
Tests: `tests/unit/test_hub_recurring_jobs.py`.

# Schedules (hub cron jobs): web UI, sync and firing

Issue: #612 · PR: #653
Status: design approved for build (mockup v16). Built so far: the read-only
socket view and engine aggregation below. Everything under "Requirements"
and "Design" is planned, not built.
Last updated: 2026-11-09

## Goal

A schedule sends a prompt to an agent on a fixed interval, and the agent
does the work. Schedules are managed from a Schedules tab in Settings (web)
and `/schedules` (TUI), are saved, sync to every device on the network, and
keep working when a device drops off and comes back.

## Requirements

1. Firing wakes the agent and sends it the prompt; the agent works on it.
2. An agent that is not running is started.
3. A busy agent: the schedule picks one of two behaviors.
   - Add to its queue (default): the prompt runs right after the current
     turn, in the same chat.
   - Open a new instance: a second instance of the same agent runs it now.
4. An agent is one identity. Lapis is one agent with one name and one
   memory; a new instance exists only for that run.
5. Schedules are saved and survive restarts.
6. Schedules sync to every device joined to the network.
7. A schedule whose device is off the network waits and shows Waiting. It
   is never deleted for that reason. When the device is back it runs once,
   then continues on its normal times.
8. Timing is correct: no drift, no burst of missed fires, no double fires
   across devices, no blind 10-second polling.
9. Every fire is recorded (time, result, error) and shown as Last run.

## Today (code on main)

- Jobs live in memory in the agent that made them
  (`HubPlugin._hub_cron_jobs`, `HubCronJob` in `plugins/hub/plugin.py`) and
  are lost when that agent stops.
- `_cron_loop` checks every 10 s. After a fire, `next_fire = now +
  interval_seconds`, so times drift by up to the check interval each run.
- A fire is a hub message from `hub-cron` (`[cron <id>] <message>`) routed
  with `_route_message`; delivery wakes the target like any hub message.
- A failed fire sets `last_error` and is skipped; the next try is one full
  interval later.
- If the target device is gone (`_cron_device_gone`) the job is deleted.
- Config sync (`plugins/hub/config_sync.py`, `config_sync_service.py`)
  already pushes config overrides, `agents/`, `skills/`, MCP servers and API
  keys from the primary device to every joined device, sealed, checked every
  `POLL_SECONDS` (10 s) and resent on reconnect. Schedules are not part of it.

## Design

### Storage

- One file, `~/.kollab/schedules.json`, owned by the daemon, written
  atomically through `kollabor_config.file_lock`.
- Row: `id`, `agents` (list of `name` or `name@device`), `prompt`, `skill`
  (optional), `interval_seconds`, `anchor` (epoch of the first fire),
  `active`, `when_busy` (`queue` | `new_instance`), `created_by`,
  `created_at`, `updated_at`, `revision`.
- Run records (not synced): `~/.kollab/schedules-runs.jsonl`, one line per
  fire with `id`, `agent`, `due`, `fired_at`, `result`, `error`. Last run and
  the error on a card read from here.

### Sync

- `schedules.json` joins the config sync snapshot as its own part, same
  seal and same push path as `agents/` and `skills/`.
- Writes go to the primary. An edit on a secondary is sent to the primary
  as a change request; the primary applies it, bumps `revision`, and the
  normal push sends it to every device. Last write by revision wins.
- A secondary that is offline keeps its last copy and keeps firing its own
  schedules from it.

### Who fires

- Each schedule fires only on the device its agent runs on (the `@device`
  part, or this device for a bare name). Other devices only show it.
- On that device one process owns the timer: the hub coordinator (the
  `flock` holder on `hub.lock`). Agents do not run timers of their own, so
  two agents in one folder never fire the same schedule twice.

### Timing

- Fire times sit on a grid: `anchor + k * interval_seconds`. The next due
  time is the first grid point after the last fire.
- The timer sleeps until the earliest due time across all schedules. A
  change to the file (local edit or a sync) wakes it early to recompute. No
  fixed-interval polling.
- On start, wake from sleep, or reconnect: if one or more grid points were
  missed, fire once now, then continue from the next grid point after now.

### Delivering a fire

- Agent not running: start it with its bundle, folder and loadout
  (`kollab --agent <name> --detached` through `kollabor.binary.kollab_argv()`),
  then deliver.
- Agent idle: deliver as a hub message that wakes it (today's path).
- Agent busy (`is_processing`):
  - `queue`: deliver into its queue; it runs after the current turn, in the
    same chat.
  - `new_instance`: start a second instance of the same agent identity for
    this run only; it shares the vault and exits when the run is done.
- The message carries the schedule id so the reply and run record line up.

### Offline and failures

- Target device off the network: the schedule shows Waiting (gray text),
  nothing fires, nothing is deleted. Back online (relay session reconnect
  signal): the catch-up rule above applies.
- Failed delivery on a reachable device: retry with backoff (30 s, 1 m,
  2 m, ...) until the next grid point; then record the miss. The error
  shows in red text on the card until a fire succeeds.

## Web UI (mockup v16)

`webui-hub-cron-jobs-mockup.html`, frames f1 to f8, B1, A1, I1, L1, T1 to T7.
Renders: `webui-hub-cron-jobs-mockup-renders/`.

- Settings tab "Schedules": list on the left (agent, how often, prompt,
  green Active or gray Paused, red error line), detail on the right
  (prompt, Every / Next / Last run / Skill, Pause, Edit, Delete).
- Top row: Refresh, Import file, Generate with agent, Add schedule.
- Form: prompt, skill, Runs in (one or more agents), If the agent is busy
  (Add to its queue / Open a new instance), Every, Active.
- No dropdown menus for actions, no toggle switches, no borders; black
  surfaces; buttons stack 2 by 2 on a phone.

## Built so far (read-only view)

1. Agent socket op (local operator only): `{"action": "cron_jobs"}` returns
   `{"type": "cron_jobs", "jobs": [...], "error": ""}`. Remote peers are
   refused (not in the remote allowlist). `plugins/hub/plugin.py`
   (`_cron_jobs_payload`), `plugins/hub/messenger.py`.
2. Engine aggregation: `GET /hub/cron-jobs` asks every live agent's socket
   at once, tags rows with the agent, reports `agents_unavailable` and
   `agents_errors`. `packages/kollabor-engine/src/kollabor_engine/routes/hub.py`.
3. `CronJobsPanel.tsx`: the first read-only tab. It is replaced by the v16
   Schedules tab.

Tests: `tests/unit/test_hub_cron_jobs_view.py`,
`tests/unit/test_engine_hub_routes_cron.py`,
`frontend/tests/cron-jobs-panel.test.tsx`.

## Build order

1. Storage + timer in the coordinator (grid, sleep-until, catch-up, run
   records). Unit tests with a fake clock.
2. Delivery: start, queue, new instance. Live test on real agents.
3. Sync part in config sync + change requests from secondaries.
4. Engine routes: list, create, update, pause, delete, import.
5. Web UI Schedules tab per v16; TUI `/schedules`.
6. Proof in Docker: three devices; take one off the network for two
   intervals, bring it back, check exactly one catch-up fire and no
   duplicates on the others.

## Acceptance

- Every requirement above has a test, and requirements 6 to 8 have the
  Docker proof.
- Web UI checked at 390 px, 820 px and desktop.

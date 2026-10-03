---
title: Unified Config Panels for the Web UI (/config, /llm, /model, /setup, /connect)
created: 2026-10-02
status: implemented (in progress)
author: maintainers
updated: 2026-10-02
---

the web ui (`kollab --web-ui`) can't change settings. the terminal's
fullscreen screens (`/config`, `/llm`, `/model`, `/setup`) can't be reached from
the browser, and typing them in the browser chat hangs the turn. this spec adds
one daemon-owned **panel** layer that both front ends use. every write goes
through the same functions the terminal already calls. it also fixes a
terminal bug found during review: a saved setting never reaches the other
processes that are running.


## problem

### 1. fullscreen commands hang the browser (verified live 2026-10-02, 0.11.1)

engine on a test port (:7533), one session, `POST /sessions/{id}/message` per
command:

| command    | result                                | daemon log                                    |
|------------|---------------------------------------|-----------------------------------------------|
| `/version` | reply + `turn_complete` in 20 ms      | n/a                                           |
| `/config`  | no reply, no `turn_complete` (10 s+)  | `AltViewStackManager: pushed 'config'`        |
| `/model`   | same hang                             | `pushed 'model-picker' (depth=2)`             |
| `/llm`     | same hang                             | `pushed 'loadout-list' (depth=3)`             |
| `/setup`   | same hang                             | `pushed 'setup' (depth=4)`                    |

bare `/connect`, `/connect code` and `/connect knocks` push fullscreen views
too (`plugins/hub/plugin.py:9267-9289`). they hang the same way (confirmed in
code; phase 0 re-runs them live). the browser chat itself posts to `/sessions/{id}/assistant` (`api.ts:296`),
not `/message`. phase 0 proves the fix on both routes.

root cause: browser input runs on the terminal slash-command path
(`LocalStateService._execute_slash_command`, `kollabor/state/local.py:2653`).
these handlers push a fullscreen AltView, and `AltViewStackManager.push()`
blocks until a user exits the view
(`packages/kollabor-tui/src/kollabor_tui/altview/stack_manager.py:91`). a
detached daemon has no terminal, so the push never returns. none of the 15
`push()` call sites check its return value (for example `system.py:281-287`
returns success with an empty message).

### 2. the Settings button reaches none of `/config`

the browser's **Settings** dialog (`SessionToolbar.tsx:645`; the sidebar
opens it by clicking that button through the DOM, `App.tsx:502-506`) only
edits session-only profile/model/effort overrides. it reaches none of the 157
`/config` fields: 20 sections, of which 72 are checkbox, 56 slider, 14
dropdown, 12 text_input, 2 spinbox and 1 label.

### 3. saved config never reaches other processes (confirmed in code; phase 1 reproduces it live first)

- `ConfigService` hot reload depends on `watchdog`. no `pyproject.toml`
  declares it and the uv-tool install doesn't have it, so
  `_start_file_watching` always returns early
  (`packages/kollabor-config/src/kollabor_config/service.py:526-530`).
- the dormant watcher is also broken: `on_modified` runs on watchdog's thread
  and calls `asyncio.get_running_loop()`, which always raises there, so it
  falls back to a synchronous reload off the event loop. it also ignores
  every change within 2 s of its own write, including changes made by other
  processes.
- in the default daemon mode, terminal `/config` runs in the **attach
  client** (`kollabor_tui/input/command_mode_handler.py:919`) on the client's
  shadow config. the daemon that owns the LLM never reloads, so saved LLM,
  tool and hub settings don't apply until a restart.
- the engine runs one daemon per web session (`daemon_pool.py:395-448`).
  without propagation, a save in one session leaves every other session on
  stale values.


## goals

1. the Settings button shows the same settings as terminal `/config`: same
   sections, fields, help text, search, managed (read-only) rows and Project
   or Global save.
2. `/llm`, `/model`, `/setup` and `/connect` work in the browser.
3. **one write path.** every config save, from the browser or a terminal in
   any process, goes through `apply_config_changes()`. every running process
   picks the value up within about 1 s.
4. **reusable.** a screen is one panel class. adding a fourth (for example
   `/mcp` or `/permissions`) needs no new routes or React components.
5. no slash command can hang a browser turn again.

## non-goals

- `/login` (OAuth) in the browser. setup's ChatGPT-OAuth choice shows "run
  /login in a terminal".
- editing or deleting a provider profile's API key or base URL in the web.
  `ProfilesDialog.tsx` + engine `/profiles` keep doing that for now (see
  follow-ups).
- rewriting the terminal AltViews. they move onto the shared functions but
  keep their rendering and keys.
- a general `set_config(path, value)` RPC.


## design

```
browser ──HTTP──> engine ──state.* RPC──> daemon: LocalStateService
  PanelHost                 get_panel / panel_action    └─ kollabor/panels/{config,llm,setup}.py
  ├ FieldRenderer                                          └─ shared functions, also called by
  ├ PickerList                                                ConfigAltView / LoadoutAltView /
  └ WizardSteps                                               ModelPickerAltView / SetupAltView

every process: ConfigService polls its config files' mtimes (1 s, on-loop) → reload → callbacks
```

### config propagation: replace the dead watcher with an on-loop mtime poll

- delete the `watchdog` import, `ConfigFileWatcher`, the observer and the 2 s
  self-write window from `kollabor_config/service.py`.
- add one asyncio task per `ConfigService` that runs once an event loop
  exists. every 1 s it `stat`s every config file the loader merges (global and
  project-local, including ones that don't exist yet). if an mtime changed,
  it calls `reload()` and `_notify_reload_callbacks()` on the loop.
- `save_key` records the mtime it just wrote, so a process's own saves never
  trigger its own reload, while another process's write is always seen.
- no new dependency and no thread hop. this fixes problem 3 for terminal
  attach clients, daemons, and the web sessions of every other engine.

### panel protocol (`kollabor/panels/`)

```python
class Panel(Protocol):
    name: str                       # "config" | "llm" | "model" | "setup"
    kind: str                       # "form" | "picker" | "wizard"
    async def describe(self, ctx, params: dict) -> dict: ...
    async def act(self, ctx, action: str, payload: dict) -> dict: ...

PANELS: dict[str, Panel]            # key == slash command name
```

`ctx` is the daemon's `LocalStateService`. config is reached through
`ctx._llm_service.config`; `LocalStateService` has no config service of its
own (`local.py:192-196`). panels keep no state between calls.

**`/llm` and `/model` stay separate panels, like in the terminal.** `/llm`
chooses a loadout (provider + preset). `/model` changes the model on the
active provider and calls the function the terminal picker calls:
`_set_active_profile_model` (`model.py:562`). that function persists the
model on the active profile, handles OpenRouter tool support and announces
the switch on the hub. effort goes through `ModelCommandHandler._handle_effort`
(`model.py:600`). two functions are **not** used for `/model`:
- `_switch_to_model`: it partial-matches profile names and fails for catalog
  models off OpenRouter (`model.py:728-751`).
- `LoadoutManager.activate`: it also applies a loadout's temperature, effort
  and max_tokens overrides.

### wire format

field (all kinds), the widget dict from `ConfigWidgetDefinitions` plus its
current value:

```json
{ "path": "terminal.render_fps", "type": "slider", "label": "...", "help": "...",
  "value": 20, "min_value": 1, "max_value": 60, "step": 1, "options": null,
  "placeholder": null, "editable": true, "managed_by": null,
  "secret": false, "is_set": true }
```

`type` is one of `checkbox | slider | spinbox | dropdown | text_input | label`.
`editable` is false for `label` rows, for the loadout rows that
`_with_loadout_rows` injects, and for managed paths.

```jsonc
// form (config; llm New/Edit)
{ "panel": "config", "kind": "form", "title": "System Configuration",
  "sections": [{ "id": "s0", "title": "Terminal Settings", "fields": [ ... ] }],  // index ids
  "actions": [{ "id": "save", "label": "Save", "targets": ["local", "global"] }],
  "save_targets": { "local": "<workspace>/.kollab/config.json", "global": "~/.kollab/config.json" } }

// picker (llm, model)
{ "panel": "llm", "kind": "picker", "title": "Loadouts", "scope_note": "...",
  "rows": [{ "id": "opus", "label": "opus", "detail": "anthropic · claude-opus-5-5",
             "group": "anthropic", "current": true, "badges": ["default"] }],
  "row_actions": [{ "id": "activate", "label": "Use" }, { "id": "edit" }, { "id": "delete", "confirm": true }],
  "toolbar_actions": [{ "id": "new", "label": "New Loadout" }, { "id": "refresh", "label": "Refresh Catalog" }],
  "controls": [ /* model panel only: Effort dropdown field */ ],
  "empty_groups": [{ "group": "openrouter", "reason": "..." }] }

// wizard (setup)
{ "panel": "setup", "kind": "wizard", "title": "Setup",
  "steps": [{ "id": "provider", "title": "Provider", "fields": [ ... ] }, ...],
  "actions": [{ "id": "test", "label": "Test Connection" }, { "id": "finish", "label": "Save and Activate" }] }
```

every `act()` returns
`{ "ok": bool, "message": str, "errors": {path: str}, "panel": <fresh describe()> | null, "open": <panel> | null }`.

### panels → shared functions

| panel | describe() | act() |
|-------|------------|-------|
| config | `ConfigWidgetDefinitions.get_config_modal_definition()` + loadout rows + managed overlay. **extract** `build_config_sections()` from `ConfigAltView._load_widgets`, `_with_loadout_rows` and `_managed_label` (`plugins/altview/config_altview.py:177-311`) | `save {changes, target}` → **extract** `apply_config_changes()` from `ConfigAltView._do_save` (`:823`): per change `config_service.set` → `save_key(path, v, save_target)`, then `_notify_reload_callbacks()`. drop `_do_save`'s `kollabor.llm.active_profile` → `llm.switch_profile` branch; that path is a read-only label now, so the branch is dead. `ConfigAltView._do_save` calls the same function |
| llm | **extract** `_build_sections` (incl. zero-row groups), `_empty_note`, `_suggest_name`, `_default_max_tokens`, `build_active_profile_base` from `plugins/altview/loadout_altview.py` (`:181-446`, `:788`) on top of `LoadoutManager.list_loadouts()` | `activate` → `LoadoutManager.activate(name, event_bus)`; `new` / `edit` → form → `create` / `update`; `delete`; `set_default(name, level)`; `refresh` → `refresh_catalogs()` |
| model | the active provider's models from the same source as `ModelPickerAltView`: models.json seed + `list_provider_models()` (`_fetch_catalog`, `model_picker_altview.py:127`). **extract** that list builder; the AltView calls it | `select {model}` → `_set_active_profile_model`; `effort {level}` → `_handle_effort` |
| setup | `PROVIDERS` (`setup_altview.py:60`) and `_STEPS` (`:147`) | `test` → **extract** `test_connection()` from `SetupAltView._run_test` (`:487`; 25 s bound). `finish` → **extract** `create_and_activate_profile()` from `_run_save` (`:549`): unique name, `pm.create_profile(..., save_to_config=True)`, `set_active_profile(name, persist=True, reload_profile=True)`. provider error text goes through the redactor in `kollabor_ai/providers/security.py` |

"extract" means the logic moves into `kollabor/panels/` and the AltView calls
it. the terminal and the browser then run the same code.

### network panels (`/connect`)

the terminal's `/connect` screens, all in `plugins/altview/connect_altview.py`:
- the Connect screen (`ConnectScreenAltView`): network, trust, this device,
  agents, join requests to accept or reject, and a join code with a countdown.
- the private join-code form (`ConnectAltView`).
- the first-launch guide (`ConnectGuideAltView`).
- the introductions review behind `/connect knocks`.

an attached terminal already drives all of these through daemon `state.*`
methods. the web panels call **those same methods**, so there is no new
network logic.

| panel | kind | describe() | act() |
|-------|------|------------|-------|
| `connect` | picker | `hub_connect_snapshot()` → `summary` rows (network, trust, this device, relay online, local/remote agents, offline devices) + one row per join request (`device`, `fingerprint`). a read-only window shows the snapshot's note and offers no actions. with no network, the empty state offers the guide's two choices (`connect_guide.CHOICES`) | `accept` / `reject {enrollment_id}` → `hub_connect_decide`; `new_code` → `hub_enrollment_offer(domain)` → `reveal`; `new_network` → **extract** `start_new_network()` from `_guided_new_network` (`plugin.py:9464`), without its screen push; `rename {name}` / `trust {level}` → `hub_connect("name …")` / `hub_connect("trust …")`; `join` → opens `connect-join`; `knocks` → opens `connect-knocks` |
| `connect-join` | wizard | steps: domain (default `kollabor.ai`), code (`secret`) | `finish` → `hub_enroll(domain, code)` → receipt + `poll`; `join_status {receipt_id}` → `hub_enroll_status` every 2 s (`_POLL_SECONDS`, `connect_altview.py:47`) until approved / connected / rejected / error. outcome text comes from `JOIN_FAILURE_REASONS` |
| `connect-knocks` | picker | `hub_contact_pending(domain)` | `allow` / `deny` → `hub_contact_decide` |

each one is just another entry in `PANELS`, which tests the "no new routes"
promise. the protocol gains three generic pieces, built once in `PanelHost`
and usable by any panel:
- `summary`: read-only label/value rows above a picker.
- `reveal`: a one-time secret in an action response (`{label, value,
  expires_at, status}`), shown with a countdown.
- `poll`: `{action, payload, every_s}` in an action response, re-sent until
  the panel reports a final state.

### rpc + routes

- `StateService.get_panel(name, params)` and
  `StateService.panel_action(name, action, payload)`: add to `interface.py`,
  `local.py`, `remote.py` and `handlers.py`. `panel_action` uses a 40 s
  per-call timeout because `RemoteStateService.DEFAULT_TIMEOUT` is 10 s
  (`remote.py:57`) and the setup test alone can take 25 s.
- engine `routes/panels.py`:
  - `GET  /sessions/{id}/panels/{name}`: query params carry only
    non-sensitive filters (`provider`). wizard values never go in a URL.
  - `POST /sessions/{id}/panels/{name}/actions/{action}`: every payload goes
    in the body.
  - after any action, refresh the session's profile/model mirror. extract a
    helper from `POST /sessions/{id}/profile` (`routes/sessions.py:424-435`)
    so the toolbar and session list never show a stale model.
  - errors: unknown panel → 404, validation → 400 + `errors`, daemon down →
    502 (409 is already "Session daemon is not running",
    `sessions.py:410`).
- `list_commands()` (`local.py:934`) adds `"panel": "<name>"` to entries in
  `PANELS`.

### phase 0: stop the hang

1. `AltViewStackManager.push()` raises `AltViewUnavailable` right away when
   the process has no interactive terminal (`app.pipe_mode` or
   `args.detached`, `application.py:875`). this is safe for attach mode,
   because an attached TUI runs slash commands on the client side.
2. `_execute_slash_command` catches it and finishes the turn with one line:
   - commands in `PANELS`: "/config opens in Settings → Configuration
     in the web UI". `/connect`, `/connect code` and `/connect knocks` point to
     Settings → Network.
   - every other command: "/matrix needs the terminal UI".

   no per-command list is needed. a command with arguments (`/model set X`,
   `/model effort high`, `/llm <name>`, `/llm default`) never pushes a view,
   so it keeps working as it does today.
3. no new SSE event. the browser opens panels itself (see web ui), so the
   daemon reply is only a fallback for curl, scripts and other clients.

### web ui (`packages/kollabor-webui/frontend/src/components/panels/`)

| component | job |
|-----------|-----|
| `PanelHost.tsx` | Dialog on desktop, full-height Sheet below 768px. fetches the panel, keeps dirty values on the client, sends actions, re-renders from the returned panel. its open state lives in `App.tsx`, so both Settings buttons call one setter (this replaces the DOM-click workaround) |
| `FieldRenderer.tsx` | checkbox → Switch · slider → Slider + number readout · spinbox → number Input · dropdown → Select · text_input → Input · `editable: false` → read-only row · `managed_by` → "Managed by X" badge · `secret` → password Input that is never prefilled and shows "Set" / "Not set"; empty means unchanged, clearing is its own button |
| `PickerList.tsx` | search, grouped rows, current marker, row and toolbar actions, `empty_groups`, optional `controls`, `summary` rows and a read-only `notice` |
| (in `PanelHost`) | `reveal` (one-time secret + countdown; no copy button, matching the terminal's private-view rule) and `poll` (re-send an action until the panel reports a final state) |
| `WizardSteps.tsx` | step header, `FieldRenderer` per step, Back / Next / Test Connection / Save and Activate |

- **tabs on the Settings button:**
  - **Session**: today's override form. labeled "This session only, not
    saved".
  - **Configuration**: the config panel.
  - **Loadouts**: the llm panel. labeled "Saved to the provider profile;
    applies to every session using it".
  - **Model**: the model panel. labeled "Saved to the active provider
    profile; applies to every session using it".
  - **Network**: the connect panel. labeled "This device's agent network".
- **composer + slash menu:** a bare `/config`, `/llm`, `/model`, `/setup` or
  `/connect` opens `PanelHost` on its tab and posts no text. so do
  `/connect code` (Network, with a new code) and `/connect knocks`
  (introductions). anything else with arguments is sent as normal. the
  daemon still refuses `/connect <code>` and removes the code from history.
- **search** matches section title, label, help and path, the same as
  `ConfigAltView._visible_sections` (`:313`).
- **save footer:** "Save to Project" / "Save Globally" with the real file
  path, a dirty count, and buttons disabled while saving.
- **copy:** "Project" and "Global" everywhere user-facing; the terminal's L/G
  keys stay. Title Case labels. there was one duplicate section title: the
  tmux plugin's "Terminal Settings" is now "Terminal Sessions" (fixed in
  `plugins/terminal_plugin.py`).
- **ui kit:** add the two shadcn primitives missing from `components/ui`:
  `slider` and `tabs`. `switch`, `select`, `dialog` and `sheet` already
  exist, and `radix-ui` is already installed.

### security

- **whitelist.** `save` accepts only paths whose field is `editable`
  (checkbox, slider, spinbox, dropdown, text_input) in the current
  `describe()`. labels, injected loadout rows, managed paths and unknown
  paths get 400.
- **validate on the server:** bools, min/max/step, dropdown options, strings
  up to 4 KB.
- **managed keys are rejected on the server**
  (`kollabor_config.managed_config.managed_by`), not only disabled in the ui.
- **secrets are never sent to the browser.** there is one shared
  `is_secret_path()`: true when the widget def says `"secret": true` or the
  last path segment ends in key/token/secret/password. it extends the
  existing `_is_sensitive_field` (`kollabor_ai/providers/security.py`)
  instead of adding a third rule, and `ConfigAltView`'s managed rows use it
  too. (the suffix fix there, so `token_threshold_k` is not treated as a
  secret, is done but uncommitted.)
- **no secrets in URLs or logs:** wizard values travel only in POST bodies,
  and provider errors are redacted.
- **save target:** `local` = the daemon's cwd = the session workspace
  (`daemon_pool.py:441-448`). the ui shows the path before saving.
- **auth:** same engine bearer token as every `/sessions/*` route.
- **join codes are one-device credentials.**
  - an issued code appears only in the `new_code` action's response body.
    `describe()` never mints one.
  - an entered code appears only in the `connect-join` POST body.
  - codes never go in a GET, a URL, SSE, conversation history or
    localStorage. panel responses carry `Cache-Control: no-store`.
  - the browser drops the code from component state on close and on expiry.
- **engine logs.** give the engine's log handler (`kollabor_engine/__main__.py:24-38`)
  the same `JoinCodeRedactionFilter` the app uses. move it and
  `redact_join_codes` (`kollabor/logging/setup.py`) into a package both
  import. the engine has no request-body or access logging today
  (`access_log=False`), but the join form will send codes through it.
- **typed codes.** `/connect ABCD-EFGH` typed into the web chat used to be
  refused but saved, code included, to conversation history. fixed
  2026-10-02 (uncommitted): `_execute_slash_command` records `/connect` lines
  through `redact_join_codes`; test in
  `tests/unit/test_web_slash_join_code_redaction.py`.


## phases

| # | scope | size | exit criteria (runtime proof, not just tests) |
|---|-------|------|-----------------------------------------------|
| 0 | `AltViewUnavailable` + reply in `_execute_slash_command` | ~2 h | the problem-1 table re-run through `/message` **and** `/assistant` (real browser): each bare command answers and completes in under 1 s; `/model effort high` and `/llm <name>` still work; stack depth stays 0. tmux spec in **daemon mode** (no `--no-daemon`): attached `/config` still opens |
| 1 | mtime poll replaces the watcher; `kollabor/panels/` core; config panel; `apply_config_changes` / `build_config_sections` extraction; RPC + routes | ~5 h | first reproduce problem 3 live on main (terminal `/config` save in daemon mode → daemon still on the old value), then after the fix the daemon logs `Config reloaded` within 2 s. curl: GET the config panel, POST save Global → file diff, the owning daemon reloads **and** a second session's daemon reloads. unit: poll, whitelist, managed reject, secret redaction. green: `test_config_altview_*`, `tests/unit/config/test_config_service.py`, tmux `regression_config_altview_save_false.json` |
| 2 | `PanelHost` + `FieldRenderer` + Settings tabs + composer/slash interception + ui primitives | ~4 h | real browser at **390px and 820px**, no horizontal overflow: edit a slider, Save Globally, the value persists and the terminal `/config` shows it; secret fields carry no value in any network response; a dirty field survives a tab switch |
| 3 | llm + model pickers (both on `PickerList`; Effort on the model panel) with the loadout and model-list extraction | ~5 h | green: `test_loadout_altview.py`, `test_loadout_catalog_merge.py`, `tests/tmux/specs/loadout_*.json`. live: Use a loadout, then pick a model, in the browser → each next turn's raw API log has that model, and the toolbar shows it without a reload |
| 4 | setup wizard; `test_connection` / `create_and_activate_profile` extraction | ~4 h | **first-run** with a throwaway `HOME` and no providers: the browser reaches setup. if session creation fails there, add a session-less `POST /panels/setup/actions/{action}` that runs the same functions in the engine. test passes with a real key; the profile is created and active; `tests/unit/plugins/test_setup_altview.py` green |


| 5 | network panels (`connect`, `connect-join`, `connect-knocks`), `summary` / `reveal` / `poll`, `start_new_network()` extraction, engine log filter | ~5 h | `summary` matches `/connect status` in a terminal on the same device. issue a code in the browser, then confirm it appears nowhere else: conversation JSONL, daemon log, engine log, localStorage and network GETs (grep each). join, accept and reject run against a throwaway `HOME` on a test network, never a live one. a read-only window shows info only. green: `tests/unit/test_hub_enrollment_commands.py` and `tests/unit/test_connect_*.py` |

## verification (done gate)

- **unit:** each panel's describe and act; route tests with
  `KOLLAB_ENGINE_BYPASS_AUTH`; `KOLLAB_NO_KEYRING=1` everywhere.
- **tmux:** existing config regression specs, plus one attach-mode spec. the
  existing specs run `--no-daemon` and never cover the path where terminal
  saves actually run.
- **live browser:** `kollab --web-ui`; each tab at 390px and 820px.
- **regressions:** `/version`, `/help` and `/mcp` still answer in the
  browser; the Session tab still applies without persisting; the terminal
  `/config`, `/llm`, `/model` and `/setup` behave as before.
- **truthfulness:** the ui renders saved values from the action's response
  and never assumes success.


## follow-ups (not in this spec)

- a provider-profile panel (edit API key / base URL, delete). only then
  retire `ProfilesDialog.tsx` and the engine `/profiles` write routes, plus
  `tests/integration/test_profiles_api.sh`,
  `tests/tmux/specs/engine-profiles-api.json`,
  `packages/kollabor-engine/tests/test_review_fixes.py` and the engine
  README.
- terminal `/config` shows editable secret fields (`plugins.hub.bridge_token`,
  `notify_telegram_token`) in plain text. mask them with `is_secret_path()`.


## deviations (as built)

what the code does where it differs from the design above.

1. **a ContextVar sink carries the refusal, not only the exception.** `SlashCommandExecutor.execute_command` and the command handlers turn every exception into a failure result, so `AltViewUnavailable` never reaches `_execute_slash_command`. `AltViewStackManager.push()` and `FullScreenManager.launch_plugin()` (so `/matrix` and every other fullscreen plugin) append the view's name to `kollabor_tui.altview.stack_manager.unavailable_attempts` (a `ContextVar[list | None]`) and then raise. `LocalStateService._run_web_slash_command` sets it around the command and answers from it. A web-originated command refuses fullscreen views even in a process that owns a terminal.
2. **"no terminal" is pipe mode or `--detached`/`-d` in `sys.argv`.** `interactive_terminal_available(renderer)` checks `renderer.pipe_mode is True` and argv (a `ponytail:` comment marks the shortcut; the upgrade is an explicit flag the app registers). Attach clients and a plain TUI stay interactive. Side effect to watch: a first-run wizard launched inside a detached process now raises instead of blocking, and the wizard's `except Exception` in `application.py` then saves `setup_completed`.
3. **`open` carries the other panel's full `describe()`**, not just its name. An action that needs input (llm New/Edit, connect join and knocks, setup) returns the next form or wizard inline and the frontend renders it with a Back button.
4. **there is a Setup tab.** The spec listed none. `/setup` (aliases `/onboard`, `/wizard`) opens Settings → Setup; the label lives in `PANEL_LOCATIONS["setup"]`.
5. **a read-only network is 403.** A window whose daemon lost the relay to another window answers connect actions with 403 and a plain-language note. 409 stays "Session daemon is not running"; 503 is hub or profile manager unavailable; 502 is daemon unreachable; 404 is an unknown panel, row or action.
6. **mtime poll, not watchdog.** `ConfigService` polls the config files' mtimes once a second on the loop and calls `reload()` (which notifies callbacks). watchdog is not installed in the daemon environment, so before this the daemon never saw a save from another process.
7. **action payload contract** (the frontend's `PanelHost.tsx` and the panels agree on exactly this):
   - row actions: POST `actions/{action.id}` with `{"id": row.id, [action.payload_key]: row.id}`. A row action whose panel reads a key other than `id` declares `payload_key` (llm `name`, model `model`, connect `enrollment_id`, knocks contact id); panels also accept `id`.
   - picker controls (`controls` fields): POST `actions/{field.action or field.path}` with `{field.path: value}`. effort is `{"path": "level", "action": "effort", "type": "dropdown"}`, connect trust is `{"path": "level", "action": "trust"}`, rename is `{"path": "name", "action": "rename"}`.
   - form actions: `{"changes": {path: value}, "target"?: "local"|"global"}`. A cleared secret arrives as `""`; untouched secrets are absent. A form's top-level `context` object is echoed back unchanged as `"context"` (llm New/Edit use it).
   - wizard actions: `{"values": {path: value}, "step": stepId}`; `finish` resets the draft.
   - toolbar actions: `{}`. One that needs input returns `open`.
   - `summary` is a list of `{label, value}`; `notice` is a string; an optional per-row `actions: [ids]` limits which row actions show; `scope_note`, when present, replaces the tab's static scope line; `list_commands()` entries carry `panel` for config/llm/model/setup/connect.
   - errors: 400 with top-level `{"message", "errors"}`; `PanelError.status` passes through.
8. **the engine routes were not on this branch when this was written.** `routes/panels.py` and `apply_profile_mirror` are still to land; the daemon RPC (`StateService.get_panel` / `panel_action`) is in.

## review log

adversarial review 2026-10-02 (Opus subagent, read-only, 67 tool calls, 19
findings). the maintainer re-checked every BLOCKER and MAJOR against the code
before changing this spec. changes from v1:

- **added problem 3 + the mtime poll.** this replaces v1's `revision`/409
  scheme, which could never fire.
- **kept `/model` separate from `/llm`.** the reviewer suggested merging
  them; rejected, because the terminal keeps them separate and the web
  `/model` must call the same function the terminal picker calls. fixed v1's
  `select` mapping (`_switch_to_model` → `_set_active_profile_model`).
- **dropped the `open_panel` SSE event.** `/assistant` would have dropped it
  (`messages.py:690-850` forwards only listed event types); the browser
  intercepts instead.
- **phase 0:** one typed exception instead of a per-command list, and bare
  commands only.
- **whitelist:** editable fields only. fixed the description of `_do_save`.
- **engine:** 40 s panel RPC timeout; session mirror refresh after actions;
  wizard values never in URLs; errors redacted; no 409 reuse.
- **llm extraction:** lists the actual functions and tests.
- **phase 5 cut.** retiring `/profiles` would have removed the only web path
  for editing provider keys.
- **scope labels** on the model-changing tabs; first-run setup criterion;
  index-based section ids; duplicate section title fixed.
- **added after review (2026-10-02):** `/connect` as the Network panels
  (phase 5), plus the history redaction fix for typed join codes.
- **factual fixes:** search covers section titles; `PROVIDERS`/`_STEPS`
  lines; `persist=True`; how config is reached; the test port labeled.

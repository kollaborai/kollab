# Handoff: replace the kollab web UI with assistant-ui

You are taking over a migration. Read this whole document before touching code.

## The job

Delete the hand-rolled web UI in `packages/kollabor-webui/src/kollabor_webui/static/`
(`app.js` ~1900 lines + `index.html` ~1400 lines) and replace it with
[assistant-ui](https://github.com/assistant-ui/assistant-ui), wiring it to the existing
kollab engine backend.

The reference checkout is already at `~/dev/assistant-ui` (HEAD `ece5a5422`). Read it
rather than guessing at its API — it moves fast and the docs lag.

**Do not touch the daemon architecture.** The backend below took a full session to get
right and is verified working. Your job is the presentation layer plus one new endpoint.

## Why assistant-transport is the integration path

assistant-ui has several runtimes. For this backend the right one is
**assistant-transport**, because:

- our backend is Python/FastAPI, and assistant-ui ships an official Python library
  (`assistant-stream`, on PyPI, v0.0.34 — source at `~/dev/assistant-ui/python/assistant-stream`)
- it is designed for a custom backend that owns conversation state, which ours does
  (the daemon owns history; the engine is a proxy)
- its `add-tool-result` command gives us a native home for permission prompts
  (see "Permission prompts" below) — this is the piece that has no analog in the
  other runtimes

Reference implementations to copy from:
- backend: `~/dev/assistant-ui/python/assistant-transport-backend/main.py`
- frontend: `~/dev/assistant-ui/examples/with-assistant-transport/app/MyRuntimeProvider.tsx`

Do not use `react-data-stream`, `ExternalStoreRuntime`, or the AI-SDK runtimes. They
either assume an AI-SDK-shaped backend or make you reimplement state sync by hand.

## What the backend already is

Read `docs/` and the code, but the short version:

Every engine session owns a real headless `kollab --detached` daemon — the same process
the terminal client forks. The engine is a thin proxy over it:

```
browser → kollabor-engine (FastAPI, :7433) → unix socket → kollab daemon (full TUI stack)
```

Key files:

| file | role |
|---|---|
| `packages/kollabor-engine/src/kollabor_engine/daemon_pool.py` | spawns/tracks/reaps one daemon per session; fans out its events |
| `packages/kollabor-engine/src/kollabor_engine/session.py` | `EngineSession` — proxy; `state` property is the daemon's 41 `state.*` RPC methods |
| `packages/kollabor-engine/src/kollabor_engine/routes/messages.py` | `POST /sessions/{id}/message` (SSE), `GET /sessions/{id}/events` (SSE), `POST .../cancel` |
| `packages/kollabor-engine/src/kollabor_engine/routes/permissions.py` | `POST .../permission`, `GET .../permissions` |
| `packages/kollabor-engine/src/kollabor_engine/routes/sessions.py` | session CRUD, history, system prompt, per-session MCP |
| `packages/kollabor-engine/src/kollabor_engine/sse.py` | the event vocabulary (single source of truth) |
| `packages/kollabor-tui/src/kollabor_tui/display_tap.py` | `publish_semantic()` — where daemon events originate |

The daemon publishes structured events alongside its ANSI output. Event types you will
receive, defined in `sse.py`:

`token`, `thinking`, `tool_start`, `tool_result`, `permission_request`,
`permission_granted`, `permission_denied`, `turn_complete`, `error`, `question_gate`

## The mapping

Add **one new endpoint**: `POST /sessions/{session_id}/assistant`, alongside (not
replacing) the existing SSE routes. Keep the old ones until the new UI is proven — they
are what the current UI and any scripts use.

```python
from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
```

Subscribe to the session's event queue exactly like `routes/messages.py` does
(`session.subscribe()` / `session.unsubscribe(queue)`), then translate:

| daemon event | RunController call |
|---|---|
| `token` | `controller.append_text(event["text"])` |
| `thinking` | `controller.append_reasoning(event["text"])` |
| `tool_start` | `tc = await controller.add_tool_call(event["tool_name"], event["tool_id"])` then `tc.append_args_text(json.dumps(event["input"]))` |
| `tool_result` | `tc.set_result({...})` on the controller you kept for that `tool_id` |
| `error` | `controller.add_error(event["message"])` |
| `turn_complete` | stop consuming; put usage into `controller.state` |

`RunController` API (verified in `create_run.py`): `append_text`, `append_reasoning`,
`append_state_text`, `add_tool_call` (async → `ToolCallController`), `add_tool_result`,
`add_stream`, `add_data`, `add_error`, `add_source`, `add_file`, `add_annotations`,
`add_step_start`, `add_step_finish`, `state` (a synced proxy — assignments stream to the
client as patches), `cancelled_event`, `is_cancelled`.

`ToolCallController`: `append_args_text`, `set_result`.

**Keep a `dict[tool_id, ToolCallController]` for the duration of a run** — `tool_start`
and `tool_result` arrive as separate events and you need the same controller for both.

### Commands from the client

The request body is `{commands: [...], state, tools, system}`. Handle two:

- `add-message` → `await session.send_message(text)`; it returns
  `{"accepted": bool, "reason": str}` and returns *on acceptance, not completion*.
  If `accepted` is false, surface `reason` (`"turn already in flight"` is a real case).
- `add-tool-result` → this is a **permission answer**. See below.

### Cancellation

`controller.is_cancelled` / `await controller.cancelled_event.wait()` → call
`await session.state.cancel_current_request()`. Do not just drop the stream; the daemon
keeps running.

## Permission prompts — read this twice

This is the part with no obvious analog, and getting it wrong wedges the daemon.

When the daemon wants to run a risky tool it **blocks** and emits `permission_request`.
Nothing proceeds until an answer arrives. If your UI drops the prompt, that session is
dead: every subsequent message returns `"turn already in flight"` forever.

Map it onto assistant-ui's human-in-the-loop:

1. On `permission_request`, emit a tool call named something like
   `request_permission`, with the prompt payload as its args (`tool_id`, `tool_name`,
   `risk_level`, `risk_reason`, `input`).
2. Render it with a custom tool UI (`makeAssistantToolUI`) showing approve/deny.
3. The user's choice comes back as an `add-tool-result` command.
4. Translate that into `await session.resolve_permission(tool_id, decision, scope)`.

`decision` is `"approve"` or `"deny"`. `scope` is one of `once | session | project |
always_edits | trust_tool`. The mapping onto the daemon's `ConfirmationResponse` names
lives in `session.py::_confirmation_response_name` and **fails closed** — anything
unrecognized denies, an unknown scope narrows to `APPROVE_ONCE`. Keep that property;
there is a test on it (`tests/unit/test_permission_tool_metadata.py`).

### Reload recovery is mandatory

`GET /sessions/{id}/permissions` returns `pending_prompts` — full prompt payloads for
anything still blocking. On mount, if a session has pending prompts you must re-render
them and follow `GET /sessions/{id}/events` to pick the turn back up. The daemon will
**not** re-send the request.

This is already implemented in the old UI (`restorePendingPermissions` in `app.js`) —
read it before you delete it, then port the behaviour.

## Everything else the UI has to keep

The current UI is ugly but it does real work. Do not lose:

- **session list + create** — `GET/POST/DELETE /sessions`. Creating a session **spawns a
  process and takes ~10 seconds** (plugin discovery + hub join). Show a real pending
  state; do not let it look like a hang. A warm daemon pool is the eventual fix and is
  out of scope here.
- **profiles** — `GET /profiles`. The create-session form picks one.
- **approval mode** — `POST /sessions/{id}/permissions/mode`, values `confirm_all |
  default | auto_approve_edits | trust_all`.
- **MCP per session** — `GET /sessions/{id}/mcp`, connect/disconnect, tools.
- **hub** — `GET /hub/agents`, `POST /hub/messages`. Web sessions are real mesh agents
  named `web-<id>`; they show up in `kollab --hub status`.
- **history** — `GET /sessions/{id}/history`, `DELETE` to clear.
- **cancel** — `POST /sessions/{id}/cancel`.

## Auth

Every engine route except `/health`, `/version`, `/status`, `/ready`, `/` needs
`Authorization: Bearer <token>`. The browser gets it from the webui server's
`/api/config`, which reads `~/.kollab/engine.token`.

**Restarting the engine rotates that token.** On a 401, re-fetch `/api/config` and retry
once — a cached token otherwise shadows the live one permanently. The old client does
this in `EngineAPI.refreshTokenFromServer`; keep the behaviour.

Use `useAssistantTransportRuntime`'s async `headers` callback for the bearer token.

## Packaging decision — flag this, do not silently pick

`kollab` ships as a Python package (`pip install kollab`) and FastAPI currently serves
two static files. A Next.js app is a different shape. Two options:

1. **Static export served by FastAPI** *(recommended)* — `next build` with static export,
   output committed/built into `kollabor_webui/static/`, FastAPI serves it as today.
   assistant-transport needs no server-side Next features, so this should work. Keeps
   `pip install kollab` self-contained with no node runtime.
2. **Separate Next server** — nicer DX, but now shipping kollab means shipping node.

Confirm with Marco before committing to either. If (1), the frontend source lives at
`packages/kollabor-webui/frontend/` and the build output is what ships.

Versions in the reference checkout: `@assistant-ui/react` 0.15.2, `assistant-ui` CLI
0.0.108, `create-assistant-ui` 0.0.71, `assistant-stream` (python) 0.0.34.

## Traps that cost me real time

Every one of these is a bug I hit and fixed. Do not re-introduce them.

1. **`kollab --detached` double-forks.** The process you spawn is a launcher that exits
   `0` as soon as the real daemon detaches. A zero exit is *success*. The real pid comes
   from hub presence. (`daemon_pool.py`)
2. **Hub presence is cached.** `HubBridge.get_agents()` caches; polling for a daemon that
   starts *after* the snapshot will never see it. Pass `use_cache=False`. This one
   silently broke every session create.
3. **`turn_complete` must fire after tools run**, not in the stats block mid-turn. It
   gates end-of-run; emitting early truncates the stream before the first tool.
   (`queue_processor.py`)
4. **A failed spawn must still reap the daemon.** Record the pid the moment presence
   reports it, or a failed create leaves an orphan process holding a hub identity.
5. **Never run `ruff check --fix` or `black` across a directory in this repo.** It is a
   shared checkout — hub agents hold uncommitted work in the same tree at all times.
   Scope every autofix to the exact files you edited.
6. **Permission answers block a live daemon.** Test deny as well as approve; I only ever
   verified approve.
7. **The daemon owns its stdio.** `--detached` does `setsid` + `dup2` on fds 0/1/2. That
   is process-wide, which is why one process cannot host two sessions.

## Definition of done

Do not report done without live evidence for each of these. Drive a real browser; tests
passing is not sufficient.

- [ ] create a session from the UI (expect ~10s), send a message, see tokens stream
- [ ] a turn with a tool: `tool_start` → args → `tool_result` → assistant text →
      run ends, with a correct tool count
- [ ] permission prompt renders, **approve** completes the turn
- [ ] permission prompt renders, **deny** is handled and does not wedge the session
- [ ] reload mid-prompt: prompt is restored, answering it completes the turn
- [ ] two concurrent sessions in two tabs, no cross-talk (never tested — I only ever ran one)
- [ ] cancel mid-turn stops the daemon's turn
- [ ] deleting a session reaps its daemon (`ps` shows no `--as web-` orphan)
- [ ] `python -m pytest tests/unit/ -q` green (2716 passed / 60 skipped at handoff)
- [ ] `ruff check` clean on files you touched

## Current state at handoff

- backend: done and verified live, including tool turns and the full permission round trip
- old UI: works, is ugly, and is what you are replacing
- test engine on `:7435`, webui on `:8082` (`8080` is taken by docker on this machine)
- `2716 passed, 60 skipped`
- known-unverified: deny path, concurrent sessions, long multi-tool chains

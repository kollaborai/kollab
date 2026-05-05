# kollabor-engine

`kollabor-engine` is the local HTTP/SSE runtime for Kollabor.

It wraps the same core AI, tool, permission, profile, MCP, and hub pieces used by
the terminal app, but exposes them through a FastAPI service. The intended use is
a local companion daemon for `kollabor-webui`, browser-based control surfaces,
and other trusted local clients.

This package is not yet a production multi-user service. It currently relies on
in-memory sessions and process-global runtime state, so treat it as a per-user
local service bound to `127.0.0.1`.

## Current Role

- Headless HTTP API for creating chat sessions and sending turns.
- SSE streaming for model tokens, thinking content, tool events, permission
  prompts, and turn completion metadata.
- Local profile management over the existing Kollabor profile store.
- Session-owned permission manager and MCP integration.
- Hub mesh read/control endpoints for active local agents.
- Web UI backend for `kollabor-webui`.

## Architecture

The engine is a thin service layer over other workspace packages:

| Package | Engine Use |
|---|---|
| `kollabor-ai` | profiles, provider calls, streaming, tool-call parsing |
| `kollabor-agent` | tool execution, MCP connections, permissions |
| `kollabor-events` | per-session event bus and permission hooks |
| `kollabor-config` | profile/config compatibility |
| `kollabor-rpc` | hub-adjacent RPC foundations |

Main engine modules:

| Module | Responsibility |
|---|---|
| `server.py` | FastAPI app, auth middleware, global session registry |
| `session.py` | `EngineSession`, per-session AI/tool/permission state |
| `turn_runner.py` | multi-step LLM turn loop and SSE event production |
| `sse.py` | event payload builders |
| `routes/sessions.py` | session lifecycle, history, system prompt, session MCP |
| `routes/messages.py` | message stream and cancellation |
| `routes/permissions.py` | permission responses and approval mode |
| `routes/profiles.py` | profile CRUD and connectivity checks |
| `routes/mcp.py` | global MCP server config CRUD |
| `routes/hub.py` / `routes/hub_ws.py` | hub mesh REST and WebSocket views |

## Usage

Start the local service:

```bash
python -m kollabor_engine serve --host 127.0.0.1 --port 7433
```

On startup, the service writes a bearer token to:

```text
~/.kollab/engine.token
```

Use that token for protected endpoints:

```bash
TOKEN="$(cat ~/.kollab/engine.token)"

curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:7433/sessions
```

Programmatic app creation:

```python
from kollabor_engine import create_app

app = create_app()
```

## API Surface

Health and metadata endpoints:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | unauthenticated liveness check |
| `GET` | `/version` | package and Python versions |
| `GET` | `/status` | session count, providers, MCP status |
| `GET` | `/ready` | profile/API credential readiness |

Session and message endpoints:

| Method | Path | Notes |
|---|---|---|
| `POST` | `/sessions` | create a session |
| `GET` | `/sessions` | list sessions |
| `GET` | `/sessions/{session_id}` | inspect one session |
| `DELETE` | `/sessions/{session_id}` | shut down and remove a session |
| `GET` | `/sessions/{session_id}/history` | read conversation history |
| `DELETE` | `/sessions/{session_id}/history` | clear history, preserving system prompt |
| `POST` | `/sessions/{session_id}/message` | send one turn; returns SSE |
| `POST` | `/sessions/{session_id}/cancel` | cancel active turn |

Permissions:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/sessions/{session_id}/permissions` | current approval mode and pending tools |
| `POST` | `/sessions/{session_id}/permission` | approve or deny a pending tool |
| `POST` | `/sessions/{session_id}/permissions/mode` | set approval mode |

Profiles, MCP, and hub routes:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/profiles` | list configured profiles |
| `POST` | `/profiles` | create profile |
| `GET` | `/profiles/{name}` | inspect profile |
| `PUT` | `/profiles/{name}` | update or rename profile |
| `DELETE` | `/profiles/{name}` | delete profile |
| `POST` | `/profiles/{name}/test` | test provider connectivity |
| `GET` | `/mcp/servers` | list global MCP server config |
| `POST` | `/mcp/servers` | add global MCP server config |
| `PUT` | `/mcp/servers/{server_name}` | replace server config |
| `DELETE` | `/mcp/servers/{server_name}` | remove server config |
| `GET` | `/sessions/{session_id}/mcp` | session MCP connection status |
| `POST` | `/sessions/{session_id}/mcp/{server_name}/connect` | intended session connect endpoint |
| `POST` | `/sessions/{session_id}/mcp/{server_name}/disconnect` | disconnect session server |
| `GET` | `/hub/agents` | list active hub agents |
| `GET` | `/hub/feed` | snapshot SSE feed |
| `WS` | `/ws/hub/feed` | polling WebSocket feed |

## Known Gaps

These are current implementation gaps to address before treating the engine as a
reliable service boundary:

- Profile read/update responses must redact API keys and other secret fields.
- Session `workspace` is stored but not yet enforced for terminal and file tools.
- Provider instances are singleton per provider type, which can leak model,
  base URL, headers, or credentials across sessions.
- The session MCP connect endpoint currently reports `connecting` but does not
  start a connection.
- Anthropic profile testing imports a missing legacy `APIService`.
- Shutdown cleans up sessions, but provider-registry and background watcher
  lifecycle still need a first-class lifespan model.

## Roadmap

### Phase 1: Make the local daemon trustworthy

- Redact profile secrets from all profile API responses.
- Enforce session workspace for shell, file, and MCP tool execution.
- Replace provider-type singletons with config-scoped or session-scoped provider
  instances.
- Implement real session MCP connect behavior and add tests that prove tools are
  registered after connection.
- Fix `/profiles/{name}/test` to use current provider APIs.
- Add targeted tests for the findings above.

### Phase 2: Harden service lifecycle

- Replace deprecated FastAPI `on_event` startup/shutdown hooks with lifespan
  management.
- Move the module-level session registry behind an explicit service object.
- Add provider-registry shutdown and WebSocket watcher cleanup to app shutdown.
- Add concurrency guards around session mutation, message turns, permission
  resolution, and history/system-prompt changes.
- Normalize error payloads and request/response schemas.

### Phase 3: Clarify service boundaries

- Decide whether engine owns tool execution directly or wraps the existing CLI
  runtime as a client-facing API.
- Add a project/session namespace model so multiple workspaces can run without
  cwd or hub-presence ambiguity.
- Define which endpoints are local-only, trusted-client-only, or safe for
  broader exposure.
- Document compatibility expectations for `kollabor-webui` and external
  clients.

### Phase 4: Productize the web/hub path

- Finish live hub event streaming beyond snapshot polling.
- Add richer permission scopes and UI feedback over SSE.
- Persist recoverable session metadata where useful.
- Add smoke tests that start engine + web UI against a temporary config home.

## Development

Run package tests against the local source tree:

```bash
PYTHONPATH=packages/kollabor-engine/src:packages/kollabor-ai/src:packages/kollabor-agent/src:packages/kollabor-events/src:packages/kollabor-config/src:packages/kollabor-rpc/src:packages/kollabor-tui/src:packages/kollabor-plugins/src \
  python -m pytest packages/kollabor-engine/tests -q
```

Without the `PYTHONPATH` override, this checkout may import an installed
`kollabor_engine` from `site-packages` instead of the local package.

## Dependencies

- `kollabor-ai`
- `kollabor-agent`
- `kollabor-events`
- `kollabor-config`
- `fastapi >= 0.115`
- `uvicorn[standard] >= 0.32`
- `sse-starlette >= 2.1`

## License

MIT

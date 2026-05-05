# kollabor-webui

`kollabor-webui` is the browser UI shell for `kollabor-engine`.

It serves the static web app, proxies engine configuration to the browser, and
connects to the local engine API for sessions, SSE message streams, permission
prompts, profiles, MCP, and hub controls.

## Current Role

- Serve the single-page web terminal UI from `kollabor_webui/static`.
- Connect browser sessions to a local engine at `KOLLAB_ENGINE_URL`.
- Provide multi-session panels, message streaming, cancellation, and export.
- Surface permission prompts and approval mode changes.
- Provide basic profile, MCP, and hub control screens around engine endpoints.

## Architecture

| Module/Asset | Responsibility |
|---|---|
| `__init__.py` | `kollabor-webui` command entrypoint and uvicorn launcher |
| `server.py` | FastAPI app serving config and static assets |
| `static/index.html` | web UI shell |
| `static/app.js` | engine API client, SSE parser, session UI, controls |
| `static/style.css` | visual styling for the web terminal |

## Usage

Start the engine:

```bash
python -m kollabor_engine serve --host 127.0.0.1 --port 7433
```

Start the web UI:

```bash
KOLLAB_ENGINE_URL=http://127.0.0.1:7433 \
KOLLAB_WEBUI_PORT=8080 \
kollabor-webui
```

Then open:

```text
http://127.0.0.1:8080
```

For an editable checkout without installed console scripts:

```bash
python -c "from kollabor_webui import main; main()"
```

## Engine Endpoints Used

- `POST /sessions`
- `GET /sessions`
- `DELETE /sessions/{session_id}`
- `POST /sessions/{session_id}/message`
- `POST /sessions/{session_id}/cancel`
- `POST /sessions/{session_id}/permission`
- `POST /sessions/{session_id}/permissions/mode`
- `GET/POST/PUT/DELETE /profiles...`
- `GET/POST/PUT/DELETE /mcp/servers...`
- `GET/POST /sessions/{session_id}/mcp...`
- `GET /hub/agents`
- `POST /hub/messages`
- `WS /ws/hub/feed`

## Known Gaps

- The UI depends on engine behavior that is still being hardened, especially
  workspace enforcement, MCP connect behavior, and profile redaction.
- The current visual style is an early terminal-like shell, not yet a polished
  product UI.
- Error handling is mostly client-side string display; richer typed engine
  errors would improve UX.
- WebSocket hub streaming still depends on engine polling behavior.

## Roadmap

### Phase 1: Track engine hardening

- Update UI flows after engine profile redaction, workspace enforcement, and real
  MCP connect behavior land.
- Improve permission prompt state for timeout, denial, retry, and scopes.
- Add smoke coverage for session creation, message SSE, permissions, and MCP
  controls against a temporary engine config.

### Phase 2: UX maturity

- Replace one-off UI strings with structured state and reusable view components.
- Improve profile/MCP forms and validation feedback.
- Add clearer connection/token setup and recovery flows.

### Phase 3: Hub and multi-session polish

- Use live hub events once the engine stream is no longer snapshot/polling based.
- Add richer agent status, output, and message controls.
- Persist useful browser-side preferences without storing secrets.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-webui/src/kollabor_webui/*.py
python -m pytest tests/test_engine_health_api.py -q
```

## Dependencies

- `kollabor-engine`
- `fastapi >= 0.115`
- `uvicorn[standard] >= 0.32`

## License

MIT

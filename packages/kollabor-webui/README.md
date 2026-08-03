# kollabor-webui

`kollabor-webui` is the browser UI shell for `kollabor-engine`.

It serves the assistant-ui single-page app, proxies engine configuration to the
browser, and connects to the local engine API for sessions, assistant-transport
streaming, permission prompts, profiles, MCP, and hub controls.

## Architecture

| Module/Asset | Responsibility |
|---|---|
| `src/kollabor_webui/__init__.py` | `kollabor-webui` command entrypoint and uvicorn launcher |
| `src/kollabor_webui/server.py` | FastAPI config endpoint, Vite assets, and SPA fallback |
| `frontend/src/` | assistant-ui React/TypeScript source |
| `frontend/dist/` | local Vite output (ignored; regenerated for package builds) |
| `src/kollabor_webui/static/` | legacy static fallback and packaged assistant-ui output |

The checked-in frontend source is the source of truth.  `hatch_build.py` runs
`npm ci` and `npm run build` when npm is available, then force-includes Vite's
`frontend/dist` output in a wheel under `kollabor_webui/static/assistant`.  The
source checkout also serves `frontend/dist` directly, which makes editable
installs useful without copying generated files into git.  Python-only builds
continue to serve the legacy `static/index.html` and `static/app.js` fallback
if Node/npm is unavailable.

## Usage

Quickest path — `kollab --web-ui` spawns the engine and this package together
(reusing an already-running engine on 7433 if one is healthy) and cleans both
up on Ctrl+C or SIGTERM:

```bash
kollab --web-ui
```

To run the two pieces separately (useful when iterating on one of them):

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

## Frontend development

```bash
cd packages/kollabor-webui/frontend
npm ci
npm run typecheck
npm run build
npm run dev
```

Vite proxies `/api`, `/sessions`, `/profiles`, `/mcp`, and `/hub` to the local
engine while developing.  The production build uses relative asset paths, so
FastAPI can serve hashed assets from any mount path and route client-side
navigation back to `index.html`.

## Engine endpoints used

- `POST /sessions`
- `GET /sessions`
- `DELETE /sessions/{session_id}`
- `POST /sessions/{session_id}/assistant`
- `POST /sessions/{session_id}/cancel`
- `POST /sessions/{session_id}/permission`
- `GET /sessions/{session_id}/permissions`
- `POST /sessions/{session_id}/permissions/mode`
- `GET/DELETE /sessions/{session_id}/history`
- `GET/POST/PUT/DELETE /profiles...`
- `GET/POST/PUT/DELETE /mcp/servers...`
- `GET/POST /sessions/{session_id}/mcp...`
- `GET /hub/agents`
- `POST /hub/messages`

## Authentication

The browser gets the current engine bearer token from `/api/config`, which
reads `~/.kollab/engine.token`.  Frontend requests retry once after a 401 by
refreshing that config, covering engine restarts that rotate the token.

## Validation

```bash
python -m py_compile packages/kollabor-webui/src/kollabor_webui/*.py
python -m pytest tests/unit/test_webui_auth_wiring.py -q
cd packages/kollabor-webui/frontend
npm run typecheck
npm run build
```

## License

MIT

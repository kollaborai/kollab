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

## Interface

- **Sidebar**: New Session starts a session with the current picks in one
  click; the options button next to it opens Agent, Gem, Model and Workspace.
  Each session row shows its gem as a live 3D avatar that acts out what the
  agent is doing (thinking, typing, searching, messaging, error).
- **Chat**: pill composer with attachments, a `/voicemode` mic, and a
  send/stop button; tool calls show while they run and keep their duration.
- **Trajectory**: every turn, request and tool call as a Table, or as a
  Waterfall built on `@assistant-ui/react-o11y` (one block per turn, tools
  under their model request, each turn on its own time scale).
- **Settings**: daemon-owned panels, see `docs/features/web-settings-panels.md`.
- **Gem Studio** (sidebar footer): every agent is born with a random look (eyes
  and hat) that sticks; dress any gem over it (eyes, hat, color) and set the
  season for all, previewed live. Random Look rolls a new outfit, Reset goes
  back to the born look. The engine keeps the looks, so every browser and every
  gem in the app (session rows, chat, Who Is Online) follows.

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

To open it from another device, such as a phone on your VPN, list the extra
addresses in `KOLLAB_WEBUI_HOSTS` (comma-separated; it also works with
`kollab --web-ui`). Pages opened there reach the engine through the web UI's
`/engine` proxy, and they get the engine token, so list private addresses only:

```bash
KOLLAB_WEBUI_HOSTS=10.8.0.1 kollab --web-ui
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

The app is not wrapped in `<StrictMode>`: its dev-only double render breaks
`@assistant-ui/store` 0.3.2 and the composer drops typed text under `npm run
dev` (production is unaffected). Restore it with the assistant-ui 0.15.25
upgrade. `src/dev/gem-lab.html` previews every gem, activity, hat and season.

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
- `GET /agents` (gem pool: colors, live state, hub agent ids)
- `GET/PUT /agents/appearance` (Gem Studio looks)
- `GET /hub/agents`
- `POST /hub/messages`

Session rows keep `session_id` as the API key and expose a separate `name` for
display. Kollab-generated timestamped names display as their slug; opaque UUIDs
receive a stable two-word label.

## Authentication

The browser gets the current engine bearer token from `/api/config`, which
reads `~/.kollab/engine.token`.  Every request waits for that first config
load, and retries once after a 401 by refreshing it, covering engine restarts
that rotate the token.

## Validation

```bash
python -m py_compile packages/kollabor-webui/src/kollabor_webui/*.py
python -m pytest tests/unit/test_webui_auth_wiring.py -q
cd packages/kollabor-webui/frontend
npm run typecheck
npm run build
node --test tests/*.test.ts
```

## License

MIT

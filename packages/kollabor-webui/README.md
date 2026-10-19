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
installs useful without copying generated files into git. When `frontend/` holds a
change newer than that build, `kollabor-webui` runs `npm run build` before it serves
(and `kollab --web-ui` does too when the UI is already running), so the UI it serves
is never older than its source.  Python-only builds
continue to serve the legacy `static/index.html` and `static/app.js` fallback
if Node/npm is unavailable.

## Interface

- **Sidebar**: New Session starts a session with the current picks in one
  click; the options button next to it opens Agent, Type, Model and Workspace.
  Agent is a persona with its picture, name and job title, grouped by roster:
  the chosen Workspace's roster first, then every other roster's personas
  (they start as `roster/name`). Picking one sets its Type, the agent bundle it
  runs. The list is the chosen Workspace's: every folder has its own hub, so an
  agent or koordinator running in another folder is free here and "In Use"
  means in use in that folder, whichever roster it came from (a folder inside
  a git repo shares the repo's hub). It starts with koordinator while the
  folder runs none (a terminal makes the first agent in a folder its
  koordinator) and says when each gem last ran there ("Ran 3d Ago", from its
  vault). Workspace is a folder picker:
  the engine's folder, recent folders with their live agents, a folder
  browser, or a typed path.
  Each session row shows its agent under its display name: a live 3D gem that
  acts out what the agent is doing (thinking, typing, searching, messaging,
  error), or the persona's picture, emoji or initials when its roster has no
  gems.
- **Agents**: the sidebar's Agents button lists every roster and team (bundled,
  yours and the chosen folder's own) with each persona's details and the files
  that failed to load. Launch Team starts a team into a folder, its lead first;
  a member already running there is skipped.
- **Agents by computer**: the sidebar lists every agent under the computer it
  runs on. This computer comes first: its chats and every agent running in a
  terminal or as a daemon, each one click from a full chat (the web UI only
  attaches to an agent it did not start, so it offers no Delete). Agents that
  ran in this folder and are not running follow as offline; a click starts one
  as itself, and its vault brings back what it knew. Then each network this
  computer is on (folders here can be on different ones), with its other
  computers. The engine keeps every agent each network has listed for a month
  (`~/.kollab/engine-network.json`): one that left shows offline with when it
  was last seen, and a network no agent here carries now shows its last
  roster. Opening one of its agents then first starts the koordinator of that
  network's folder to carry it. One click opens one as a full chat, through the network, as that
  computer's trust allows: under `open` (the default) every computer of your
  network, under `agents` only the agents it lets this one message, under
  `manual` none.
- **Hidden agents**: Hide Agent in a row's menu takes it off the sidebar in
  every window that uses this engine (Kollab.app, browsers, the phone); the
  footer's Hidden Agents list shows it again. The engine keeps the list
  (`~/.kollab/hub/hidden-agents.json`) and sends it with `GET /sessions` as
  `hidden`. A running agent is hidden as the process it is (its `instance`), so
  one launched later under the same name shows.
- **Multi-network (#158)**: when this device belongs to more than one network,
  the sidebar nests them — NETWORKS > DEVICES > WORKSPACES > AGENTS — with
  the devices and workspaces of every membership (`network.networks` on
  `GET /sessions`). A CONTACTS section lists peers this device accepted from
  another network's rooms (a knock); it is display-only: nothing is permitted
  with a contact until `/connect allow` says so. Engines without the
  aggregate render the flat single-network view exactly as before.
- **Chat**: pill composer with attachments, a `/voice` mic, and a
  send/stop button; tool calls show while they run and keep their duration.
  Messages from other agents show as messages from their gems ("Aquamarine →
  Lapis"), the way the terminal draws its hub boxes; one the agent only
  overheard is dimmed. Turns the page did not run (a hub message that woke the
  agent, a turn typed in the terminal) appear without a reload. A failed turn
  shows its error under your message, also after a reload.
- **Trajectory**: every turn, request and tool call as a Table, or as a
  Waterfall built on `@assistant-ui/react-o11y` (one block per turn, tools
  under their model request, each turn on its own time scale).
- **Settings**: daemon-owned panels, see `docs/features/web-settings-panels.md`.
- **Gem Studio** (sidebar footer): every agent is born with a random look (eyes
  and hat) that sticks; dress any gem over it (eyes, hat, color) and set the
  season for all, previewed live. Random Look rolls a new outfit, Reset goes
  back to the born look. The engine keeps the looks, so every browser and every
  gem in the app (session rows, chat, Who Is Online) follows. Each agent has its
  own look: the studio's list dresses the gems of the folder the web UI runs in,
  and an agent of the same name in another folder or on another computer is born
  with its own eyes, hat and color (dress it from its chat's Properties).
- **Session Properties**: right-click a session row (long-press on a phone) or
  double-click its gem to dress that one gem in place. The Chat tab takes the
  agent off the hub (your other agents stop seeing it; your chat keeps working)
  or puts it back, with no restart. `/hub leave` and `/hub join` do the same in
  the terminal.

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
- `GET /sessions` (with `offline`, agents that ran in the engine's folder, and `network`)
- `DELETE /sessions/{session_id}`
- `POST /sessions/{session_id}/assistant`
- `POST /sessions/{session_id}/cancel` (Stop; a closed assistant stream never cancels the turn)
- `POST /sessions/{session_id}/permission`
- `GET /sessions/{session_id}/permissions`
- `POST /sessions/{session_id}/permissions/mode`
- `GET/DELETE /sessions/{session_id}/history` (with `last_turn_error`, why the last turn failed)
- `GET/POST/PUT/DELETE /profiles...`
- `GET/POST/PUT/DELETE /mcp/servers...`
- `GET/POST /sessions/{session_id}/mcp...`
- `GET /agents` (gem pool: colors, live state, hub agent ids, `last_ran`)
- `GET /workspaces`, `GET /workspaces/browse?path=` (New Session's folder picker)
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

Apache-2.0

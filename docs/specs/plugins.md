# Plugins for the web UI and the backend

Issue: #1028  
Status: Approved (2026-11-14, by the owner, with mockups of every surface)  
Owner: maintainers

## Problem

Kollab's terminal can be extended with plugins. The web UI and the engine cannot, and nothing can be
installed from outside this repository.

Who has the problem: anyone who wants their own screens or background jobs in kollab without changing
kollab, and the maintainer, whose PR-chain runner is the first real case.

- Python plugins (`BasePlugin`, `packages/kollabor-plugins/`) load only from the bundled `plugins/`
  folder, or `./plugins` in a dev checkout (`kollabor/application.py:238`). There is no user plugin
  folder, no install command and no package format.
- The web UI is one prebuilt bundle (`packages/kollabor-webui/frontend/dist`). Its main-area views are a
  closed list (`SessionView`, `components/shell/SessionPanel.tsx:87`), the right-hand menu rows are
  hard-coded (MCP Servers, Who Is Online, Settings, Clear History, `SessionPanel.tsx:476`) and daemon
  panels are a closed tuple (`PANEL_MODULES`, `kollabor/panels/base.py:45`).
- The engine (`kollabor_engine`, port 7433) mounts a fixed list of routers (`server.py:344-383`).
- The configuration menu finds plugin settings with its own scanner, apart from the loader: it walks
  only the bundled `plugins/` folder by file name (`*_plugin.py`, `<folder>/plugin.py`), imports every
  module and guesses the class from a name ending in `Plugin`
  (`packages/kollabor-tui/src/kollabor_tui/config_widgets.py:137-386`). `PluginConfigManager`
  (`kollabor_config/plugin_config_manager.py`) keeps a third view. A plugin that lives anywhere else
  never shows up in `/config`, in the terminal or in the web's Settings → Configuration.

So every new web feature is a core change. The PR-chain runner shows that cost. It is a maintainer's
tool that takes one PR at a time through review, fix, audit, UI review, CI and merge, each stage run by
a fresh agent. Outside kollab it rebuilt what kollab already has: its own web board on its own port, a
loop kept alive with `nohup`, an agent launcher made of command lines, its own spend math and its own
config page. Inside kollab it got a hard-coded read-only reader and dialog (#1008:
`kollabor_engine/pr_chain.py`, `routes/pr_chain.py`, `components/pr-chain/`).

## Goal and non-goals

Goal: one plugin package format that can add anything to kollab, in the web UI and the backend,
installed from a git URL or a folder.

The acceptance test is the PR-chain runner rebuilt as a plugin at full parity with its current board:
the Board, Pull Requests, Issues, Spend and Config pages, the loop, the stage agents and the spend
numbers, installed into a throwaway HOME from its git URL.

A plugin can add:

1. Web UI: a row in the right-hand menu, a main-area tab beside Chat and Trajectory, a modal with tabs,
   a full page with tabs, a Settings section, composer slash commands, chat cards and notices.
2. Backend: engine routes, supervised background processes and agent runs through the user's loadouts.
3. The pieces kollab already reads from files (agents, skills, slash commands, config hooks, MCP
   servers) and a `BasePlugin` that loads in every agent, terminal and web alike.

Every surface that lists plugins or their settings reads one discovered list, so nothing about a
plugin is hard-coded in kollab: the terminal's `/config` and the web's Settings → Plugins show the same
plugins, built-in and installed, with the same fields.

Non-goals:

- A sandbox for plugin code (decision D1).
- A hosted registry, a marketplace or an Apps catalog entry. Plugins are added in Settings → Plugins or
  with `kollab plugin add`.
- Plugins inside a project folder (`<folder>/.kollab/plugins`). Code that arrives with a cloned
  repository would run without a trust prompt; that needs its own folder-trust design.
- Hot module reload. A page reload picks up a rebuilt plugin.
- Publishing an SDK package to npm. The types ship inside the hello plugin template.
- Moving the bundled `plugins/` (hub, voice, MCP and the rest) into packages.
- Changing what pr-chain does. It keeps its stages, guards and files.

## What people see

### Web UI

**Right-hand menu.** Each enabled plugin can add rows after Who Is Online, before Settings: an icon, a
Title Case label and a short count on the right, like MCP Servers' `2/5`. PR Chain shows `PR Chain  4`,
the chains running now. Hovering shows `4 chains running, 1 chain needs you`; the second part shows
only when something needs you, and both parts use the singular for 1. Plugin rows sort by plugin name.
On a phone the menu is the existing right-hand sheet and the rows are there too.

**What a row opens.** The plugin picks one of three per row:

- **Tab**: a main-area view beside Chat, Trajectory, Code Editor, Git and Terminal. It belongs to the
  open chat and receives its session and folder. The view list at the top of the right-hand menu shows
  it with the plugin's icon.
- **Modal**: a dialog with the plugin's tabs, the same shape as Settings.
- **Page**: the whole main area with the plugin's own tab bar. PR Chain opens a page with Board, Pull
  Requests, Issues, Spend and Config. Picking an agent in the left sidebar leaves the page.

On a phone a page or modal fills the screen and its tab bar scrolls sideways.

**Plugin agents.** An agent a plugin starts is a normal kollab session. While it runs it shows in the
left sidebar under a group named after the plugin (`PR Chain  2 Agent Runs`, or `1 Agent Run`), and Chat and Trajectory show
its work live. Run states read Done, Timed Out, Token Cap Reached, Failed or Cancelled.

**Settings → Plugins.** A new Settings tab lists every plugin in two groups, Built In (Hub, Voice, MCP
and the rest of the bundled `plugins/`) and Installed: name, version, source, an on/off switch, its
Background Processes with their state, what the plugin uses, and its settings form. Buttons: Add Plugin,
and Update and Remove for installed plugins. Add Plugin takes a git URL or a folder path. Plugin
settings move here from Settings → Configuration, which keeps only kollab's own settings, so each field
shows in one place.

Background process states: `Running · 2 h`; `Running · No Heartbeat for 6 min` (a process that
promised a heartbeat and went quiet); `Exited`; `Stopped`; `Stopped After 5 Crashes · View Log`.

**Add sheet.** Shown before anything is copied or run:

```
Add PR Chain?
From      github.com/<owner>/kollab-pr-chain at 3f2a9c1 (main)
Adds      a right-menu row and a page · code that runs in every agent · engine routes
          1 background process · 2 slash commands · 6 agents · no hooks · no MCP servers
Through kollab  runs agents with your loadouts · agents that run tools without asking
                the secret GITHUB_TOKEN
On its own      runs gh, git, codex and claude · talks to github.com
Kollab refuses requests made through kollab that the plugin did not list here. It cannot stop
plugin code from doing more on its own: plugins run as you, with your files and keys.
Daily Token Cap  2,000,000 tokens a day for agent runs (change it in Settings → Plugins)
Add only plugins you trust.
[Add Plugin]  [Cancel]
```

Every update shows the same sheet with the commit range and the folders that changed, and waits for a
click. Nothing updates by itself. `kollab plugin dev <folder>` shows the sheet with
`From  Working Folder (not pinned)`.

**Turning a plugin off.** With agent runs working, Off asks first: `Turn off PR Chain? 2 agent runs are
working and will be cancelled.` (`1 agent run is working` for one) [Turn Off] [Cancel]. Its background
processes get their stop time to finish the current step. A page that was open then shows `PR Chain is
off` with a Back to Chat button.

**Notices.** A plugin can raise a NoticeBar notice: one line and one button, with no ids or version
numbers in the text: `A pull request is waiting for your review.  [Open]`.

**Composer and chat.** Plugin slash commands appear in the composer palette. A plugin can draw its own
card for a tool or message it owns, such as a PR status card.

**Failures.** A plugin whose code fails while loading keeps its row as `PR Chain  Failed to Load`, and
one that fails while drawing shows `Failed to Render`; both offer Retry and View Error, and everything
else keeps working. `?plugins=off` in the page URL loads the web UI with no plugin code and Settings →
Plugins says `Plugins are off for this page`.

When the daily cap is reached, agent runs fail with `Daily Token Cap reached. Agent runs start again at
00:00 local time.`

### Terminal

```
kollab plugin add <source>          show the add sheet as a prompt, then install
kollab plugin list                  id, version, on/off, background processes, source
kollab plugin enable <id> | disable <id>
kollab plugin update <id>
kollab plugin remove <id> [--purge] --purge also deletes the data folder, after a prompt
kollab plugin logs <id> [<process>]
kollab plugin dev <folder>          run a plugin from a working folder, no copy
kollab plugin new <id>              copy the bundled hello plugin into ./<id>
kollab --no-plugins                 start with no installed plugins ("Plugins are off for this run")
```

The `--purge` prompt: `Delete PR Chain's data folder (~/.kollab/plugin-data/pr-chain)? This cannot be
undone. [y/N]`. `/plugins` in a chat prints the same list as `kollab plugin list`. The Plugins part of
`/config` shows the same plugins and fields as the web's Settings → Plugins, from the same list.

## Contract

### Package layout

```
<plugin>/
  kollab-plugin.json      required
  python/                 optional: BasePlugin and engine router; pure Python, vendored deps in python/vendor/
  web/dist/index.js       optional: built ES module; web/dist/index.css optional
  bin/                    optional: whatever background processes run
  agents/ skills/ commands/ hooks/hooks.json .mcp.json   optional, existing formats
  settings.json           optional: settings fields
```

### Manifest: `kollab-plugin.json`

```jsonc
{
  "id": "pr-chain",                 // ^[a-z0-9][a-z0-9-]{1,39}$; also the install folder name
  "name": "PR Chain",               // Title Case: menu row, sidebar group, Settings
  "version": "0.4.0",
  "description": "Takes one GitHub PR at a time through review, fix, audit, UI review, CI and merge.",
  "kollab": ">=0.23.0",             // add refuses an older kollab
  "python": "python/pr_chain_agent.py:PrChainPlugin",   // BasePlugin; loads in every agent daemon
  "engine": "python/pr_chain_engine.py:make_router",    // def make_router(ctx: PluginContext) -> APIRouter
  "services": {
    "loop": {
      "command": ["python3", "-B", "bin/pr-chain-loop"],
      "autostart": true,
      "restart": "on-failure",      // on-failure (default) | always | never
      "stop_timeout_s": 900,        // time to finish the current step after SIGTERM; default 30
      "heartbeat_s": 900            // optional: warn when heartbeat events stop for this long
    }
  },
  "web": { "entry": "web/dist/index.js", "style": "web/dist/index.css" },
  "agents": "agents", "skills": "skills", "commands": "commands",
  "hooks": "hooks/hooks.json", "mcp": ".mcp.json",
  "settings": "settings.json",
  "uses": ["agents", "agents:unattended", "secret:GITHUB_TOKEN", "run:gh,git,codex,claude", "net:github.com"]
}
```

Every path resolves inside the plugin folder (by realpath; a symlink that leads out is refused).
Unknown keys are refused, so a typo is an error and not a silent no-op. `services` is the manifest key;
the UI calls them Background Processes.

### What `uses` means

| Entry | Lets the plugin | Kollab refuses it when undeclared |
|---|---|---|
| `agents` | start agent runs through `ctx.agents` or `POST /plugins/{id}/agents` | yes, on its routes and `ctx` |
| `agents:unattended` | give those runs approval mode `trust_all` (tools run without asking) | yes, on its routes and `ctx` |
| `secret:<NAME>` | read that one secret through `ctx.secrets`, or pass it into an agent run | yes, on its routes and `ctx` |
| `run:<programs>` | run these programs | no |
| `net:<hosts>` | talk to these hosts | no |
| `files:<paths>` | read or write outside its data folder | no |

A request through kollab without its entry fails with 403 `pr-chain did not declare agents`. That
refusal covers only requests that go through kollab's routes and `ctx`. Plugin code runs as the user,
so it could go around kollab (start `kollab` itself, read the keychain). The add sheet lists the first
three under Through kollab and the rest under On its own, and says so.

### Where plugins come from

`<source>` is one of: an `https://` or `ssh://` git URL, an scp-style `git@host:owner/repo`, or an
existing local folder. Anything else is refused, and nothing starting with `-` reaches git: kollab runs
`git clone -- <url>` and `git -C <dir> checkout --detach <sha>`. A ref follows the last `@` that comes
after the last `/` (`https://github.com/o/r@v1.2`); `git@github.com:o/r` has none and means the default
branch. Before the sheet, kollab resolves the ref to a full 40-character commit with `git ls-remote`;
the sheet, `plugins.json` and `update` use that commit. An update follows the ref recorded at add.

### Where plugins live

| Path | Holds | Written by |
|---|---|---|
| `~/.kollab/plugins/<id>/` | the installed plugin, a clean copy at the pinned commit | `kollab plugin add`, `update` |
| `~/.kollab/plugins.json` | `{"plugins": {"<id>": {"source", "ref", "commit", "enabled", "granted": [...], "dev": "<folder>" or null, "added", "updated"}}}` | `kollab plugin` and Settings → Plugins, under `kollabor_config.file_lock` |
| `~/.kollab/plugin-data/<id>/` | the plugin's own state (`KOLLAB_PLUGIN_DATA`); kept on update and remove | the plugin |
| `~/.kollab/plugin-data/<id>/settings.json` | a copy of its current settings, rewritten on every save | kollab |
| `~/.kollab/plugin-data/<id>/run/` | `token` (mode 0600), `<process>.pid`, `<process>.log`, `<process>.state`, `runs.json` | kollab |

A dev plugin runs from its folder and shadows an installed copy with the same id; Settings → Plugins
marks the installed copy `Shadowed by a Dev Folder`.

Python: the in-process parts (`python`, `engine`) can import kollab's own dependencies and pure-Python
packages vendored under `python/vendor/`. Kollab refuses to load a plugin whose vendored top-level
names are already imported or vendored by another plugin; its row shows `Failed to Load` with the
clashing name. Kollab also ships as one binary, so `add` never runs pip; a background process is any
command and can use its own interpreter.

Plugin Python obeys the terminal rules: it never writes to stdout or stderr. It logs with
`logging.getLogger(__name__)` and shows text only through `message_coordinator`. A `python` entry that
raises while loading is skipped, the agent daemon starts without it, and `/plugins` reports
`state: "error"` with the message.

The web bundle is built before publishing, so `add` never runs Node. `add` and `dev` refuse a manifest
that names `web/dist/index.js` without the file (`web/dist/index.js is missing: build the plugin before
publishing it`) and a bundle that carries its own copy of React (`web/dist/index.js bundles its own
React: mark react as external`).

### Where each part runs

| Part | Process | Lifetime |
|---|---|---|
| `python` (`BasePlugin`) | every agent daemon, after the bundled plugins | the daemon |
| `engine` | the engine | while the engine runs |
| `services` | its own detached process group | until stopped; survives engine and web UI restarts |
| `web` | the browser page | the page |
| agents from `agents.run` | a `kollab --detached` daemon started through the engine's `DaemonPool` | one run |

### Engine routes (`kollabor_engine/routes/plugins.py`)

All sit behind the existing bearer-token middleware.

| Route | Does | Plugin token |
|---|---|---|
| `GET /plugins` | `[{id, name, version, enabled, state: "ok" \| "error" \| "off", error, web: {entry, style, hash} \| null, contributes, services: [{name, state, pid, since, restarts, heartbeat}], uses, granted}]` | no |
| `ANY /plugins/{id}/api/{path}` | the plugin's router. One dispatcher route forwards to a per-plugin sub-app, so enable and disable take effect without restarting the engine | own id |
| `GET`, `POST /plugins/{id}/events` | SSE of `{topic, data, ts}`; publish one. Topic `heartbeat` feeds the process state | own id |
| `GET`, `PUT /plugins/{id}/settings` | read and save its settings (checked against `settings.json`) | own id |
| `POST /plugins/{id}/usage` | report tokens a plugin spent outside kollab agents (`{model, input, output, labels}`) | own id |
| `POST /plugins/{id}/agents`, `GET`, `DELETE /plugins/{id}/agents/{run_id}` | agent runs (below) | own id |
| `POST /plugins/{id}/services/{name}/start`, `/stop`, `/restart`; `GET .../log?tail=200` | background processes | own id |
| `POST /plugins/install` → `{job_id}`; `GET /plugins/install/{job_id}` | clone into staging, check, report progress and the sheet facts | no |
| `POST /plugins/install/{job_id}/confirm` | `{commit, manifest_sha256}`: refused unless they match the staged copy; then move into place and enable | no |
| `POST /plugins/{id}/enable`, `/disable`, `/update`, `/remove` | manage | no |

"No" means the route takes only the user's engine token, so the plugin token cannot enable, update or
install anything, itself included. Plugin code that reads the user's engine token from disk could
(decision D1).

The web UI server serves `GET /plugins/{id}/web/{path}` from the plugin folder with the same
`_safe_asset` check as the app's own files. Plugin code is therefore same-origin on `localhost`, over
PhoneLink and through the `/engine` proxy. Asset URLs carry `?v=<hash of the file>` so a browser never
runs an old bundle against new routes.

The plugin token (`KOLLAB_PLUGIN_TOKEN`) is minted once per plugin, kept in `run/token`, and reused
across engine restarts so adopted processes keep working. The engine accepts it only on its own id's
routes marked above and logs those calls under the plugin id. It narrows mistakes and attributes calls;
it is not a boundary against a plugin that reads `~/.kollab/engine.token` itself.

### Engine-side context (`kollabor_plugins.context.PluginContext`)

Passed to `make_router(ctx)`:

| Member | Does |
|---|---|
| `ctx.id`, `ctx.root`, `ctx.data` | id, plugin folder, data folder |
| `ctx.settings` | current values of the plugin's settings |
| `ctx.events.publish(topic, data)` | SSE to its web code |
| `ctx.notify(text, button=None)` | a NoticeBar notice in every open web UI |
| `await ctx.agents.run(...)` | an agent run; needs `agents` |
| `ctx.secrets.get(name)` | a secret from kollab's secret store; needs `secret:<name>` |
| `ctx.services.status()` | its own background processes |
| `ctx.lock(name)` | a `kollabor_config.file_lock` lock in its data folder |

Background processes get the same facts as environment variables (`KOLLAB_PLUGIN_ID`,
`KOLLAB_PLUGIN_ROOT`, `KOLLAB_PLUGIN_DATA`, `KOLLAB_ENGINE_URL`, `KOLLAB_PLUGIN_TOKEN`) and read
settings from `settings.json` in the data folder.

### Agent runs

```jsonc
// POST /plugins/pr-chain/agents
{
  "brief": "Read brief.md and follow it exactly.",   // first message
  "folder": "/abs/path/to/worktree",                 // checked with folder_or_400
  "agent": "reviewer",                               // agent bundle
  "loadout": "luna-high",                            // one of the user's loadouts
  "tools": ["file-read", "file-grep"],               // agent.json tools allowlist
  "approval": "ask",                                 // "trust_all" needs agents:unattended
  "secrets": [],                                     // names; each needs secret:<NAME>
  "env": { "GH_CONFIG_DIR": "<data>/empty-gh" },     // secret-looking names are refused
  "timeout_s": 3600,                                 // default 3600
  "max_tokens": 250000,                              // default 250000
  "labels": { "pr": "970", "stage": "review" }
}
```

- The engine builds the run's environment itself: the user's environment with secret-looking names
  removed (the filter the Code terminal uses, #987), then `env`, then each granted secret named in
  `secrets`. No other secret reaches the agent.
- It starts the daemon through `DaemonPool` with `KOLLAB_USAGE_LANE=plugin:pr-chain` and
  `KOLLAB_USAGE_PR=970` (usage ledger, #723), sends the brief, waits for `turn_complete`, stops the
  daemon and returns `{status: "done" | "timeout" | "capped" | "failed" | "cancelled", final_text,
  transcript, usage: {input, output, cost, model}}`. The conversation stays on disk for the record.
- Each run is written to `run/runs.json` (run id, session id, daemon pid and start time, deadline,
  token cap, tokens so far) before the daemon starts. A restarted engine re-arms every live run's
  deadline and cap from that file, and cancels a run it cannot re-arm.
- A plugin runs at most 4 agents at once (`plugins.<id>.max_concurrent_runs`); the next start gets 429.
- `tools` goes through the agent bundle's tools allowlist. The allowlist has open gaps (#889, #891), so
  agent runs ship only after both close; build step 2 waits for them.
- Deadlines and caps are enforced while an engine runs. While none runs, nothing stops a run; the next
  engine start cancels any run already past its deadline or cap.

A plugin can still run another harness (Codex, Claude Code) from a background process, as pr-chain does
today. Those tokens count toward the daily cap and the usage ledger only when the plugin reports them
through `POST /plugins/{id}/usage`; tokens it does not report are not capped, and how many such stages
run at once is the plugin's own promise (`loop.threads` for pr-chain).

### Web plugin API

`web/dist/index.js` default-exports a `definePlugin` call from `@kollab/plugin`:

```tsx
import { definePlugin } from "@kollab/plugin";
import { Board, PullRequests, Issues, Spend, Config } from "./pages";

export default definePlugin((k) => {
  k.page({
    id: "board",
    title: "PR Chain",
    tabs: [
      { id: "board", label: "Board", render: Board },
      { id: "prs", label: "Pull Requests", render: PullRequests },
      { id: "issues", label: "Issues", render: Issues },
      { id: "spend", label: "Spend", render: Spend },
      { id: "config", label: "Config", render: Config },
    ],
  });
  k.menu({
    id: "pr-chain",
    label: "PR Chain",
    icon: "git-pull-request",
    count: k.live("/badge", (b) => ({
      text: String(b.running),
      hint: [plural(b.running, "chain running", "chains running"),
             b.waiting ? plural(b.waiting, "chain needs you", "chains need you") : null]
        .filter(Boolean).join(", "),
    })),
    open: { page: "board" },
  });
});
```

Contribution points. Each is one existing component that also reads the plugin registry:

| Call | Shows in | Existing owner |
|---|---|---|
| `k.menu({id, label, icon, count?, open})` | a right-hand menu row | menu rows in `SessionPanel.tsx` |
| `k.view({id, label, icon, render})` | a main-area tab for the open chat | `VIEWS` in `SessionPanel.tsx` |
| `k.page({id, title, tabs})` | the whole main area with a plugin tab bar | main column in `App.tsx` |
| `k.modal({id, title, tabs})` | a dialog with tabs | `components/ui/dialog` |
| `k.settings({render})` | Settings → Plugins → this plugin, under its generated form | `panel-model.ts`, `PanelHost.tsx` |
| `k.command({name, description, run})` | the composer palette | the composer's slash palette |
| `k.renderer({tool or tag, render})` | a chat card for that tool or message tag | `Thread.tsx` |
| `k.notice({text, button})` | NoticeBar | `NoticeBar.tsx` |

`open` is `{page}`, `{modal}`, `{view}` (switches the open chat to that tab) or `{url}`.

What `k` gives a plugin:

- `k.api.get`, `post`, `put`, `delete`: its own engine routes, with auth, through `/engine` when the
  page is remote.
- `k.events.on(topic, fn)` and `k.live(path, map)`: its SSE stream, and a value that refreshes on it.
- `k.settings.get()` and `k.settings.save(values)`: the same store Settings → Plugins edits.
- `k.session` inside a `view`: `{id, folder, agent}`. `k.agents.open(sessionId)` opens an agent's Chat.
- `k.navigate(target)`, `k.copy(text)`, `k.toast(text)`.
- `k.ui`: the app's own components (Button, Tabs, Table, Dialog, Select, Switch, Skeleton, Tooltip,
  FieldRenderer, Gem), so a plugin looks like kollab. The theme's CSS variables (`--background`,
  `--card`, `--muted-foreground`, `--highlight`, `--destructive` and the rest) are on `:root` in both
  themes.

Each plugin view mounts inside `<div data-plugin="<id>">` and an error boundary. Plugin CSS scopes under
that attribute; `kollab plugin add` warns when `index.css` has rules outside it.

Loading: at boot the app reads `GET /plugins`, `import()`s every enabled entry in parallel and awaits
each plugin function. A 5 s limit covers the import and that await. It cannot stop a synchronous loop
in plugin code: that freezes the tab, and `?plugins=off` is the way back in.

One React: the app build adds a `plugin-runtime` entry that re-exports the app's own `react`,
`react-dom`, `react/jsx-runtime` and `@kollab/plugin`, and `index.html` carries an import map from those
names to the built files. A plugin build marks them external, as the hello plugin's `vite.config.ts`
does. The types live in the hello plugin as `web/src/kollab-plugin.d.ts`, generated from
`frontend/src/plugins/sdk.ts`.

### Settings fields

`settings.json` is a list in the existing panel field format (`make_field`,
`docs/specs/webui-unified-config.md`, "wire format"):

```json
[
  { "path": "loop.threads", "type": "spinbox", "label": "Max Threads", "help": "Chains that run at once.",
    "min_value": 1, "max_value": 4, "step": 1, "default": 1 },
  { "path": "issues.build", "type": "checkbox", "label": "Build Issues Into PRs", "default": true }
]
```

There is one store: `plugins.<id>.` in `~/.kollab/config.json`, saved through `PUT /plugins/{id}/settings`
(which checks each value against its field) and copied to `<data>/settings.json` on every save.
Settings → Plugins draws the fields with `FormPanel`, the terminal's `/config` shows them under the
plugin name, and a plugin's own page saves through `k.settings.save`. A plugin whose `uses` includes
`agents` also gets kollab's Daily Token Cap field (`plugins.<id>.max_tokens_per_day`, default
2,000,000) and Max Agent Runs At Once (`plugins.<id>.max_concurrent_runs`, default 4). The daily cap
counts the plugin's agent-run tokens and reported usage from 00:00 to 00:00 local time.

### One list of plugins and their settings

The configuration menu's own scanner goes (`_discover_plugin_modules`, `_find_plugin_class`,
`get_available_plugins` and `get_plugin_config_sections` in `config_widgets.py`). One function on the
plugin registry replaces it:

`kollabor_plugins.registry.plugin_settings() -> [{id, name, kind: "built-in" | "installed" | "dev", enabled, error, sections}]`

- It lists what the loader found: the bundled `plugins/` and every package plugin, on or off. Nothing
  is matched by file name or class name.
- `sections` comes from the plugin's `get_config_widgets()` (any `BasePlugin`) and its `settings.json`
  (packages), both in the panel field format.
- The terminal's `/config` builds its Plugins part from it. The web reads it through `GET /plugins`
  (each entry gains `settings`), and the `config` panel stops adding plugin sections.
- `PluginConfigManager` folds into it. Its readers (`kollabor_config/loader.py:310-315` and `:770`) are
  traced and moved in the same change.

### Background processes

`command` runs with the plugin folder as its working directory, in its own process group, with stdin
closed and output to `run/<process>.log`. `autostart: true` starts it whenever the plugin is enabled.

Supervisor (`kollabor_plugins/services.py`):

- **When it runs.** Every 30 s while an engine runs, and once whenever a kollab process starts: the
  engine, an agent daemon (including the one `kollab service install` starts at login) or a
  `kollab plugin` command. After a reboot, background processes come back with the first of those.
- **Finding a process.** Each child carries `KOLLAB_SERVICE_ID=<plugin>/<process>` and its install's
  `KOLLAB_PLUGIN_DATA` in its environment. Before starting anything, the supervisor looks for a live
  process whose environment has both values of this install (the same environment scan kollab uses to
  tear down test stacks) and adopts it, so a crash between start and writing the pid file never leaves a
  second copy, and a test install never adopts the real one. Starting also takes
  `try_lock(run/<process>.lock)`.
- **Running.** The recorded pid is alive and its start time matches (`_pid_matches_start_time`, as in
  `DaemonPool`). With `heartbeat_s`, the state reads `Running · No Heartbeat for <n> min` once a
  heartbeat event is that late.
- **Crashes.** Restarts back off 2 s, 4 s, 8 s and so on up to 5 min. The count lives in
  `run/<process>.state`, so a restart of kollab does not reset it. Five crashes in 10 minutes stop the
  retries: Settings shows `Stopped After 5 Crashes · View Log`, the menu row's count reads `Stopped`,
  and a notice says `PR Chain stopped after 5 crashes.  [View Log]`.
- **Stopping.** SIGTERM to the process itself, then up to `stop_timeout_s` (default 30 s) for it to
  finish its current step and stop its own children, then SIGKILL to its whole process group. A process
  should treat SIGTERM as "finish this step, start nothing new, exit". Processes it moved into their own
  session are its own to stop.
- **Logs.** The supervisor copies and truncates `run/<process>.log` at 10 MB and keeps 3 copies, so the
  running process keeps writing to the live file.

Disable and remove first ask about live agent runs (see What people see), cancel them, then stop the
plugin's processes. Update stops processes the same way, swaps the folder, and starts them again.

### pr-chain as a plugin

The plugin lives in its own repository and installs with `kollab plugin add <git-url>`.

| pr-chain today | As a plugin |
|---|---|
| the `pr-chain` command | the same script in `bin/`; `/pr-chain <args>` runs it from a chat through its `BasePlugin` command |
| the loop kept alive with `nohup` | background process `loop`, autostart, restart on failure; SIGTERM ends it after the current lap (a small change: it has no SIGTERM handling today) |
| the loop's thread cap in `board-config.json` | plugin settings `loop.threads` and `issues.build`, imported once from `board-config.json` on first enable and read from `<data>/settings.json` every lap after that; the Config tab and Settings → Plugins edit the same values |
| the board's own web server and port | an `engine` router and a `web` page with the same five tabs |
| board JSON routes `/api/state`, `/api/prs`, `/api/pr/<n>` (and `/diff`), `/api/issues`, `/api/issue/<n>`, `/api/spend`, `/api/config` (GET, POST) | the same handlers at `/plugins/pr-chain/api/<same path>`, with the same query parameters (`?pr=`, `?state=`) |
| Agent Output read from the bigger of each stage's `err.txt` and `out.jsonl`, polled every 3 s | unchanged, except that lines holding a token URL are redacted, because the page now reaches other devices |
| stage agents as command lines (`codex exec`, `claude -p`) | still allowed and reported through `/usage`; a stage can also name a kollab loadout, which runs through `agents.run` and shows in the sidebar |
| `ledger.jsonl`, `prs/<n>/STATE.json`, locks, the `STOP` file | the same files under `~/.kollab/plugin-data/pr-chain/` |
| a relay process to reach the board from other devices | not needed: the web UI already serves remote devices with auth |
| `routes/pr_chain.py`, `pr_chain.py` and `components/pr-chain/` in kollab (#1008) | removed from core once the plugin page has parity |

Agents keep the guard pr-chain has today: they get no GitHub credentials, and the plugin's own code,
not an agent, pushes, labels, comments and merges. Command stages keep it too: a scratch HOME, an empty
`GH_CONFIG_DIR` and no GitHub token in their environment. Kollab does not sandbox them.

Parity means that for the same data folder every route above answers what the current board answers,
and screenshots of the five tabs match at 1440, 820 and 390 px wide in dark and light.

### Owning files

| File | Change |
|---|---|
| `packages/kollabor-plugins/src/kollabor_plugins/packages.py` | new: source checks, manifest checks, add, update, remove, `plugins.json` |
| `packages/kollabor-plugins/src/kollabor_plugins/services.py` | new: supervisor |
| `packages/kollabor-plugins/src/kollabor_plugins/context.py` | new: `PluginContext` |
| `packages/kollabor-plugins/src/kollabor_plugins/registry.py` | `plugin_settings()`: the one list |
| `packages/kollabor-tui/src/kollabor_tui/config_widgets.py` | drop the file-name scanner; build the Plugins part from `plugin_settings()` |
| `kollabor/panels/config.py` | stop adding plugin sections to Settings → Configuration |
| `packages/kollabor-config/src/kollabor_config/plugin_config_manager.py`, `loader.py` | fold into `plugin_settings()` |
| `kollabor/application.py` | load `python` entries from enabled packages after the bundled plugins |
| `kollabor/cli.py` | `kollab plugin ...`, `--no-plugins` |
| `packages/kollabor-engine/src/kollabor_engine/routes/plugins.py` | new: the routes above |
| `packages/kollabor-engine/src/kollabor_engine/plugin_agents.py` | new: agent runs over `DaemonPool`, `runs.json`, caps |
| `packages/kollabor-engine/src/kollabor_engine/server.py` | include the plugins router; supervisor tick |
| `packages/kollabor-webui/src/kollabor_webui/server.py` | serve `/plugins/{id}/web/*` |
| `packages/kollabor-webui/frontend/src/plugins/` | new: loader, registry, `sdk.ts`, error boundary |
| `packages/kollabor-webui/frontend/vite.config.ts`, `index.html` | `plugin-runtime` entry and import map |
| `SessionPanel.tsx`, `App.tsx`, `panel-model.ts`, `PanelHost.tsx`, `Thread.tsx`, `NoticeBar.tsx`, `AppSidebar.tsx` | read the registry |
| `bundles/plugins/hello/` | new: the smallest plugin using every part; test fixture and `kollab plugin new` template |

### Build order

1. Packages, supervisor and the one plugin list (`packages.py`, `services.py`, `plugin_settings()` with
   `/config` reading it, the CLI) with the hello plugin. Terminal only.
2. Engine routes and agent runs. Agent runs ship after #889 and #891 close.
3. Web loader, registry and the eight contribution points; Settings → Plugins; the add sheet.
4. The pr-chain plugin in its own repository, to parity.
5. Remove the #1008 reader and dialog.

Steps 2 and 3 share no files and can run at the same time once step 1 lands.

## Scenarios

1. If a plugin is enabled and declares a menu row, then the row shows in the right-hand menu of every
   chat and opens its target. If the plugin is turned off, its row, page and tab go away without a page
   reload, and a page that was open shows `PR Chain is off` with Back to Chat.
2. If a plugin's import or awaited function throws or passes 5 s, then its row shows Failed to Load
   (Retry, View Error) and the rest of the app works. If its view throws while drawing, that view shows
   Failed to Render. If Retry works, the row returns.
3. If background process `loop` exits non-zero, then it restarts after 2 s, 4 s, 8 s and so on; after
   five crashes in 10 minutes it stays stopped, Settings shows `Stopped After 5 Crashes · View Log` and
   a notice appears. If it exits 0 with `restart: on-failure`, it stays stopped and shows `Exited`.
4. If the engine restarts while `loop` runs, then `loop` keeps running, the new engine adopts it by its
   `KOLLAB_SERVICE_ID`, and its plugin token still works. If no live process carries that id, the
   supervisor starts one.
5. If a plugin starts an agent without `agents` in `uses`, then it gets 403 `pr-chain did not declare
   agents`; asking for `approval: "trust_all"` without `agents:unattended` gets the same 403. With both
   declared and granted, the agent shows under PR Chain in the left sidebar while it runs.
6. If a run passes `timeout_s` or `max_tokens`, then the engine stops its daemon and returns `timeout`
   or `capped` with the usage so far. If the engine restarts during the run, the run keeps its deadline
   and cap. A run that finishes first returns `done`.
7. If an update is available, then `kollab plugin update` and Update show the sheet with the commit
   range and changed folders, and nothing changes until the user accepts; the old version keeps running
   meanwhile.
8. If two plugins register the same slash command, then the one enabled first keeps it and the other's
   Settings row says `/deploy is taken by Deploy Bot`. Turning off the first frees it for the second.
9. If the page is open on a phone through PhoneLink, then plugin files load from the web UI server on
   the same origin and `k.api` goes through `/engine`. On `localhost` it goes to the engine directly.
10. If a plugin needs a newer kollab than the one running, then `add` refuses with `PR Chain needs
    kollab 0.23.0 or newer; this is 0.22.1`. A plugin that accepts the running version installs.
11. If the page URL has `?plugins=off` or kollab starts with `--no-plugins`, then no plugin code runs in
    that page or process. Without it, every enabled plugin loads.
12. If a stage agent appends to its transcript, then the Board's Agent Output shows the new text within
    5 s without a page reload (the page polls `/api/state` every 3 s, as the board does today).
13. If the day's tokens for a plugin reach its Daily Token Cap, then its next agent run fails with 429
    and the cap message; at 00:00 local time runs start again.
14. If a plugin is installed from a git URL, then its settings show in the terminal's `/config` and the
    web's Settings → Plugins with no change to kollab's code; if it is removed, they leave both. A
    built-in plugin shows in both lists too, without Update or Remove.

## Failure modes

1. **Web bundle throws or fails to render.** A per-plugin error boundary and a 5 s limit on the import
   and the awaited function; the app boots without it. A synchronous hang freezes the tab; recovery is
   a reload with `?plugins=off`.
2. **Engine router or `python` entry fails while loading.** The engine or the agent daemon logs it,
   skips that part and keeps running; `/plugins` reports `state: "error"` with the message.
3. **Background process crash loop.** Backoff, the five-crash stop that survives restarts, a notice and
   the log one click away.
4. **Runaway agent spend.** Per-run `timeout_s` and `max_tokens` with defaults, at most 4 runs at once,
   and a Daily Token Cap of 2,000,000 tokens by default. Every usage ledger row names `plugin:<id>`.
   Tokens a plugin spends outside kollab agents count only when it reports them. Limits hold while an
   engine runs; the next engine start cancels a run already past its deadline or cap.
5. **Indirect prompt injection.** PR text, diffs and comments are written by PR authors and can carry
   instructions to an agent that reads them. Runs default to `approval: "ask"`; unattended runs need
   `agents:unattended`, which the sheet shows. Review, audit and UI stages get read-only tools and no
   secrets. The plugin's own code, never an agent, pushes, labels, comments and merges.
6. **A malicious or compromised plugin.** Install is the trust decision. The sheet names the source and
   pinned commit, every kind of part (including code that runs in every agent, hooks and MCP servers),
   what kollab checks and what is only declared. Every update asks again; nothing updates by itself; a
   plugin token cannot manage plugins. `kollab plugin disable`, `--no-plugins` and `?plugins=off` turn
   plugins off.
7. **Hostile install source.** Only the source forms above are accepted, git never sees a value that
   starts with `-`, the ref resolves to a commit before the sheet, and confirm checks that commit and
   the manifest hash against the staged copy.
8. **Path escape.** Manifest paths and served files resolve inside the plugin folder, and symlinks out
   are refused. A plugin route that takes a folder calls `folder_or_400`, like every core route.
9. **Remote callers.** Plugin routes sit behind the engine's token middleware and the web UI's private
   boundary, like every core route.
10. **Engine down.** Background processes keep running. Plugin views show the app's existing engine-down
    state, and `k.api` calls fail with that message instead of hanging.
11. **Orphaned runs and half-finished steps.** Turning a plugin off or removing it cancels its live
    agent runs after a confirm. Stopping gives each process `stop_timeout_s` to finish its step.
12. **A cached old bundle.** Asset URLs carry the file hash.

## Decisions

| # | Decision | Who | Why |
|---|---|---|---|
| D1 | Plugin code is trusted and runs as the user. No sandbox. | maintainers, owner-approved | Terminal plugins already run in-process. An iframe would cover only the web half while the Python and process halves keep full access, and it would block shared components, gems and chat cards. VS Code, Claude Code and DeepSeek Harness make the same call. The sheet says it plainly. |
| D2 | Web plugins are ES modules loaded with `import()` that share the app's React through an import map. | maintainers, owner-approved | One React instance, so hooks work across plugin and app, and no Node at install time. DeepSeek Harness serves each plugin's `client.js` and shares React as one platform module the same way. |
| D3 | Plugin entries live in the right-hand menu and open as a tab, a modal with tabs or a page with tabs; eight fixed contribution points in all, each an existing component. | the owner, for the menu and the three targets; maintainers, owner-approved, for the rest | One design system and one set of copy rules; nothing injects markup into arbitrary places. Chat cards and custom settings stay because the owner asked for plugins that can add anything. |
| D4 | Background processes are separate detached processes. | maintainers, owner-approved | Crash isolation, any language, and a web UI restart must not kill a chain mid-stage, which leaves claims, locks and worktrees behind. |
| D5 | Plugin agents are real kollab sessions on the user's loadouts. | maintainers, owner-approved | Live Chat and Trajectory, the usage ledger, tool allowlists and approval modes come for free, and kollab stays the harness under test. |
| D6 | Settings use the panel field format and one store. | maintainers, owner-approved | `FormPanel` and the terminal config already draw it; one store means the plugin's page and Settings never disagree. |
| D7 | Installs come from git URLs and folders, pinned to a resolved commit. | maintainers, owner-approved | Self-hosting needs no hosted account, and a pinned commit keeps the sheet truthful. |
| D8 | No project-folder plugins in this spec. | maintainers, owner-approved | They need a folder-trust design that the goal does not need. |
| D9 | `uses` is a declaration. Kollab's routes and `ctx` refuse undeclared agent runs, unattended runs and secrets; nothing stops plugin code that goes around kollab, and the sheet says so. | maintainers, owner-approved | The refusal catches mistakes and attributes calls; calling it more than that would make the sheet untrue. |
| D10 | Every update asks; a plugin token cannot manage plugins. | maintainers, owner-approved | Changed code that already holds a granted secret is a new trust decision. |
| D11 | Agent runs default to 250,000 tokens, 1 hour, 4 at once and 2,000,000 tokens a day per plugin. | maintainers, owner-approved | An unattended loop with no cap can drain a week of usage; the defaults show on the sheet and change in Settings. |
| D12 | One discovered list of plugins and their settings feeds `/config` and Settings → Plugins; the configuration menu's file-name scanner goes. | the owner | Plugin settings must be discovered, not hard-coded, and one list keeps the terminal and the web from disagreeing. |

### Prior art

| | DeepSeek Harness (`dsh`) | Claude Code | Kollab, this spec |
|---|---|---|---|
| Unit | a Cordis plugin (npm package) in a profile | a folder with `.claude-plugin/plugin.json` | a folder with `kollab-plugin.json` |
| Web UI | the browser runs its own Cordis tree; each plugin's `client.js` loads into named slots (`sidebar.panellist`, `main`, `settings.plugin.item`); React is one shared module | the CLI has none; the desktop app's mods add panels beside the chat, a status line, toasts and slash commands | one ES module per plugin, into eight contribution points |
| Backend | everything is a plugin (model adapter, tools, loop, sandbox, storage) in one process tree | slash commands, agents, skills, shell hooks with JSON on stdin, MCP servers | `BasePlugin` in every agent, engine routes, supervised background processes and agent runs, plus the file-based pieces kollab already reads (hooks use the Claude Code format) |
| Install | `dsh plugin --profile web add <pkg>` through pnpm; a Plugins page in the web sidebar | `/plugin install <name>@<marketplace>`, where a marketplace is a git repository | `kollab plugin add <source>` or Settings → Plugins |
| Trust | in-process; manifest compatibility is not enforced | runs as the user | runs as the user; the sheet; kollab refuses undeclared agent and secret requests on its own routes |
| Reload | hot, per plugin | restart | page reload |

## Test plan

Red first; each test lands failing before its code:

- `tests/unit/plugins/test_manifest.py`: bad id, unknown key, path and symlink escape, kollab too old,
  missing `web/dist`, a bundle with its own React, vendored name clash; the hello plugin passes.
- `tests/unit/plugins/test_sources.py`: a source starting with `-` is refused; `https`, `ssh`, scp form
  and folder accepted; the `@ref` rule including `git@host:o/r`; the ref resolves to a 40-character
  commit; confirm refuses a changed commit or manifest hash.
- `tests/unit/plugins/test_supervisor.py`: detached start, adoption by `KOLLAB_SERVICE_ID` and
  `KOLLAB_PLUGIN_DATA` (two installs with the same plugin never adopt each other's process), pid reuse,
  the lock against a double start, backoff, the five-crash stop surviving a restart, SIGTERM then
  `stop_timeout_s` then SIGKILL, log copy and truncate, the heartbeat label. The fake clock patches
  `time.monotonic` together with `time.sleep`.
- `tests/unit/plugins/test_plugin_settings.py`: built-in, installed and dev plugins all listed with
  their sections; `get_config_widgets()` and `settings.json` give the same field shape; `/config` lists
  a plugin installed outside `plugins/`; the Configuration panel carries no plugin sections.
- `tests/unit/plugins/test_python_entries.py`: every enabled `python` entry loads with stdout and
  stderr captured, and any output fails the test; an entry that raises is skipped and reported.
- `tests/unit/engine/test_plugin_routes.py`: `GET /plugins`; the dispatcher mounts on enable and unmounts
  on disable; 401 without a token; the plugin token refused outside its own id and on every management
  route; 403 for undeclared `agents`, `agents:unattended` and secrets; settings saved to one store and
  copied to `settings.json`.
- `tests/unit/engine/test_plugin_agents.py`: run lifecycle against a stub daemon; defaults applied;
  `timeout`, `capped` and `cancelled`; a secret-looking `env` name refused; `runs.json` re-armed after a
  simulated engine restart; the fifth concurrent run gets 429; the daily cap's 429 and its reset.
- `packages/kollabor-webui/frontend/src/plugins/*.test.ts`: loader error boundary, Failed to Render,
  the 5 s limit, registry add and remove, one React instance through the import map, plural hover text.
- Browser acceptance `frontend/tests/acceptance/plugins.browser.mjs`: the hello plugin adds a row and
  opens a page, a modal and a tab; turning it off removes them live; a throwing plugin shows Failed to
  Load.
- tmux spec `tests/tmux/specs/plugin-cli.json` in a throwaway HOME: `kollab plugin add
  bundles/plugins/hello` shows the prompt, `kollab plugin list` shows the plugin, `/hello` answers.

Live proof, with nothing able to write to GitHub: run kollab under `env -u GH_TOKEN -u GITHUB_TOKEN -u
SSH_AUTH_SOCK GH_CONFIG_DIR=<empty folder>` with a throwaway HOME, load the plugin with `kollab plugin
dev` from a scratch copy whose manifest sets `"autostart": false`, and point it at a copy of a real data
folder.

- Add the plugin from its git URL once to check the sheet and the pinned commit, then run the dev copy
  on the test stack ports (7434 and 8081) and compare every route and the five tabs with the running
  board.
- The copied data folder's `board-config.json` sets a thread count other than the default; after the
  first enable, `GET /plugins/pr-chain/api/config` and the loop's settings file both report it.
- Restart the web UI while a stub background process runs: it keeps running and is adopted.
- Run one review stage through `POST /plugins/pr-chain/agents` on a local worktree copy: the agent shows
  under PR Chain in the sidebar and in Trajectory while it works.
- Pushing, labeling, commenting or merging through the plugin is a separate step that needs the owner's
  yes.

Sibling surfaces to sweep: the right-hand menu (desktop and the phone sheet), Settings tabs, the
composer palette, chat cards, NoticeBar, the left sidebar groups and the terminal's `/config`. UI
review: a vision reviewer runs `ui-slop-review` on 1440, 820 and 390 px screenshots in dark and light.

## Docs and changelog fragment

- `docs/features/plugins.md`: add, manage and write a plugin.
- `docs/reference/agent-guide/plugins-and-hooks.md`: package plugins, contribution points, background
  processes.
- `README.md`: one line in the feature list.
- `changes/1028.added.md` in the first PR that ships behavior: `- Plugins: add one from a git URL or a
  folder; it can add a right-menu row, a tab, modal or page in the web UI, background processes and
  agents (#1028).`

This spec file is docs only, so it carries no changelog fragment.

## Open questions

1. Where the pr-chain plugin repository lives (public or private) and under which account. Its prompts
   and command lines are written for this repository's workflow.
